import json
from contextlib import redirect_stderr
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest import TestCase

from motu_proxy.cli import build_parser
from motu_proxy.datastore import DatastoreDeviceUnavailable, DatastoreNoResponse
from motu_proxy.http_server import (
    STATUS_PATH,
    WRITE_TOKEN_HEADER,
    BadRequest,
    CrossOriginWrite,
    DatastoreDispatcher,
    DispatchResult,
    HostNotAllowed,
    MotuProxyHandler,
    MotuProxyServer,
    RequestBodyTimeout,
    RequestBodyTooLarge,
    WritesDisabled,
    WriteTokenRequired,
    dispatch_datastore_request,
    log_write_attempt,
    log_write_attempt_debug,
    response_content_type,
    serve,
)
from motu_proxy.json_body import InvalidJsonBody
from motu_proxy.parser import DatastorePayload
from motu_proxy.protocol import ProtocolFrameTooLarge, max_post_json_body_bytes
from motu_proxy.schema import DatastorePermissionError, DatastoreValidationError

WRITE_TOKEN = "test-write-token"
_UNSET = object()


class RecordingLock:
    def __init__(self) -> None:
        self.entries = 0
        self.exits = 0

    def __enter__(self):
        self.entries += 1
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.exits += 1


class TrackingBody(BytesIO):
    def __init__(self, body: bytes) -> None:
        super().__init__(body)
        self.reads = 0

    def read(self, *args, **kwargs):
        self.reads += 1
        return super().read(*args, **kwargs)


class TimeoutBody:
    def read(self, *args, **kwargs):
        raise TimeoutError("client stalled")


class RecordingConnection:
    def __init__(self, timeout: float | None = None) -> None:
        self.timeout = timeout
        self.timeouts: list[float | None] = []

    def gettimeout(self) -> float | None:
        return self.timeout

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout
        self.timeouts.append(timeout)


class BufferedSocket:
    def __init__(self, request: bytes) -> None:
        self.request = BytesIO(request)
        self.response = BytesIO()

    def makefile(self, mode: str, *args, **kwargs):
        if "r" in mode:
            return self.request
        if "w" in mode:
            return self.response
        raise ValueError(f"unsupported mode {mode!r}")

    def sendall(self, data: bytes) -> None:
        self.response.write(data)


