"""Localhost HTTP proxy for MOTU datastore requests."""

from __future__ import annotations

import hmac
import ipaddress
import json
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .datastore import (
    DatastoreNoResponse,
    DatastoreResponseLimit,
    DatastoreTimeout,
    ShortUsbFrame,
    ShortUsbWrite,
)
from .device import DeviceDiscoveryError
from .json_body import InvalidJsonBody, validate_json_body
from .paths import normalize_path
from .parser import DatastorePayload, ResponseFrameError
from .protocol import ProtocolFrameTooLarge, validate_post_frame_size
from .schema import DatastorePermissionError, DatastoreValidationError, validate_datastore_write


DatastoreRead = Callable[..., bytes | DatastorePayload]
DatastoreWrite = Callable[[str, str, str | None], bytes | DatastorePayload]
WriteLogger = Callable[[str, str, str], None]
# Keep the default comfortably below the protocol's single-frame u16 limits.
# Path/client-specific validation below catches exact frame overflows.
DEFAULT_MAX_WRITE_BODY_BYTES = 60 * 1024
WRITE_TOKEN_HEADER = "X-Motu-Proxy-Token"
MAX_CLIENT_ID = 0xFFFFFFFF


class WritesDisabled(RuntimeError):
    pass


class CrossOriginWrite(RuntimeError):
    pass


class HostNotAllowed(RuntimeError):
    pass


class WriteTokenRequired(RuntimeError):
    pass


class RequestBodyTooLarge(RuntimeError):
    pass


class BadRequest(RuntimeError):
    pass


@dataclass(frozen=True)
class DispatchResult:
    response: bytes
    path: str
    etag: str | None = None
    status: int = 200


def parse_write_body(raw: str, content_type: str) -> str:
    if "application/x-www-form-urlencoded" in content_type or raw.startswith("json="):
        try:
            values = parse_qs(
                raw,
                keep_blank_values=True,
                encoding="utf-8",
                errors="strict",
            ).get("json")
        except UnicodeDecodeError as exc:
            raise BadRequest("request body must be valid UTF-8") from exc
        if values:
            return values[0]
    return raw


def parse_client_query(request_path: str) -> str | None:
    values = parse_qs(urlparse(request_path).query, keep_blank_values=True).get("client")
    if not values:
        return None
    value = values[0].strip()
    if not value.isdecimal():
        raise BadRequest("client must be a 32-bit unsigned integer")
    client = int(value, 10)
    if client > MAX_CLIENT_ID:
        raise BadRequest("client must be a 32-bit unsigned integer")
    return str(client)


def _origin_matches_host(origin: str, host: str) -> bool:
    parsed = urlparse(origin)
    return parsed.scheme == "http" and bool(parsed.netloc) and parsed.netloc.lower() == host.lower()


def _host_name(host: str | None) -> str:
    if not host:
        return ""
    parsed = urlparse(f"//{host}", allow_fragments=False)
    return (parsed.hostname or "").lower()


def _is_loopback_host(host: str | None) -> bool:
    name = _host_name(host)
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def validate_write_host(method: str, allow_writes: bool, host: str | None, allow_remote_writes: bool) -> None:
    if method == "GET" or not allow_writes or allow_remote_writes:
        return
    if not _is_loopback_host(host):
        raise HostNotAllowed("write requests require a loopback Host header")


def validate_write_origin(method: str, allow_writes: bool, origin: str | None, host: str | None) -> None:
    if method == "GET" or not allow_writes or not origin:
        return
    if origin == "null":
        raise CrossOriginWrite("cross-origin writes are blocked")
    if not host or not _origin_matches_host(origin, host):
        raise CrossOriginWrite("cross-origin writes are blocked")


def validate_write_token(method: str, allow_writes: bool, expected_token: str | None, request_token: str | None) -> None:
    if method == "GET" or not allow_writes:
        return
    if not expected_token or not request_token or not hmac.compare_digest(expected_token, request_token):
        raise WriteTokenRequired("valid write token required")


