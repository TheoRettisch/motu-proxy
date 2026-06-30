"""Response helpers for the MOTU datastore protocol."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

from .protocol import crc32


class ResponseFrameError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResponseFrame:
    header: bytes
    wrapper_seq: int
    wrapper_type: int
    wrapper_len: int
    message_seq: int
    final: bool
    segment_index: int
    payload: bytes


def is_device_ack(packet: bytes) -> bool:
    return len(packet) == 8 and packet[:4] == packet[4:] and packet[1] == 0 and packet[2:4] == b"\x08\x00"


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def parse_response_frame(packet: bytes, expected_message_seq: int) -> ResponseFrame:
    if len(packet) < 28:
        raise ResponseFrameError(f"short response frame: got {len(packet)} bytes")

    wrapper_seq = packet[0]
    wrapper_type = packet[1]
    wrapper_len = _u16(packet, 2)
    if wrapper_type != 0:
        raise ResponseFrameError(f"unexpected response wrapper type 0x{wrapper_type:02x}")
    if wrapper_len < 28:
        raise ResponseFrameError(f"invalid response wrapper length {wrapper_len}")
    if wrapper_len > len(packet):
        raise ResponseFrameError(f"short response frame: got {len(packet)} of {wrapper_len} bytes")

    logical_packet = packet[:wrapper_len]
    wrapper = logical_packet[:4]
    body = logical_packet[4:]
    header = body[:4]
    if header not in (b"NREK", b"PTTH"):
        raise ResponseFrameError(f"unexpected response header {header!r}")

    stored_crc = _u32(body, 4)
    message_seq = _u32(body, 8)
    final_field = _u32(body, 12)
    segment_index = _u16(body, 16)
    payload_len = _u16(body, 18)
    payload_end = 20 + payload_len
    trailer_end = payload_end + 4

    if message_seq != expected_message_seq:
        raise ResponseFrameError(f"response message sequence {message_seq} != expected {expected_message_seq}")
    if final_field not in (0, 1):
        raise ResponseFrameError(f"unexpected response final field {final_field}")
    if trailer_end > len(body):
        raise ResponseFrameError(f"response payload length {payload_len} exceeds frame body")
    if trailer_end != len(body):
        raise ResponseFrameError(f"unexpected response body trailer length {len(body) - payload_end}")

    payload = body[20:payload_end]
    computed_crc = crc32(payload)
    if stored_crc != computed_crc:
        raise ResponseFrameError(f"response CRC mismatch: stored=0x{stored_crc:08x} computed=0x{computed_crc:08x}")

    trailer = body[payload_end:trailer_end]
    if trailer != wrapper:
        raise ResponseFrameError(f"response trailer {trailer.hex()} != wrapper {wrapper.hex()}")

    return ResponseFrame(
        header=header,
        wrapper_seq=wrapper_seq,
        wrapper_type=wrapper_type,
        wrapper_len=wrapper_len,
        message_seq=message_seq,
        final=bool(final_field),
        segment_index=segment_index,
        payload=payload,
    )


def join_response_frames(frames: list[bytes], expected_message_seq: int) -> bytes:
    parsed = [parse_response_frame(frame, expected_message_seq) for frame in frames]
    if not parsed:
        return b""

    pieces: list[bytes] = []
    for expected_index, frame in enumerate(parsed):
        if frame.segment_index != expected_index:
            raise ResponseFrameError(
                f"response segment index {frame.segment_index} != expected {expected_index}"
            )
        if frame.final and expected_index != len(parsed) - 1:
            raise ResponseFrameError(f"response final flag set before last segment {expected_index}")
        if not frame.final and expected_index == len(parsed) - 1:
            raise ResponseFrameError("response missing final segment flag")
        pieces.append(frame.payload)
    return b"".join(pieces)


def extract_json_bytes(response: bytes) -> bytes | None:
    start = response.find(b"{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(response)):
        byte = response[index]
        if in_string:
            if escape:
                escape = False
            elif byte == ord("\\"):
                escape = True
            elif byte == ord('"'):
                in_string = False
            continue
        if byte == ord('"'):
            in_string = True
        elif byte == ord("{"):
            depth += 1
        elif byte == ord("}"):
            depth -= 1
            if depth == 0:
                return response[start : index + 1]
    return None


def response_to_text(response: bytes, pretty: bool = True) -> str:
    json_bytes = extract_json_bytes(response)
    if json_bytes is not None:
        text = json_bytes.decode("utf-8", errors="replace")
        if pretty:
            try:
                return json.dumps(json.loads(text), indent=2, sort_keys=True)
            except json.JSONDecodeError:
                return text
        return text
    return response.decode("utf-8", errors="replace")
