import json
from io import BytesIO
from types import SimpleNamespace
from unittest import TestCase

from motu_proxy.cli import build_parser
from motu_proxy.datastore import DatastoreNoResponse
from motu_proxy.http_server import (
    WRITE_TOKEN_HEADER,
    BadRequest,
    CrossOriginWrite,
    DatastoreDispatcher,
    DispatchResult,
    HostNotAllowed,
    MotuProxyHandler,
    RequestBodyTooLarge,
    WriteTokenRequired,
    WritesDisabled,
    dispatch_datastore_request,
    response_content_type,
)
from motu_proxy.json_body import InvalidJsonBody


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
    ):
        calls: list[tuple[str, str, str | None]] = []

        def get(path: str) -> bytes:
            calls.append(("GET", path, None))
            return b'{"value":"0001f2fffe00c719"}'

        def post(path: str, body: str) -> bytes:
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
        )
        return result, calls

    def test_get_returns_json_and_normalizes_path(self) -> None:
        result, calls = self.dispatch("GET", "/uid")
        self.assertEqual(result.response, b'{"value":"0001f2fffe00c719"}')
        self.assertEqual(result.path, "/datastore/uid")
        self.assertEqual(calls, [("GET", "/datastore/uid", None)])

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

    def test_patch_is_post_alias_not_partial_update(self) -> None:
        result, calls = self.dispatch("PATCH", "/host/os", body='{"value":"linux"}', allow_writes=True)
        self.assertEqual(result.response, b'{"ok":true}')
        self.assertEqual(calls, [("POST", "/datastore/host/os", '{"value":"linux"}')])

    def test_cross_origin_write_is_rejected_when_writes_are_enabled(self) -> None:
        calls: list[tuple[str, str, str | None]] = []

        def get(path: str) -> bytes:
            calls.append(("GET", path, None))
            return b"{}"

        def post(path: str, body: str) -> bytes:
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

        def get(path: str) -> bytes:
            calls.append(("GET", path))
            return b"{}"

        def post(path: str, body: str) -> bytes:
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
            lambda path: b"{}",
            lambda path, body: b"{}",
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
            lambda path: b"{}",
            lambda path, body: b"{}",
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
            lambda path: b"{}",
            lambda path, body: b"{}",
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

    def test_invalid_write_json_is_rejected_before_usb_call(self) -> None:
        calls: list[tuple[str, str]] = []

        def post(path: str, body: str) -> bytes:
            calls.append((path, body))
            return b"{}"

        with self.assertRaises(InvalidJsonBody):
            dispatch_datastore_request(
                "POST",
                "/host/os",
                '{"value":',
                "application/json",
                True,
                lambda path: b"{}",
                post,
                host="127.0.0.1:1280",
                write_token=WRITE_TOKEN,
                request_token=WRITE_TOKEN,
            )
        self.assertEqual(calls, [])

    def test_serve_defaults_are_read_only_localhost(self) -> None:
        args = build_parser().parse_args(["serve"])
        self.assertEqual(args.listen, "127.0.0.1")
        self.assertFalse(args.allow_writes)

    def test_response_content_type_uses_octet_stream_for_concatenated_json(self) -> None:
        self.assertEqual(response_content_type(b'{"first":true}{"second":true}'), "application/octet-stream")


class HttpHandlerTests(TestCase):
    def make_handler(self, headers=None, body: bytes = b"", dispatcher=None, max_write_body_bytes: int = 64 * 1024):
        handler = object.__new__(MotuProxyHandler)
        handler.headers = headers or {}
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.path = "/datastore"
        handler.server = SimpleNamespace(
            debug=False,
            dispatcher=dispatcher,
            max_write_body_bytes=max_write_body_bytes,
        )
        handler.statuses = []
        handler.sent_headers = []
        handler.send_response = lambda status: handler.statuses.append(status)
        handler.send_header = lambda key, value: handler.sent_headers.append((key, value))
        handler.end_headers = lambda: None
        return handler

    def test_handler_does_not_truncate_concatenated_json_response(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                return DispatchResult(b'{"first":true}{"second":true}', "/datastore")

        handler = self.make_handler(dispatcher=Dispatcher())
        handler.handle_datastore_request("GET")
        self.assertEqual(handler.statuses, [200])
        self.assertIn(("Content-Type", "application/octet-stream"), handler.sent_headers)
        self.assertEqual(handler.wfile.getvalue(), b'{"first":true}{"second":true}')

    def test_handler_returns_json_403_when_token_is_missing(self) -> None:
        class Dispatcher:
            def dispatch(self, *args, **kwargs):
                raise WriteTokenRequired("valid write token required")

        handler = self.make_handler({"Content-Length": "17"}, b'{"value":"linux"}', Dispatcher())
        handler.handle_datastore_request("POST")
        self.assertEqual(handler.statuses, [403])
        self.assertIn(("Content-Type", "application/json"), handler.sent_headers)
        self.assertIn("valid write token required", json.loads(handler.wfile.getvalue().decode("utf-8"))["error"])

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

    def test_handler_accepts_bearer_token_for_local_scripts(self) -> None:
        handler = self.make_handler({"Authorization": f"Bearer {WRITE_TOKEN}"})
        self.assertEqual(handler.read_write_token(), WRITE_TOKEN)

    def test_handler_rejects_oversized_write_body_before_dispatch(self) -> None:
        handler = self.make_handler({"Content-Length": "5", WRITE_TOKEN_HEADER: WRITE_TOKEN}, b"12345", max_write_body_bytes=4)
        with self.assertRaises(RequestBodyTooLarge):
            handler.read_raw_body()

    def test_handler_rejects_invalid_utf8_write_body(self) -> None:
        handler = self.make_handler({"Content-Length": "1"}, b"\xff")
        with self.assertRaisesRegex(BadRequest, "valid UTF-8"):
            handler.read_raw_body()
