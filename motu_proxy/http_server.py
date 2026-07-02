"""Localhost HTTP proxy for MOTU datastore requests."""

from __future__ import annotations

import hmac
import ipaddress
import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, parse_qsl, urlparse

from .datastore import (
    DatastoreDeviceUnavailable,
    DatastoreNoResponse,
    DatastoreResponseLimit,
    DatastoreTimeout,
    ShortUsbFrame,
    ShortUsbWrite,
)
from .device import DeviceDiscoveryError
from .json_body import InvalidJsonBody, load_json_object
from .parser import (
    DatastorePayload,
    ResponseFrameError,
    datastore_body_content_type,
    is_single_json_container,
)
from .paths import normalize_path
from .protocol import (
    ProtocolFrameTooLarge,
    max_post_json_body_bytes,
    validate_post_frame_size,
)
from .schema import (
    DatastorePermissionError,
    DatastoreValidationError,
    validate_datastore_write_object,
)

DatastoreRead = Callable[
    [str, str | None, str | None, tuple[tuple[str, str], ...]],
    bytes | DatastorePayload,
]
DatastoreWrite = Callable[[str, bytes, str | None], bytes | DatastorePayload]
WriteLogger = Callable[[str, str, bytes], None]
StatusProvider = Callable[[], dict[str, object | None]]
# Keep the default comfortably below the protocol's single-frame u16 limits.
# Path/client-specific validation below catches exact frame overflows.
DEFAULT_MAX_WRITE_BODY_BYTES = 60 * 1024
MAX_CONFIGURABLE_WRITE_BODY_BYTES = max_post_json_body_bytes("/datastore")
DEFAULT_WRITE_BODY_READ_TIMEOUT_S = 5.0
DEFAULT_IDLE_CONNECTION_TIMEOUT_S = 65.0
WRITE_TOKEN_HEADER = "X-Motu-Proxy-Token"
MAX_CLIENT_ID = 0xFFFFFFFF
STATUS_PATH = "/__motu_proxy/status"


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


class RequestBodyTimeout(RuntimeError):
    pass


class BadRequest(RuntimeError):
    pass


@dataclass(frozen=True)
class DispatchResult:
    response: bytes
    path: str
    etag: str | None = None
    status: int = 200
    content_type: str | None = None


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


def validate_client_query_value(value: str) -> str:
    value = value.strip()
    if not value.isdecimal():
        raise BadRequest("client must be a 32-bit unsigned integer")
    client = int(value, 10)
    if client > MAX_CLIENT_ID:
        raise BadRequest("client must be a 32-bit unsigned integer")
    return str(client)


def parse_client_query(request_path: str) -> str | None:
    values = parse_qs(urlparse(request_path).query, keep_blank_values=True).get("client")
    if not values:
        return None
    return validate_client_query_value(values[0])


def parse_get_query_fields(request_path: str) -> tuple[tuple[tuple[str, str], ...], str | None]:
    try:
        pairs = parse_qsl(
            urlparse(request_path).query,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
        )
    except UnicodeDecodeError as exc:
        raise BadRequest("query string must be valid UTF-8") from exc
    fields: list[tuple[str, str]] = []
    client: str | None = None
    for name, value in pairs:
        if not name:
            raise BadRequest("query field name must not be empty")
        if name == "client":
            value = validate_client_query_value(value)
            if client is not None:
                raise BadRequest("client query field must not be repeated")
            client = value
        fields.append((name, value))
    return tuple(fields), client


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
    if method == "GET" or not allow_writes or expected_token is None:
        return
    if not request_token:
        raise WriteTokenRequired("valid write token required")
    if not hmac.compare_digest(_token_bytes(expected_token), _token_bytes(request_token)):
        raise WriteTokenRequired("valid write token required")


