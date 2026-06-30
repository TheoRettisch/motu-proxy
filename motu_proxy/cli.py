"""Command-line interface for the MOTU USB datastore proxy."""

from __future__ import annotations

import argparse
import ipaddress
import os
import secrets
import socket
import sys
import time
from pathlib import Path

from .datastore import DatastoreConfig, DatastoreCoordinator, ResponseStats, open_datastore
from .device import DEFAULT_DEVFS_ROOT, DEFAULT_SYSFS_ROOT, UsbDeviceInfo, find_motu_device
from .fixtures import EXPECTED_GET_DATASTORE, EXPECTED_POST_HOST_OS
from .http_server import DEFAULT_MAX_WRITE_BODY_BYTES, MotuProxyServer, serve
from .json_body import validate_json_body
from .parser import response_to_text
from .paths import normalize_path
from .protocol import (
    DEFAULT_MESSAGE_SEQ,
    DEFAULT_SEQ_START,
    DEFAULT_TIMEOUT_MS,
    MOTU_AVB_PID,
    MOTU_VID,
    build_get_frame,
    build_post_frame,
    crc32,
)


DEFAULT_WRITE_TOKEN_FILE = Path("/run/motu-proxy/write-token")
DEFAULT_SMOKE_PATHS = ("/datastore/uid", "/datastore/ext/maxUSBToHost", "/datastore/host/mode")


def config_from_args(args) -> DatastoreConfig:
    return DatastoreConfig(
        vid=args.vid,
        pid=args.pid,
        serial=args.serial,
        interface=args.interface,
        ep_out=args.ep_out,
        ep_in=args.ep_in,
        timeout_ms=args.timeout_ms,
        seq_start=args.seq_start,
        message_seq=args.message_seq,
        no_init=args.no_init,
        debug=args.debug,
        sysfs_root=Path(args.sysfs_root),
        devfs_root=Path(args.devfs_root),
    )


def _is_loopback_listen_address(address: str) -> bool:
    if address == "localhost":
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def validate_serve_write_safety(listen: str, allow_writes: bool, unsafe_allow_remote_writes: bool) -> None:
    if allow_writes and not unsafe_allow_remote_writes and not _is_loopback_listen_address(listen):
        raise RuntimeError(
            "--allow-writes requires a loopback --listen address unless --unsafe-allow-remote-writes is set"
        )


def write_token_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(token)
        handle.write("\n")
    os.chmod(path, 0o600)


def prepare_write_token(token_file: str | None) -> tuple[str, str | None]:
    token = secrets.token_urlsafe(32)
    if token_file is None:
        return token, None
    path = Path(token_file)
    write_token_file(path, token)
    return token, str(path)


def command_info(args) -> int:
    config = config_from_args(args)
    device = find_motu_device(
        config.vid,
        config.pid,
        serial=config.serial,
        sysfs_root=config.sysfs_root,
        devfs_root=config.devfs_root,
        interface=config.interface,
        ep_out=config.ep_out,
        ep_in=config.ep_in,
    )
    print_device_info(config, device)
    return 0


def print_device_info(config: DatastoreConfig, device: UsbDeviceInfo) -> None:
    print(f"vid: 0x{config.vid:04x}")
    print(f"pid: 0x{config.pid:04x}")
    print(f"product: {device.product or '(unknown)'}")
    print(f"serial: {device.serial or '(none)'}")
    print(f"interface: {device.interface}")
    print(f"ep_out: 0x{device.ep_out:02x}")
    print(f"ep_in: 0x{device.ep_in:02x}")
    print(f"max_packet_size: {device.max_packet_size}")
    print(f"sysfs_path: {device.sysfs_path}")
    print(f"devfs_path: {device.devfs_path}")


def command_get(args) -> int:
    path = normalize_path(args.path)
    with open_datastore(config_from_args(args)) as datastore:
        response = datastore.get(path, etag=args.etag)
    if args.raw:
        sys.stdout.buffer.write(response)
    else:
        print(response_to_text(response, pretty=not args.compact))
    return 0


def command_post(args) -> int:
    path = normalize_path(args.path)
    validate_json_body(args.json_body)
    with open_datastore(config_from_args(args)) as datastore:
        response = datastore.post(path, args.json_body)
    if args.raw:
        sys.stdout.buffer.write(response)
    else:
        print(response_to_text(response, pretty=not args.compact))
    return 0


def command_probe(args) -> int:
    paths = list(DEFAULT_SMOKE_PATHS)
    with open_datastore(config_from_args(args)) as datastore:
        for path in paths:
            try:
                response = datastore.get(path)
                print(f"\n# {path}")
                print(response_to_text(response, pretty=not args.compact))
            except Exception as exc:
                print(f"\n# {path}\nERROR: {exc}", file=sys.stderr)
    return 0


def command_smoke(args) -> int:
    paths = [normalize_path(path) for path in (args.paths or DEFAULT_SMOKE_PATHS)]
    failures = 0
    with open_datastore(config_from_args(args)) as datastore:
        for path in paths:
            started = time.monotonic()
            print(f"\n# {path}")
            try:
                response = datastore.get(path)
                elapsed_ms = (time.monotonic() - started) * 1000
                print(f"OK {format_response_stats(elapsed_ms, datastore.last_response_stats)}")
                if not args.no_body:
                    print(response_to_text(response, pretty=not args.compact))
            except Exception as exc:
                failures += 1
                elapsed_ms = (time.monotonic() - started) * 1000
                print(f"FAIL {format_response_stats(elapsed_ms, datastore.last_response_stats)}")
                print(f"ERROR: {exc}", file=sys.stderr)
                if not args.continue_on_error:
                    return 1
    return 1 if failures else 0