def dispatch_datastore_request(
    method: str,
    request_path: str,
    raw_body: str,
    content_type: str,
    allow_writes: bool,
    run_get: DatastoreRead,
    run_post: DatastoreWrite,
    log_write: WriteLogger | None = None,
    origin: str | None = None,
    host: str | None = None,
    write_token: str | None = None,
    request_token: str | None = None,
    allow_remote_writes: bool = False,
    if_none_match: str | None = None,
    validate_writes: bool = True,
    allow_unknown_writes: bool = False,
) -> DispatchResult:
    path = normalize_path(urlparse(request_path).path)
    client = parse_client_query(request_path)
    if method == "GET":
        if if_none_match is None:
            payload = _datastore_payload(run_get(path, client))
        else:
            payload = _datastore_payload(run_get(path, client, if_none_match.strip()))
        status = 304 if payload.not_modified else 200
        return DispatchResult(payload.body, path, payload.etag, status=status)
    if not allow_writes:
        raise WritesDisabled("writes require --allow-writes")
    validate_write_host(method, allow_writes, host, allow_remote_writes)
    validate_write_origin(method, allow_writes, origin, host)
    validate_write_token(method, allow_writes, write_token, request_token)
    write_body = parse_write_body(raw_body, content_type)
    validate_json_body(write_body)
    if validate_writes:
        validate_datastore_write(path, write_body, allow_unknown=allow_unknown_writes)
    validate_post_frame_size(path, write_body, client=client)
    if log_write is not None:
        log_write(method, path, write_body)
    # HTTP PATCH is a compatibility alias for the MOTU datastore POST write.
    payload = _datastore_payload(run_post(path, write_body, client))
    return DispatchResult(payload.body, path, payload.etag)


def _datastore_payload(value: bytes | DatastorePayload) -> DatastorePayload:
    if isinstance(value, DatastorePayload):
        return value
    return DatastorePayload(value)


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def log_write_attempt(method: str, path: str, body: str) -> None:
    print(f"write attempt method={method} path={path} body={body!r}", file=sys.stderr)


class DatastoreDispatcher:
    def __init__(
        self,
        allow_writes: bool,
        run_get: DatastoreRead,
        run_post: DatastoreWrite,
        write_token: str | None = None,
        allow_remote_writes: bool = False,
        log_write: WriteLogger | None = log_write_attempt,
        lock: threading.Lock | _NullLock | None = None,
        serialize_dispatch: bool = True,
        validate_writes: bool = True,
        allow_unknown_writes: bool = False,
    ) -> None:
        self.allow_writes = allow_writes
        self.run_get = run_get
        self.run_post = run_post
        self.write_token = write_token
        self.allow_remote_writes = allow_remote_writes
        self.log_write = log_write
        self.lock = lock if lock is not None else (threading.Lock() if serialize_dispatch else _NullLock())
        self.validate_writes = validate_writes
        self.allow_unknown_writes = allow_unknown_writes

    def dispatch(
        self,
        method: str,
        request_path: str,
        raw_body: str = "",
        content_type: str = "",
        origin: str | None = None,
        host: str | None = None,
        request_token: str | None = None,
        if_none_match: str | None = None,
    ) -> DispatchResult:
        with self.lock:
            return dispatch_datastore_request(
                method,
                request_path,
                raw_body,
                content_type,
                self.allow_writes,
                self.run_get,
                self.run_post,
                log_write=self.log_write,
                origin=origin,
                host=host,
                write_token=self.write_token,
                request_token=request_token,
                allow_remote_writes=self.allow_remote_writes,
                if_none_match=if_none_match,
                validate_writes=self.validate_writes,
                allow_unknown_writes=self.allow_unknown_writes,
            )


