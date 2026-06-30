"""MOTU datastore request orchestration over a transport."""

from __future__ import annotations

import struct
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

from .device import DEFAULT_DEVFS_ROOT, DEFAULT_SYSFS_ROOT, find_motu_device
from .parser import is_device_ack, join_response_frames
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

    def _next_host_seq(self) -> int:
        return self.host_seq.take()

    def _write_frame(self, frame: bytes) -> None:
        written = self.transport.bulk_write(frame)
        if written != len(frame):
            raise ShortUsbWrite(f"short USB write: wrote {written} of {len(frame)} bytes")

    def init(self) -> None:
        self._write_frame(build_init(self._next_host_seq()))
        self._drain_quiet(quiet_reads=1, timeout_ms=200)

    def get(self, path: str, etag: str = "0") -> bytes:
        frame = build_get_frame(self._next_host_seq(), self.message_seq, path, etag=etag)
        self.message_seq += 1
        self._write_frame(frame)
        return self._collect_response()

    def post(self, path: str, json_body: str) -> bytes:
        frame = build_post_frame(self._next_host_seq(), self.message_seq, path, json_body)
        self.message_seq += 1
        self._write_frame(frame)
        return self._collect_response()

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

    def _collect_response(self, max_bytes: int = 1024 * 1024) -> bytes:
        frames: list[bytes] = []
        pending: list[bytes] = []
        total = 0
        quiet_reads = 0

        while quiet_reads < 2:
            packet = pending.pop(0) if pending else self._read_logical_frame()
            if not packet:
                quiet_reads += 1
                continue
            quiet_reads = 0

            if is_device_ack(packet):
                continue

            body = packet[4:] if len(packet) >= 4 else packet
            if body.startswith((b"NREK", b"PTTH")):
                frames.append(body)
                total += len(body)
                self._write_frame(build_ack(self._next_host_seq()))
                pending.extend(self._drain_quiet(quiet_reads=1, timeout_ms=120))
                if total > max_bytes:
                    raise RuntimeError(f"response exceeded {max_bytes} bytes")

        if not frames:
            return b""
        return join_response_frames(frames)

    def _drain_quiet(self, quiet_reads: int, timeout_ms: int) -> list[bytes]:
        packets: list[bytes] = []
        quiet = 0
        while quiet < quiet_reads:
            packet = self._read_logical_frame(timeout_ms=timeout_ms)
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