def format_response_stats(elapsed_ms: float, stats: ResponseStats | None) -> str:
    if stats is None:
        return f"{elapsed_ms:.1f} ms"
    return (
        f"{elapsed_ms:.1f} ms bytes={stats.response_bytes} frames={stats.accepted_frames} "
        f"reads={stats.reads} ignored={stats.ignored_packets} ack={stats.ack_packets}"
    )


def command_serve(args) -> int:
    config = config_from_args(args)
    validate_serve_write_safety(args.listen, args.allow_writes, args.unsafe_allow_remote_writes)

    write_token, write_token_file_path = (
        prepare_write_token(args.write_token_file) if args.allow_writes else (None, None)
    )
    with open_datastore(config) as datastore:
        coordinator = DatastoreCoordinator(datastore)
        coordinator.start()
        try:
            server = MotuProxyServer(
                (args.listen, args.port),
                args.allow_writes,
                args.debug,
                coordinator.get,
                coordinator.post,
                write_token=write_token,
                write_token_file=write_token_file_path,
                allow_remote_writes=args.unsafe_allow_remote_writes,
                max_write_body_bytes=args.max_write_body_bytes,
                serialize_dispatch=False,
            )
            return serve(server)
        finally:
            coordinator.close()


def command_selftest(_args) -> int:
    got_get = build_get_frame(0x24, 2, "/datastore")
    got_post = build_post_frame(0x23, 2, "/datastore/host/os", '{"value": "win"}', header="PTTH")
    if got_get != EXPECTED_GET_DATASTORE:
        raise AssertionError("GET frame does not match capture")
    if got_post != EXPECTED_POST_HOST_OS:
        raise AssertionError("POST frame does not match capture")
    if crc32(b"123456789") != 0xCBF43926:
        raise AssertionError("CRC32 self-test failed")
    print("selftest ok")
    return 0


def _int_arg(value: str) -> int:
    return int(value, 0)


def add_usb_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vid", type=_int_arg, default=MOTU_VID)
    parser.add_argument("--pid", type=_int_arg, default=MOTU_AVB_PID)
    parser.add_argument("--serial")
    parser.add_argument("--interface", type=_int_arg)
    parser.add_argument("--ep-out", type=_int_arg)
    parser.add_argument("--ep-in", type=_int_arg)
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--seq-start", type=_int_arg, default=DEFAULT_SEQ_START)
    parser.add_argument("--message-seq", type=int, default=DEFAULT_MESSAGE_SEQ)
    parser.add_argument("--no-init", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--sysfs-root", default=str(DEFAULT_SYSFS_ROOT))
    parser.add_argument("--devfs-root", default=str(DEFAULT_DEVFS_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal MOTU AVB USB datastore probe/proxy")
    sub = parser.add_subparsers(dest="command", required=True)

    get_parser = sub.add_parser("get", help="read a datastore path over USB")
    add_usb_args(get_parser)
    get_parser.add_argument("path", nargs="?", default="/datastore/uid")
    get_parser.add_argument("--etag", default="0")
    get_parser.add_argument("--raw", action="store_true")
    get_parser.add_argument("--compact", action="store_true")
    get_parser.set_defaults(func=command_get)

    info_parser = sub.add_parser("info", help="show discovered MOTU USB control endpoint details")
    add_usb_args(info_parser)
    info_parser.set_defaults(func=command_info)

    post_parser = sub.add_parser("post", help="POST json=<body> to a datastore path over USB")
    add_usb_args(post_parser)
    post_parser.add_argument("path")
    post_parser.add_argument("json_body")
    post_parser.add_argument("--raw", action="store_true")
    post_parser.add_argument("--compact", action="store_true")
    post_parser.set_defaults(func=command_post)

    probe_parser = sub.add_parser("probe", help="read a few harmless baseline paths")
    add_usb_args(probe_parser)
    probe_parser.add_argument("--compact", action="store_true")
    probe_parser.set_defaults(func=command_probe)

    smoke_parser = sub.add_parser("smoke", help="run a read-only USB datastore smoke test")
    add_usb_args(smoke_parser)
    smoke_parser.add_argument(
        "--path",
        dest="paths",
        action="append",
        help="datastore path to read; may be repeated; defaults to harmless baseline paths",
    )
    smoke_parser.add_argument("--compact", action="store_true")
    smoke_parser.add_argument("--no-body", action="store_true", help="print only timing and frame statistics")
    smoke_parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="continue reading remaining paths after a smoke read failure",
    )
    smoke_parser.set_defaults(func=command_smoke)

    serve_parser = sub.add_parser("serve", help="serve a tiny localhost datastore proxy")
    add_usb_args(serve_parser)
    serve_parser.add_argument("--listen", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=1280)
    serve_parser.add_argument("--allow-writes", action="store_true")
    serve_parser.add_argument(
        "--write-token-file",
        default=str(DEFAULT_WRITE_TOKEN_FILE),
        help="write the generated HTTP write token here for local automation",
    )
    serve_parser.add_argument(
        "--no-write-token-file",
        dest="write_token_file",
        action="store_const",
        const=None,
        help="print the generated write token but do not write a token file",
    )
    serve_parser.add_argument(
        "--unsafe-allow-remote-writes",
        action="store_true",
        help="allow --allow-writes on a non-loopback listen address; token is still required",
    )
    serve_parser.add_argument(
        "--max-write-body-bytes",
        type=int,
        default=DEFAULT_MAX_WRITE_BODY_BYTES,
        help="maximum accepted HTTP write body size",
    )
    serve_parser.set_defaults(func=command_serve)

    selftest_parser = sub.add_parser("selftest", help="verify frame builder against capture bytes")
    selftest_parser.set_defaults(func=command_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        return 1
    except (OSError, RuntimeError, AssertionError, socket.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
