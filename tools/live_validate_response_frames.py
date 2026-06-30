#!/usr/bin/env python3
"""Validate live MOTU response frame CRC and message sequence fields."""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass, field
from pathlib import Path

from motu_proxy.datastore import MotuUsbDatastore
from motu_proxy.device import DEFAULT_DEVFS_ROOT, DEFAULT_SYSFS_ROOT, find_motu_device
from motu_proxy.parser import is_device_ack
from motu_proxy.paths import normalize_path
from motu_proxy.protocol import (
    DEFAULT_MESSAGE_SEQ,
    DEFAULT_SEQ_START,
    DEFAULT_TIMEOUT_MS,
    MOTU_AVB_PID,
    MOTU_VID,
    build_ack,
    build_get_frame,
    crc32,
)
from motu_proxy.transports.usbfs import UsbFsTransport


DEFAULT_PATHS = (
    "/datastore/uid",
    "/datastore/host/mode",
)


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


@dataclass
class FrameCheck:
    path: str
    index: int
    packet_len: int
    wrapper_seq: int
    wrapper_type: int
    wrapper_len: int
    trailing_padding: int
    header: str
    stored_crc: int
    computed_crc: int
    message_seq: int
    expected_message_seq: int
    final_field: int
    segment_index: int
    payload_len: int
    actual_payload_len: int
    trailer: bytes
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class CaptureResult:
    path: str
    request_seq: int
    message_seq: int
    device_acks: list[bytes] = field(default_factory=list)
    response_packets: list[bytes] = field(default_factory=list)
    host_acks: list[bytes] = field(default_factory=list)
    unexpected_packets: list[bytes] = field(default_factory=list)
    frame_checks: list[FrameCheck] = field(default_factory=list)
    joined_response: bytes = b""

    @property
    def ok(self) -> bool:
        return (
            bool(self.response_packets)
            and not self.unexpected_packets
            and all(check.ok for check in self.frame_checks)
        )


def validate_response_packet(path: str, index: int, packet: bytes, expected_message_seq: int) -> FrameCheck:
    errors: list[str] = []
    if len(packet) < 24:
        errors.append(f"packet too short for wrapper plus MOTU header: {len(packet)} bytes")
        return FrameCheck(
            path=path,
            index=index,
            packet_len=len(packet),
            wrapper_seq=packet[0] if len(packet) > 0 else -1,
            wrapper_type=packet[1] if len(packet) > 1 else -1,
            wrapper_len=_u16(packet + b"\x00" * 4, 2),
            trailing_padding=0,
            header="",
            stored_crc=0,
            computed_crc=0,
            message_seq=-1,
            expected_message_seq=expected_message_seq,
            final_field=0,
            segment_index=0,
            payload_len=0,
            actual_payload_len=0,
            trailer=b"",
            errors=errors,
        )

    wrapper_seq = packet[0]
    wrapper_type = packet[1]
    wrapper_len = _u16(packet, 2)
    if wrapper_len > len(packet):
        logical_packet = packet
        trailing_padding = 0
        errors.append(f"wrapper length {wrapper_len} > packet length {len(packet)}")
    else:
        logical_packet = packet[:wrapper_len]
        trailing_padding = len(packet) - wrapper_len
    wrapper_header = logical_packet[:4]
    body = logical_packet[4:]
    header_bytes = body[:4]
    try:
        header = header_bytes.decode("ascii")
    except UnicodeDecodeError:
        header = header_bytes.hex()
        errors.append(f"non-ascii MOTU header {header_bytes.hex()}")

    stored_crc = _u32(body, 4)
    message_seq = _u32(body, 8)
    final_field = _u32(body, 12)
    segment_index = _u16(body, 16)
    payload_len = _u16(body, 18)
    payload_end = 20 + payload_len
    if payload_end <= len(body):
        payload = body[20:payload_end]
        trailer = body[payload_end:]
    else:
        payload = body[20:]
        trailer = b""
        errors.append(f"payload length {payload_len} exceeds available {len(payload)} bytes")
    computed_crc = crc32(payload)

    if header not in ("NREK", "PTTH"):
        errors.append(f"unexpected MOTU header {header!r}")
    if stored_crc != computed_crc:
        errors.append(f"crc mismatch stored=0x{stored_crc:08x} computed=0x{computed_crc:08x}")
    if message_seq != expected_message_seq:
        errors.append(f"message sequence {message_seq} != expected {expected_message_seq}")
    if payload_len != len(payload):
        errors.append(f"payload length {payload_len} != actual {len(payload)}")
    if len(trailer) != 4:
        errors.append(f"response trailer length {len(trailer)} != 4")
    elif trailer != wrapper_header:
        errors.append(f"response trailer {trailer.hex()} != wrapper {wrapper_header.hex()}")

    return FrameCheck(
        path=path,
        index=index,
        packet_len=len(packet),
        wrapper_seq=wrapper_seq,
        wrapper_type=wrapper_type,
        wrapper_len=wrapper_len,
        trailing_padding=trailing_padding,
        header=header,
        stored_crc=stored_crc,
        computed_crc=computed_crc,
        message_seq=message_seq,
        expected_message_seq=expected_message_seq,
        final_field=final_field,
        segment_index=segment_index,
        payload_len=payload_len,
        actual_payload_len=len(payload),
        trailer=trailer,
        errors=errors,
    )