def _token_bytes(token: str) -> bytes:
    return token.encode("utf-8", "surrogateescape")


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
    status_provider: StatusProvider | None = None,
) -> DispatchResult:
    request_url = urlparse(request_path)
    if method == "GET" and request_url.path == STATUS_PATH and status_provider is not None:
        body = json.dumps(status_provider(), sort_keys=True).encode("utf-8")
        return DispatchResult(body, STATUS_PATH, content_type="application/json")
    path = normalize_path(request_url.path)
    if method == "GET":
        query_fields, client = parse_get_query_fields(request_path)
        etag = if_none_match.strip() if if_none_match is not None else None
        payload = _coerce_datastore_payload(run_get(path, client, etag, query_fields))
        status = 304 if payload.not_modified else 200
        return DispatchResult(
            payload.body,
            path,
            payload.etag,
            status=status,
            content_type=payload.content_type,
        )
    client = parse_client_query(request_path)
    if not allow_writes:
        raise WritesDisabled("writes require --allow-writes")
    validate_write_host(method, allow_writes, host, allow_remote_writes)
    validate_write_origin(method, allow_writes, origin, host)
    validate_write_token(method, allow_writes, write_token, request_token)
    write_body = parse_write_body(raw_body, content_type)
    write_object = load_json_object(write_body)
    write_body_bytes = write_body.encode("utf-8")
    if validate_writes:
        validate_datastore_write_object(
            path,
            write_object,
            allow_unknown=allow_unknown_writes,
        )
    validate_post_frame_size(path, write_body_bytes, client=client)
    if log_write is not None:
        log_write(method, path, write_body_bytes)
    # HTTP PATCH is a compatibility alias for the MOTU datastore POST write.
    payload = _coerce_datastore_payload(run_post(path, write_body_bytes, client))
    return DispatchResult(payload.body, path, payload.etag, content_type=payload.content_type)


def _coerce_datastore_payload(value: bytes | DatastorePayload) -> DatastorePayload:
    if isinstance(value, DatastorePayload):
        return value
    return DatastorePayload(value)


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def log_write_attempt(method: str, path: str, body: bytes) -> None:
    print(f"write attempt method={method} path={path} body_bytes={len(body)}", file=sys.stderr)


def log_write_attempt_debug(method: str, path: str, body: bytes) -> None:
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
        status_provider: StatusProvider | None = None,
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
        self.status_provider = status_provider

    def validate_write_headers(
        self,
        method: str,
        origin: str | None = None,
        host: str | None = None,
        request_token: str | None = None,
    ) -> None:
        if method == "GET":
            return
        if not self.allow_writes:
            raise WritesDisabled("writes require --allow-writes")
        validate_write_host(method, self.allow_writes, host, self.allow_remote_writes)
        validate_write_origin(method, self.allow_writes, origin, host)
        validate_write_token(method, self.allow_writes, self.write_token, request_token)

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
                status_provider=self.status_provider,
            )


