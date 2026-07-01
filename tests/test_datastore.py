import threading
import time
from contextlib import redirect_stderr
from io import StringIO
from unittest import TestCase

from motu_proxy.datastore import (
    DatastoreCoordinator,
    DatastoreNoResponse,
    DatastoreTimeout,
    MotuUsbDatastore,
    ResponseStats,
    ShortUsbFrame,
    ShortUsbWrite,
    read_device_capability_info,
)
from motu_proxy.parser import DatastorePayload, ResponseFrameError
from motu_proxy.protocol import build_ack, build_get_frame, build_post_frame

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


class BlockingTransport:
    max_packet_size = 512

    def __init__(self, reads: list[bytes] | None = None) -> None:
        self.reads = list(reads or [])
        self.writes: list[bytes] = []
        self.read_timeouts: list[int | None] = []
        self.cancellable_read_timeouts: list[int | None] = []
        self.cancelled_reads = 0
        self._condition = threading.Condition()

    def bulk_write(self, data: bytes) -> int:
        with self._condition:
            self.writes.append(data)
            self._condition.notify_all()
        return len(data)

    def bulk_read(self, size: int | None = None, timeout_ms: int | None = None) -> bytes:
        deadline = None if timeout_ms is None else time.monotonic() + (timeout_ms / 1000)
        with self._condition:
            self.read_timeouts.append(timeout_ms)
            while not self.reads:
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b""
                self._condition.wait(remaining)
            return self.reads.pop(0)

    def begin_cancellable_bulk_read(
        self,
        size: int | None = None,
        timeout_ms: int | None = None,
    ) -> "BlockingCancellableRead":
        with self._condition:
            self.cancellable_read_timeouts.append(timeout_ms)
            self._condition.notify_all()
        return BlockingCancellableRead(self, timeout_ms)

    def push(self, *packets: bytes) -> None:
        with self._condition:
            self.reads.extend(packets)
            self._condition.notify_all()

    def wait_for_writes(self, count: int, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self.writes) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def wait_for_cancellable_reads(self, count: int, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self.cancellable_read_timeouts) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


class BlockingCancellableRead:
    def __init__(self, transport: BlockingTransport, timeout_ms: int | None) -> None:
        self.transport = transport
        self.timeout_ms = timeout_ms
        self.cancelled = False

    def read(self) -> bytes:
        deadline = None if self.timeout_ms is None else time.monotonic() + (self.timeout_ms / 1000)
        with self.transport._condition:
            while not self.transport.reads and not self.cancelled:
                if deadline is None:
                    self.transport._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return b""
                self.transport._condition.wait(remaining)
            if self.cancelled:
                raise InterruptedError("fake USB read cancelled")
            return self.transport.reads.pop(0)

    def cancel(self) -> None:
        with self.transport._condition:
            if not self.cancelled:
                self.cancelled = True
                self.transport.cancelled_reads += 1
            self.transport._condition.notify_all()


class BlockingNonCancellableTransport(BlockingTransport):
    begin_cancellable_bulk_read = None


