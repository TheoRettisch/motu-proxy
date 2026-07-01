import json
import os
import stat
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import patch

from motu_proxy.cli import (
    DEFAULT_WRITE_TOKEN_FILE,
    build_parser,
    config_from_args,
    prepare_write_token,
    remove_write_token_file,
    validate_serve_write_safety,
    write_token_file,
)
from motu_proxy.datastore import DatastoreNoResponse, ResponseStats
from motu_proxy.device import NoDeviceFound
from motu_proxy.http_server import DatastoreDispatcher


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="ascii")


def add_device(root: Path, name: str, serial: str, bus: int, dev: int) -> None:
    device = root / name
    device.mkdir(parents=True)
    write(device / "idVendor", "07fd")
    write(device / "idProduct", "0005")
    write(device / "serial", serial)
    write(device / "product", "624")
    write(device / "busnum", str(bus))
    write(device / "devnum", str(dev))

    control = root / f"{name}:1.3"
    control.mkdir()
    write(control / "bInterfaceClass", "ff")
    write(control / "bInterfaceNumber", "03")
    write(control / "ep_03" / "type", "Bulk")
    write(control / "ep_03" / "wMaxPacketSize", "0200")
    write(control / "ep_83" / "type", "Bulk")
    write(control / "ep_83" / "wMaxPacketSize", "0200")


class FakeOpenDatastore:
    def __init__(self, datastore) -> None:
        self.datastore = datastore

    def __enter__(self):
        return self.datastore

    def __exit__(self, exc_type, exc, tb) -> None:
        pass


class FakeSmokeDatastore:
    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []
        self.last_response_stats: ResponseStats | None = None

    def get(self, path: str) -> bytes:
        self.calls.append(path)
        self.last_response_stats = None
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        self.last_response_stats = ResponseStats(
            timeout_ms=1200,
            elapsed_ms=1.0,
            reads=2,
            accepted_frames=1,
            ignored_packets=0,
            ack_packets=1,
            response_bytes=len(response),
        )
        return response


def info_responses() -> dict[str, bytes | Exception]:
    return {
        "/apiversion": b'{"value":"1.0.0"}',
        "/datastore/ext/caps/avb": b'{"value":"2.0.0"}',
        "/datastore/ext/caps/router": b'{"value":"3.0.0"}',
        "/datastore/ext/caps/mixer": RuntimeError("404 not found"),
        "/datastore/uid": b'{"value":"0001f2fffe00c719"}',
        "/datastore/model_name": b'{"value":"624"}',
        "/datastore/firmware_version": b'{"value":"1.4.1\\n06/27/25"}',
        "/datastore/serial_number": DatastoreNoResponse("no datastore response after 1200 ms"),
    }


class FakeServeDatastore:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.calls: list[tuple[str, str | None]] = []

    def get(
        self,
        path: str,
        etag: str = "0",
        client: str | None = None,
        timeout_ms: int = 1200,
        **kwargs,
    ) -> bytes:
        self.calls.append((path, client))
        return self.response

    def post(
        self,
        path: str,
        json_body: str,
        client: str | None = None,
        timeout_ms: int = 1200,
        **kwargs,
    ) -> bytes:
        raise AssertionError("unexpected post")


class FakePostDatastore:
    def __init__(self, response: bytes = b'{"ok":true}') -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def post(self, path: str, json_body: str) -> bytes:
        self.calls.append((path, json_body))
        return self.response


class FakeMetersDatastore:
    def __init__(self, response: bytes = b'{"mix/level/1":[0]}') -> None:
        self.response = response
        self.calls: list[tuple[str, str, tuple[tuple[str, str], ...] | None]] = []

    def get(
        self,
        path: str,
        etag: str = "0",
        client: str | None = None,
        timeout_ms: int = 1200,
        query_fields: tuple[tuple[str, str], ...] | None = None,
    ) -> bytes:
        self.calls.append((path, etag, query_fields))
        return self.response


