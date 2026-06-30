"""MOTU datastore request orchestration over a transport."""

from __future__ import annotations

import json
import struct
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Protocol

from .device import DEFAULT_DEVFS_ROOT, DEFAULT_SYSFS_ROOT, find_motu_device
from .parser import (
    DatastorePayload,
    datastore_payload,
    extract_response_etag,
    is_device_ack,
    join_response_frames,
    parse_response_frame,
    response_status_code,
)
from .protocol import (
    DEFAULT_MAX_USB_CHUNK,
    DEFAULT_MESSAGE_SEQ,
    DEFAULT_SEQ_START,
    DEFAULT_TIMEOUT_MS,
    MOTU_AVB_PID,
    MOTU_VID,
    HostSequencer,
    build_ack,
    build_get_frame,
    build_init,
    build_post_frame,
)
from .transports.usbfs import UsbFsTransport


DEFAULT_RESPONSE_TIMEOUT_MS = DEFAULT_TIMEOUT_MS * 2
DEFAULT_LONG_POLL_TIMEOUT_MS = 16_000
DEFAULT_HTTP_LONG_POLL_WAIT_MS = 15_500
DEFAULT_ETAG_HISTORY_SIZE = 64
DEFAULT_MAX_RESPONSE_READS = 256
DEFAULT_MAX_IGNORED_PACKETS = 32
DEFAULT_MAX_RESPONSE_FRAMES = 256
DEFAULT_POLL_PATH = "/datastore"
DEFAULT_POLL_READ_TIMEOUT_SLICE_MS = 250
CAPABILITY_SECTIONS = ("avb", "router", "mixer")
IDENTITY_KEYS = ("uid", "model_name", "firmware_version", "serial_number")


class Transport(Protocol):
    max_packet_size: int

    def bulk_write(self, data: bytes) -> int:
        ...

    def bulk_read(self, size: int | None = None, timeout_ms: int | None = None) -> bytes:
        ...


class DatastoreReader(Protocol):
    def get(self, path: str) -> bytes:
        ...


class ShortUsbFrame(RuntimeError):
    pass


class ShortUsbWrite(RuntimeError):
    pass


class DatastoreNoResponse(RuntimeError):
    pass


class DatastoreTimeout(RuntimeError):
    pass


class DatastoreResponseLimit(RuntimeError):
    pass


class DatastoreCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ResponseStats:
    timeout_ms: int
    elapsed_ms: float
    reads: int
    accepted_frames: int
    ignored_packets: int
    ack_packets: int
    response_bytes: int


@dataclass(frozen=True)
class DatastoreConfig:
    vid: int = MOTU_VID
    pid: int = MOTU_AVB_PID
    serial: str | None = None
    interface: int | None = None
    ep_out: int | None = None
    ep_in: int | None = None
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    seq_start: int = DEFAULT_SEQ_START
    message_seq: int = DEFAULT_MESSAGE_SEQ
    no_init: bool = False
    debug: bool = False
    sysfs_root: Path = DEFAULT_SYSFS_ROOT
    devfs_root: Path = DEFAULT_DEVFS_ROOT


@dataclass(frozen=True)
class DatastoreTransition:
    from_etag: str
    to_etag: str
    body: bytes
    origin_client: str | None = None


@dataclass(frozen=True)
class CapabilityVersion:
    present: bool
    version: object | None

    def as_dict(self) -> dict[str, object | None]:
        return {
            "present": self.present,
            "version": self.version,
        }


@dataclass(frozen=True)
class DeviceCapabilityInfo:
    apiversion: object | None
    capabilities: dict[str, CapabilityVersion]
    identity: dict[str, object | None]

    def as_dict(self) -> dict[str, object | None]:
        return {
            "apiversion": self.apiversion,
            "capabilities": {
                section: capability.as_dict()
                for section, capability in self.capabilities.items()
            },
            "identity": dict(self.identity),
        }


