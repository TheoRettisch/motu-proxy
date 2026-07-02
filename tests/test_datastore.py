import errno
import threading
import time
from contextlib import redirect_stderr
from io import StringIO
from unittest import TestCase

from motu_proxy.datastore import (
    DEFAULT_RESPONSE_TIMEOUT_MS,
    DatastoreConfig,
    DatastoreCoordinator,
    DatastoreDeviceUnavailable,
    DatastoreNoResponse,
    DatastoreTimeout,
    ManagedDatastore,
    MotuUsbDatastore,
    ResponseStats,
    ShortUsbFrame,
    ShortUsbWrite,
    is_reconnectable_device_loss,
    read_device_capability_info,
)
from motu_proxy.device import NoDeviceFound
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

    def wait_for_read_timeouts(self, count: int, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self.read_timeouts) < count:
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


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeManagedSession:
    supports_cancellable_bulk_reads = False

    def __init__(
        self,
        get_effects: list[bytes | Exception] | None = None,
        post_effects: list[bytes | Exception] | None = None,
    ) -> None:
        self.get_effects = list(get_effects or [])
        self.post_effects = list(post_effects or [])
        self.get_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.post_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.closed = False
        self.last_response_stats: ResponseStats | None = None
        self.last_response_etag: str | None = None

    def get(self, *args, **kwargs) -> bytes:
        self.get_calls.append((args, kwargs))
        return self._pop_effect(self.get_effects)

    def post(self, *args, **kwargs) -> bytes:
        self.post_calls.append((args, kwargs))
        return self._pop_effect(self.post_effects)

    def _pop_effect(self, effects: list[bytes | Exception]) -> bytes:
        if not effects:
            raise AssertionError("unexpected fake datastore call")
        effect = effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


class FakeManagedContext:
    def __init__(self, effect: FakeManagedSession | Exception) -> None:
        self.effect = effect

    def __enter__(self):
        if isinstance(self.effect, Exception):
            raise self.effect
        return self.effect

    def __exit__(self, exc_type, exc, tb) -> None:
        if isinstance(self.effect, FakeManagedSession):
            self.effect.closed = True


class FakeManagedOpener:
    def __init__(self, effects: list[FakeManagedSession | Exception]) -> None:
        self.effects = list(effects)
        self.calls = 0

    def __call__(self, _config):
        self.calls += 1
        if not self.effects:
            raise AssertionError("unexpected datastore open")
        return FakeManagedContext(self.effects.pop(0))