class CliUsbOverrideTests(TestCase):
    def test_partial_manual_usb_override_is_rejected(self) -> None:
        args = build_parser().parse_args(["get", "--interface", "4"])
        with self.assertRaisesRegex(RuntimeError, "--interface, --ep-out, and --ep-in"):
            config_from_args(args)

    def test_complete_manual_usb_override_is_accepted(self) -> None:
        args = build_parser().parse_args(["get", "--interface", "4", "--ep-out", "0x04", "--ep-in", "0x84"])
        config = config_from_args(args)
        self.assertEqual(config.interface, 4)
        self.assertEqual(config.ep_out, 0x04)
        self.assertEqual(config.ep_in, 0x84)

    def test_invalid_seq_start_is_rejected_before_usb_open(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            build_parser().parse_args(["get", "--seq-start", "0x40"])
        self.assertIn("0x20..0x3f", stderr.getvalue())

    def test_invalid_timeout_is_rejected_by_argparse(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            build_parser().parse_args(["get", "--timeout-ms", "-1"])
        self.assertIn("value must be > 0", stderr.getvalue())

    def test_invalid_message_sequence_is_rejected_by_argparse(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            build_parser().parse_args(["get", "--message-seq", "0x100000000"])
        self.assertIn("message sequence", stderr.getvalue())

    def test_invalid_endpoint_override_is_rejected_by_argparse(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            build_parser().parse_args(["get", "--interface", "3", "--ep-out", "0x83", "--ep-in", "0x83"])
        self.assertIn("OUT endpoint", stderr.getvalue())


class CliServeSecurityTests(TestCase):
    def test_serve_write_token_file_defaults_to_run_path(self) -> None:
        args = build_parser().parse_args(["serve"])
        self.assertEqual(args.write_token_file, str(DEFAULT_WRITE_TOKEN_FILE))
        self.assertFalse(args.require_write_token)

    def test_serve_requires_write_token_only_when_requested(self) -> None:
        args = build_parser().parse_args(["serve", "--require-write-token"])
        self.assertTrue(args.require_write_token)

    def test_allow_writes_rejects_non_loopback_listen_without_unsafe_flag(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unsafe-allow-remote-writes"):
            validate_serve_write_safety("0.0.0.0", allow_writes=True, unsafe_allow_remote_writes=False)

    def test_allow_writes_accepts_non_loopback_listen_with_unsafe_flag(self) -> None:
        validate_serve_write_safety("0.0.0.0", allow_writes=True, unsafe_allow_remote_writes=True)

    def test_invalid_serve_port_is_rejected_by_argparse(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            build_parser().parse_args(["serve", "--port", "0"])
        self.assertIn("port", stderr.getvalue())

    def test_invalid_max_write_body_size_is_rejected_by_argparse(self) -> None:
        stderr = StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            build_parser().parse_args(["serve", "--max-write-body-bytes", "0"])
        self.assertIn("value must be > 0", stderr.getvalue())

    def test_write_token_file_is_owner_only(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "run" / "motu-proxy" / "write-token"
            write_token_file(path, "secret-token")
            self.assertEqual(path.read_text(encoding="ascii"), "secret-token\n")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    @skipIf(not hasattr(os, "symlink"), "symlinks are not supported on this platform")
    def test_write_token_file_refuses_symlink(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            link = Path(tmp) / "write-token"
            target.write_text("keep\n", encoding="ascii")
            os.symlink(target, link)

            with self.assertRaisesRegex(RuntimeError, "symlink"):
                write_token_file(link, "secret-token")

            self.assertEqual(target.read_text(encoding="ascii"), "keep\n")

    def test_prepare_write_token_writes_generated_token_to_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "write-token"
            token, token_file = prepare_write_token(str(path))
            self.assertEqual(token_file, str(path))
            self.assertEqual(path.read_text(encoding="ascii"), f"{token}\n")

    def test_remove_write_token_file_only_removes_matching_token(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "write-token"
            path.write_text("replacement\n", encoding="ascii")
            remove_write_token_file(str(path), "original")
            self.assertEqual(path.read_text(encoding="ascii"), "replacement\n")

            remove_write_token_file(str(path), "replacement")
            self.assertFalse(path.exists())

    def test_serve_removes_write_token_file_when_usb_is_unavailable(self) -> None:
        class FakeServer:
            def __init__(
                self,
                server_address,
                allow_writes,
                debug,
                run_get,
                run_post,
                write_token=None,
                write_token_file=None,
                allow_remote_writes=False,
                max_write_body_bytes=64 * 1024,
                serialize_dispatch=True,
                validate_writes=True,
                allow_unknown_writes=False,
                status_provider=None,
            ) -> None:
                self.write_token = write_token

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "write-token"
            args = build_parser().parse_args(
                ["serve", "--allow-writes", "--require-write-token", "--write-token-file", str(path)]
            )

            def fake_serve(server, before_close=None) -> int:
                self.assertTrue(path.exists())
                self.assertEqual(path.read_text(encoding="ascii"), f"{server.write_token}\n")
                if before_close is not None:
                    before_close()
                return 0

            with (
                patch(
                    "motu_proxy.cli.open_datastore",
                    side_effect=NoDeviceFound("missing"),
                ),
                patch("motu_proxy.cli.MotuProxyServer", FakeServer),
                patch("motu_proxy.cli.serve", side_effect=fake_serve),
            ):
                result = args.func(args)

            self.assertEqual(result, 0)
            self.assertFalse(path.exists())

    def test_serve_allow_writes_without_token_mode_does_not_write_token_file(self) -> None:
        datastore = FakeServeDatastore(b'{"value":"ok"}')

        class FakeServer:
            def __init__(
                self,
                server_address,
                allow_writes,
                debug,
                run_get,
                run_post,
                write_token=None,
                write_token_file=None,
                allow_remote_writes=False,
                max_write_body_bytes=64 * 1024,
                serialize_dispatch=True,
                validate_writes=True,
                allow_unknown_writes=False,
                status_provider=None,
            ) -> None:
                self.allow_writes = allow_writes
                self.write_token = write_token
                self.write_token_file = write_token_file

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "write-token"
            args = build_parser().parse_args(["serve", "--allow-writes", "--write-token-file", str(path)])

            def fake_serve(server, before_close=None) -> int:
                self.assertTrue(server.allow_writes)
                self.assertIsNone(server.write_token)
                self.assertIsNone(server.write_token_file)
                self.assertFalse(path.exists())
                if before_close is not None:
                    before_close()
                return 0

            with (
                patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
                patch("motu_proxy.cli.MotuProxyServer", FakeServer),
                patch("motu_proxy.cli.serve", side_effect=fake_serve),
            ):
                result = args.func(args)

            self.assertEqual(result, 0)
            self.assertFalse(path.exists())

    def test_serve_removes_write_token_file_after_shutdown(self) -> None:
        datastore = FakeServeDatastore(b'{"value":"ok"}')

        class FakeServer:
            def __init__(
                self,
                server_address,
                allow_writes,
                debug,
                run_get,
                run_post,
                write_token=None,
                write_token_file=None,
                allow_remote_writes=False,
                max_write_body_bytes=64 * 1024,
                serialize_dispatch=True,
                validate_writes=True,
                allow_unknown_writes=False,
                status_provider=None,
            ) -> None:
                self.server_address = server_address
                self.allow_writes = allow_writes
                self.debug = debug
                self.write_token = write_token
                self.write_token_file = write_token_file
                self.allow_remote_writes = allow_remote_writes
                self.validate_writes = validate_writes

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "write-token"
            args = build_parser().parse_args(
                ["serve", "--allow-writes", "--require-write-token", "--write-token-file", str(path)]
            )

            def fake_serve(server, before_close=None) -> int:
                self.assertTrue(path.exists())
                self.assertEqual(path.read_text(encoding="ascii"), f"{server.write_token}\n")
                if before_close is not None:
                    before_close()
                return 0

            with (
                patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
                patch("motu_proxy.cli.MotuProxyServer", FakeServer),
                patch("motu_proxy.cli.serve", side_effect=fake_serve),
            ):
                result = args.func(args)

            self.assertEqual(result, 0)
            self.assertFalse(path.exists())

    def test_serve_removes_write_token_file_after_server_startup_failure(self) -> None:
        datastore = FakeServeDatastore(b'{"value":"ok"}')

        class FailingServer:
            def __init__(self, *args, **kwargs) -> None:
                raise RuntimeError("bind failed")

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "write-token"
            args = build_parser().parse_args(
                ["serve", "--allow-writes", "--require-write-token", "--write-token-file", str(path)]
            )
            with (
                patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
                patch("motu_proxy.cli.MotuProxyServer", FailingServer),
                self.assertRaisesRegex(RuntimeError, "bind failed"),
            ):
                args.func(args)

            self.assertFalse(path.exists())

    def test_serve_get_does_not_truncate_raw_concatenated_json_response(self) -> None:
        datastore = FakeServeDatastore(b'{"first":true}{"second":true}')
        captured = {}

        class FakeServer:
            def __init__(
                self,
                server_address,
                allow_writes,
                debug,
                run_get,
                run_post,
                write_token=None,
                write_token_file=None,
                allow_remote_writes=False,
                max_write_body_bytes=64 * 1024,
                serialize_dispatch=True,
                validate_writes=True,
                allow_unknown_writes=False,
                status_provider=None,
            ) -> None:
                self.server_address = server_address
                self.allow_writes = allow_writes
                self.debug = debug
                self.write_token = write_token
                self.write_token_file = write_token_file
                self.allow_remote_writes = allow_remote_writes
                self.max_write_body_bytes = max_write_body_bytes
                self.dispatcher = DatastoreDispatcher(
                    allow_writes,
                    run_get,
                    run_post,
                    write_token=write_token,
                    allow_remote_writes=allow_remote_writes,
                    serialize_dispatch=serialize_dispatch,
                    validate_writes=validate_writes,
                    allow_unknown_writes=allow_unknown_writes,
                )

            def server_close(self) -> None:
                pass

        def fake_serve(server, before_close=None) -> int:
            try:
                captured["result"] = server.dispatcher.dispatch("GET", "/datastore")
            finally:
                if before_close is not None:
                    before_close()
                server.server_close()
            return 0

        args = build_parser().parse_args(["serve", "--port", "1281"])
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            patch("motu_proxy.cli.MotuProxyServer", FakeServer),
            patch("motu_proxy.cli.serve", side_effect=fake_serve),
        ):
            result = args.func(args)

        self.assertEqual(result, 0)
        self.assertEqual(captured["result"].response, b'{"first":true}{"second":true}')
        self.assertIn(("/datastore", None), datastore.calls)

    def test_serve_no_validate_disables_dispatch_validation(self) -> None:
        datastore = FakeServeDatastore(b'{"value":"ok"}')
        captured = {}

        class FakeServer:
            def __init__(
                self,
                server_address,
                allow_writes,
                debug,
                run_get,
                run_post,
                write_token=None,
                write_token_file=None,
                allow_remote_writes=False,
                max_write_body_bytes=64 * 1024,
                serialize_dispatch=True,
                validate_writes=True,
                allow_unknown_writes=False,
                status_provider=None,
            ) -> None:
                captured["validate_writes"] = validate_writes
                captured["allow_unknown_writes"] = allow_unknown_writes
                self.server_address = server_address
                self.allow_writes = allow_writes
                self.debug = debug
                self.write_token = write_token
                self.write_token_file = write_token_file
                self.allow_remote_writes = allow_remote_writes
                self.max_write_body_bytes = max_write_body_bytes
                self.dispatcher = DatastoreDispatcher(
                    allow_writes,
                    run_get,
                    run_post,
                    write_token=write_token,
                    allow_remote_writes=allow_remote_writes,
                    serialize_dispatch=serialize_dispatch,
                    validate_writes=validate_writes,
                    allow_unknown_writes=allow_unknown_writes,
                )

            def server_close(self) -> None:
                pass

        args = build_parser().parse_args(["serve", "--no-validate", "--port", "1281"])
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            patch("motu_proxy.cli.MotuProxyServer", FakeServer),
            patch("motu_proxy.cli.serve", return_value=0),
        ):
            result = args.func(args)

        self.assertEqual(result, 0)
        self.assertFalse(captured["validate_writes"])
        self.assertFalse(captured["allow_unknown_writes"])

    def test_serve_allow_unknown_writes_passes_dispatch_validation_policy(self) -> None:
        datastore = FakeServeDatastore(b'{"value":"ok"}')
        captured = {}

        class FakeServer:
            def __init__(
                self,
                server_address,
                allow_writes,
                debug,
                run_get,
                run_post,
                write_token=None,
                write_token_file=None,
                allow_remote_writes=False,
                max_write_body_bytes=64 * 1024,
                serialize_dispatch=True,
                validate_writes=True,
                allow_unknown_writes=False,
                status_provider=None,
            ) -> None:
                captured["validate_writes"] = validate_writes
                captured["allow_unknown_writes"] = allow_unknown_writes
                self.server_address = server_address
                self.allow_writes = allow_writes
                self.debug = debug
                self.write_token = write_token
                self.write_token_file = write_token_file
                self.allow_remote_writes = allow_remote_writes
                self.max_write_body_bytes = max_write_body_bytes
                self.dispatcher = DatastoreDispatcher(
                    allow_writes,
                    run_get,
                    run_post,
                    write_token=write_token,
                    allow_remote_writes=allow_remote_writes,
                    serialize_dispatch=serialize_dispatch,
                    validate_writes=validate_writes,
                    allow_unknown_writes=allow_unknown_writes,
                )

            def server_close(self) -> None:
                pass

        args = build_parser().parse_args(["serve", "--allow-unknown-writes", "--port", "1281"])
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            patch("motu_proxy.cli.MotuProxyServer", FakeServer),
            patch("motu_proxy.cli.serve", return_value=0),
        ):
            result = args.func(args)

        self.assertEqual(result, 0)
        self.assertTrue(captured["validate_writes"])
        self.assertTrue(captured["allow_unknown_writes"])


class CliPostValidationTests(TestCase):
    def test_post_validation_failure_happens_before_opening_datastore(self) -> None:
        args = build_parser().parse_args(["post", "/uid", '{"value":"changed"}'])
        with (
            patch(
                "motu_proxy.cli.open_datastore",
                side_effect=AssertionError("opened USB"),
            ),
            self.assertRaisesRegex(RuntimeError, "read-only"),
        ):
            args.func(args)

    def test_post_no_validate_bypasses_schema_validation(self) -> None:
        datastore = FakePostDatastore()
        args = build_parser().parse_args(["post", "--no-validate", "/uid", '{"value":"changed"}', "--compact"])
        stdout = StringIO()
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            redirect_stdout(stdout),
        ):
            result = args.func(args)

        self.assertEqual(result, 0)
        self.assertEqual(datastore.calls, [("/datastore/uid", '{"value":"changed"}')])

    def test_post_unknown_path_rejected_before_opening_datastore(self) -> None:
        args = build_parser().parse_args(["post", "/future/path", '{"value":{"new":true}}'])
        with (
            patch(
                "motu_proxy.cli.open_datastore",
                side_effect=AssertionError("opened USB"),
            ),
            self.assertRaisesRegex(RuntimeError, "known writable schema"),
        ):
            args.func(args)

    def test_post_allow_unknown_writes_forwards_unknown_path(self) -> None:
        datastore = FakePostDatastore()
        args = build_parser().parse_args(
            ["post", "--allow-unknown-writes", "/future/path", '{"value":{"new":true}}', "--compact"]
        )
        stdout = StringIO()
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            redirect_stdout(stdout),
        ):
            result = args.func(args)

        self.assertEqual(result, 0)
        self.assertEqual(datastore.calls, [("/datastore/future/path", '{"value":{"new":true}}')])


class CliMetersTests(TestCase):
    def test_meters_reads_one_group(self) -> None:
        datastore = FakeMetersDatastore()
        args = build_parser().parse_args(["meters", "--compact", "mix/level"])
        stdout = StringIO()
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            redirect_stdout(stdout),
        ):
            result = args.func(args)

        self.assertEqual(result, 0)
        self.assertEqual(datastore.calls, [("/meters", "0", (("meters", "mix/level"),))])
        self.assertIn('"mix/level/1"', stdout.getvalue())


class CliInfoTests(TestCase):
    def test_info_prints_datastore_capabilities(self) -> None:
        datastore = FakeSmokeDatastore(info_responses())
        args = build_parser().parse_args(["info"])
        stdout = StringIO()
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            redirect_stdout(stdout),
        ):
            result = args.func(args)

        self.assertEqual(result, 0)
        output = stdout.getvalue()
        self.assertIn("apiversion: 1.0.0", output)
        self.assertIn("capabilities:", output)
        self.assertIn("avb: 2.0.0", output)
        self.assertIn("router: 3.0.0", output)
        self.assertIn("mixer: not present", output)
        self.assertIn("identity:", output)
        self.assertIn("uid: 0001f2fffe00c719", output)
        self.assertIn("model_name: 624", output)
        self.assertIn("firmware_version: 1.4.1\\n06/27/25", output)
        self.assertIn("serial_number: not present", output)
        self.assertEqual(
            datastore.calls,
            [
                "/apiversion",
                "/datastore/ext/caps/avb",
                "/datastore/ext/caps/router",
                "/datastore/ext/caps/mixer",
                "/datastore/uid",
                "/datastore/model_name",
                "/datastore/firmware_version",
                "/datastore/serial_number",
            ],
        )

    def test_info_json_prints_datastore_capabilities_for_tooling(self) -> None:
        datastore = FakeSmokeDatastore(info_responses())
        args = build_parser().parse_args(["info", "--json"])
        stdout = StringIO()
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            redirect_stdout(stdout),
        ):
            result = args.func(args)

        self.assertEqual(result, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["apiversion"], "1.0.0")
        self.assertEqual(payload["capabilities"]["avb"], {"present": True, "version": "2.0.0"})
        self.assertEqual(payload["capabilities"]["router"], {"present": True, "version": "3.0.0"})
        self.assertEqual(payload["capabilities"]["mixer"], {"present": False, "version": None})
        self.assertEqual(
            payload["identity"],
            {
                "uid": "0001f2fffe00c719",
                "model_name": "624",
                "firmware_version": "1.4.1\n06/27/25",
                "serial_number": None,
            },
        )


class CliSmokeTests(TestCase):
    def test_smoke_aborts_after_failed_read_by_default(self) -> None:
        datastore = FakeSmokeDatastore(
            {
                "/datastore/uid": b'{"value":"uid"}',
                "/datastore/host/mode": RuntimeError("boom"),
                "/datastore/ext/maxUSBToHost": b'{"value":24}',
            }
        )
        args = build_parser().parse_args(
            ["smoke", "--no-body", "--path", "/uid", "--path", "/host/mode", "--path", "/ext/maxUSBToHost"]
        )
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = args.func(args)

        self.assertEqual(result, 1)
        self.assertEqual(datastore.calls, ["/datastore/uid", "/datastore/host/mode"])
        output = stdout.getvalue()
        self.assertIn("# /datastore/uid", output)
        self.assertIn("OK ", output)
        self.assertIn("bytes=15 frames=1 reads=2 ignored=0 ack=1", output)
        self.assertIn("# /datastore/host/mode", output)
        self.assertIn("FAIL ", output)
        self.assertIn("ERROR: boom", stderr.getvalue())

    def test_smoke_can_continue_after_failure_when_requested(self) -> None:
        datastore = FakeSmokeDatastore(
            {
                "/datastore/uid": b'{"value":"uid"}',
                "/datastore/host/mode": RuntimeError("boom"),
                "/datastore/ext/maxUSBToHost": b'{"value":24}',
            }
        )
        args = build_parser().parse_args(
            [
                "smoke",
                "--continue-on-error",
                "--no-body",
                "--path",
                "/uid",
                "--path",
                "/host/mode",
                "--path",
                "/ext/maxUSBToHost",
            ]
        )
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = args.func(args)

        self.assertEqual(result, 1)
        self.assertEqual(
            datastore.calls,
            ["/datastore/uid", "/datastore/host/mode", "/datastore/ext/maxUSBToHost"],
        )
        self.assertIn("# /datastore/ext/maxUSBToHost", stdout.getvalue())
        self.assertIn("ERROR: boom", stderr.getvalue())