def response_payload(packet: bytes) -> bytes:
    if len(packet) < 24:
        return b""
    wrapper_len = _u16(packet, 2)
    if wrapper_len > len(packet):
        wrapper_len = len(packet)
    body = packet[:wrapper_len][4:]
    if len(body) < 20:
        return b""
    payload_len = _u16(body, 18)
    return body[20 : 20 + payload_len]


def capture_get(
    datastore: MotuUsbDatastore,
    path: str,
    etag: str,
    max_bytes: int,
) -> CaptureResult:
    path = normalize_path(path)
    request_seq = datastore._next_host_seq()
    message_seq = datastore.message_seq
    datastore.message_seq += 1
    datastore._write_frame(build_get_frame(request_seq, message_seq, path, etag=etag))

    result = CaptureResult(path=path, request_seq=request_seq, message_seq=message_seq)
    total = 0
    quiet_reads = 0

    while quiet_reads < 2:
        packet = datastore._read_logical_frame()
        if not packet:
            quiet_reads += 1
            continue
        quiet_reads = 0

        if is_device_ack(packet):
            result.device_acks.append(packet)
            continue

        body = packet[4:] if len(packet) >= 4 else b""
        if body.startswith((b"NREK", b"PTTH")):
            result.response_packets.append(packet)
            total += len(packet)
            ack = build_ack(datastore._next_host_seq())
            datastore._write_frame(ack)
            result.host_acks.append(ack)
            if total > max_bytes:
                raise RuntimeError(f"response exceeded {max_bytes} bytes")
            continue

        result.unexpected_packets.append(packet)

    result.frame_checks = [
        validate_response_packet(path, index, packet, message_seq)
        for index, packet in enumerate(result.response_packets, start=1)
    ]
    result.joined_response = b"".join(response_payload(packet) for packet in result.response_packets)
    return result


def _preview(data: bytes, limit: int = 96) -> str:
    collapsed = " ".join(data.decode("utf-8", errors="replace").split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[:limit]}..."


def print_result(result: CaptureResult) -> None:
    status = "PASS" if result.ok else "FAIL"
    print(
        f"{status} {result.path}: request_seq=0x{result.request_seq:02x} "
        f"message_seq={result.message_seq} response_frames={len(result.response_packets)} "
        f"device_acks={len(result.device_acks)} host_acks={len(result.host_acks)} "
        f"joined_bytes={len(result.joined_response)}"
    )
    for check in result.frame_checks:
        check_status = "ok" if check.ok else "bad"
        print(
            f"  frame {check.index}: {check_status} wrapper=0x{check.wrapper_seq:02x}/"
            f"0x{check.wrapper_type:02x} len={check.wrapper_len} header={check.header} "
            f"crc=0x{check.stored_crc:08x} msg={check.message_seq} "
            f"final={check.final_field} segment={check.segment_index} "
            f"payload={check.payload_len} trailer={check.trailer.hex()} "
            f"padding={check.trailing_padding}"
        )
        for error in check.errors:
            print(f"    ERROR {error}")
    for index, packet in enumerate(result.unexpected_packets, start=1):
        print(f"  unexpected {index}: len={len(packet)} head={packet[:32].hex(' ')}")
    if result.joined_response:
        print(f"  response preview: {_preview(result.joined_response)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vid", type=lambda value: int(value, 0), default=MOTU_VID)
    parser.add_argument("--pid", type=lambda value: int(value, 0), default=MOTU_AVB_PID)
    parser.add_argument("--serial")
    parser.add_argument("--interface", type=lambda value: int(value, 0))
    parser.add_argument("--ep-out", type=lambda value: int(value, 0))
    parser.add_argument("--ep-in", type=lambda value: int(value, 0))
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--seq-start", type=lambda value: int(value, 0), default=DEFAULT_SEQ_START)
    parser.add_argument("--message-seq", type=int, default=DEFAULT_MESSAGE_SEQ)
    parser.add_argument("--sysfs-root", default=str(DEFAULT_SYSFS_ROOT))
    parser.add_argument("--devfs-root", default=str(DEFAULT_DEVFS_ROOT))
    parser.add_argument("--etag", default="0")
    parser.add_argument("--max-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--no-init", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        help="datastore path to validate; may be repeated",
    )
    parser.add_argument(
        "--include-full-datastore",
        action="store_true",
        help="also validate /datastore, which can return many response frames",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = list(args.paths or DEFAULT_PATHS)
    if args.include_full_datastore and "/datastore" not in paths:
        paths.append("/datastore")

    device = find_motu_device(
        args.vid,
        args.pid,
        serial=args.serial,
        sysfs_root=Path(args.sysfs_root),
        devfs_root=Path(args.devfs_root),
        interface=args.interface,
        ep_out=args.ep_out,
        ep_in=args.ep_in,
    )

    all_results: list[CaptureResult] = []
    with UsbFsTransport(device, timeout_ms=args.timeout_ms, debug=args.debug) as transport:
        datastore = MotuUsbDatastore(transport, seq_start=args.seq_start, message_seq=args.message_seq)
        if not args.no_init:
            datastore.init()
        for path in paths:
            result = capture_get(datastore, path, args.etag, args.max_bytes)
            print_result(result)
            all_results.append(result)

    total_frames = sum(len(result.response_packets) for result in all_results)
    if all(result.ok for result in all_results):
        print(f"live response-frame validation PASS: paths={len(all_results)} frames={total_frames}")
        return 0
    print(f"live response-frame validation FAIL: paths={len(all_results)} frames={total_frames}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
