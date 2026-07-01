"""MOTU USB datastore frame construction."""

from __future__ import annotations

import struct
from collections.abc import Iterable

MOTU_VID = 0x07FD
MOTU_AVB_PID = 0x0005

DEFAULT_INTERFACE = 3
DEFAULT_EP_OUT = 0x03
DEFAULT_EP_IN = 0x83
DEFAULT_TIMEOUT_MS = 600
DEFAULT_SEQ_START = 0x20
DEFAULT_MESSAGE_SEQ = 2
DEFAULT_MAX_USB_CHUNK = 512

HOST_SEQ_MIN = 0x20
HOST_SEQ_COUNT = 0x20
HOST_SEQ_MAX_EXCLUSIVE = HOST_SEQ_MIN + HOST_SEQ_COUNT
MAX_U16 = 0xFFFF
USB_LOGICAL_HEADER_BYTES = 4
MOTU_FRAME_CONTROL_BYTES = 16
UTOM_REQUEST_PREFIX_BYTES = 20
POST_BODY_PREFIX_BYTES = 12


class ProtocolFrameTooLarge(RuntimeError):
    pass


class InvalidHostSequence(RuntimeError):
    pass


def _crc32_table() -> list[int]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1
        table.append(crc & 0xFFFFFFFF)
    return table


CRC32_TABLE = _crc32_table()


def crc32(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc = CRC32_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def u16(value: int) -> bytes:
    return struct.pack("<H", value)


def u32(value: int) -> bytes:
    return struct.pack("<I", value)


def sized_word(value: str | bytes) -> bytes:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return u32(len(value)) + value


def sized_word_len(value: str | bytes) -> int:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return 4 + len(value)


QueryFieldValue = str | int
QueryField = tuple[str, QueryFieldValue]


def _normalize_query_fields(
    fields: Iterable[QueryField] | str | int | None = None,
    client: str | int | None = None,
) -> tuple[tuple[str, str], ...]:
    if fields is None:
        normalized: list[tuple[str, str]] = []
    elif isinstance(fields, str | int):
        normalized = [("client", str(fields))]
    else:
        normalized = [(name, str(value)) for name, value in fields]
    if client is not None:
        normalized.append(("client", str(client)))
    return tuple(normalized)


def encode_query_fields(
    fields: Iterable[QueryField] | str | int | None = None,
    client: str | int | None = None,
) -> bytes:
    normalized = _normalize_query_fields(fields, client)
    return u32(len(normalized)) + b"".join(
        sized_word(name) + sized_word(value)
        for name, value in normalized
    )


def query_fields(
    fields: Iterable[QueryField] | str | int | None = None,
    client: str | int | None = None,
) -> bytes:
    return encode_query_fields(fields, client)


def query_fields_len(
    fields: Iterable[QueryField] | str | int | None = None,
    client: str | int | None = None,
) -> int:
    normalized = _normalize_query_fields(fields, client)
    return 4 + sum(
        sized_word_len(name) + sized_word_len(value)
        for name, value in normalized
    )


def next_host_seq(seq: int) -> int:
    return ((seq + 1 - HOST_SEQ_MIN) % HOST_SEQ_COUNT) + HOST_SEQ_MIN


def validate_host_seq(seq: int) -> int:
    if not HOST_SEQ_MIN <= seq < HOST_SEQ_MAX_EXCLUSIVE:
        raise InvalidHostSequence(
            f"host sequence must be in range 0x{HOST_SEQ_MIN:02x}..0x{HOST_SEQ_MAX_EXCLUSIVE - 1:02x}"
        )
    return seq


class HostSequencer:
    def __init__(self, seq_start: int = DEFAULT_SEQ_START) -> None:
        self._next = validate_host_seq(seq_start)

    def take(self) -> int:
        seq = self._next
        self._next = next_host_seq(self._next)
        return seq


def _header_bytes(header: str) -> bytes:
    encoded = header.encode("ascii")
    if len(encoded) != 4:
        raise ValueError("MOTU frame header must be exactly four ASCII bytes")
    return encoded


def max_motu_payload_bytes(header: str = "NREK") -> int:
    header_len = len(_header_bytes(header))
    return MAX_U16 - USB_LOGICAL_HEADER_BYTES - header_len - MOTU_FRAME_CONTROL_BYTES


def max_post_json_body_bytes(
    path: str,
    header: str = "NREK",
    client: str | int | None = None,
) -> int:
    request_len = (
        sized_word_len("POST")
        + sized_word_len(path)
        + 4
        + sized_word_len("Unsecure-Auth-MOTU")
        + sized_word_len("unicorn666")
        + query_fields_len(client)
    )
    fixed_payload_len = UTOM_REQUEST_PREFIX_BYTES + request_len + 4 + POST_BODY_PREFIX_BYTES
    return max_motu_payload_bytes(header) - fixed_payload_len


def validate_post_frame_size(
    path: str,
    json_body: str,
    header: str = "NREK",
    client: str | int | None = None,
) -> None:
    body_len = len(json_body.encode("utf-8"))
    max_body_len = max_post_json_body_bytes(path, header=header, client=client)
    if body_len > max_body_len:
        raise ProtocolFrameTooLarge(
            f"POST JSON body is {body_len} bytes; maximum single-frame body "
            f"for {path} is {max_body_len} bytes"
        )


def build_motu_frame(seq: int, header: str, message_seq: int, motu_payload: bytes) -> bytes:
    header_bytes = _header_bytes(header)
    max_payload_len = max_motu_payload_bytes(header)
    if len(motu_payload) > max_payload_len:
        raise ProtocolFrameTooLarge(
            f"MOTU payload is {len(motu_payload)} bytes; maximum single-frame "
            f"payload is {max_payload_len} bytes"
        )
    body = (
        header_bytes
        + u32(crc32(motu_payload))
        + u32(message_seq)
        + u32(1)
        + u16(0)
        + u16(len(motu_payload))
        + motu_payload
    )
    return bytes([seq & 0xFF, 0x80]) + u16(len(body) + 4) + body


def build_get_frame(
    seq: int,
    message_seq: int,
    path: str,
    etag: str = "0",
    header: str = "NREK",
    client: str | int | None = None,
    query_fields: Iterable[QueryField] | None = None,
) -> bytes:
    request = (
        sized_word("GET")
        + sized_word(path)
        + u32(1)
        + sized_word("If-None-Match")
        + sized_word(etag)
        + encode_query_fields(query_fields, client=client)
    )
    motu_payload = b"UTOM" + u32(8) + u32(1) + u32(0) + u32(len(request)) + request
    return build_motu_frame(seq, header, message_seq, motu_payload)


def build_post_frame(
    seq: int,
    message_seq: int,
    path: str,
    json_body: str,
    header: str = "NREK",
    client: str | int | None = None,
) -> bytes:
    request = (
        sized_word("POST")
        + sized_word(path)
        + u32(1)
        + sized_word("Unsecure-Auth-MOTU")
        + sized_word("unicorn666")
        + query_fields(client)
    )
    validate_post_frame_size(path, json_body, header=header, client=client)
    body = sized_word("json") + sized_word(json_body)
    motu_payload = b"UTOM" + u32(8) + u32(1) + u32(0) + u32(len(request)) + request + u32(len(body)) + body
    return build_motu_frame(seq, header, message_seq, motu_payload)


def build_ack(seq: int) -> bytes:
    return bytes([seq & 0xFF, 0x81, 0x04, 0x00])


def build_init(seq: int) -> bytes:
    return bytes([seq & 0xFF, 0x82, 0x04, 0x00])
