from unittest import TestCase

from motu_proxy.datastore import MotuUsbDatastore, ShortUsbFrame, ShortUsbWrite
from motu_proxy.protocol import build_get_frame, build_post_frame


def logical_packet(body: bytes) -> bytes:
    total = len(body) + 4
    return bytes([0x77, 0x00, total & 0xFF, total >> 8]) + body


class FakeTransport:
    max_packet_size = 64

    def __init__(self, reads: list[bytes], short_writes: bool = False) -> None:
        self.reads = reads
        self.short_writes = short_writes
        self.writes: list[bytes] = []

    def bulk_write(self, data: bytes) -> int:
        self.writes.append(data)
        if self.short_writes:
            return len(data) - 1
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

    def test_get_collects_response_frames_read_during_ack_drain(self) -> None:
        first = b"NREK" + b"\x00" * 16 + b'{"first":true}'
        second = b"NREK" + b"\x00" * 16 + b'{"second":true}'
        transport = FakeTransport([logical_packet(first), logical_packet(second)])
        datastore = MotuUsbDatastore(transport)
        response = datastore.get("/datastore")
        self.assertEqual(response, b'{"first":true}{"second":true}')
        self.assertEqual(transport.writes[0], build_get_frame(0x20, 2, "/datastore"))
        self.assertEqual(transport.writes[1], bytes.fromhex("21 81 04 00"))
        self.assertEqual(transport.writes[2], bytes.fromhex("22 81 04 00"))

    def test_get_rejects_partial_logical_frame_without_ack(self) -> None:
        body = b"NREK" + b"\x00" * 16 + b'{"value":"partial"}'
        transport = FakeTransport([logical_packet(body)[:-3]])
        datastore = MotuUsbDatastore(transport)
        with self.assertRaises(ShortUsbFrame):
            datastore.get("/datastore/uid")
        self.assertEqual(transport.writes, [build_get_frame(0x20, 2, "/datastore/uid")])

    def test_get_rejects_short_write_before_reading_response(self) -> None:
        transport = FakeTransport([], short_writes=True)
        datastore = MotuUsbDatastore(transport)
        with self.assertRaises(ShortUsbWrite):
            datastore.get("/datastore/uid")
        self.assertEqual(transport.writes, [build_get_frame(0x20, 2, "/datastore/uid")])

    def test_post_uses_post_frame(self) -> None:
        body = b"NREK" + b"\x00" * 16 + b'{"ok":true}'
        transport = FakeTransport([logical_packet(body)])
        datastore = MotuUsbDatastore(transport)
        datastore.post("/datastore/host/os", '{"value":"linux"}')
        self.assertEqual(transport.writes[0], build_post_frame(0x20, 2, "/datastore/host/os", '{"value":"linux"}'))