class MotuUsbDatastore:
    def __init__(
        self,
        transport: Transport,
        seq_start: int = DEFAULT_SEQ_START,
        message_seq: int = DEFAULT_MESSAGE_SEQ,
    ) -> None:
        self.transport = transport
        self.host_seq = HostSequencer(seq_start)
        self.message_seq = message_seq
        self.last_response_stats: ResponseStats | None = None
        self.last_response_etag: str | None = None

    def _next_host_seq(self) -> int:
        return self.host_seq.take()

    def _write_frame(self, frame: bytes) -> None:
        written = self.transport.bulk_write(frame)
        if written != len(frame):
            raise ShortUsbWrite(f"short USB write: wrote {written} of {len(frame)} bytes")

    def init(self) -> None:
        self._write_frame(build_init(self._next_host_seq()))
        self._drain_quiet(quiet_reads=1, timeout_ms=200)

    def get(
        self,
        path: str,
        etag: str = "0",
        client: str | int | None = None,
        timeout_ms: int = DEFAULT_RESPONSE_TIMEOUT_MS,
        should_cancel: Callable[[], bool] | None = None,
        read_timeout_slice_ms: int | None = None,
    ) -> bytes:
        message_seq = self.message_seq
        frame = build_get_frame(self._next_host_seq(), message_seq, path, etag=etag, client=client)
        self._write_frame(frame)
        self.message_seq += 1
        return self._collect_response(
            message_seq,
            timeout_ms=timeout_ms,
            should_cancel=should_cancel,
            read_timeout_slice_ms=read_timeout_slice_ms,
        )

    def post(
        self,
        path: str,
        json_body: str,
        client: str | int | None = None,
        timeout_ms: int = DEFAULT_RESPONSE_TIMEOUT_MS,
    ) -> bytes:
        message_seq = self.message_seq
        frame = build_post_frame(self._next_host_seq(), message_seq, path, json_body, client=client)
        self._write_frame(frame)
        self.message_seq += 1
        return self._collect_response(message_seq, timeout_ms=timeout_ms)

    def _read_logical_frame(self, timeout_ms: int | None = None) -> bytes:
        max_chunk = getattr(self.transport, "max_packet_size", DEFAULT_MAX_USB_CHUNK) or DEFAULT_MAX_USB_CHUNK
        first = self.transport.bulk_read(max_chunk, timeout_ms=timeout_ms)
        if not first:
            return b""
        if len(first) < 4:
            raise ShortUsbFrame(f"short USB logical frame header: got {len(first)} bytes")
        expected = struct.unpack_from("<H", first, 2)[0]
        if expected < 4:
            raise ShortUsbFrame(f"invalid USB logical frame length {expected}")
        chunks = [first]
        got = len(first)
        while got < expected:
            chunk = self.transport.bulk_read(max_chunk, timeout_ms=timeout_ms)
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
        if got < expected:
            raise ShortUsbFrame(f"short USB logical frame: got {got} of {expected} bytes")
        return b"".join(chunks)

    def _collect_response(
        self,
        expected_message_seq: int,
        max_bytes: int = 1024 * 1024,
        timeout_ms: int = DEFAULT_RESPONSE_TIMEOUT_MS,
        max_reads: int = DEFAULT_MAX_RESPONSE_READS,
        max_ignored_packets: int = DEFAULT_MAX_IGNORED_PACKETS,
        max_response_frames: int = DEFAULT_MAX_RESPONSE_FRAMES,
        should_cancel: Callable[[], bool] | None = None,
        read_timeout_slice_ms: int | None = None,
    ) -> bytes:
        self.last_response_stats = None
        self.last_response_etag = None
        frames: list[bytes] = []
        total = 0
        quiet_reads = 0
        reads = 0
        ignored_packets = 0
        ack_packets = 0
        started = time.monotonic()
        deadline = time.monotonic() + (timeout_ms / 1000)

        while quiet_reads < 2:
            if should_cancel is not None and should_cancel():
                self._record_response_stats(
                    timeout_ms,
                    started,
                    reads,
                    len(frames),
                    ignored_packets,
                    ack_packets,
                    total,
                )
                raise DatastoreCancelled("datastore response collection cancelled")
            if reads >= max_reads:
                self._record_response_stats(
                    timeout_ms,
                    started,
                    reads,
                    len(frames),
                    ignored_packets,
                    ack_packets,
                    total,
                )
                raise DatastoreTimeout(
                    _response_wait_message(
                        f"response read limit exceeded after {max_reads} reads",
                        timeout_ms,
                        reads,
                        len(frames),
                        ignored_packets,
                        ack_packets,
                    )
                )
            now = time.monotonic()
            if now >= deadline:
                error_cls = DatastoreTimeout if frames else DatastoreNoResponse
                reason = (
                    f"response timed out after {timeout_ms} ms"
                    if frames
                    else f"no datastore response after {timeout_ms} ms"
                )
                self._record_response_stats(
                    timeout_ms,
                    started,
                    reads,
                    len(frames),
                    ignored_packets,
                    ack_packets,
                    total,
                )
                raise error_cls(
                    _response_wait_message(
                        reason,
                        timeout_ms,
                        reads,
                        len(frames),
                        ignored_packets,
                        ack_packets,
                    )
                )
            read_timeout_ms = max(1, int((deadline - now) * 1000))
            if read_timeout_slice_ms is not None:
                read_timeout_ms = min(read_timeout_ms, max(1, read_timeout_slice_ms))
            packet = self._read_logical_frame(timeout_ms=read_timeout_ms)
            reads += 1
            if not packet:
                if read_timeout_slice_ms is not None and time.monotonic() < deadline:
                    continue
                quiet_reads += 1
                continue
            quiet_reads = 0

            if is_device_ack(packet):
                ack_packets += 1
                continue

            body = packet[4:] if len(packet) >= 4 else packet
            if body.startswith((b"NREK", b"PTTH")):
                parsed = parse_response_frame(packet, expected_message_seq)
                frames.append(packet)
                total += len(packet)
                if len(frames) > max_response_frames:
                    self._record_response_stats(
                        timeout_ms,
                        started,
                        reads,
                        len(frames),
                        ignored_packets,
                        ack_packets,
                        total,
                    )
                    raise DatastoreResponseLimit(
                        f"response exceeded {max_response_frames} frames"
                    )
                self._write_frame(build_ack(self._next_host_seq()))
                if total > max_bytes:
                    self._record_response_stats(
                        timeout_ms,
                        started,
                        reads,
                        len(frames),
                        ignored_packets,
                        ack_packets,
                        total,
                    )
                    raise DatastoreResponseLimit(f"response exceeded {max_bytes} bytes")
                if parsed.final:
                    break
                continue

            ignored_packets += 1
            if ignored_packets > max_ignored_packets:
                self._record_response_stats(
                    timeout_ms,
                    started,
                    reads,
                    len(frames),
                    ignored_packets,
                    ack_packets,
                    total,
                )
                raise DatastoreTimeout(
                    _response_wait_message(
                        f"response ignored packet limit exceeded after {max_ignored_packets} packets",
                        timeout_ms,
                        reads,
                        len(frames),
                        ignored_packets,
                        ack_packets,
                    )
                )

        if not frames:
            self._record_response_stats(
                timeout_ms,
                started,
                reads,
                0,
                ignored_packets,
                ack_packets,
                total,
            )
            raise DatastoreNoResponse(
                _response_wait_message(
                    f"no datastore response after {timeout_ms} ms",
                    timeout_ms,
                    reads,
                    0,
                    ignored_packets,
                    ack_packets,
                )
            )
        response = join_response_frames(frames, expected_message_seq)
        self.last_response_etag = extract_response_etag(response)
        self._record_response_stats(
            timeout_ms,
            started,
            reads,
            len(frames),
            ignored_packets,
            ack_packets,
            total,
        )
        return response

    def _record_response_stats(
        self,
        timeout_ms: int,
        started: float,
        reads: int,
        accepted_frames: int,
        ignored_packets: int,
        ack_packets: int,
        response_bytes: int,
    ) -> None:
        self.last_response_stats = ResponseStats(
            timeout_ms=timeout_ms,
            elapsed_ms=(time.monotonic() - started) * 1000,
            reads=reads,
            accepted_frames=accepted_frames,
            ignored_packets=ignored_packets,
            ack_packets=ack_packets,
            response_bytes=response_bytes,
        )

    def _drain_quiet(self, quiet_reads: int, timeout_ms: int, max_reads: int = 16) -> list[bytes]:
        packets: list[bytes] = []
        quiet = 0
        reads = 0
        while quiet < quiet_reads:
            if reads >= max_reads:
                raise DatastoreTimeout(f"USB drain did not become quiet after {max_reads} reads")
            packet = self._read_logical_frame(timeout_ms=timeout_ms)
            reads += 1
            if packet:
                packets.append(packet)
                quiet = 0
            else:
                quiet += 1
        return packets


