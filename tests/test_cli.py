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

    def test_prepare_write_token_writes_generated_token_to_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "write-token"
            token, token_file = prepare_write_token(str(path))
            self.assertEqual(token_file, str(path))
            self.assertEqual(path.read_text(encoding="ascii"), f"{token}\n")


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
    def test_smoke_reads_paths_and_returns_failure_when_any_read_fails(self) -> None:
        datastore = FakeSmokeDatastore(
            {
                "/datastore/uid": b'{"value":"uid"}',
                "/datastore/host/mode": RuntimeError("boom"),
            }
        )
        args = build_parser().parse_args(
            ["smoke", "--no-body", "--path", "/uid", "--path", "/host/mode"]
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
