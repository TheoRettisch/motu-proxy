"""MOTU datastore request orchestration over a transport."""

from __future__ import annotations

import struct
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from .device import DEFAULT_DEVFS_ROOT, DEFAULT_SYSFS_ROOT, find_motu_device
from .parser import extract_response_etag, is_device_ack, join_response_frames, parse_response_frame
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
DEFAULT_MAX_RESPONSE_READS = 256
DEFAULT_MAX_IGNORED_PACKETS = 32
DEFAULT_MAX_RESPONSE_FRAMES = 256


class Transport(Protocol):
    max_packet_size: int

    def bulk_write(self, data: bytes) -> int:
        ...

    def bulk_read(self, size: int | None = None, timeout_ms: int | None = None) -> bytes:
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

    def get(self, path: str, etag: str = "0", client: str | int | None = None) -> bytes:
        message_seq = self.message_seq
        frame = build_get_frame(self._next_host_seq(), message_seq, path, etag=etag, client=client)
        self.message_seq += 1
        self._write_frame(frame)
        return self._collect_response(message_seq)

    def post(self, path: str, json_body: str, client: str | int | None = None) -> bytes:
        message_seq = self.message_seq
        frame = build_post_frame(self._next_host_seq(), message_seq, path, json_body, client=client)
        self.message_seq += 1
        self._write_frame(frame)
        return self._collect_response(message_seq)

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
            chunk = self.transport.bulk_read(min(max_chunk, expected - got), timeout_ms=timeout_ms)
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
            packet = self._read_logical_frame(timeout_ms=read_timeout_ms)
            reads += 1
            if not packet:
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
