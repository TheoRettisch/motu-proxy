import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from motu_proxy.cli import (
    DEFAULT_WRITE_TOKEN_FILE,
    build_parser,
    prepare_write_token,
    validate_serve_write_safety,
    write_token_file,
)


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
