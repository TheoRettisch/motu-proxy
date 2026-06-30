from unittest import TestCase

from motu_proxy.datastore import (
    DatastoreNoResponse,
    DatastoreTimeout,
    MotuUsbDatastore,
    ShortUsbFrame,
    ShortUsbWrite,
)
from motu_proxy.parser import ResponseFrameError
from motu_proxy.protocol import build_get_frame, build_post_frame

from tests.helpers import response_packet


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
        transport = FakeTransport([bytes.fromhex("20 00 08 00 20 00 08 00"), response_packet(b'{"value":"ok"}')])
        datastore = MotuUsbDatastore(transport)
        response = datastore.get("/datastore/uid")
        self.assertEqual(response, b'{"value":"ok"}')
        self.assertIsNotNone(datastore.last_response_stats)
        assert datastore.last_response_stats is not None
        self.assertEqual(datastore.last_response_stats.reads, 2)
        self.assertEqual(datastore.last_response_stats.accepted_frames, 1)
        self.assertEqual(datastore.last_response_stats.ack_packets, 1)
        self.assertEqual(transport.writes[0], build_get_frame(0x20, 2, "/datastore/uid"))
        self.assertEqual(transport.writes[1], bytes.fromhex("21 81 04 00"))

    def test_get_forwards_client_identifier(self) -> None:
        transport = FakeTransport([response_packet(b'{"value":"ok"}')])
        datastore = MotuUsbDatastore(transport)
        datastore.get("/datastore/uid", client=1479701624)
        self.assertEqual(
            transport.writes[0],
            build_get_frame(0x20, 2, "/datastore/uid", client=1479701624),
        )

    def test_get_records_response_etag(self) -> None:
        transport = FakeTransport([response_packet(b'HTTP/1.1 200 OK\r\nETag: 5678\r\n\r\n{"value":"ok"}')])
        datastore = MotuUsbDatastore(transport)
        datastore.get("/datastore/uid")
        self.assertEqual(datastore.last_response_etag, "5678")

    def test_get_collects_response_frames_read_during_ack_drain(self) -> None:
        first = response_packet(b'{"first":true}', final=False, segment_index=0, wrapper_seq=0x40)
        second = response_packet(b'{"second":true}', final=True, segment_index=1, wrapper_seq=0x41)
        transport = FakeTransport([first, second])
        datastore = MotuUsbDatastore(transport)
        response = datastore.get("/datastore")
        self.assertEqual(response, b'{"first":true}{"second":true}')
        self.assertEqual(transport.writes[0], build_get_frame(0x20, 2, "/datastore"))
        self.assertEqual(transport.writes[1], bytes.fromhex("21 81 04 00"))
        self.assertEqual(transport.writes[2], bytes.fromhex("22 81 04 00"))

    def test_get_rejects_partial_logical_frame_without_ack(self) -> None:
        transport = FakeTransport([response_packet(b'{"value":"partial"}')[:-3]])
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
        self.assertEqual(datastore.message_seq, 2)

    def test_get_rejects_missing_response(self) -> None:
        transport = FakeTransport([])
        datastore = MotuUsbDatastore(transport)
        with self.assertRaises(DatastoreNoResponse):
            datastore.get("/datastore/uid")
        self.assertEqual(transport.writes, [build_get_frame(0x20, 2, "/datastore/uid")])

    def test_get_bounds_ignored_response_packets(self) -> None:
        ignored_packet = bytes.fromhex("77 00 04 00")
        transport = FakeTransport([ignored_packet] * 33)
        datastore = MotuUsbDatastore(transport)
        with self.assertRaises(DatastoreTimeout):
            datastore.get("/datastore/uid")
        self.assertEqual(transport.writes, [build_get_frame(0x20, 2, "/datastore/uid")])

    def test_post_uses_post_frame(self) -> None:
        transport = FakeTransport([response_packet(b'{"ok":true}')])
        datastore = MotuUsbDatastore(transport)
        datastore.post("/datastore/host/os", '{"value":"linux"}')
        self.assertEqual(transport.writes[0], build_post_frame(0x20, 2, "/datastore/host/os", '{"value":"linux"}'))

    def test_post_short_write_does_not_advance_message_sequence(self) -> None:
        transport = FakeTransport([], short_writes=True)
        datastore = MotuUsbDatastore(transport)
        with self.assertRaises(ShortUsbWrite):
            datastore.post("/datastore/host/os", '{"value":"linux"}')
        self.assertEqual(datastore.message_seq, 2)

    def test_post_forwards_client_identifier(self) -> None:
        transport = FakeTransport([response_packet(b'{"ok":true}')])
        datastore = MotuUsbDatastore(transport)
        datastore.post("/datastore/host/os", '{"value":"linux"}', client=1479701624)
        self.assertEqual(
            transport.writes[0],
            build_post_frame(0x20, 2, "/datastore/host/os", '{"value":"linux"}', client=1479701624),
        )

    def test_get_rejects_response_with_wrong_message_sequence(self) -> None:
        transport = FakeTransport([response_packet(b'{"value":"wrong"}', message_seq=3)])
        datastore = MotuUsbDatastore(transport)
        with self.assertRaises(ResponseFrameError):
            datastore.get("/datastore/uid")
        self.assertEqual(transport.writes, [build_get_frame(0x20, 2, "/datastore/uid")])

    def test_get_allows_response_padding_after_logical_wrapper(self) -> None:
        transport = FakeTransport([response_packet(b'{"value":"ok"}', padding=b"\x00")])
        datastore = MotuUsbDatastore(transport)
        self.assertEqual(datastore.get("/datastore/uid"), b'{"value":"ok"}')
