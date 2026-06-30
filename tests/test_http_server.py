from unittest import TestCase

from motu_proxy.cli import build_parser
from motu_proxy.http_server import CrossOriginWrite, DatastoreDispatcher, WritesDisabled, dispatch_datastore_request


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
        host: str | None = None,
    ):
        calls: list[tuple[str, str, str | None]] = []

        def get(path: str) -> bytes:
            calls.append(("GET", path, None))
            return b'{"value":"0001f2fffe00c719"}'

        def post(path: str, body: str) -> bytes:
            calls.append(("POST", path, body))
            return b'{"ok":true}'

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
            )
        self.assertEqual(calls, [])

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

    def test_write_attempts_are_logged_when_disabled(self) -> None:
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
        self.assertEqual(logs, [("PATCH", "/datastore/host/os", '{"value":"linux"}')])

    def test_write_attempts_are_logged_when_enabled(self) -> None:
        logs: list[tuple[str, str, str]] = []
        dispatcher = DatastoreDispatcher(
            True,
            lambda path: b"{}",
            lambda path, body: b"{}",
            log_write=lambda method, path, body: logs.append((method, path, body)),
            lock=RecordingLock(),
        )
        dispatcher.dispatch("POST", "/host/os", 'json={"value":"linux"}', "application/x-www-form-urlencoded")
        self.assertEqual(logs, [("POST", "/datastore/host/os", '{"value":"linux"}')])

    def test_serve_defaults_are_read_only_localhost(self) -> None:
        args = build_parser().parse_args(["serve"])
        self.assertEqual(args.listen, "127.0.0.1")
        self.assertFalse(args.allow_writes)