class SizeCheckingTransport(FakeTransport):
    def bulk_read(self, size: int | None = None, timeout_ms: int | None = None) -> bytes:
        if self.reads and size is not None and len(self.reads[0]) > size:
            raise OSError(75, "Value too large for defined data type")
        return super().bulk_read(size=size, timeout_ms=timeout_ms)


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

    def test_get_wraps_message_sequence_after_u32_max(self) -> None:
        transport = FakeTransport(
            [
                response_packet(b'{"value":"last"}', message_seq=0xFFFFFFFF),
                response_packet(b'{"value":"wrapped"}', message_seq=0),
            ]
        )
        datastore = MotuUsbDatastore(transport, message_seq=0xFFFFFFFF)

        self.assertEqual(datastore.get("/datastore/uid"), b'{"value":"last"}')
        self.assertEqual(datastore.get("/datastore/uid"), b'{"value":"wrapped"}')

        self.assertEqual(transport.writes[0], build_get_frame(0x20, 0xFFFFFFFF, "/datastore/uid"))
        self.assertEqual(transport.writes[2], build_get_frame(0x22, 0, "/datastore/uid"))
        self.assertEqual(datastore.message_seq, 1)

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

    def test_get_rejects_corrupt_current_sequence_frame(self) -> None:
        corrupt = bytearray(response_packet(b'{"value":"bad"}'))
        corrupt[24] ^= 0x01
        valid = response_packet(b'{"value":"ok"}')
        transport = FakeTransport([bytes(corrupt), valid])
        datastore = MotuUsbDatastore(transport)

        with self.assertRaisesRegex(ResponseFrameError, "CRC mismatch"):
            datastore.get("/datastore/uid")

        self.assertIsNotNone(datastore.last_response_stats)
        assert datastore.last_response_stats is not None
        self.assertEqual(datastore.last_response_stats.ignored_packets, 0)
        self.assertEqual(datastore.last_response_stats.accepted_frames, 0)
        self.assertEqual(transport.writes, [build_get_frame(0x20, 2, "/datastore/uid")])

    def test_get_ignores_corrupt_stale_sequence_frame_and_collects_current(self) -> None:
        corrupt = bytearray(response_packet(b'{"value":"late"}', message_seq=2))
        corrupt[24] ^= 0x01
        valid = response_packet(b'{"value":"ok"}', message_seq=3)
        transport = FakeTransport([bytes(corrupt), valid])
        datastore = MotuUsbDatastore(transport, message_seq=3)

        self.assertEqual(datastore.get("/datastore/uid"), b'{"value":"ok"}')

        self.assertIsNotNone(datastore.last_response_stats)
        assert datastore.last_response_stats is not None
        self.assertEqual(datastore.last_response_stats.ignored_packets, 1)
        self.assertEqual(datastore.last_response_stats.accepted_frames, 1)
        self.assertEqual(
            transport.writes,
            [
                build_get_frame(0x20, 3, "/datastore/uid"),
                build_ack(0x21),
                build_ack(0x22),
            ],
        )

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

    def test_get_ignores_stale_wrong_sequence_before_current_response(self) -> None:
        transport = FakeTransport(
            [
                response_packet(b'{"value":"late"}', message_seq=2, wrapper_seq=0x40),
                response_packet(b'{"value":"ok"}', message_seq=3, wrapper_seq=0x41),
            ]
        )
        datastore = MotuUsbDatastore(transport, message_seq=3)

        response = datastore.get("/datastore/uid")

        self.assertEqual(response, b'{"value":"ok"}')
        self.assertIsNotNone(datastore.last_response_stats)
        assert datastore.last_response_stats is not None
        self.assertEqual(datastore.last_response_stats.ignored_packets, 1)
        self.assertEqual(datastore.last_response_stats.accepted_frames, 1)
        self.assertEqual(transport.writes[0], build_get_frame(0x20, 3, "/datastore/uid"))
        self.assertEqual(transport.writes[1], bytes.fromhex("21 81 04 00"))
        self.assertEqual(transport.writes[2], bytes.fromhex("22 81 04 00"))

    def test_get_drains_large_stale_wrong_sequence_response_before_current(self) -> None:
        stale_frames = [
            response_packet(
                b"x",
                message_seq=2,
                wrapper_seq=0x40 + index,
                final=index == 63,
                segment_index=index,
            )
            for index in range(64)
        ]
        current = response_packet(b'{"value":"ok"}', message_seq=3, wrapper_seq=0x90)
        transport = FakeTransport([*stale_frames, current])
        datastore = MotuUsbDatastore(transport, message_seq=3)

        response = datastore.get("/datastore/uid")

        self.assertEqual(response, b'{"value":"ok"}')
        self.assertIsNotNone(datastore.last_response_stats)
        assert datastore.last_response_stats is not None
        self.assertEqual(datastore.last_response_stats.ignored_packets, 64)
        self.assertEqual(datastore.last_response_stats.accepted_frames, 1)
        ack_writes = [write for write in transport.writes[1:] if len(write) > 1 and write[1] == 0x81]
        self.assertEqual(len(ack_writes), 65)

    def test_get_allows_response_padding_after_logical_wrapper(self) -> None:
        transport = FakeTransport([response_packet(b'{"value":"ok"}', padding=b"\x00")])
        datastore = MotuUsbDatastore(transport)
        self.assertEqual(datastore.get("/datastore/uid"), b'{"value":"ok"}')

    def test_get_allows_padded_final_usb_packet_for_split_logical_frame(self) -> None:
        packet = response_packet(b'{"value":"' + (b"x" * 80) + b'"}', padding=b"\x00" * 8)
        transport = SizeCheckingTransport([packet[:64], packet[64:]], short_writes=False)
        datastore = MotuUsbDatastore(transport)
        self.assertEqual(datastore.get("/datastore/uid"), b'{"value":"' + (b"x" * 80) + b'"}')

    def test_read_device_capability_info_assembles_values_and_absent_caps(self) -> None:
        transport = FakeTransport(
            [
                response_packet(b'{"value":"1.0.0"}', message_seq=2, wrapper_seq=0x40),
                response_packet(b'{"value":"2.0.0"}', message_seq=3, wrapper_seq=0x41),
                response_packet(b'{"value":"3.0.0"}', message_seq=4, wrapper_seq=0x42),
                response_packet(
                    b"HTTP/1.1 404 Not Found\r\n\r\n{\"error\":\"missing\"}",
                    message_seq=5,
                    wrapper_seq=0x43,
                ),
                response_packet(b'{"value":"0001f2fffe00c719"}', message_seq=6, wrapper_seq=0x44),
                response_packet(b'{"value":"624"}', message_seq=7, wrapper_seq=0x45),
                response_packet(b'{"value":"1.4.1"}', message_seq=8, wrapper_seq=0x46),
                response_packet(b'{"value":"0001f2fffe00c719"}', message_seq=9, wrapper_seq=0x47),
            ]
        )
        datastore = MotuUsbDatastore(transport)

        info = read_device_capability_info(datastore)

        self.assertEqual(info.apiversion, "1.0.0")
        self.assertEqual(info.capabilities["avb"].version, "2.0.0")
        self.assertTrue(info.capabilities["avb"].present)
        self.assertEqual(info.capabilities["router"].version, "3.0.0")
        self.assertFalse(info.capabilities["mixer"].present)
        self.assertIsNone(info.capabilities["mixer"].version)
        self.assertEqual(info.identity["uid"], "0001f2fffe00c719")
        self.assertEqual(info.identity["model_name"], "624")
        self.assertEqual(info.identity["firmware_version"], "1.4.1")
        self.assertEqual(info.identity["serial_number"], "0001f2fffe00c719")
        self.assertEqual(
            transport.writes[0],
            build_get_frame(0x20, 2, "/apiversion"),
        )
        self.assertEqual(
            transport.writes[2],
            build_get_frame(0x22, 3, "/datastore/ext/caps/avb"),
        )


