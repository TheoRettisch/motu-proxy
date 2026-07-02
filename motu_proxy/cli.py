"""Command-line interface for the MOTU USB datastore proxy."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import stat
import sys
import time
from pathlib import Path

from .datastore import (
    CAPABILITY_SECTIONS,
    IDENTITY_KEYS,
    MAX_MESSAGE_SEQ,
    DatastoreConfig,
    DatastoreCoordinator,
    DeviceCapabilityInfo,
    ManagedDatastore,
    ResponseStats,
    open_datastore,
    read_device_capability_info,
)
from .device import DEFAULT_DEVFS_ROOT, DEFAULT_SYSFS_ROOT
from .fixtures import EXPECTED_GET_DATASTORE, EXPECTED_POST_HOST_OS
from .http_server import (
    DEFAULT_MAX_WRITE_BODY_BYTES,
    MAX_CONFIGURABLE_WRITE_BODY_BYTES,
    MotuProxyServer,
    serve,
)
from .json_body import load_json_object
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
    validate_host_seq,
)
from .schema import validate_datastore_write_object

DEFAULT_WRITE_TOKEN_FILE = Path("/run/motu-proxy/write-token")
DEFAULT_SMOKE_PATHS = ("/datastore/uid", "/datastore/ext/maxUSBToHost", "/datastore/host/mode")


def config_from_args(args) -> DatastoreConfig:
    validate_usb_overrides(args.interface, args.ep_out, args.ep_in)
    validate_host_seq(args.seq_start)
    validate_cli_range(args.timeout_ms, 1, None, "--timeout-ms")
    validate_cli_range(args.message_seq, 0, MAX_MESSAGE_SEQ, "--message-seq")
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


def validate_usb_overrides(interface: int | None, ep_out: int | None, ep_in: int | None) -> None:
    overrides = (interface, ep_out, ep_in)
    if any(value is not None for value in overrides) and not all(
        value is not None for value in overrides
    ):
        raise RuntimeError("--interface, --ep-out, and --ep-in must be provided together")


def validate_cli_range(
    value: int,
    minimum: int,
    maximum: int | None,
    name: str,
) -> None:
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"{name} must be <= {maximum}")


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
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise RuntimeError(f"refusing to write token file through symlink: {path}")
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(token)
        handle.write("\n")


def prepare_write_token(token_file: str | None) -> tuple[str, str | None]:
    token = secrets.token_urlsafe(32)
    if token_file is None:
        return token, None
    path = Path(token_file)
    write_token_file(path, token)
    return token, str(path)


def remove_write_token_file(token_file: str | None, token: str | None) -> None:
    if token_file is None or token is None:
        return
    path = Path(token_file)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return
        if path.read_text(encoding="ascii") != f"{token}\n":
            return
        path.unlink()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return


def command_info(args) -> int:
    with open_datastore(config_from_args(args)) as datastore:
        info = read_device_capability_info(datastore)
    if args.json:
        print(json.dumps(info.as_dict(), indent=2, sort_keys=True))
    else:
        print_capability_info(info)
    return 0


def print_capability_info(info: DeviceCapabilityInfo) -> None:
    print(f"apiversion: {_format_info_value(info.apiversion)}")
    print("capabilities:")
    for section in CAPABILITY_SECTIONS:
        capability = info.capabilities[section]
        value = capability.version if capability.present else None
        print(f"  {section}: {_format_info_value(value)}")
    print("identity:")
    for key in IDENTITY_KEYS:
        print(f"  {key}: {_format_info_value(info.identity.get(key))}")


def _format_info_value(value: object | None) -> str:
    if value is None:
        return "not present"
    if isinstance(value, str):
        return value.rstrip("\n").replace("\r\n", "\n").replace("\n", "\\n")
    return str(value)


def command_get(args) -> int:
    path = normalize_path(args.path)
    with open_datastore(config_from_args(args)) as datastore:
        response = datastore.get(path, etag=args.etag)
    if args.raw:
        sys.stdout.buffer.write(response)
    else:
        print(response_to_text(response, pretty=not args.compact))
    return 0


def command_meters(args) -> int:
    with open_datastore(config_from_args(args)) as datastore:
        response = datastore.get(
            "/meters",
            etag=args.etag,
            query_fields=(("meters", args.group),),
        )
    if args.raw:
        sys.stdout.buffer.write(response)
    else:
        print(response_to_text(response, pretty=not args.compact))
    return 0


def command_post(args) -> int:
    path = normalize_path(args.path)
    write_object = load_json_object(args.json_body)
    write_body = args.json_body.encode("utf-8")
    if not args.no_validate:
        validate_datastore_write_object(
            path,
            write_object,
            allow_unknown=args.allow_unknown_writes,
        )
    with open_datastore(config_from_args(args)) as datastore:
        response = datastore.post(path, write_body)
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

    datastore = ManagedDatastore(config, opener=open_datastore)
    coordinator = DatastoreCoordinator(datastore)
    write_token: str | None = None
    write_token_file_path: str | None = None
    coordinator.start()
    try:
        if args.allow_writes and args.require_write_token:
            write_token, write_token_file_path = prepare_write_token(args.write_token_file)
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
            validate_writes=not args.no_validate,
            allow_unknown_writes=args.allow_unknown_writes,
            status_provider=coordinator.status,
        )
        return serve(server, before_close=coordinator.close)
    finally:
        coordinator.close()
        datastore.close()
        remove_write_token_file(write_token_file_path, write_token)


def command_selftest(_args) -> int:
    got_get = build_get_frame(0x24, 2, "/datastore")
    got_post = build_post_frame(0x23, 2, "/datastore/host/os", b'{"value": "win"}', header="PTTH")
    if got_get != EXPECTED_GET_DATASTORE:
        raise AssertionError("GET frame does not match capture")
    if got_post != EXPECTED_POST_HOST_OS:
        raise AssertionError("POST frame does not match capture")
    if crc32(b"123456789") != 0xCBF43926:
        raise AssertionError("CRC32 self-test failed")
    print("selftest ok")
    return 0


def _int_arg(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc


def _bounded_int_arg(value: str, minimum: int, maximum: int, name: str) -> int:
    parsed = _int_arg(value)
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"{name} must be in range {minimum}..{maximum}")
    return parsed


def _positive_int_arg(value: str) -> int:
    parsed = _int_arg(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be > 0")
    return parsed


def _u16_arg(value: str) -> int:
    return _bounded_int_arg(value, 0, 0xFFFF, "value")


def _interface_arg(value: str) -> int:
    return _bounded_int_arg(value, 0, 0xFF, "interface")


def _ep_out_arg(value: str) -> int:
    endpoint = _int_arg(value)
    if not 0x01 <= endpoint <= 0x0F:
        raise argparse.ArgumentTypeError("--ep-out must be an OUT endpoint address 0x01..0x0f")
    return endpoint


def _ep_in_arg(value: str) -> int:
    endpoint = _int_arg(value)
    if not 0x81 <= endpoint <= 0x8F:
        raise argparse.ArgumentTypeError("--ep-in must be an IN endpoint address 0x81..0x8f")
    return endpoint


def _host_seq_arg(value: str) -> int:
    seq = _int_arg(value)
    try:
        return validate_host_seq(seq)
    except RuntimeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _message_seq_arg(value: str) -> int:
    return _bounded_int_arg(value, 0, MAX_MESSAGE_SEQ, "message sequence")


def _port_arg(value: str) -> int:
    return _bounded_int_arg(value, 1, 65535, "port")


def _max_write_body_bytes_arg(value: str) -> int:
    parsed = _positive_int_arg(value)
    if parsed > MAX_CONFIGURABLE_WRITE_BODY_BYTES:
        raise argparse.ArgumentTypeError(
            f"--max-write-body-bytes must be <= {MAX_CONFIGURABLE_WRITE_BODY_BYTES}"
        )
    return parsed


def add_usb_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vid", type=_u16_arg, default=MOTU_VID)
    parser.add_argument("--pid", type=_u16_arg, default=MOTU_AVB_PID)
    parser.add_argument("--serial")
    parser.add_argument("--interface", type=_interface_arg)
    parser.add_argument("--ep-out", type=_ep_out_arg)
    parser.add_argument("--ep-in", type=_ep_in_arg)
    parser.add_argument("--timeout-ms", type=_positive_int_arg, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--seq-start", type=_host_seq_arg, default=DEFAULT_SEQ_START)
    parser.add_argument("--message-seq", type=_message_seq_arg, default=DEFAULT_MESSAGE_SEQ)
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

    meters_parser = sub.add_parser("meters", help="read one meters group over USB")
    add_usb_args(meters_parser)
    meters_parser.add_argument("group", help="meter group to request, for example mix/level")
    meters_parser.add_argument("--etag", default="0")
    meters_parser.add_argument("--raw", action="store_true")
    meters_parser.add_argument("--compact", action="store_true")
    meters_parser.set_defaults(func=command_meters)

    info_parser = sub.add_parser("info", help="show datastore API, capability, and identity details")
    add_usb_args(info_parser)
    info_parser.add_argument("--json", action="store_true", help="emit capability details as JSON")
    info_parser.set_defaults(func=command_info)

    post_parser = sub.add_parser("post", help="POST json=<body> to a datastore path over USB")
    add_usb_args(post_parser)
    post_parser.add_argument("path")
    post_parser.add_argument("json_body")
    post_parser.add_argument("--raw", action="store_true")
    post_parser.add_argument("--compact", action="store_true")
    post_parser.add_argument(
        "--no-validate",
        action="store_true",
        help="forward the datastore write without checking type, range, enum, permission, or unknown paths",
    )
    post_parser.add_argument(
        "--allow-unknown-writes",
        action="store_true",
        help="allow writes to paths absent from the embedded schema while still validating known paths",
    )
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
    serve_parser.add_argument("--port", type=_port_arg, default=1280)
    serve_parser.add_argument("--allow-writes", action="store_true")
    serve_parser.add_argument(
        "--require-write-token",
        action="store_true",
        help="require a generated token for HTTP writes when --allow-writes is enabled",
    )
    serve_parser.add_argument(
        "--write-token-file",
        default=str(DEFAULT_WRITE_TOKEN_FILE),
        help="write the generated HTTP write token here when --require-write-token is set",
    )
    serve_parser.add_argument(
        "--no-write-token-file",
        dest="write_token_file",
        action="store_const",
        const=None,
        help="print the generated write token but do not write a token file when --require-write-token is set",
    )
    serve_parser.add_argument(
        "--unsafe-allow-remote-writes",
        action="store_true",
        help="allow --allow-writes on a non-loopback listen address; combine with --require-write-token for token protection",
    )
    serve_parser.add_argument(
        "--max-write-body-bytes",
        type=_max_write_body_bytes_arg,
        default=DEFAULT_MAX_WRITE_BODY_BYTES,
        help="maximum accepted HTTP write body size",
    )
    serve_parser.add_argument(
        "--no-validate",
        action="store_true",
        help="forward HTTP writes without checking type, range, enum, permission, or unknown paths",
    )
    serve_parser.add_argument(
        "--allow-unknown-writes",
        action="store_true",
        help="allow writes to paths absent from the embedded schema while still validating known paths",
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
    except (OSError, RuntimeError, AssertionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