class MotuProxyHandler(BaseHTTPRequestHandler):
    server_version = "MotuProxy/0.1"

    def do_GET(self) -> None:
        self.handle_datastore_request("GET")

    def do_POST(self) -> None:
        self.handle_datastore_request("POST")

    def do_PATCH(self) -> None:
        # Compatibility alias: MOTU's USB datastore write frame is POST.
        self.handle_datastore_request("PATCH")

    def log_message(self, fmt: str, *args) -> None:
        if self.server.debug:
            super().log_message(fmt, *args)

    def handle_datastore_request(self, method: str) -> None:
        try:
            raw_body = self.read_raw_body() if method != "GET" else ""
            result = self.server.dispatcher.dispatch(
                method,
                self.path,
                raw_body,
                self.headers.get("Content-Type", ""),
                origin=self.headers.get("Origin"),
                host=self.headers.get("Host"),
                request_token=self.read_write_token(),
                if_none_match=self.headers.get("If-None-Match") if method == "GET" else None,
            )
            body = result.response
            self.send_response(result.status)
            if result.status != 304:
                self.send_header("Content-Type", response_content_type(body))
            self.send_header("Cache-Control", "no-cache")
            if method == "GET" and result.etag is not None:
                self.send_header("ETag", result.etag)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (WritesDisabled, CrossOriginWrite, HostNotAllowed, WriteTokenRequired, DatastorePermissionError) as exc:
            self.send_json_error(403, str(exc))
        except DatastoreValidationError as exc:
            self.send_json_error(422, str(exc))
        except RequestBodyTooLarge as exc:
            self.send_json_error(413, str(exc))
        except ProtocolFrameTooLarge as exc:
            self.send_json_error(413, str(exc))
        except (BadRequest, InvalidJsonBody) as exc:
            self.send_json_error(400, str(exc))
        except DeviceDiscoveryError as exc:
            self.send_backend_error(503, "MOTU USB device is not available", exc)
        except (DatastoreNoResponse, DatastoreTimeout) as exc:
            self.send_backend_error(504, "MOTU USB datastore did not respond", exc)
        except (ResponseFrameError, DatastoreResponseLimit, ShortUsbFrame, ShortUsbWrite) as exc:
            self.send_backend_error(502, "MOTU USB datastore returned an invalid response", exc)
        except Exception as exc:
            self.send_backend_error(502, "MOTU USB datastore request failed", exc)

    def read_raw_body(self) -> str:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError as exc:
            raise BadRequest("invalid Content-Length") from exc
        if length < 0:
            raise BadRequest("invalid Content-Length")
        if length > self.server.max_write_body_bytes:
            raise RequestBodyTooLarge(f"request body exceeds {self.server.max_write_body_bytes} bytes")
        try:
            return self.rfile.read(length).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise BadRequest("request body must be valid UTF-8") from exc

    def read_write_token(self) -> str | None:
        token = self.headers.get(WRITE_TOKEN_HEADER)
        if token:
            return token.strip()
        authorization = self.headers.get("Authorization", "")
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value.strip()
        return None

    def send_json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_backend_error(self, status: int, public_message: str, exc: Exception) -> None:
        if self.server.debug:
            self.send_json_error(status, str(exc))
            return
        print(f"{status} {public_message}: {exc}", file=sys.stderr)
        self.send_json_error(status, public_message)


class MotuProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        server_address,
        allow_writes: bool,
        debug: bool,
        run_get: DatastoreRead,
        run_post: DatastoreWrite,
        write_token: str | None = None,
        write_token_file: str | None = None,
        allow_remote_writes: bool = False,
        max_write_body_bytes: int = DEFAULT_MAX_WRITE_BODY_BYTES,
        serialize_dispatch: bool = True,
        validate_writes: bool = True,
        allow_unknown_writes: bool = False,
    ) -> None:
        super().__init__(server_address, MotuProxyHandler)
        self.allow_writes = allow_writes
        self.debug = debug
        self.write_token = write_token
        self.write_token_file = write_token_file
        self.allow_remote_writes = allow_remote_writes
        self.validate_writes = validate_writes
        self.allow_unknown_writes = allow_unknown_writes
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


def response_content_type(body: bytes) -> str:
    try:
        json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "application/octet-stream"
    return "application/json"


def serve(server: MotuProxyServer, before_close: Callable[[], None] | None = None) -> int:
    host, port = server.server_address[:2]
    print(f"listening on http://{host}:{port} writes={'on' if server.allow_writes else 'off'}", file=sys.stderr)
    if server.allow_writes:
        print(f"write token: {server.write_token}", file=sys.stderr)
        print(f"write token header: {WRITE_TOKEN_HEADER} or Authorization: Bearer", file=sys.stderr)
        if server.write_token_file:
            print(f"write token file: {server.write_token_file}", file=sys.stderr)
        if server.allow_remote_writes:
            print("WARNING: remote HTTP writes are enabled; keep the token secret", file=sys.stderr)
        if not server.validate_writes:
            print("WARNING: datastore write validation is disabled", file=sys.stderr)
        elif server.allow_unknown_writes:
            print("WARNING: unknown datastore write paths are allowed", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", file=sys.stderr)
    finally:
        if before_close is not None:
            before_close()
        server.server_close()
    return 0