class HttpServerTests(TestCase):
    def dispatch(
        self,
        method: str,
        path: str,
        body: str = "",
        content_type: str = "",
        allow_writes: bool = False,
        origin: str | None = None,
        host: str | None | object = _UNSET,
        write_token: str | None = WRITE_TOKEN,
        request_token: str | None | object = _UNSET,
        allow_remote_writes: bool = False,
        allow_unknown_writes: bool = False,
    ):
        calls: list[tuple[str, str, str | None]] = []

        def get(path: str, client: str | None = None) -> bytes:
            calls.append(("GET", path, None))
            return b'{"value":"0001f2fffe00c719"}'

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append(("POST", path, body))
            return b'{"ok":true}'

        if host is _UNSET:
            host = "127.0.0.1:1280" if allow_writes else None
        if request_token is _UNSET:
            request_token = write_token if allow_writes else None
        result = dispatch_datastore_request(
            method,
            path,
            body,
            content_type,
            allow_writes,
            get,
            post,
            origin=origin,
            host=host,
            write_token=write_token,
            request_token=request_token,
            allow_remote_writes=allow_remote_writes,
            allow_unknown_writes=allow_unknown_writes,
        )
        return result, calls

    def test_get_returns_json_and_normalizes_path(self) -> None:
        result, calls = self.dispatch("GET", "/uid")
        self.assertEqual(result.response, b'{"value":"0001f2fffe00c719"}')
        self.assertEqual(result.path, "/datastore/uid")
        self.assertEqual(calls, [("GET", "/datastore/uid", None)])

    def test_get_response_shapes_are_forwarded_verbatim(self) -> None:
        cases = [
            ("/datastore/uid", b'{"value":"0001f2fffe00c719"}'),
            ("/datastore/mix/chan/0/gate", b'{"enable":{"value":0},"threshold":{"value":-60}}'),
            ("/datastore", b'{"uid":{"value":"0001f2fffe00c719"},"host":{"mode":{"value":"UAC"}}}'),
        ]
        for request_path, body in cases:
            with self.subTest(request_path=request_path):
                result = dispatch_datastore_request(
                    "GET",
                    request_path,
                    "",
                    "",
                    False,
                    lambda path, client=None, body=body: body,
                    lambda path, body, client=None: b"{}",
                )
                self.assertEqual(result.response, body)

    def test_get_forwards_client_identifier(self) -> None:
        calls: list[tuple[str, str | None]] = []

        def get(path: str, client: str | None = None) -> bytes:
            calls.append((path, client))
            return b'{"value":"0001f2fffe00c719"}'

        result = dispatch_datastore_request(
            "GET",
            "/uid?client=1479701624",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
        )
        self.assertEqual(result.path, "/datastore/uid")
        self.assertEqual(calls, [("/datastore/uid", "1479701624")])

    def test_get_forwards_ordered_query_fields(self) -> None:
        calls: list[tuple[str, str | None, str | None, tuple[tuple[str, str], ...]]] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> bytes:
            calls.append((path, client, if_none_match, query_fields))
            return b'{"mix/level/1":[0]}'

        result = dispatch_datastore_request(
            "GET",
            "/meters?meters=mix/level&client=1479701624",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
        )

        self.assertEqual(result.path, "/meters")
        self.assertEqual(result.response, b'{"mix/level/1":[0]}')
        self.assertEqual(
            calls,
            [
                (
                    "/meters",
                    "1479701624",
                    None,
                    (("meters", "mix/level"), ("client", "1479701624")),
                )
            ],
        )

    def test_get_preserves_repeated_query_fields_and_blank_values(self) -> None:
        calls: list[tuple[tuple[tuple[str, str], ...]]] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> bytes:
            calls.append((query_fields,))
            return b"{}"

        dispatch_datastore_request(
            "GET",
            "/meters?meters=mix/level&meters=ext/input&label=",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
        )

        self.assertEqual(
            calls,
            [((("meters", "mix/level"), ("meters", "ext/input"), ("label", "")),)],
        )

    def test_get_rejects_empty_query_field_name_before_usb_call(self) -> None:
        calls: list[str] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> bytes:
            calls.append(path)
            return b"{}"

        with self.assertRaisesRegex(BadRequest, "query field name"):
            dispatch_datastore_request(
                "GET",
                "/meters?=mix/level",
                "",
                "",
                False,
                get,
                lambda path, body, client=None: b"{}",
            )
        self.assertEqual(calls, [])

    def test_get_forwards_unknown_datastore_query_field(self) -> None:
        calls: list[tuple[str, tuple[tuple[str, str], ...]]] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> bytes:
            calls.append((path, query_fields))
            return b'{"value":"ok"}'

        result = dispatch_datastore_request(
            "GET",
            "/uid?future=raw",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
        )

        self.assertEqual(result.path, "/datastore/uid")
        self.assertEqual(calls, [("/datastore/uid", (("future", "raw"),))])

    def test_get_validates_client_before_general_query_passthrough(self) -> None:
        calls: list[str] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> bytes:
            calls.append(path)
            return b"{}"

        with self.assertRaisesRegex(BadRequest, "client"):
            dispatch_datastore_request(
                "GET",
                "/meters?meters=mix/level&client=-1",
                "",
                "",
                False,
                get,
                lambda path, body, client=None: b"{}",
            )
        self.assertEqual(calls, [])

    def test_get_rejects_repeated_client_query_fields_before_usb_call(self) -> None:
        calls: list[str] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> bytes:
            calls.append(path)
            return b"{}"

        with self.assertRaisesRegex(BadRequest, "client.*repeated"):
            dispatch_datastore_request(
                "GET",
                "/datastore?client=1&client=2",
                "",
                "",
                False,
                get,
                lambda path, body, client=None: b"{}",
            )
        self.assertEqual(calls, [])

    def test_meters_response_body_and_etag_are_forwarded(self) -> None:
        body = b'{"unknown/future":[123,456]}'
        calls: list[tuple[tuple[tuple[str, str], ...]]] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> DatastorePayload:
            calls.append((query_fields,))
            return DatastorePayload(body, etag="3197889")

        result = dispatch_datastore_request(
            "GET",
            "/meters?meters=unknown/future",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
        )

        self.assertEqual(result.path, "/meters")
        self.assertEqual(result.response, body)
        self.assertEqual(result.etag, "3197889")
        self.assertEqual(calls, [((("meters", "unknown/future"),),)])

    def test_meter_if_none_match_is_forwarded_to_device_read(self) -> None:
        calls: list[tuple[str, str | None, tuple[tuple[str, str], ...]]] = []

        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> DatastorePayload:
            calls.append((path, if_none_match, query_fields))
            return DatastorePayload(b'{"mix/level/1":[1]}', etag="3197890")

        result = dispatch_datastore_request(
            "GET",
            "/meters?meters=mix/level",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
            if_none_match=" 3197889 ",
        )

        self.assertEqual(result.status, 200)
        self.assertEqual(result.etag, "3197890")
        self.assertEqual(calls, [("/meters", "3197889", (("meters", "mix/level"),))])

    def test_meter_no_change_response_is_forwarded(self) -> None:
        def get(
            path: str,
            client: str | None = None,
            if_none_match: str | None = None,
            query_fields: tuple[tuple[str, str], ...] = (),
        ) -> DatastorePayload:
            return DatastorePayload(b"", etag=if_none_match, not_modified=True)

        result = dispatch_datastore_request(
            "GET",
            "/meters?meters=mix/level",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
            if_none_match="3197890",
        )

        self.assertEqual(result.status, 304)
        self.assertEqual(result.response, b"")
        self.assertEqual(result.etag, "3197890")

    def test_get_forwards_if_none_match(self) -> None:
        calls: list[tuple[str, str | None, str | None]] = []

        def get(path: str, client: str | None = None, if_none_match: str | None = None) -> DatastorePayload:
            calls.append((path, client, if_none_match))
            return DatastorePayload(b'{"changed":true}', etag="5679")

        result = dispatch_datastore_request(
            "GET",
            "/datastore?client=1479701624",
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
            if_none_match="5678",
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.response, b'{"changed":true}')
        self.assertEqual(result.etag, "5679")
        self.assertEqual(calls, [("/datastore", "1479701624", "5678")])

    def test_get_maps_not_modified_payload_to_304(self) -> None:
        result = dispatch_datastore_request(
            "GET",
            "/datastore",
            "",
            "",
            False,
            lambda path, client=None, if_none_match=None: DatastorePayload(
                b"",
                etag=if_none_match,
                not_modified=True,
            ),
            lambda path, body, client=None: b"{}",
            if_none_match="5678",
        )
        self.assertEqual(result.status, 304)
        self.assertEqual(result.response, b"")
        self.assertEqual(result.etag, "5678")

    def test_status_endpoint_returns_provider_json_without_datastore_dispatch(self) -> None:
        def get(path: str, client: str | None = None) -> bytes:
            raise AssertionError("unexpected get")

        result = dispatch_datastore_request(
            "GET",
            STATUS_PATH,
            "",
            "",
            False,
            get,
            lambda path, body, client=None: b"{}",
            status_provider=lambda: {
                "long_poll_mode": "native-preemptive",
                "latest_etag": "5678",
                "last_poller_error": None,
            },
        )

        self.assertEqual(result.path, STATUS_PATH)
        self.assertEqual(
            json.loads(result.response.decode("utf-8")),
            {
                "long_poll_mode": "native-preemptive",
                "latest_etag": "5678",
                "last_poller_error": None,
            },
        )

    def test_invalid_client_identifier_is_rejected(self) -> None:
        with self.assertRaisesRegex(BadRequest, "client"):
            dispatch_datastore_request(
                "GET",
                "/uid?client=-1",
                "",
                "",
                False,
                lambda path, client=None: b"{}",
                lambda path, body, client=None: b"{}",
            )

    def test_writes_rejected_by_default(self) -> None:
        with self.assertRaises(WritesDisabled):
            self.dispatch("POST", "/datastore/uid", body='{"value":"x"}')

    def test_post_accepts_json_form_field_when_enabled(self) -> None:
        result, calls = self.dispatch(
            "POST",
            "/datastore/host/os",
            body='json={"value":"linux"}',
            content_type="application/x-www-form-urlencoded",
            allow_writes=True,
        )
        self.assertEqual(result.response, b'{"ok":true}')
        self.assertEqual(calls, [("POST", "/datastore/host/os", '{"value":"linux"}')])

    def test_post_forwards_client_identifier(self) -> None:
        calls: list[tuple[str, str, str | None]] = []

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body, client))
            return b'{"ok":true}'

        result = dispatch_datastore_request(
            "POST",
            "/host/os?client=1479701624",
            '{"value":"linux"}',
            "application/json",
            True,
            lambda path, client=None: b"{}",
            post,
            host="127.0.0.1:1280",
            write_token=WRITE_TOKEN,
            request_token=WRITE_TOKEN,
        )
        self.assertEqual(result.path, "/datastore/host/os")
        self.assertEqual(calls, [("/datastore/host/os", '{"value":"linux"}', "1479701624")])

    def test_post_ignores_non_client_query_fields(self) -> None:
        calls: list[tuple[str, str, str | None]] = []

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body, client))
            return b'{"ok":true}'

        dispatch_datastore_request(
            "POST",
            "/host/os?meters=mix/level&client=1479701624",
            '{"value":"linux"}',
            "application/json",
            True,
            lambda path, client=None: b"{}",
            post,
            host="127.0.0.1:1280",
            write_token=WRITE_TOKEN,
            request_token=WRITE_TOKEN,
        )

        self.assertEqual(calls, [("/datastore/host/os", '{"value":"linux"}', "1479701624")])

    def test_patch_is_post_alias_not_partial_update(self) -> None:
        result, calls = self.dispatch("PATCH", "/host/os", body='{"value":"linux"}', allow_writes=True)
        self.assertEqual(result.response, b'{"ok":true}')
        self.assertEqual(calls, [("POST", "/datastore/host/os", '{"value":"linux"}')])

    def test_cross_origin_write_is_rejected_when_writes_are_enabled(self) -> None:
        calls: list[tuple[str, str, str | None]] = []

        def get(path: str, client: str | None = None) -> bytes:
            calls.append(("GET", path, None))
            return b"{}"

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append(("POST", path, body))
            return b"{}"

        with self.assertRaises(CrossOriginWrite):
            dispatch_datastore_request(
                "POST",
                "/host/os",
                '{"value":"linux"}',
                "application/json",
                True,
                get,
                post,
                origin="https://example.test",
                host="127.0.0.1:1280",
                write_token=WRITE_TOKEN,
                request_token=WRITE_TOKEN,
            )
        self.assertEqual(calls, [])

    def test_missing_write_token_is_rejected_when_writes_are_enabled(self) -> None:
        with self.assertRaises(WriteTokenRequired):
            self.dispatch("POST", "/host/os", body='{"value":"linux"}', allow_writes=True, request_token=None)

    def test_unknown_host_is_rejected_when_writes_are_enabled(self) -> None:
        with self.assertRaises(HostNotAllowed):
            self.dispatch(
                "POST",
                "/host/os",
                body='{"value":"linux"}',
                allow_writes=True,
                host="device.local:1280",
            )

    def test_null_origin_is_rejected_when_writes_are_enabled(self) -> None:
        with self.assertRaises(CrossOriginWrite):
            self.dispatch(
                "POST",
                "/host/os",
                body='{"value":"linux"}',
                allow_writes=True,
                origin="null",
            )

    def test_unsafe_remote_write_mode_still_requires_token(self) -> None:
        with self.assertRaises(WriteTokenRequired):
            self.dispatch(
                "POST",
                "/host/os",
                body='{"value":"linux"}',
                allow_writes=True,
                host="device.local:1280",
                request_token=None,
                allow_remote_writes=True,
            )

    def test_unsafe_remote_write_mode_allows_non_loopback_host_with_token(self) -> None:
        result, calls = self.dispatch(
            "POST",
            "/host/os",
            body='{"value":"linux"}',
            allow_writes=True,
            host="device.local:1280",
            allow_remote_writes=True,
        )
        self.assertEqual(result.response, b'{"ok":true}')
        self.assertEqual(calls, [("POST", "/datastore/host/os", '{"value":"linux"}')])

    def test_https_origin_is_rejected_for_plain_http_server(self) -> None:
        with self.assertRaises(CrossOriginWrite):
            self.dispatch(
                "POST",
                "/host/os",
                body='{"value":"linux"}',
                content_type="application/json",
                allow_writes=True,
                origin="https://127.0.0.1:1280",
                host="127.0.0.1:1280",
            )

    def test_same_origin_write_is_allowed_when_writes_are_enabled(self) -> None:
        result, calls = self.dispatch(
            "POST",
            "/host/os",
            body='{"value":"linux"}',
            content_type="application/json",
            allow_writes=True,
            origin="http://127.0.0.1:1280",
            host="127.0.0.1:1280",
        )
        self.assertEqual(result.response, b'{"ok":true}')
        self.assertEqual(calls, [("POST", "/datastore/host/os", '{"value":"linux"}')])

    def test_dispatcher_serializes_access_with_lock(self) -> None:
        calls: list[tuple[str, str]] = []

        def get(path: str, client: str | None = None) -> bytes:
            calls.append(("GET", path))
            return b"{}"

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append(("POST", path))
            return b"{}"

        lock = RecordingLock()
        dispatcher = DatastoreDispatcher(False, get, post, log_write=None, lock=lock)
        dispatcher.dispatch("GET", "/uid")
        self.assertEqual(lock.entries, 1)
        self.assertEqual(lock.exits, 1)
        self.assertEqual(calls, [("GET", "/datastore/uid")])

    def test_disabled_write_attempts_do_not_log_body(self) -> None:
        logs: list[tuple[str, str, str]] = []
        dispatcher = DatastoreDispatcher(
            False,
            lambda path, client=None: b"{}",
            lambda path, body, client=None: b"{}",
            log_write=lambda method, path, body: logs.append((method, path, body)),
            lock=RecordingLock(),
        )
        with self.assertRaises(WritesDisabled):
            dispatcher.dispatch("PATCH", "/host/os", '{"value":"linux"}')
        self.assertEqual(logs, [])

    def test_write_attempts_are_not_logged_before_token_validation(self) -> None:
        logs: list[tuple[str, str, str]] = []
        dispatcher = DatastoreDispatcher(
            True,
            lambda path, client=None: b"{}",
            lambda path, body, client=None: b"{}",
            write_token=WRITE_TOKEN,
            log_write=lambda method, path, body: logs.append((method, path, body)),
            lock=RecordingLock(),
        )
        with self.assertRaises(WriteTokenRequired):
            dispatcher.dispatch(
                "POST",
                "/host/os",
                '{"value":"linux"}',
                "application/json",
                host="127.0.0.1:1280",
                request_token=None,
            )
        self.assertEqual(logs, [])

    def test_write_attempts_are_logged_when_enabled(self) -> None:
        logs: list[tuple[str, str, str]] = []
        dispatcher = DatastoreDispatcher(
            True,
            lambda path, client=None: b"{}",
            lambda path, body, client=None: b"{}",
            write_token=WRITE_TOKEN,
            log_write=lambda method, path, body: logs.append((method, path, body)),
            lock=RecordingLock(),
        )
        dispatcher.dispatch(
            "POST",
            "/host/os",
            'json={"value":"linux"}',
            "application/x-www-form-urlencoded",
            host="127.0.0.1:1280",
            request_token=WRITE_TOKEN,
        )
        self.assertEqual(logs, [("POST", "/datastore/host/os", '{"value":"linux"}')])

    def test_default_write_logger_redacts_body(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            log_write_attempt("POST", "/datastore/host/os", '{"value":"linux"}')
        output = stderr.getvalue()
        self.assertIn("body_bytes=17", output)
        self.assertNotIn("linux", output)

    def test_debug_write_logger_includes_body(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr):
            log_write_attempt_debug("POST", "/datastore/host/os", '{"value":"linux"}')
        self.assertIn('body=\'{"value":"linux"}\'', stderr.getvalue())

    def test_invalid_write_json_is_rejected_before_usb_call(self) -> None:
        calls: list[tuple[str, str]] = []

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body))
            return b"{}"

        with self.assertRaises(InvalidJsonBody):
            dispatch_datastore_request(
                "POST",
                "/host/os",
                '{"value":',
                "application/json",
                True,
                lambda path, client=None: b"{}",
                post,
                host="127.0.0.1:1280",
                write_token=WRITE_TOKEN,
                request_token=WRITE_TOKEN,
            )
        self.assertEqual(calls, [])

    def test_non_finite_write_json_is_rejected_before_usb_call(self) -> None:
        calls: list[tuple[str, str]] = []

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body))
            return b"{}"

        with self.assertRaisesRegex(InvalidJsonBody, "valid JSON"):
            dispatch_datastore_request(
                "POST",
                "/mix/chan/0/matrix/fader",
                '{"value":NaN}',
                "application/json",
                True,
                lambda path, client=None: b"{}",
                post,
                host="127.0.0.1:1280",
                write_token=WRITE_TOKEN,
                request_token=WRITE_TOKEN,
            )
        self.assertEqual(calls, [])

    def test_oversized_write_frame_is_rejected_before_usb_call(self) -> None:
        calls: list[tuple[str, str]] = []
        logs: list[tuple[str, str, str]] = []
        max_body = max_post_json_body_bytes("/datastore/host/os")
        body = '{"value":"' + ("x" * (max_body - 11)) + '"}'

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body))
            return b"{}"

        with self.assertRaises(ProtocolFrameTooLarge):
            dispatch_datastore_request(
                "POST",
                "/host/os",
                body,
                "application/json",
                True,
                lambda path, client=None: b"{}",
                post,
                log_write=lambda method, path, body: logs.append((method, path, body)),
                host="127.0.0.1:1280",
                write_token=WRITE_TOKEN,
                request_token=WRITE_TOKEN,
            )
        self.assertEqual(calls, [])
        self.assertEqual(logs, [])

    def test_non_object_write_json_is_rejected_before_usb_call(self) -> None:
        calls: list[tuple[str, str]] = []

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body))
            return b"{}"

        for body in ("[]", "5", "true", '"x"'):
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(InvalidJsonBody, "JSON object"),
            ):
                dispatch_datastore_request(
                    "POST",
                    "/host/os",
                    body,
                    "application/json",
                    True,
                    lambda path, client=None: b"{}",
                    post,
                    host="127.0.0.1:1280",
                    write_token=WRITE_TOKEN,
                    request_token=WRITE_TOKEN,
                )
        self.assertEqual(calls, [])

    def test_read_only_write_is_rejected_before_usb_call(self) -> None:
        calls: list[tuple[str, str]] = []

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body))
            return b"{}"

        with self.assertRaisesRegex(DatastorePermissionError, "read-only"):
            dispatch_datastore_request(
                "POST",
                "/uid",
                '{"value":"changed"}',
                "application/json",
                True,
                lambda path, client=None: b"{}",
                post,
                host="127.0.0.1:1280",
                write_token=WRITE_TOKEN,
                request_token=WRITE_TOKEN,
            )
        self.assertEqual(calls, [])

    def test_invalid_known_write_value_is_rejected_before_usb_call(self) -> None:
        calls: list[tuple[str, str]] = []

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body))
            return b"{}"

        with self.assertRaisesRegex(DatastoreValidationError, "<= 4"):
            dispatch_datastore_request(
                "POST",
                "/mix/chan/0/matrix/fader",
                '{"value":5}',
                "application/json",
                True,
                lambda path, client=None: b"{}",
                post,
                host="127.0.0.1:1280",
                write_token=WRITE_TOKEN,
                request_token=WRITE_TOKEN,
            )
        self.assertEqual(calls, [])

    def test_unknown_path_is_rejected_by_default(self) -> None:
        calls: list[tuple[str, str]] = []

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body))
            return b"{}"

        with self.assertRaisesRegex(DatastoreValidationError, "known writable schema"):
            dispatch_datastore_request(
                "POST",
                "/future/path",
                '{"value":{"new":true}}',
                "application/json",
                True,
                lambda path, client=None: b"{}",
                post,
                host="127.0.0.1:1280",
                write_token=WRITE_TOKEN,
                request_token=WRITE_TOKEN,
            )
        self.assertEqual(calls, [])

    def test_unknown_path_is_forwarded_with_explicit_opt_in(self) -> None:
        result, calls = self.dispatch(
            "POST",
            "/future/path",
            body='{"value":{"new":true}}',
            content_type="application/json",
            allow_writes=True,
            allow_unknown_writes=True,
        )
        self.assertEqual(result.response, b'{"ok":true}')
        self.assertEqual(calls, [("POST", "/datastore/future/path", '{"value":{"new":true}}')])

    def test_no_validate_bypasses_read_only_and_value_checks(self) -> None:
        calls: list[tuple[str, str]] = []

        def post(path: str, body: str, client: str | None = None) -> bytes:
            calls.append((path, body))
            return b'{"ok":true}'

        result = dispatch_datastore_request(
            "POST",
            "/uid",
            '{"value":"changed"}',
            "application/json",
            True,
            lambda path, client=None: b"{}",
            post,
            host="127.0.0.1:1280",
            write_token=WRITE_TOKEN,
            request_token=WRITE_TOKEN,
            validate_writes=False,
        )
        self.assertEqual(result.response, b'{"ok":true}')
        self.assertEqual(calls, [("/datastore/uid", '{"value":"changed"}')])

    def test_serve_defaults_are_read_only_localhost(self) -> None:
        args = build_parser().parse_args(["serve"])
        self.assertEqual(args.listen, "127.0.0.1")
        self.assertFalse(args.allow_writes)

    def test_response_content_type_uses_octet_stream_for_concatenated_json(self) -> None:
        self.assertEqual(response_content_type(b'{"first":true}{"second":true}'), "application/octet-stream")

    def test_response_content_type_uses_json_for_single_json_container(self) -> None:
        self.assertEqual(response_content_type(b'  {"value":"ok"}\n'), "application/json")
        self.assertEqual(response_content_type(b'[{"value":"ok"}]'), "application/json")

    def test_response_content_type_ignores_json_braces_inside_strings(self) -> None:
        self.assertEqual(response_content_type(b'{"value":"}{ still text"}'), "application/json")

    def test_server_close_does_not_wait_on_worker_threads(self) -> None:
        self.assertTrue(MotuProxyServer.daemon_threads)
        self.assertFalse(MotuProxyServer.block_on_close)

    def test_serve_runs_shutdown_callback_before_server_close(self) -> None:
        calls = []

        class Server:
            server_address = ("127.0.0.1", 0)
            allow_writes = False
            write_token = None
            write_token_file = None
            allow_remote_writes = False
            validate_writes = True

            def serve_forever(self):
                calls.append("serve")
                raise KeyboardInterrupt

            def server_close(self):
                calls.append("close")

        result = serve(Server(), before_close=lambda: calls.append("before_close"))

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["serve", "before_close", "close"])

    def test_serve_redacts_token_when_token_file_is_available(self) -> None:
        class Server:
            server_address = ("127.0.0.1", 0)
            allow_writes = True
            debug = False
            write_token = "secret-token"
            write_token_file = "/run/motu-proxy/write-token"
            allow_remote_writes = False
            validate_writes = True
            allow_unknown_writes = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        stderr = StringIO()
        with redirect_stderr(stderr):
            result = serve(Server())

        self.assertEqual(result, 0)
        output = stderr.getvalue()
        self.assertIn("stored in token file", output)
        self.assertNotIn("secret-token", output)

    def test_serve_prints_token_in_debug_mode(self) -> None:
        class Server:
            server_address = ("127.0.0.1", 0)
            allow_writes = True
            debug = True
            write_token = "secret-token"
            write_token_file = "/run/motu-proxy/write-token"
            allow_remote_writes = False
            validate_writes = True
            allow_unknown_writes = False

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                pass

        stderr = StringIO()
        with redirect_stderr(stderr):
            result = serve(Server())

        self.assertEqual(result, 0)
        self.assertIn("write token: secret-token", stderr.getvalue())


