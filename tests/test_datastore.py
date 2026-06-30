from unittest import TestCase

from motu_proxy.datastore import MotuUsbDatastore
from motu_proxy.protocol import build_get_frame, build_post_frame


def logical_packet(body: bytes) -> bytes:
    total = len(body) + 4
    return bytes([0x77, 0x00, total & 0xFF, total >> 8]) + body


class FakeTransport:
    max_packet_size = 64

    def __init__(self, reads: list[bytes]) -> None:
        self.reads = reads
        self.writes: list[bytes] = []

    def bulk_write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def bulk_read(self, size: int | None = None, timeout_ms: int | None = None) -> bytes:
        if self.reads:
            return self.reads.pop(0)
        return b""


class DatastoreTests(TestCase):
    def test_get_collects_response_and_acks(self) -> None:
        body = b"NREK" + b"\x00" * 16 + b'{"value":"ok"}'
        transport = FakeTransport([bytes.fromhex("20 00 08 00 20 00 08 00"), logical_packet(body)])
        datastore = MotuUsbDatastore(transport)
        response = datastore.get("/datastore/uid")
        self.assertEqual(response, b'{"value":"ok"}')
        self.assertEqual(transport.writes[0], build_get_frame(0x20, 2, "/datastore/uid"))
        self.assertEqual(transport.writes[1], bytes.fromhex("21 81 04 00"))

    def test_post_uses_post_frame(self) -> None:
        body = b"NREK" + b"\x00" * 16 + b'{"ok":true}'
        transport = FakeTransport([logical_packet(body)])
        datastore = MotuUsbDatastore(transport)
        datastore.post("/datastore/host/os", '{"value":"linux"}')
        self.assertEqual(transport.writes[0], build_post_frame(0x20, 2, "/datastore/host/os", '{"value":"linux"}'))