def read_device_capability_info(datastore: DatastoreReader) -> DeviceCapabilityInfo:
    apiversion = _read_required_datastore_value(datastore, "/apiversion")
    capabilities = {
        section: CapabilityVersion(present, value)
        for section in CAPABILITY_SECTIONS
        for present, value in [_read_optional_datastore_value(datastore, f"/datastore/ext/caps/{section}")]
    }
    identity = {
        key: value
        for key in IDENTITY_KEYS
        for _present, value in [_read_optional_datastore_value(datastore, f"/datastore/{key}")]
    }
    return DeviceCapabilityInfo(
        apiversion=apiversion,
        capabilities=capabilities,
        identity=identity,
    )


def _read_required_datastore_value(datastore: DatastoreReader, path: str) -> object | None:
    return _decode_datastore_value(datastore.get(path))


def _read_optional_datastore_value(datastore: DatastoreReader, path: str) -> tuple[bool, object | None]:
    try:
        response = datastore.get(path)
    except DatastoreNoResponse:
        return False, None
    except RuntimeError as exc:
        if _looks_like_absent_path(exc):
            return False, None
        raise
    if response_status_code(response) == 404:
        return False, None
    value = _decode_datastore_value(response)
    return value is not None, value


def _decode_datastore_value(response: bytes) -> object | None:
    body = datastore_payload(response).body.strip()
    if not body:
        return None
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body.decode("utf-8", errors="replace").strip() or None
    if isinstance(decoded, dict) and "value" in decoded:
        return decoded["value"]
    return decoded


