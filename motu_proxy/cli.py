"""Command-line interface for the MOTU USB datastore proxy."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

from .datastore import DatastoreConfig, open_datastore
from .device import DEFAULT_DEVFS_ROOT, DEFAULT_SYSFS_ROOT
from .fixtures import EXPECTED_GET_DATASTORE, EXPECTED_POST_HOST_OS
from .http_server import MotuProxyServer, serve
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
    with open_datastore(config_from_args(args)) as datastore:
        response = datastore.post(path, args.json_body)
    if args.raw:
        sys.stdout.buffer.write(response)
    else:
        print(response_to_text(response, pretty=not args.compact))
    return 0


def command_probe(args) -> int:
    paths = ["/datastore/uid", "/datastore/ext/maxUSBToHost", "/datastore/host/mode"]
    with open_datastore(config_from_args(args)) as datastore:
        for path in paths:
            try:
                response = datastore.get(path)
                print(f"\n# {path}")
                print(response_to_text(response, pretty=not args.compact))
            except Exception as exc:
                print(f"\n# {path}\nERROR: {exc}", file=sys.stderr)
    return 0


def command_serve(args) -> int:
    config = config_from_args(args)

    def run_get(path: str) -> bytes:
        with open_datastore(config) as datastore:
            return datastore.get(path)

    def run_post(path: str, json_body: str) -> bytes:
        with open_datastore(config) as datastore:
            return datastore.post(path, json_body)

    server = MotuProxyServer((args.listen, args.port), args.allow_writes, args.debug, run_get, run_post)
    return serve(server)


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

    serve_parser = sub.add_parser("serve", help="serve a tiny localhost datastore proxy")
    add_usb_args(serve_parser)
    serve_parser.add_argument("--listen", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=1280)
    serve_parser.add_argument("--allow-writes", action="store_true")
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
