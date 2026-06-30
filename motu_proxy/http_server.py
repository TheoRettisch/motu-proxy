"""Localhost HTTP proxy for MOTU datastore requests."""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .parser import extract_json_bytes
from .paths import normalize_path


DatastoreRead = Callable[[str], bytes]
DatastoreWrite = Callable[[str, str], bytes]
WriteLogger = Callable[[str, str, str], None]


class WritesDisabled(RuntimeError):
    pass


class CrossOriginWrite(RuntimeError):
    pass


@dataclass(frozen=True)
class DispatchResult:
    response: bytes
    path: str


def parse_write_body(raw: str, content_type: str) -> str:
    if "application/x-www-form-urlencoded" in content_type or raw.startswith("json="):
        values = parse_qs(raw, keep_blank_values=True).get("json")
        if values:
            return values[0]
    return raw


def _origin_matches_host(origin: str, host: str) -> bool:
    parsed = urlparse(origin)
    return parsed.scheme == "http" and bool(parsed.netloc) and parsed.netloc.lower() == host.lower()


def validate_write_origin(method: str, allow_writes: bool, origin: str | None, host: str | None) -> None:
    if method == "GET" or not allow_writes or not origin:
        return
    if not host or not _origin_matches_host(origin, host):
        raise CrossOriginWrite("cross-origin writes are blocked")


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
) -> DispatchResult:
    path = normalize_path(urlparse(request_path).path)
    if method == "GET":
        return DispatchResult(run_get(path), path)
    write_body = parse_write_body(raw_body, content_type)
    if log_write is not None:
        log_write(method, path, write_body)
    if not allow_writes:
        raise WritesDisabled("writes require --allow-writes")
    validate_write_origin(method, allow_writes, origin, host)
    # HTTP PATCH is a compatibility alias for the MOTU datastore POST write.
    return DispatchResult(run_post(path, write_body), path)


def log_write_attempt(method: str, path: str, body: str) -> None:
    print(f"write attempt method={method} path={path} body={body!r}", file=sys.stderr)


class DatastoreDispatcher:
    def __init__(
        self,
        allow_writes: bool,
        run_get: DatastoreRead,
        run_post: DatastoreWrite,
        log_write: WriteLogger | None = log_write_attempt,
        lock: threading.Lock | None = None,
    ) -> None:
        self.allow_writes = allow_writes
        self.run_get = run_get
        self.run_post = run_post
        self.log_write = log_write
        self.lock = lock if lock is not None else threading.Lock()

    def dispatch(
        self,
        method: str,
        request_path: str,
        raw_body: str = "",
        content_type: str = "",
        origin: str | None = None,
        host: str | None = None,
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
            )
            body = extract_json_bytes(result.response) or result.response
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (WritesDisabled, CrossOriginWrite) as exc:
            self.send_error(403, str(exc))
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def read_raw_body(self) -> str:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length).decode("utf-8", errors="replace")


class MotuProxyServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address,
        allow_writes: bool,
        debug: bool,
        run_get: DatastoreRead,
        run_post: DatastoreWrite,
    ) -> None:
        super().__init__(server_address, MotuProxyHandler)
        self.allow_writes = allow_writes
        self.debug = debug
        self.dispatcher = DatastoreDispatcher(allow_writes, run_get, run_post)


def serve(server: MotuProxyServer) -> int:
    host, port = server.server_address[:2]
    print(f"listening on http://{host}:{port} writes={'on' if server.allow_writes else 'off'}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", file=sys.stderr)
    finally:
        server.server_close()
    return 0