class DatastoreCoordinatorTests(TestCase):
    def test_non_poll_path_read_does_not_replace_global_etag(self) -> None:
        transport = FakeTransport(
            [
                response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}'),
                response_packet(
                    b'HTTP/1.1 200 OK\r\nETag: uid\r\n\r\n{"value":"0001f2fffe00c719"}',
                    message_seq=3,
                ),
            ]
        )
        coordinator = DatastoreCoordinator(MotuUsbDatastore(transport))

        self.assertEqual(coordinator.read("/datastore").etag, "1")
        self.assertEqual(coordinator.read("/datastore/uid").etag, "uid")

        self.assertEqual(coordinator.latest_etag, "1")
        self.assertEqual(coordinator.history, ())

    def test_background_poller_fans_out_to_multiple_waiters(self) -> None:
        initial = response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')
        changed = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"changed":true}',
            message_seq=3,
        )
        transport = BlockingTransport([initial])
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            long_poll_timeout_ms=500,
            http_wait_timeout_ms=1000,
            poll_interval_s=0,
        )
        try:
            self.assertEqual(coordinator.read("/datastore").etag, "1")
            coordinator.start()
            self.assertTrue(transport.wait_for_writes(3))
            self.assertIn(build_get_frame(0x22, 3, "/datastore", etag="1"), transport.writes)

            results: list[DatastorePayload] = []

            def wait_for_change() -> None:
                results.append(coordinator.wait_for_change("/datastore", "1"))

            threads = [threading.Thread(target=wait_for_change) for _ in range(2)]
            for thread in threads:
                thread.start()
            transport.push(changed)
            for thread in threads:
                thread.join(timeout=1)

            self.assertEqual([result.body for result in results], [b'{"changed":true}', b'{"changed":true}'])
            self.assertEqual([result.etag for result in results], ["2", "2"])
        finally:
            coordinator.close()

    def test_wait_timeout_returns_not_modified(self) -> None:
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(FakeTransport([])),
            http_wait_timeout_ms=1,
        )
        result = coordinator.wait_for_change("/datastore", "5678")
        self.assertTrue(result.not_modified)
        self.assertEqual(result.etag, "5678")
        self.assertEqual(result.body, b"")

    def test_status_reports_poller_error_and_response_stats(self) -> None:
        coordinator = DatastoreCoordinator(MotuUsbDatastore(FakeTransport([])))
        coordinator.last_poller_error = DatastoreNoResponse("no datastore response")
        coordinator.datastore.last_response_stats = ResponseStats(
            timeout_ms=1200,
            elapsed_ms=10.5,
            reads=2,
            accepted_frames=1,
            ignored_packets=0,
            ack_packets=1,
            response_bytes=17,
        )

        status = coordinator.status()

        self.assertEqual(status["long_poll_mode"], "degraded-refresh")
        self.assertEqual(status["last_poller_error"], {"type": "DatastoreNoResponse", "message": "no datastore response"})
        self.assertEqual(
            status["last_response_stats"],
            {
                "timeout_ms": 1200,
                "elapsed_ms": 10.5,
                "reads": 2,
                "accepted_frames": 1,
                "ignored_packets": 0,
                "ack_packets": 1,
                "response_bytes": 17,
            },
        )

    def test_poller_error_logging_is_rate_limited(self) -> None:
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(FakeTransport([])),
            poll_error_log_interval_s=60,
        )
        stderr = StringIO()
        with redirect_stderr(stderr):
            coordinator._record_poller_error(DatastoreTimeout("poll failed"))
            coordinator._record_poller_error(DatastoreTimeout("poll failed"))
            coordinator._record_poller_error(DatastoreTimeout("different failure"))

        output = stderr.getvalue()
        self.assertEqual(output.count("motu-proxy poller error"), 2)
        self.assertIn("poll failed", output)
        self.assertIn("different failure", output)

    def test_history_returns_adjacent_delta_and_stale_refreshes(self) -> None:
        transport = FakeTransport(
            [
                response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}'),
                response_packet(
                    b'HTTP/1.1 200 OK\r\nETag: 4\r\n\r\n{"state":4}',
                    message_seq=3,
                ),
            ]
        )
        coordinator = DatastoreCoordinator(MotuUsbDatastore(transport), history_size=1)
        self.assertEqual(coordinator.read("/datastore").etag, "1")
        coordinator._publish_payload(DatastorePayload(b'{"delta":2}', etag="2"), None, from_etag="1")

        adjacent = coordinator.wait_for_change("/datastore", "1")
        self.assertEqual(adjacent.body, b'{"delta":2}')
        self.assertEqual(adjacent.etag, "2")

        coordinator._publish_payload(DatastorePayload(b'{"delta":3}', etag="3"), None, from_etag="2")
        stale = coordinator.wait_for_change("/datastore", "1")
        self.assertEqual(stale.body, b'{"state":4}')
        self.assertEqual(stale.etag, "4")

    def test_post_publishes_refreshed_datastore_instead_of_post_body(self) -> None:
        transport = FakeTransport(
            [
                response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}'),
                response_packet(
                    b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"post":true}',
                    message_seq=3,
                ),
                response_packet(
                    b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"full":true}',
                    message_seq=4,
                ),
            ]
        )
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            http_wait_timeout_ms=1,
        )
        coordinator.read("/datastore")

        returned = coordinator.post("/datastore/host/os", '{"value":"linux"}', client="7")
        other = coordinator.wait_for_change("/datastore", "1", client="8")
        own = coordinator.wait_for_change("/datastore", "1", client="7")

        self.assertEqual(returned.body, b'{"post":true}')
        self.assertEqual(other.body, b'{"full":true}')
        self.assertEqual(other.etag, "2")
        self.assertTrue(own.not_modified)

    def test_post_returns_write_response_when_refresh_fails(self) -> None:
        transport = FakeTransport(
            [
                response_packet(b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"post":true}'),
            ]
        )
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            http_wait_timeout_ms=1,
        )

        returned = coordinator.post("/datastore/host/os", '{"value":"linux"}', client="7")

        self.assertEqual(returned.body, b'{"post":true}')
        self.assertEqual(returned.etag, "2")
        self.assertIsInstance(coordinator.last_poller_error, DatastoreNoResponse)
        self.assertEqual(
            transport.writes[2],
            build_get_frame(0x22, 3, "/datastore"),
        )

    def test_degraded_refresh_is_shared_across_concurrent_waiters(self) -> None:
        initial = response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')
        changed = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"changed":true}',
            message_seq=3,
        )
        transport = BlockingNonCancellableTransport([initial])
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            http_wait_timeout_ms=1000,
            degraded_refresh_interval_s=10,
        )
        first: threading.Thread | None = None
        second: threading.Thread | None = None
        released = False
        try:
            coordinator.read("/datastore")

            waiter_results: list[DatastorePayload] = []
            first = threading.Thread(
                target=lambda: waiter_results.append(coordinator.wait_for_change("/datastore", "1"))
            )
            first.start()
            self.assertTrue(transport.wait_for_writes(3))

            second = threading.Thread(
                target=lambda: waiter_results.append(coordinator.wait_for_change("/datastore", "1"))
            )
            second.start()
            time.sleep(0.05)
            self.assertTrue(first.is_alive())
            self.assertTrue(second.is_alive())

            transport.push(changed)
            released = True
            first.join(timeout=1)
            second.join(timeout=1)

            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual([result.body for result in waiter_results], [b'{"changed":true}', b'{"changed":true}'])
            self.assertEqual([result.etag for result in waiter_results], ["2", "2"])
            get_writes = [write for write in transport.writes if len(write) > 1 and write[1] == 0x80]
            self.assertEqual(
                get_writes,
                [
                    build_get_frame(0x20, 2, "/datastore"),
                    build_get_frame(0x22, 3, "/datastore"),
                ],
            )
        finally:
            if not released:
                transport.push(changed)
            if first is not None:
                first.join(timeout=1)
            if second is not None:
                second.join(timeout=1)
            coordinator.close()

    def test_client_filter_suppresses_proxy_originated_own_change(self) -> None:
        transport = FakeTransport([response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')])
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            http_wait_timeout_ms=1,
        )
        coordinator.read("/datastore")
        coordinator._publish_payload(
            DatastorePayload(b'{"own":true}', etag="2"),
            origin_client="1479701624",
            from_etag="1",
        )

        own = coordinator.wait_for_change("/datastore", "1", client="1479701624")
        other = coordinator.wait_for_change("/datastore", "1", client="9")

        self.assertTrue(own.not_modified)
        self.assertEqual(own.etag, "1")
        self.assertEqual(other.body, b'{"own":true}')
        self.assertEqual(other.etag, "2")

    def test_foreground_read_preempts_active_held_poll_within_budget(self) -> None:
        initial = response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')
        read_response = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"value":"ok"}',
            message_seq=4,
        )
        transport = BlockingTransport([initial])
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            long_poll_timeout_ms=5000,
            http_wait_timeout_ms=1000,
            poll_interval_s=1,
            foreground_preemption_budget_ms=250,
        )
        try:
            coordinator.read("/datastore")
            coordinator.start()
            self.assertTrue(transport.wait_for_writes(3))
            self.assertTrue(transport.wait_for_cancellable_reads(1))

            results: list[DatastorePayload] = []
            thread = threading.Thread(target=lambda: results.append(coordinator.read("/datastore/uid")))
            started = time.monotonic()
            thread.start()

            self.assertTrue(transport.wait_for_writes(4, timeout=0.25))
            dispatch_elapsed = time.monotonic() - started
            self.assertLess(dispatch_elapsed, 0.25)
            self.assertEqual(transport.cancelled_reads, 1)
            self.assertEqual(
                transport.writes[3],
                build_get_frame(0x23, 4, "/datastore/uid"),
            )
            transport.push(read_response)
            thread.join(timeout=1)

            self.assertEqual([result.body for result in results], [b'{"value":"ok"}'])
        finally:
            coordinator.close()

    def test_foreground_write_preempts_active_poll_and_publishes_refresh(self) -> None:
        initial = response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')
        post_response = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"post":true}',
            message_seq=4,
        )
        refresh_response = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"full":true}',
            message_seq=5,
        )
        transport = BlockingTransport([initial])
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            long_poll_timeout_ms=5000,
            http_wait_timeout_ms=1000,
            poll_interval_s=1,
            foreground_preemption_budget_ms=250,
        )
        try:
            coordinator.read("/datastore")
            coordinator.start()
            self.assertTrue(transport.wait_for_writes(3))
            self.assertTrue(transport.wait_for_cancellable_reads(1))

            waiter_results: list[DatastorePayload] = []
            waiter = threading.Thread(
                target=lambda: waiter_results.append(
                    coordinator.wait_for_change("/datastore", "1", client="8")
                )
            )
            waiter.start()

            post_results: list[DatastorePayload] = []
            post = threading.Thread(
                target=lambda: post_results.append(
                    coordinator.post("/datastore/host/os", '{"value":"linux"}', client="7")
                )
            )
            post.start()
            self.assertTrue(transport.wait_for_writes(4, timeout=0.25))
            self.assertEqual(
                transport.writes[3],
                build_post_frame(0x23, 4, "/datastore/host/os", '{"value":"linux"}', client="7"),
            )

            transport.push(post_response, refresh_response)
            post.join(timeout=1)
            waiter.join(timeout=1)

            self.assertEqual([result.body for result in post_results], [b'{"post":true}'])
            self.assertEqual([result.body for result in waiter_results], [b'{"full":true}'])
            self.assertEqual([result.etag for result in waiter_results], ["2"])
        finally:
            coordinator.close()

    def test_cancelled_poll_response_is_quarantined_from_foreground_response(self) -> None:
        initial = response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')
        stale_poll = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"stale":true}',
            message_seq=3,
        )
        read_response = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: uid\r\n\r\n{"value":"ok"}',
            message_seq=4,
        )
        transport = BlockingTransport([initial])
        datastore = MotuUsbDatastore(transport)
        coordinator = DatastoreCoordinator(
            datastore,
            long_poll_timeout_ms=5000,
            http_wait_timeout_ms=1000,
            poll_interval_s=1,
        )
        try:
            coordinator.read("/datastore")
            coordinator.start()
            self.assertTrue(transport.wait_for_writes(3))
            self.assertTrue(transport.wait_for_cancellable_reads(1))

            results: list[DatastorePayload] = []
            thread = threading.Thread(target=lambda: results.append(coordinator.read("/datastore/uid")))
            thread.start()
            self.assertTrue(transport.wait_for_writes(4, timeout=0.25))

            transport.push(stale_poll, read_response)
            thread.join(timeout=1)

            self.assertEqual([result.body for result in results], [b'{"value":"ok"}'])
            self.assertEqual(coordinator.latest_etag, "1")
            self.assertGreaterEqual(len(transport.writes), 6)
            self.assertEqual(transport.writes[4], build_ack(0x24))
            self.assertEqual(transport.writes[5], build_ack(0x25))
        finally:
            coordinator.close()

    def test_close_waits_for_blocked_long_poll_to_exit(self) -> None:
        transport = BlockingTransport()
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            long_poll_timeout_ms=1000,
            poll_read_timeout_slice_ms=20,
            poll_interval_s=0,
        )
        coordinator.start()
        try:
            self.assertTrue(transport.wait_for_writes(1))
            self.assertTrue(transport.wait_for_cancellable_reads(1))

            started = time.monotonic()
            coordinator.close()
            elapsed = time.monotonic() - started

            self.assertLess(elapsed, 0.5)
            assert coordinator._worker is not None
            self.assertFalse(coordinator._worker.is_alive())
        finally:
            coordinator.close()

    def test_local_long_poll_waiters_continue_after_preempted_foreground_read(self) -> None:
        initial = response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')
        read_response = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"value":"ok"}',
            message_seq=4,
        )
        changed = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"changed":true}',
            message_seq=5,
        )
        transport = BlockingTransport([initial])
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            long_poll_timeout_ms=5000,
            http_wait_timeout_ms=1000,
            poll_interval_s=0,
        )
        try:
            coordinator.read("/datastore")
            coordinator.start()
            self.assertTrue(transport.wait_for_writes(3))
            self.assertTrue(transport.wait_for_cancellable_reads(1))

            foreground_results: list[DatastorePayload] = []
            thread = threading.Thread(
                target=lambda: foreground_results.append(coordinator.read("/datastore/uid"))
            )
            thread.start()
            self.assertTrue(transport.wait_for_writes(4, timeout=0.25))
            transport.push(read_response)
            thread.join(timeout=1)
            self.assertEqual([result.body for result in foreground_results], [b'{"value":"ok"}'])

            self.assertTrue(transport.wait_for_writes(6))
            self.assertEqual(
                transport.writes[5],
                build_get_frame(0x25, 5, "/datastore", etag="1"),
            )

            waiter_results: list[DatastorePayload] = []
            waiter = threading.Thread(
                target=lambda: waiter_results.append(coordinator.wait_for_change("/datastore", "1"))
            )
            waiter.start()
            transport.push(changed)
            waiter.join(timeout=1)

            self.assertEqual([result.body for result in waiter_results], [b'{"changed":true}'])
            self.assertEqual([result.etag for result in waiter_results], ["2"])
        finally:
            coordinator.close()

    def test_unsupported_transport_uses_degraded_refresh_mode(self) -> None:
        transport = FakeTransport(
            [
                response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}'),
                response_packet(
                    b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"changed":true}',
                    message_seq=3,
                ),
            ]
        )
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            http_wait_timeout_ms=100,
            degraded_refresh_interval_s=0,
        )

        self.assertFalse(coordinator.foreground_preemptive_native_long_poll_available)
        self.assertEqual(coordinator.long_poll_mode, "degraded-refresh")
        coordinator.start()
        self.assertIsNone(coordinator._worker)

        result = coordinator.wait_for_change("/datastore", "1")

        self.assertEqual(result.body, b'{"changed":true}')
        self.assertEqual(result.etag, "2")
        self.assertEqual(
            transport.writes[0],
            build_get_frame(0x20, 2, "/datastore"),
        )
        self.assertEqual(
            transport.writes[2],
            build_get_frame(0x22, 3, "/datastore"),
        )
