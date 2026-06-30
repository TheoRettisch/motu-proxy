import struct

from motu_proxy.protocol import crc32


def response_packet(
    payload: bytes,
    message_seq: int = 2,
    wrapper_seq: int = 0x77,
    final: bool = True,
    segment_index: int = 0,
    header: bytes = b"NREK",
    padding: bytes = b"",
) -> bytes:
    total = 28 + len(payload)
    wrapper = bytes([wrapper_seq & 0xFF, 0x00]) + struct.pack("<H", total)
    body = (
        header
        + struct.pack("<I", crc32(payload))
        + struct.pack("<I", message_seq)
        + struct.pack("<I", 1 if final else 0)
        + struct.pack("<H", segment_index)
        + struct.pack("<H", len(payload))
        + payload
        + wrapper
    )
    return wrapper + body + padding
