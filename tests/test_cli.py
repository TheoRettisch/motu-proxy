from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipIf
from unittest.mock import patch

from motu_proxy.cli import (
    DEFAULT_WRITE_TOKEN_FILE,
    build_parser,
    prepare_write_token,
    validate_serve_write_safety,
    write_token_file,
)
from motu_proxy.datastore import ResponseStats
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
    ) -> bytes:
        self.calls.append((path, client))
        return self.response

    def post(
        self,
        path: str,
        json_body: str,
        client: str | None = None,
        timeout_ms: int = 1200,
    ) -> bytes:
        raise AssertionError("unexpected post")


class CliServeSecurityTests(TestCase):
    def test_serve_write_token_file_defaults_to_run_path(self) -> None:
        args = build_parser().parse_args(["serve"])
        self.assertEqual(args.write_token_file, str(DEFAULT_WRITE_TOKEN_FILE))

    def test_allow_writes_rejects_non_loopback_listen_without_unsafe_flag(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unsafe-allow-remote-writes"):
            validate_serve_write_safety("0.0.0.0", allow_writes=True, unsafe_allow_remote_writes=False)

    def test_allow_writes_accepts_non_loopback_listen_with_unsafe_flag(self) -> None:
        validate_serve_write_safety("0.0.0.0", allow_writes=True, unsafe_allow_remote_writes=True)

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
                )

            def server_close(self) -> None:
                pass

        def fake_serve(server) -> int:
            try:
                captured["result"] = server.dispatcher.dispatch("GET", "/datastore")
            finally:
                server.server_close()
            return 0

        args = build_parser().parse_args(["serve", "--port", "0"])
        with (
            patch("motu_proxy.cli.open_datastore", return_value=FakeOpenDatastore(datastore)),
            patch("motu_proxy.cli.MotuProxyServer", FakeServer),
            patch("motu_proxy.cli.serve", side_effect=fake_serve),
        ):
            result = args.func(args)

        self.assertEqual(result, 0)
        self.assertEqual(captured["result"].response, b'{"first":true}{"second":true}')
        self.assertIn(("/datastore", None), datastore.calls)


@skipIf(os.name == "nt", "fake sysfs interface names use ':' like Linux")
class CliInfoTests(TestCase):
    def test_info_prints_discovered_usb_control_details(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            add_device(root, "3-3", "0001f2fffe00c719", 3, 4)
            args = build_parser().parse_args(
                ["info", "--sysfs-root", str(root), "--devfs-root", str(root / "dev")]
            )
            stdout = StringIO()
            with redirect_stdout(stdout):
                result = args.func(args)
            self.assertEqual(result, 0)
            output = stdout.getvalue()
            self.assertIn("vid: 0x07fd", output)
            self.assertIn("pid: 0x0005", output)
            self.assertIn("product: 624", output)
            self.assertIn("serial: 0001f2fffe00c719", output)
            self.assertIn("interface: 3", output)
            self.assertIn("ep_out: 0x03", output)
            self.assertIn("ep_in: 0x83", output)
            self.assertIn("max_packet_size: 512", output)
            self.assertIn(f"devfs_path: {root / 'dev' / '003' / '004'}", output)


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
