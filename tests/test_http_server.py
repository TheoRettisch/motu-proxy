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
    MotuProxyServer,
    RequestBodyTooLarge,
    WriteTokenRequired,
    WritesDisabled,
    dispatch_datastore_request,
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
            with self.subTest(body=body):
                with self.assertRaisesRegex(InvalidJsonBody, "JSON object"):
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
                return DispatchResult(b"", "/datastore", etag="5678", status=304)

        handler = self.make_handler(dispatcher=Dispatcher())
        handler.handle_datastore_request("GET")
        self.assertEqual(handler.statuses, [304])
        self.assertIn(("Cache-Control", "no-cache"), handler.sent_headers)
        self.assertIn(("ETag", "5678"), handler.sent_headers)
        self.assertIn(("Content-Length", "0"), handler.sent_headers)
        self.assertNotIn(("Content-Type", "application/json"), handler.sent_headers)
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
        self.assertEqual(handler.statuses, [403])
        self.assertIn(("Content-Type", "application/json"), handler.sent_headers)
        self.assertIn("valid write token required", json.loads(handler.wfile.getvalue().decode("utf-8"))["error"])

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

    def test_handler_rejects_invalid_utf8_write_body(self) -> None:
        handler = self.make_handler({"Content-Length": "1"}, b"\xff")
        with self.assertRaisesRegex(BadRequest, "valid UTF-8"):
            handler.read_raw_body()