class HttpHandlerTests(TestCase):
    def make_handler(
        self,
        headers=None,
        body: bytes = b"",
        dispatcher=None,
        max_write_body_bytes: int = 64 * 1024,
        write_body_read_timeout_s: float | None = 5.0,
        connection=None,
    ):
        handler = object.__new__(MotuProxyHandler)
        handler.headers = headers or {}
        handler.rfile = body if hasattr(body, "read") else BytesIO(body)
        handler.wfile = BytesIO()
        handler.path = "/datastore"
        handler.connection = connection
        handler.close_connection = False
        handler.server = SimpleNamespace(
            debug=False,
            dispatcher=dispatcher,
            max_write_body_bytes=max_write_body_bytes,
            write_body_read_timeout_s=write_body_read_timeout_s,
        )
        handler.statuses = []
        handler.sent_headers = []
        handler.send_response = lambda status: handler.statuses.append(status)
        handler.send_header = lambda key, value: handler.sent_headers.append((key, value))
        handler.end_headers = lambda: None
        return handler

    def handle_socket_requests(self, request: bytes, dispatcher=None) -> bytes:
        if dispatcher is None:

            class Dispatcher:
                def dispatch(self, *args, **kwargs):
                    return DispatchResult(b'{"value":"ok"}', "/datastore")

            dispatcher = Dispatcher()

        request_socket = BufferedSocket(request)
        server = SimpleNamespace(
            debug=False,
            dispatcher=dispatcher,
            max_write_body_bytes=64 * 1024,
            write_body_read_timeout_s=5.0,
        )
        MotuProxyHandler(request_socket, ("127.0.0.1", 0), server)
        return request_socket.response.getvalue()

    def parse_socket_responses(self, data: bytes) -> list[SimpleNamespace]:
        responses = []
        offset = 0
        while offset < len(data):
            header_end = data.find(b"\r\n\r\n", offset)
            self.assertNotEqual(header_end, -1, "response headers were not terminated")

            header_block = data[offset:header_end]
            header_lines = header_block.decode("iso-8859-1").split("\r\n")
            headers = {}
            for line in header_lines[1:]:
                key, _, value = line.partition(":")
                headers[key.lower()] = value.strip()

            content_length = int(headers.get("content-length", "0"))
            body_start = header_end + len(b"\r\n\r\n")
            body_end = body_start + content_length
            self.assertLessEqual(body_end, len(data), "response body was incomplete")
            responses.append(
                SimpleNamespace(
                    status_line=header_lines[0],
                    headers=headers,
                    body=data[body_start:body_end],
                )
            )
            offset = body_end

        return responses

    def test_handler_protocol_version_is_http_11(self) -> None:
        self.assertEqual(MotuProxyHandler.protocol_version, "HTTP/1.1")

    def test_handler_length_frames_success_without_unconditional_close(self) -> None:
        body = b'{"value":"ok"}'

        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                return DispatchResult(body, "/datastore/uid")

        handler = self.make_handler(dispatcher=Dispatcher())
        handler.handle_datastore_request("GET")
        self.assertEqual(handler.statuses, [200])
        self.assertIn(("Content-Length", str(len(body))), handler.sent_headers)
        self.assertNotIn(("Connection", "close"), handler.sent_headers)
        self.assertEqual(handler.wfile.getvalue(), body)

    def test_http11_success_responses_can_reuse_socket(self) -> None:
        calls: list[str] = []
        body = b'{"value":"ok"}'

        class Dispatcher:
            def dispatch(self, method, request_path, *args, **kwargs):
                result = dispatch_datastore_request(
                    method,
                    request_path,
                    "",
                    "",
                    False,
                    self.get,
                    lambda path, body, client=None: b"{}",
                )
                return result

            def get(self, path: str, client: str | None = None) -> bytes:
                calls.append(path)
                return body

        request = (
            "GET /datastore/uid HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "\r\n"
        ).encode("ascii")

        responses = self.parse_socket_responses(
            self.handle_socket_requests(request + request, Dispatcher())
        )

        self.assertEqual(len(responses), 2)
        first, second = responses
        self.assertEqual(first.status_line, "HTTP/1.1 200 OK")
        self.assertEqual(second.status_line, "HTTP/1.1 200 OK")
        self.assertEqual(first.headers["content-length"], str(len(body)))
        self.assertEqual(second.headers["content-length"], str(len(body)))
        self.assertNotEqual(first.headers.get("connection", "").lower(), "close")
        self.assertNotEqual(second.headers.get("connection", "").lower(), "close")
        self.assertEqual(first.body, body)
        self.assertEqual(second.body, body)
        self.assertEqual(calls, ["/datastore/uid", "/datastore/uid"])

    def test_http11_connection_close_request_closes_socket_after_success(self) -> None:
        body = b'{"value":"ok"}'

        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                return DispatchResult(body, "/datastore/uid")

        request = (
            "GET /datastore/uid HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        followup = (
            "GET /datastore/uid HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "\r\n"
        ).encode("ascii")

        responses = self.parse_socket_responses(
            self.handle_socket_requests(request + followup, Dispatcher())
        )

        self.assertEqual(len(responses), 1)
        response = responses[0]
        self.assertEqual(response.status_line, "HTTP/1.1 200 OK")
        self.assertEqual(response.headers["content-length"], str(len(body)))
        self.assertEqual(response.headers.get("connection", "").lower(), "close")
        self.assertEqual(response.body, body)

    def test_http11_get_with_body_closes_socket_after_success(self) -> None:
        calls: list[str] = []
        body = b'{"value":"ok"}'

        class Dispatcher:
            def dispatch(self, method, request_path, *args, **kwargs):
                calls.append(request_path)
                return DispatchResult(body, "/datastore/uid")

        request = (
            "GET /datastore/uid HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Length: 5\r\n"
            "\r\n"
            "hello"
        ).encode("ascii")
        followup = (
            "GET /datastore/uid HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "\r\n"
        ).encode("ascii")

        responses = self.parse_socket_responses(
            self.handle_socket_requests(request + followup, Dispatcher())
        )

        self.assertEqual(len(responses), 1)
        response = responses[0]
        self.assertEqual(response.status_line, "HTTP/1.1 200 OK")
        self.assertEqual(response.headers["content-length"], str(len(body)))
        self.assertEqual(response.headers.get("connection", "").lower(), "close")
        self.assertEqual(response.body, body)
        self.assertEqual(calls, ["/datastore/uid"])

    def test_http11_unknown_method_error_is_length_framed_and_closes(self) -> None:
        request = (
            "BREW /datastore HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "\r\n"
        ).encode("ascii")
        followup = (
            "GET /datastore HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "\r\n"
        ).encode("ascii")

        responses = self.parse_socket_responses(
            self.handle_socket_requests(request + followup)
        )

        self.assertEqual(len(responses), 1)
        response = responses[0]
        self.assertTrue(response.status_line.startswith("HTTP/1.1 501 "))
        self.assertIn("content-length", response.headers)
        self.assertEqual(int(response.headers["content-length"]), len(response.body))
        self.assertEqual(response.headers.get("connection", "").lower(), "close")
        self.assertIn(b"Unsupported method", response.body)

    def test_handler_does_not_truncate_concatenated_json_response(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                return DispatchResult(b'{"first":true}{"second":true}', "/datastore")

        handler = self.make_handler(dispatcher=Dispatcher())
        handler.handle_datastore_request("GET")
        self.assertEqual(handler.statuses, [200])
        self.assertIn(("Content-Type", "application/octet-stream"), handler.sent_headers)
        self.assertIn(("Cache-Control", "no-cache"), handler.sent_headers)
        self.assertEqual(handler.wfile.getvalue(), b'{"first":true}{"second":true}')

    def test_handler_emits_etag_for_get_response(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                return DispatchResult(b'{"value":"ok"}', "/datastore/uid", etag="5678")

        handler = self.make_handler(dispatcher=Dispatcher())
        handler.handle_datastore_request("GET")
        self.assertEqual(handler.statuses, [200])
        self.assertIn(("Cache-Control", "no-cache"), handler.sent_headers)
        self.assertIn(("ETag", "5678"), handler.sent_headers)

    def test_handler_sends_304_without_body(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                return DispatchResult(b'{"unexpected":true}', "/datastore", etag="5678", status=304)

        handler = self.make_handler(dispatcher=Dispatcher())
        handler.handle_datastore_request("GET")
        self.assertEqual(handler.statuses, [304])
        self.assertIn(("Cache-Control", "no-cache"), handler.sent_headers)
        self.assertIn(("ETag", "5678"), handler.sent_headers)
        self.assertFalse(any(key == "Content-Length" for key, _ in handler.sent_headers))
        self.assertFalse(any(key == "Content-Type" for key, _ in handler.sent_headers))
        self.assertEqual(handler.wfile.getvalue(), b"")

    def test_dispatch_accepts_datastore_payload_metadata(self) -> None:
        result = dispatch_datastore_request(
            "GET",
            "/uid",
            "",
            "",
            False,
            lambda path, client=None: DatastorePayload(b'{"value":"ok"}', etag="5678"),
            lambda path, body, client=None: b"{}",
        )
        self.assertEqual(result.response, b'{"value":"ok"}')
        self.assertEqual(result.etag, "5678")

    def test_handler_returns_json_403_when_token_is_missing(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                raise WriteTokenRequired("valid write token required")

        handler = self.make_handler({"Content-Length": "17"}, b'{"value":"linux"}', Dispatcher())
        handler.handle_datastore_request("POST")
        body = handler.wfile.getvalue()
        self.assertEqual(handler.statuses, [403])
        self.assertIn(("Content-Type", "application/json"), handler.sent_headers)
        self.assertIn(("Content-Length", str(len(body))), handler.sent_headers)
        self.assertIn(("Connection", "close"), handler.sent_headers)
        self.assertIn("valid write token required", json.loads(body.decode("utf-8"))["error"])

    def test_handler_rejects_unsafe_writes_before_reading_body(self) -> None:
        def get(path: str, client: str | None = None) -> bytes:
            raise AssertionError("unexpected get")

        def post(path: str, body: str, client: str | None = None) -> bytes:
            raise AssertionError("unexpected post")

        cases = [
            (
                "disabled",
                DatastoreDispatcher(False, get, post, log_write=None),
                {"Content-Length": "17"},
                "writes require --allow-writes",
            ),
            (
                "bad host",
                DatastoreDispatcher(True, get, post, write_token=WRITE_TOKEN, log_write=None),
                {
                    "Content-Length": "17",
                    "Host": "device.local:1280",
                    WRITE_TOKEN_HEADER: WRITE_TOKEN,
                },
                "loopback Host",
            ),
            (
                "bad origin",
                DatastoreDispatcher(True, get, post, write_token=WRITE_TOKEN, log_write=None),
                {
                    "Content-Length": "17",
                    "Host": "127.0.0.1:1280",
                    "Origin": "https://127.0.0.1:1280",
                    WRITE_TOKEN_HEADER: WRITE_TOKEN,
                },
                "cross-origin",
            ),
            (
                "bad token",
                DatastoreDispatcher(True, get, post, write_token=WRITE_TOKEN, log_write=None),
                {"Content-Length": "17", "Host": "127.0.0.1:1280"},
                "valid write token required",
            ),
        ]

        for name, dispatcher, headers, message in cases:
            with self.subTest(name=name):
                body = TrackingBody(b'{"value":"linux"}')
                handler = self.make_handler(headers, body, dispatcher)
                handler.handle_datastore_request("POST")
                self.assertEqual(handler.statuses, [403])
                self.assertEqual(body.reads, 0)
                self.assertIn(("Connection", "close"), handler.sent_headers)
                self.assertIn(message, json.loads(handler.wfile.getvalue().decode("utf-8"))["error"])

    def test_handler_returns_json_403_for_read_only_path(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                raise DatastorePermissionError("/datastore/uid is read-only")

        handler = self.make_handler({"Content-Length": "17"}, b'{"value":"linux"}', Dispatcher())
        handler.handle_datastore_request("POST")
        self.assertEqual(handler.statuses, [403])
        self.assertIn("read-only", json.loads(handler.wfile.getvalue().decode("utf-8"))["error"])

    def test_handler_returns_json_422_for_value_validation(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                raise DatastoreValidationError("/datastore/mix/chan/0/matrix/fader must be <= 4")

        handler = self.make_handler({"Content-Length": "17"}, b'{"value":"linux"}', Dispatcher())
        handler.handle_datastore_request("POST")
        self.assertEqual(handler.statuses, [422])
        self.assertIn("<= 4", json.loads(handler.wfile.getvalue().decode("utf-8"))["error"])

    def test_handler_returns_504_for_missing_datastore_response(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                raise DatastoreNoResponse("no datastore response")

        handler = self.make_handler(dispatcher=Dispatcher())
        handler.handle_datastore_request("GET")
        self.assertEqual(handler.statuses, [504])
        self.assertEqual(
            json.loads(handler.wfile.getvalue().decode("utf-8"))["error"],
            "MOTU USB datastore did not respond",
        )

    def test_handler_returns_503_for_temporary_device_unavailable(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                raise DatastoreDeviceUnavailable("MOTU USB datastore is temporarily unavailable")

        handler = self.make_handler(dispatcher=Dispatcher())
        handler.handle_datastore_request("GET")
        self.assertEqual(handler.statuses, [503])
        self.assertEqual(
            json.loads(handler.wfile.getvalue().decode("utf-8"))["error"],
            "MOTU USB device is not available",
        )

    def test_handler_does_not_emit_second_status_after_response_write_failure(self) -> None:
        class BrokenWriter(BytesIO):
            def write(self, data):
                raise BrokenPipeError("client closed")

        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                return DispatchResult(b'{"value":"ok"}', "/datastore/uid")

        handler = self.make_handler(dispatcher=Dispatcher())
        handler.wfile = BrokenWriter()
        handler.handle_datastore_request("GET")

        self.assertEqual(handler.statuses, [200])
        self.assertTrue(handler.close_connection)

    def test_handler_returns_413_for_oversized_protocol_frame(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                raise ProtocolFrameTooLarge("too large")

        handler = self.make_handler({"Content-Length": "17"}, b'{"value":"linux"}', Dispatcher())
        handler.handle_datastore_request("POST")
        self.assertEqual(handler.statuses, [413])
        self.assertEqual(json.loads(handler.wfile.getvalue().decode("utf-8"))["error"], "too large")

    def test_handler_accepts_bearer_token_for_local_scripts(self) -> None:
        handler = self.make_handler({"Authorization": f"Bearer {WRITE_TOKEN}"})
        self.assertEqual(handler.read_write_token(), WRITE_TOKEN)

    def test_handler_rejects_oversized_write_body_before_dispatch(self) -> None:
        handler = self.make_handler({"Content-Length": "5", WRITE_TOKEN_HEADER: WRITE_TOKEN}, b"12345", max_write_body_bytes=4)
        with self.assertRaises(RequestBodyTooLarge):
            handler.read_raw_body()

    def test_handler_rejects_chunked_write_body(self) -> None:
        handler = self.make_handler({"Transfer-Encoding": "chunked", WRITE_TOKEN_HEADER: WRITE_TOKEN}, b"")
        with self.assertRaisesRegex(BadRequest, "Transfer-Encoding"):
            handler.read_raw_body()

    def test_handler_body_read_failures_emit_connection_close(self) -> None:
        class Dispatcher:
            def validate_write_headers(self, *args, **kwargs) -> None:
                return None

            def dispatch(self, *args, **kwargs):
                raise AssertionError("unexpected dispatch")

        cases = [
            ("unsupported transfer encoding", {"Transfer-Encoding": "chunked"}, b"", 400, None),
            ("short body", {"Content-Length": "17"}, b"{}", 400, None),
            ("timeout", {"Content-Length": "17"}, TimeoutBody(), 408, RecordingConnection()),
        ]

        for name, headers, body, status, connection in cases:
            with self.subTest(name=name):
                handler = self.make_handler(headers, body, Dispatcher(), connection=connection)
                handler.handle_datastore_request("POST")
                response_body = handler.wfile.getvalue()
                self.assertEqual(handler.statuses, [status])
                self.assertTrue(handler.close_connection)
                self.assertIn(("Connection", "close"), handler.sent_headers)
                self.assertIn(("Content-Length", str(len(response_body))), handler.sent_headers)

    def test_handler_times_out_stalled_write_body_and_restores_socket_timeout(self) -> None:
        connection = RecordingConnection(timeout=None)
        handler = self.make_handler(
            {"Content-Length": "17", WRITE_TOKEN_HEADER: WRITE_TOKEN},
            TimeoutBody(),
            write_body_read_timeout_s=1.25,
            connection=connection,
        )
        with self.assertRaisesRegex(RequestBodyTimeout, "timed out"):
            handler.read_raw_body()
        self.assertTrue(handler.close_connection)
        self.assertEqual(connection.timeouts, [1.25, None])

    def test_handler_rejects_short_write_body(self) -> None:
        handler = self.make_handler({"Content-Length": "17", WRITE_TOKEN_HEADER: WRITE_TOKEN}, b"{}")
        with self.assertRaisesRegex(BadRequest, "Content-Length"):
            handler.read_raw_body()
        self.assertTrue(handler.close_connection)

    def test_handler_rejects_invalid_utf8_write_body(self) -> None:
        handler = self.make_handler({"Content-Length": "1"}, b"\xff")
        with self.assertRaisesRegex(BadRequest, "valid UTF-8"):
            handler.read_raw_body()