def http_response(etag: str, body: bytes) -> bytes:
    return b"HTTP/1.1 200 OK\r\nETag: " + etag.encode("ascii") + b"\r\n\r\n" + body


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

    def test_get_forwards_ordered_query_fields(self) -> None:
        transport = FakeTransport([response_packet(b'{"mix/level/1":[0]}')])
        datastore = MotuUsbDatastore(transport)
        datastore.get("/meters", query_fields=(("meters", "mix/level"),))
        self.assertEqual(
            transport.writes[0],
            build_get_frame(
                0x20,
                2,
                "/meters",
                query_fields=(("meters", "mix/level"),),
            ),
        )
        self.assertNotIn(b"/meters?meters", transport.writes[0])

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
        datastore.post("/datastore/host/os", b'{"value":"linux"}')
        self.assertEqual(transport.writes[0], build_post_frame(0x20, 2, "/datastore/host/os", b'{"value":"linux"}'))

    def test_post_short_write_does_not_advance_message_sequence(self) -> None:
        transport = FakeTransport([], short_writes=True)
        datastore = MotuUsbDatastore(transport)
        with self.assertRaises(ShortUsbWrite):
            datastore.post("/datastore/host/os", b'{"value":"linux"}')
        self.assertEqual(datastore.message_seq, 2)

    def test_post_forwards_client_identifier(self) -> None:
        transport = FakeTransport([response_packet(b'{"ok":true}')])
        datastore = MotuUsbDatastore(transport)
        datastore.post("/datastore/host/os", b'{"value":"linux"}', client=1479701624)
        self.assertEqual(
            transport.writes[0],
            build_post_frame(0x20, 2, "/datastore/host/os", b'{"value":"linux"}', client=1479701624),
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


class ManagedDatastoreTests(TestCase):
    def test_device_loss_taxonomy_is_explicit(self) -> None:
        self.assertTrue(is_reconnectable_device_loss(NoDeviceFound("missing"), during_open=True))
        self.assertTrue(is_reconnectable_device_loss(OSError(errno.ENODEV, "device disappeared")))
        self.assertTrue(is_reconnectable_device_loss(ShortUsbWrite("short write")))
        self.assertTrue(is_reconnectable_device_loss(DatastoreNoResponse("init timed out"), during_open=True))

        self.assertFalse(is_reconnectable_device_loss(PermissionError(errno.EACCES, "denied")))
        self.assertFalse(is_reconnectable_device_loss(ResponseFrameError("CRC mismatch")))
        self.assertFalse(is_reconnectable_device_loss(DatastoreNoResponse("request timed out")))
        self.assertFalse(is_reconnectable_device_loss(ShortUsbFrame("short frame")))

    def test_open_failure_maps_to_temporary_unavailable(self) -> None:
        clock = ManualClock()
        opener = FakeManagedOpener([NoDeviceFound("missing")])
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=1.0,
            clock=clock,
        )

        with self.assertRaises(DatastoreDeviceUnavailable):
            manager.get("/datastore/uid")

        status = manager.status()
        self.assertFalse(status["datastore_available"])
        self.assertEqual(status["datastore_reconnect_state"], "backoff")
        self.assertEqual(
            status["datastore_last_reconnect_error"],
            {"type": "NoDeviceFound", "message": "missing"},
        )
        self.assertEqual(status["datastore_retry_delay_s"], 1.0)
        self.assertEqual(status["datastore_next_retry_in_s"], 1.0)

    def test_reconnectable_status_reports_current_retry_delay(self) -> None:
        clock = ManualClock()
        opener = FakeManagedOpener([NoDeviceFound("missing"), NoDeviceFound("still missing")])
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=1.0,
            reconnect_max_delay_s=3.0,
            clock=clock,
        )

        with self.assertRaises(DatastoreDeviceUnavailable):
            manager.get("/datastore/uid")
        first_status = manager.status()
        self.assertEqual(first_status["datastore_retry_delay_s"], 1.0)
        self.assertEqual(first_status["datastore_next_retry_in_s"], 1.0)

        clock.advance(1.0)
        with self.assertRaises(DatastoreDeviceUnavailable):
            manager.get("/datastore/uid")
        second_status = manager.status()
        self.assertEqual(second_status["datastore_retry_delay_s"], 2.0)
        self.assertEqual(second_status["datastore_next_retry_in_s"], 2.0)

    def test_non_reconnectable_open_failure_uses_configuration_backoff(self) -> None:
        clock = ManualClock()
        opener = FakeManagedOpener(
            [
                PermissionError(errno.EACCES, "permission denied"),
                PermissionError(errno.EACCES, "permission denied"),
            ]
        )
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=1.0,
            reconnect_max_delay_s=3.0,
            configuration_error_retry_delay_s=7.0,
            clock=clock,
        )

        with self.assertRaises(PermissionError):
            manager.get("/datastore/uid")
        status = manager.status()
        self.assertEqual(status["datastore_reconnect_state"], "configuration_error")
        self.assertEqual(status["datastore_retry_delay_s"], 7.0)
        self.assertEqual(status["datastore_next_retry_in_s"], 7.0)

        with self.assertRaises(DatastoreDeviceUnavailable):
            manager.get("/datastore/uid")
        self.assertEqual(opener.calls, 1)

        clock.advance(7.0)
        with self.assertRaises(PermissionError):
            manager.get("/datastore/uid")
        self.assertEqual(opener.calls, 2)

    def test_status_does_not_wait_for_slow_session_open(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        session = FakeManagedSession(get_effects=[http_response("uid", b'{"value":"ok"}')])

        class BlockingContext:
            def __enter__(self):
                entered.set()
                release.wait(timeout=1.0)
                return session

            def __exit__(self, exc_type, exc, tb) -> None:
                session.closed = True

        class BlockingOpener:
            def __init__(self) -> None:
                self.calls = 0

            def __call__(self, _config):
                self.calls += 1
                return BlockingContext()

        opener = BlockingOpener()
        manager = ManagedDatastore(DatastoreConfig(), opener=opener)
        errors: list[Exception] = []

        def request() -> None:
            try:
                manager.get("/datastore/uid")
            except Exception as exc:
                errors.append(exc)

        request_thread = threading.Thread(target=request)
        request_thread.start()
        status_results: list[dict[str, object | None]] = []
        status_thread = threading.Thread(target=lambda: status_results.append(manager.status()))
        try:
            self.assertTrue(entered.wait(timeout=1.0))
            status_thread.start()
            status_thread.join(timeout=0.2)
            self.assertFalse(status_thread.is_alive())
            self.assertEqual(status_results[0]["datastore_reconnect_state"], "reconnecting")
            self.assertFalse(status_results[0]["datastore_available"])
        finally:
            release.set()
            request_thread.join(timeout=1.0)
            status_thread.join(timeout=1.0)

        self.assertFalse(request_thread.is_alive())
        self.assertEqual(errors, [])

    def test_session_is_discarded_after_reconnectable_usb_loss(self) -> None:
        session = FakeManagedSession(get_effects=[OSError(errno.ENODEV, "device disappeared")])
        opener = FakeManagedOpener([session])
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=1.0,
            clock=ManualClock(),
        )

        with self.assertRaises(DatastoreDeviceUnavailable):
            manager.get("/datastore/uid")

        self.assertTrue(session.closed)
        self.assertFalse(manager.status()["datastore_available"])
        self.assertEqual(manager.session_generation, 1)

    def test_reconnect_succeeds_after_fake_device_returns(self) -> None:
        clock = ManualClock()
        recovered = FakeManagedSession(get_effects=[http_response("uid", b'{"value":"ok"}')])
        opener = FakeManagedOpener([NoDeviceFound("missing"), recovered])
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=1.0,
            clock=clock,
        )

        with self.assertRaises(DatastoreDeviceUnavailable):
            manager.get("/datastore/uid")
        clock.advance(1.0)
        response = manager.get("/datastore/uid")

        self.assertEqual(response, http_response("uid", b'{"value":"ok"}'))
        self.assertTrue(manager.status()["datastore_available"])
        self.assertEqual(manager.session_generation, 1)
        self.assertEqual(opener.calls, 2)

    def test_concurrent_outage_requests_share_one_reconnect_attempt(self) -> None:
        opener = FakeManagedOpener([NoDeviceFound("missing")])
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=10.0,
            clock=ManualClock(),
        )
        errors: list[Exception] = []

        def request() -> None:
            try:
                manager.get("/datastore/uid")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=request) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertEqual(opener.calls, 1)
        self.assertEqual(len(errors), 2)
        self.assertTrue(all(isinstance(exc, DatastoreDeviceUnavailable) for exc in errors))

    def test_write_failure_is_not_replayed_after_reconnect(self) -> None:
        clock = ManualClock()
        first = FakeManagedSession(post_effects=[OSError(errno.ENODEV, "device disappeared")])
        second = FakeManagedSession(post_effects=[http_response("2", b'{"ok":true}')])
        opener = FakeManagedOpener([first, second])
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=1.0,
            clock=clock,
        )

        with self.assertRaises(DatastoreDeviceUnavailable):
            manager.post("/datastore/host/os", b'{"value":"linux"}')
        clock.advance(1.0)
        response = manager.post("/datastore/host/os", b'{"value":"linux"}')

        self.assertEqual(response, http_response("2", b'{"ok":true}'))
        self.assertEqual(len(first.post_calls), 1)
        self.assertEqual(len(second.post_calls), 1)


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

    def test_read_publishes_poll_path_before_releasing_foreground_io(self) -> None:
        transport = FakeTransport([response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')])
        coordinator = DatastoreCoordinator(MotuUsbDatastore(transport))
        release_etags: list[str | None] = []
        release_io = coordinator._release_io

        def record_release_etag() -> None:
            release_etags.append(coordinator.latest_etag)
            release_io()

        coordinator._release_io = record_release_etag

        payload = coordinator.read("/datastore")

        self.assertEqual(payload.etag, "1")
        self.assertEqual(release_etags, ["1"])

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

    def test_poller_publishes_payload_before_releasing_io(self) -> None:
        initial = response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')
        changed = response_packet(
            b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"changed":true}',
            message_seq=3,
        )
        transport = BlockingTransport([initial])
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            long_poll_timeout_ms=5000,
            http_wait_timeout_ms=1000,
            poll_interval_s=10,
        )
        release_etags: list[str | None] = []
        release_io = coordinator._release_io

        def record_release_etag() -> None:
            release_etags.append(coordinator.latest_etag)
            release_io()

        try:
            self.assertEqual(coordinator.read("/datastore").etag, "1")
            coordinator._release_io = record_release_etag
            coordinator.start()
            self.assertTrue(transport.wait_for_writes(3))

            transport.push(changed)
            deadline = time.monotonic() + 1
            while not release_etags and time.monotonic() < deadline:
                time.sleep(0.01)

            self.assertEqual(release_etags[:1], ["2"])
        finally:
            coordinator.close()

    def test_background_poller_initial_refresh_is_not_cancellable_native_hold(self) -> None:
        initial = response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')
        transport = BlockingTransport()
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            long_poll_timeout_ms=5000,
            poll_read_timeout_slice_ms=20,
            poll_interval_s=0,
        )
        try:
            coordinator.start()
            self.assertTrue(transport.wait_for_writes(1))
            self.assertTrue(transport.wait_for_read_timeouts(1))
            self.assertEqual(transport.cancellable_read_timeouts, [])
            self.assertLessEqual(transport.read_timeouts[0], DEFAULT_RESPONSE_TIMEOUT_MS)

            transport.push(initial)
            self.assertTrue(transport.wait_for_cancellable_reads(1))
            self.assertEqual(coordinator.latest_etag, "1")
            self.assertGreaterEqual(transport.cancellable_read_timeouts[0], 4900)
            self.assertLessEqual(transport.cancellable_read_timeouts[0], 5000)
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

    def test_meters_if_none_match_uses_one_shot_read_not_datastore_wait(self) -> None:
        transport = FakeTransport(
            [response_packet(b"HTTP/1.1 304 Not Modified\r\nETag: 3197890\r\n\r\n")]
        )
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            http_wait_timeout_ms=1,
        )
        wait_calls = []

        def wait_for_change(*args, **kwargs):
            wait_calls.append((args, kwargs))
            raise AssertionError("meters must not use datastore long-poll wait")

        coordinator.wait_for_change = wait_for_change
        payload = coordinator.get(
            "/meters",
            client="1479701624",
            if_none_match="3197890",
            query_fields=(("meters", "mix/level"), ("client", "1479701624")),
        )

        get_writes = [write for write in transport.writes if len(write) > 1 and write[1] == 0x80]
        self.assertEqual(len(get_writes), 1)
        self.assertEqual(
            get_writes[0],
            build_get_frame(
                0x20,
                2,
                "/meters",
                etag="3197890",
                query_fields=(("meters", "mix/level"), ("client", "1479701624")),
            ),
        )
        self.assertEqual(wait_calls, [])
        self.assertTrue(payload.not_modified)
        self.assertEqual(payload.etag, "3197890")
        self.assertEqual(payload.body, b"")

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

    def test_status_includes_managed_datastore_reconnect_state(self) -> None:
        clock = ManualClock()
        opener = FakeManagedOpener([NoDeviceFound("missing")])
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=1.0,
            clock=clock,
        )
        coordinator = DatastoreCoordinator(manager)

        with self.assertRaises(DatastoreDeviceUnavailable):
            coordinator.read("/datastore/uid")
        status = coordinator.status()

        self.assertFalse(status["datastore_available"])
        self.assertEqual(status["datastore_reconnect_state"], "backoff")
        self.assertEqual(
            status["datastore_last_reconnect_error"],
            {"type": "NoDeviceFound", "message": "missing"},
        )
        self.assertEqual(status["datastore_next_retry_in_s"], 1.0)

    def test_foreground_request_returns_after_reconnect_success(self) -> None:
        clock = ManualClock()
        recovered = FakeManagedSession(get_effects=[http_response("uid", b'{"value":"ok"}')])
        opener = FakeManagedOpener([NoDeviceFound("missing"), recovered])
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=1.0,
            clock=clock,
        )
        coordinator = DatastoreCoordinator(manager)

        with self.assertRaises(DatastoreDeviceUnavailable):
            coordinator.read("/datastore/uid")
        clock.advance(1.0)
        payload = coordinator.read("/datastore/uid")

        self.assertEqual(payload.body, b'{"value":"ok"}')
        self.assertEqual(payload.etag, "uid")
        self.assertTrue(coordinator.status()["datastore_available"])

    def test_reconnect_clears_etag_and_delta_history(self) -> None:
        clock = ManualClock()
        first = FakeManagedSession(
            get_effects=[
                http_response("1", b'{"state":1}'),
                OSError(errno.ENODEV, "device disappeared"),
            ]
        )
        second = FakeManagedSession(get_effects=[http_response("fresh", b'{"state":"fresh"}')])
        opener = FakeManagedOpener([first, second])
        manager = ManagedDatastore(
            DatastoreConfig(),
            opener=opener,
            reconnect_initial_delay_s=1.0,
            clock=clock,
        )
        coordinator = DatastoreCoordinator(manager)
        self.assertEqual(coordinator.read("/datastore").etag, "1")
        coordinator._publish_payload(DatastorePayload(b'{"delta":2}', etag="2"), None, from_etag="1")
        self.assertEqual(len(coordinator.history), 1)

        with self.assertRaises(DatastoreDeviceUnavailable):
            coordinator.read("/datastore")
        clock.advance(1.0)
        recovered = coordinator.read("/datastore")

        self.assertEqual(recovered.body, b'{"state":"fresh"}')
        self.assertEqual(recovered.etag, "fresh")
        self.assertEqual(coordinator.latest_etag, "fresh")
        self.assertEqual(coordinator.history, ())

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

    def test_cached_transition_carries_content_type(self) -> None:
        coordinator = DatastoreCoordinator(MotuUsbDatastore(FakeTransport([])))
        coordinator._publish_payload(
            DatastorePayload(b'{"first":true}{"second":true}', etag="2"),
            None,
            from_etag="1",
        )

        result = coordinator.wait_for_change("/datastore", "1")

        self.assertEqual(result.content_type, "application/octet-stream")

    def test_direct_read_carries_content_type(self) -> None:
        transport = FakeTransport(
            [
                response_packet(
                    b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"first":true}{"second":true}'
                ),
            ]
        )
        coordinator = DatastoreCoordinator(MotuUsbDatastore(transport))

        result = coordinator.read("/datastore")

        self.assertEqual(result.content_type, "application/octet-stream")

    def test_non_client_query_fields_bypass_cached_long_poll_transition(self) -> None:
        transport = FakeTransport(
            [
                response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}'),
                response_packet(
                    b'HTTP/1.1 200 OK\r\nETag: 2\r\n\r\n{"future":"raw"}',
                    message_seq=3,
                ),
            ]
        )
        coordinator = DatastoreCoordinator(
            MotuUsbDatastore(transport),
            http_wait_timeout_ms=1,
        )
        self.assertEqual(coordinator.read("/datastore").etag, "1")
        coordinator._publish_payload(DatastorePayload(b'{"delta":2}', etag="2"), None, from_etag="1")

        result = coordinator.get(
            "/datastore",
            if_none_match="1",
            query_fields=(("future", "raw"),),
        )

        self.assertEqual(result.body, b'{"future":"raw"}')
        self.assertEqual(result.etag, "2")
        get_writes = [write for write in transport.writes if len(write) > 1 and write[1] == 0x80]
        self.assertEqual(
            get_writes[-1],
            build_get_frame(
                0x22,
                3,
                "/datastore",
                query_fields=(("future", "raw"),),
            ),
        )

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

        returned = coordinator.post("/datastore/host/os", b'{"value":"linux"}', client="7")
        other = coordinator.wait_for_change("/datastore", "1", client="8")
        own = coordinator.wait_for_change("/datastore", "1", client="7")

        self.assertEqual(returned.body, b'{"post":true}')
        self.assertEqual(other.body, b'{"full":true}')
        self.assertEqual(other.etag, "2")
        self.assertTrue(own.not_modified)

    def test_post_publishes_refresh_before_releasing_foreground_io(self) -> None:
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
        release_etags: list[str | None] = []
        release_io = coordinator._release_io

        def record_release_etag() -> None:
            release_etags.append(coordinator.latest_etag)
            release_io()

        coordinator._release_io = record_release_etag

        returned = coordinator.post("/datastore/host/os", b'{"value":"linux"}', client="7")

        self.assertEqual(returned.body, b'{"post":true}')
        self.assertEqual(release_etags, ["2"])

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

        returned = coordinator.post("/datastore/host/os", b'{"value":"linux"}', client="7")

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
                    coordinator.post("/datastore/host/os", b'{"value":"linux"}', client="7")
                )
            )
            post.start()
            self.assertTrue(transport.wait_for_writes(4, timeout=0.25))
            self.assertEqual(
                transport.writes[3],
                build_post_frame(0x23, 4, "/datastore/host/os", b'{"value":"linux"}', client="7"),
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
        initial = response_packet(b'HTTP/1.1 200 OK\r\nETag: 1\r\n\r\n{"state":1}')
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
            transport.push(initial)
            self.assertTrue(transport.wait_for_cancellable_reads(1))
            self.assertGreaterEqual(transport.cancellable_read_timeouts[0], 900)
            self.assertLessEqual(transport.cancellable_read_timeouts[0], 1000)

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