class MotuProxyHandler(BaseHTTPRequestHandler):
    server_version = "MotuProxy/0.1"
    protocol_version = "HTTP/1.1"
    timeout = DEFAULT_IDLE_CONNECTION_TIMEOUT_S

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

    def send_error(self, code, message=None, explain=None) -> None:
        self.close_connection = True
        super().send_error(code, message, explain)

    def handle_datastore_request(self, method: str) -> None:
        response_started = False
        try:
            origin = self.headers.get("Origin")
            host = self.headers.get("Host")
            request_token = self.read_write_token() if method != "GET" else None
            if method == "GET":
                self.close_get_connection_if_body_announced()
            if method != "GET":
                validator = getattr(self.server.dispatcher, "validate_write_headers", None)
                if validator is not None:
                    validator(method, origin=origin, host=host, request_token=request_token)
            raw_body = self.read_raw_body() if method != "GET" else ""
            result = self.server.dispatcher.dispatch(
                method,
                self.path,
                raw_body,
                self.headers.get("Content-Type", ""),
                origin=origin,
                host=host,
                request_token=request_token,
                if_none_match=self.headers.get("If-None-Match") if method == "GET" else None,
            )
            body = b"" if result.status == 304 else result.response
            self.send_response(result.status)
            response_started = True
            if result.status != 304:
                self.send_header(
                    "Content-Type",
                    result.content_type or response_content_type(body),
                )
            self.send_header("Cache-Control", "no-cache")
            if method == "GET" and result.etag is not None:
                self.send_header("ETag", result.etag)
            if result.status != 304:
                self.send_header("Content-Length", str(len(body)))
            if getattr(self, "close_connection", False):
                self.send_header("Connection", "close")
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (WritesDisabled, CrossOriginWrite, HostNotAllowed, WriteTokenRequired, DatastorePermissionError) as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_json_error(403, str(exc))
        except DatastoreValidationError as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_json_error(422, str(exc))
        except RequestBodyTooLarge as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_json_error(413, str(exc))
        except ProtocolFrameTooLarge as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_json_error(413, str(exc))
        except RequestBodyTimeout as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_json_error(408, str(exc))
        except (BadRequest, InvalidJsonBody) as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_json_error(400, str(exc))
        except (DatastoreDeviceUnavailable, DeviceDiscoveryError) as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_backend_error(503, "MOTU USB device is not available", exc)
        except (DatastoreNoResponse, DatastoreTimeout) as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_backend_error(504, "MOTU USB datastore did not respond", exc)
        except (ResponseFrameError, DatastoreResponseLimit, ShortUsbFrame, ShortUsbWrite) as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_backend_error(502, "MOTU USB datastore returned an invalid response", exc)
        except Exception as exc:
            self.close_write_connection(method)
            if self.can_send_error_response(response_started):
                self.send_backend_error(502, "MOTU USB datastore request failed", exc)

    def close_get_connection_if_body_announced(self) -> None:
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            return
        try:
            length = int(content_length or "0")
        except ValueError as exc:
            self.close_connection = True
            raise BadRequest("invalid Content-Length") from exc
        if length < 0:
            self.close_connection = True
            raise BadRequest("invalid Content-Length")
        if length > 0:
            self.close_connection = True

    def read_raw_body(self) -> str:
        transfer_encoding = self.headers.get("Transfer-Encoding", "")
        if transfer_encoding and transfer_encoding.lower() != "identity":
            raise BadRequest("Transfer-Encoding is not supported")
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError as exc:
            raise BadRequest("invalid Content-Length") from exc
        if length < 0:
            raise BadRequest("invalid Content-Length")
        if length > self.server.max_write_body_bytes:
            raise RequestBodyTooLarge(f"request body exceeds {self.server.max_write_body_bytes} bytes")
        raw = self._read_exact_body_bytes(length)
        if len(raw) != length:
            self.close_connection = True
            raise BadRequest("request body ended before Content-Length bytes")
        try:
            return raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise BadRequest("request body must be valid UTF-8") from exc

    def _read_exact_body_bytes(self, length: int) -> bytes:
        if length == 0:
            return b""
        connection = getattr(self, "connection", None)
        timeout_s = getattr(self.server, "write_body_read_timeout_s", DEFAULT_WRITE_BODY_READ_TIMEOUT_S)
        previous_timeout = None
        timeout_was_set = False
        if connection is not None and timeout_s is not None:
            previous_timeout = connection.gettimeout()
            connection.settimeout(timeout_s)
            timeout_was_set = True
        try:
            return self.rfile.read(length)
        except TimeoutError as exc:
            self.close_connection = True
            raise RequestBodyTimeout("request body read timed out") from exc
        finally:
            if timeout_was_set:
                connection.settimeout(previous_timeout)

    def read_write_token(self) -> str | None:
        token = self.headers.get(WRITE_TOKEN_HEADER)
        if token:
            return token.strip()
        authorization = self.headers.get("Authorization", "")
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value.strip()
        return None

    def close_write_connection(self, method: str) -> None:
        if method != "GET":
            self.close_connection = True

    def can_send_error_response(self, response_started: bool) -> bool:
        if response_started:
            self.close_connection = True
            return False
        return True

    def send_json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if getattr(self, "close_connection", False):
            self.send_header("Connection", "close")
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
        write_body_read_timeout_s: float | None = DEFAULT_WRITE_BODY_READ_TIMEOUT_S,
        serialize_dispatch: bool = True,
        validate_writes: bool = True,
        allow_unknown_writes: bool = False,
        status_provider: StatusProvider | None = None,
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
        self.write_body_read_timeout_s = write_body_read_timeout_s
        self.dispatcher = DatastoreDispatcher(
            allow_writes,
            run_get,
            run_post,
            write_token=write_token,
            allow_remote_writes=allow_remote_writes,
            log_write=log_write_attempt_debug if debug else log_write_attempt,
            serialize_dispatch=serialize_dispatch,
            validate_writes=validate_writes,
            allow_unknown_writes=allow_unknown_writes,
            status_provider=status_provider,
        )


def response_content_type(body: bytes) -> str:
    return datastore_body_content_type(body)


def _is_single_json_container(body: bytes) -> bool:
    return is_single_json_container(body)


def serve(server: MotuProxyServer, before_close: Callable[[], None] | None = None) -> int:
    host, port = server.server_address[:2]
    print(f"listening on http://{host}:{port} writes={'on' if server.allow_writes else 'off'}", file=sys.stderr)
    if server.allow_writes:
        if server.write_token is None:
            print("write token: disabled (use --require-write-token to require one)", file=sys.stderr)
        else:
            if server.debug or not server.write_token_file:
                print(f"write token: {server.write_token}", file=sys.stderr)
            else:
                print("write token: stored in token file (use --debug to print)", file=sys.stderr)
            print(f"write token header: {WRITE_TOKEN_HEADER} or Authorization: Bearer", file=sys.stderr)
            if server.write_token_file:
                print(f"write token file: {server.write_token_file}", file=sys.stderr)
        if server.allow_remote_writes:
            if server.write_token is None:
                print("WARNING: remote HTTP writes are enabled without token protection", file=sys.stderr)
            else:
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