def _looks_like_absent_path(exc: RuntimeError) -> bool:
    message = str(exc).lower()
    return "404" in message or "not found" in message or "does not exist" in message


class DatastoreCoordinator:
    def __init__(
        self,
        datastore: MotuUsbDatastore,
        poll_path: str = DEFAULT_POLL_PATH,
        long_poll_timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS,
        http_wait_timeout_ms: int = DEFAULT_HTTP_LONG_POLL_WAIT_MS,
        history_size: int = DEFAULT_ETAG_HISTORY_SIZE,
        poll_interval_s: float = 0.05,
        poll_read_timeout_slice_ms: int = DEFAULT_POLL_READ_TIMEOUT_SLICE_MS,
    ) -> None:
        self.datastore = datastore
        self.poll_path = poll_path
        self.long_poll_timeout_ms = long_poll_timeout_ms
        self.http_wait_timeout_ms = http_wait_timeout_ms
        self.poll_interval_s = poll_interval_s
        self.poll_read_timeout_slice_ms = poll_read_timeout_slice_ms
        self._condition = threading.Condition()
        self._io_busy = False
        self._foreground_waiters = 0
        self._history: deque[DatastoreTransition] = deque(maxlen=history_size)
        self._latest_etag: str | None = None
        self._closed = False
        self._worker: threading.Thread | None = None
        self.last_poller_error: Exception | None = None

    @property
    def latest_etag(self) -> str | None:
        with self._condition:
            return self._latest_etag

    @property
    def history(self) -> tuple[DatastoreTransition, ...]:
        with self._condition:
            return tuple(self._history)

    def start(self) -> None:
        with self._condition:
            if self._worker is not None:
                return
            self._worker = threading.Thread(
                target=self._poll_loop,
                name="motu-datastore-long-poll",
                daemon=True,
            )
            self._worker.start()

    def close(self, timeout: float | None = None) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=timeout)

    def get(
        self,
        path: str,
        client: str | int | None = None,
        if_none_match: str | None = None,
    ) -> DatastorePayload:
        etag = _clean_etag(if_none_match)
        if etag is not None:
            return self.wait_for_change(path, etag, client=client)
        return self.read(path, client=client)

    def read(
        self,
        path: str,
        etag: str = "0",
        client: str | int | None = None,
        timeout_ms: int = DEFAULT_RESPONSE_TIMEOUT_MS,
    ) -> DatastorePayload:
        self._acquire_foreground_io()
        try:
            response = self.datastore.get(path, etag=etag, client=client, timeout_ms=timeout_ms)
            payload = self._payload_from_response(response)
        finally:
            self._release_io()
        self._publish_payload(payload, origin_client=None, record_transition=path == self.poll_path)
        return payload

    def post(self, path: str, json_body: str, client: str | int | None = None) -> DatastorePayload:
        origin_client = _client_string(client)
        refresh: DatastorePayload | None = None
        self._acquire_foreground_io()
        try:
            response = self.datastore.post(path, json_body, client=client)
            payload = self._payload_from_response(response)
            try:
                refresh = self._read_locked(self.poll_path, etag="0", client=None)
                self.last_poller_error = None
            except Exception as exc:
                self.last_poller_error = exc
        finally:
            self._release_io()
        if refresh is not None:
            self._publish_payload(refresh, origin_client=origin_client)
        return payload

    def wait_for_change(
        self,
        path: str,
        etag: str,
        client: str | int | None = None,
    ) -> DatastorePayload:
        deadline = time.monotonic() + (self.http_wait_timeout_ms / 1000)
        client_id = _client_string(client)
        while True:
            with self._condition:
                transition, should_refresh = self._find_wait_outcome_locked(etag, client_id)
                if transition is not None:
                    if path == self.poll_path:
                        return DatastorePayload(transition.body, etag=transition.to_etag)
                    should_refresh = True
                if should_refresh:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closed:
                    return DatastorePayload(b"", etag=etag, not_modified=True)
                self._condition.wait(remaining)
        return self.read(path, etag="0", client=client)

    def _poll_loop(self) -> None:
        while True:
            with self._condition:
                if self._closed:
                    return
                etag = self._latest_etag or "0"
            try:
                if not self._acquire_poller_io():
                    return
                try:
                    response = self.datastore.get(
                        self.poll_path,
                        etag=etag,
                        timeout_ms=self.long_poll_timeout_ms,
                        should_cancel=self._is_closed,
                        read_timeout_slice_ms=self.poll_read_timeout_slice_ms,
                    )
                    payload = self._payload_from_response(response)
                finally:
                    self._release_io()
                self.last_poller_error = None
                self._publish_payload(payload, origin_client=None, from_etag=etag)
            except DatastoreCancelled as exc:
                if self._is_closed():
                    return
                self.last_poller_error = exc
            except (DatastoreNoResponse, DatastoreTimeout) as exc:
                self.last_poller_error = exc
            except Exception as exc:
                self.last_poller_error = exc
            with self._condition:
                if self._closed:
                    return
                self._condition.wait(self.poll_interval_s)

    def _is_closed(self) -> bool:
        with self._condition:
            return self._closed

    def _read_locked(
        self,
        path: str,
        etag: str = "0",
        client: str | int | None = None,
        timeout_ms: int = DEFAULT_RESPONSE_TIMEOUT_MS,
    ) -> DatastorePayload:
        response = self.datastore.get(path, etag=etag, client=client, timeout_ms=timeout_ms)
        return self._payload_from_response(response)

    def _acquire_foreground_io(self) -> None:
        with self._condition:
            self._foreground_waiters += 1
            self._condition.notify_all()
            try:
                while self._io_busy:
                    self._condition.wait()
                self._io_busy = True
            finally:
                self._foreground_waiters -= 1
                self._condition.notify_all()

    def _acquire_poller_io(self) -> bool:
        with self._condition:
            while self._io_busy or self._foreground_waiters > 0:
                if self._closed:
                    return False
                self._condition.wait()
            if self._closed:
                return False
            self._io_busy = True
            return True

    def _release_io(self) -> None:
        with self._condition:
            self._io_busy = False
            self._condition.notify_all()

    def _payload_from_response(self, response: bytes) -> DatastorePayload:
        payload = datastore_payload(response)
        return DatastorePayload(
            payload.body,
            etag=payload.etag or getattr(self.datastore, "last_response_etag", None),
            not_modified=payload.not_modified,
        )

    def _publish_payload(
        self,
        payload: DatastorePayload,
        origin_client: str | None,
        from_etag: str | None = None,
        record_transition: bool = True,
    ) -> None:
        if payload.etag is None:
            return
        with self._condition:
            if payload.not_modified:
                self._latest_etag = payload.etag
                self._condition.notify_all()
                return
            previous = from_etag if from_etag is not None else self._latest_etag
            if record_transition and previous is not None and previous != payload.etag:
                self._history.append(
                    DatastoreTransition(
                        from_etag=previous,
                        to_etag=payload.etag,
                        body=payload.body,
                        origin_client=origin_client,
                    )
                )
            self._latest_etag = payload.etag
            self._condition.notify_all()

    def _find_wait_outcome_locked(
        self,
        etag: str,
        client: str | None,
    ) -> tuple[DatastoreTransition | None, bool]:
        suppressed: DatastoreTransition | None = None
        for transition in reversed(self._history):
            if transition.from_etag != etag:
                continue
            if client is not None and transition.origin_client == client:
                suppressed = transition
                continue
            return transition, False
        if suppressed is not None:
            return None, self._latest_etag != suppressed.to_etag
        if self._latest_etag is not None and self._latest_etag != etag:
            return None, True
        return None, False


@contextmanager
def open_datastore(config: DatastoreConfig) -> Iterator[MotuUsbDatastore]:
    device = find_motu_device(
        config.vid,
        config.pid,
        serial=config.serial,
        sysfs_root=config.sysfs_root,
        devfs_root=config.devfs_root,
        interface=config.interface,
        ep_out=config.ep_out,
        ep_in=config.ep_in,
    )
    with UsbFsTransport(device, timeout_ms=config.timeout_ms, debug=config.debug) as transport:
        datastore = MotuUsbDatastore(transport, seq_start=config.seq_start, message_seq=config.message_seq)
        if not config.no_init:
            datastore.init()
        yield datastore


def _response_wait_message(
    reason: str,
    timeout_ms: int,
    reads: int,
    accepted: int,
    ignored: int,
    ack: int,
) -> str:
    return (
        f"{reason}; timeout_ms={timeout_ms} reads={reads} "
        f"accepted={accepted} ignored={ignored} ack={ack}"
    )


def _clean_etag(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _client_string(value: str | int | None) -> str | None:
    if value is None:
        return None
    return str(value)
