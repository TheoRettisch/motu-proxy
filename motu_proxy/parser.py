"""Response helpers for the MOTU datastore protocol."""

from __future__ import annotations

import json
import struct
from collections.abc import Sequence
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


@dataclass(frozen=True)
class DatastorePayload:
    body: bytes
    etag: str | None = None
    status: int | None = None
    content_type: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status == 304


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


def join_parsed_response_frames(frames: Sequence[ResponseFrame]) -> bytes:
    if not frames:
        return b""

    pieces: list[bytes] = []
    for expected_index, frame in enumerate(frames):
        if frame.segment_index != expected_index:
            raise ResponseFrameError(
                f"response segment index {frame.segment_index} != expected {expected_index}"
            )
        if frame.final and expected_index != len(frames) - 1:
            raise ResponseFrameError(f"response final flag set before last segment {expected_index}")
        if not frame.final and expected_index == len(frames) - 1:
            raise ResponseFrameError("response missing final segment flag")
        pieces.append(frame.payload)
    return b"".join(pieces)


def join_response_frames(frames: Sequence[bytes], expected_message_seq: int) -> bytes:
    parsed = [parse_response_frame(frame, expected_message_seq) for frame in frames]
    return join_parsed_response_frames(parsed)


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


def extract_response_etag(response: bytes) -> str | None:
    return parse_datastore_response(response).etag


def parse_datastore_response(
    response: bytes,
    classify_body: bool = False,
) -> DatastorePayload:
    parsed = _parse_utom_response(response) or _parse_text_http_response(response)
    if parsed is None:
        body = response
        etag = None
        status = None
    else:
        body, etag, status = parsed
    return DatastorePayload(
        body=body,
        etag=etag,
        status=status,
        content_type=datastore_body_content_type(body) if classify_body else None,
    )


def _parse_utom_response(response: bytes) -> tuple[bytes, str | None, int | None] | None:
    if len(response) < 28 or not response.startswith(b"UTOM"):
        return None
    metadata_len = _u32(response, 16)
    metadata_start = 20
    metadata_end = metadata_start + metadata_len
    if metadata_end > len(response) or metadata_len < 8:
        return None

    raw_status = _u32(response, 20)
    status = raw_status if 100 <= raw_status <= 599 else None
    header_count = _u32(response, 24)
    offset = 28
    etag: str | None = None
    for _ in range(header_count):
        name, offset = _read_sized_value(response, offset, metadata_end)
        value, offset = _read_sized_value(response, offset, metadata_end)
        if name is None or value is None:
            return response[metadata_end:], etag, status
        decoded_name = _decode_header_value(name)
        if decoded_name is not None and decoded_name.lower() == "etag":
            etag = _decode_header_value(value)
    return response[metadata_end:], etag, status


def _parse_text_http_response(response: bytes) -> tuple[bytes, str | None, int | None] | None:
    split = _split_text_http_response(response)
    if split is None:
        return None
    header_block, body = split
    text = header_block.decode("iso-8859-1", errors="ignore")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    status = _parse_text_status(lines[0] if lines else "")
    etag: str | None = None
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "etag":
            value = value.strip()
            etag = value or None
    return body, etag, status


def _split_text_http_response(response: bytes) -> tuple[bytes, bytes] | None:
    if not response.startswith(b"HTTP/"):
        return None
    separators = (b"\r\n\r\n", b"\n\n")
    for separator in separators:
        index = response.find(separator)
        if index >= 0:
            body_start = index + len(separator)
            return response[:index], response[body_start:]
    return None


def _parse_text_status(status_line: str) -> int | None:
    parts = status_line.strip().split(None, 2)
    if len(parts) < 2:
        return None
    try:
        status = int(parts[1])
    except ValueError:
        return None
    return status if 100 <= status <= 599 else None


def datastore_body_content_type(body: bytes) -> str:
    if is_single_json_container(body):
        return "application/json"
    return "application/octet-stream"


def is_single_json_container(body: bytes) -> bool:
    start = 0
    end = len(body)
    while start < end and body[start] in b" \t\r\n":
        start += 1
    while end > start and body[end - 1] in b" \t\r\n":
        end -= 1
    if start >= end:
        return False
    if body[start] == ord("{"):
        expected_stack = [ord("}")]
    elif body[start] == ord("["):
        expected_stack = [ord("]")]
    else:
        return False

    in_string = False
    escaped = False
    for index in range(start + 1, end):
        byte = body[index]
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
            continue
        if byte == ord('"'):
            in_string = True
            continue
        if byte == ord("{"):
            expected_stack.append(ord("}"))
            continue
        if byte == ord("["):
            expected_stack.append(ord("]"))
            continue
        if byte in (ord("}"), ord("]")):
            if not expected_stack or byte != expected_stack.pop():
                return False
            if not expected_stack:
                return index == end - 1
    return False


def response_status_code(response: bytes) -> int | None:
    return parse_datastore_response(response).status


def extract_datastore_body(response: bytes) -> bytes:
    return parse_datastore_response(response).body


def _read_sized_value(data: bytes, offset: int, end: int) -> tuple[bytes | None, int]:
    if offset + 4 > end:
        return None, offset
    size = _u32(data, offset)
    value_start = offset + 4
    value_end = value_start + size
    if size > 4096 or value_end > end:
        return None, offset
    return data[value_start:value_end], value_end


def _decode_header_value(value: bytes) -> str | None:
    if any(byte < 0x20 or byte > 0x7E for byte in value):
        return None
    try:
        decoded = value.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    return decoded or None


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
