"""MOTU USB datastore frame construction."""

from __future__ import annotations

import struct


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


def next_host_seq(seq: int) -> int:
    return ((seq + 1 - HOST_SEQ_MIN) % HOST_SEQ_COUNT) + HOST_SEQ_MIN


class HostSequencer:
    def __init__(self, seq_start: int = DEFAULT_SEQ_START) -> None:
        self._next = seq_start & 0xFF

    def take(self) -> int:
        seq = self._next
        self._next = next_host_seq(self._next)
        return seq


def build_motu_frame(seq: int, header: str, message_seq: int, motu_payload: bytes) -> bytes:
    body = (
        header.encode("ascii")
        + u32(crc32(motu_payload))
        + u32(message_seq)
        + u32(1)
        + u16(0)
        + u16(len(motu_payload))
        + motu_payload
    )
    return bytes([seq & 0xFF, 0x80]) + u16(len(body) + 4) + body


def build_get_frame(seq: int, message_seq: int, path: str, etag: str = "0", header: str = "NREK") -> bytes:
    request = (
        sized_word("GET")
        + sized_word(path)
        + u32(1)
        + sized_word("If-None-Match")
        + sized_word(etag)
        + u32(0)
    )
    motu_payload = b"UTOM" + u32(8) + u32(1) + u32(0) + u32(len(request)) + request
    return build_motu_frame(seq, header, message_seq, motu_payload)


def build_post_frame(seq: int, message_seq: int, path: str, json_body: str, header: str = "NREK") -> bytes:
    request = (
        sized_word("POST")
        + sized_word(path)
        + u32(1)
        + sized_word("Unsecure-Auth-MOTU")
        + sized_word("unicorn666")
        + u32(0)
    )
    body = sized_word("json") + sized_word(json_body)
    motu_payload = b"UTOM" + u32(8) + u32(1) + u32(0) + u32(len(request)) + request + u32(len(body)) + body
    return build_motu_frame(seq, header, message_seq, motu_payload)


def build_ack(seq: int) -> bytes:
    return bytes([seq & 0xFF, 0x81, 0x04, 0x00])


def build_init(seq: int) -> bytes:
    return bytes([seq & 0xFF, 0x82, 0x04, 0x00])
