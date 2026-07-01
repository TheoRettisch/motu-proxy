"""HTTP and CLI datastore path compatibility helpers."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

UID_PREFIX_RE = re.compile(r"^/[0-9a-fA-F]{16}(/.*)$")


def normalize_path(path: str) -> str:
    path = unquote(path.strip())
    if not path:
        return "/datastore"
    if path.startswith("http://") or path.startswith("https://"):
        parsed = urlparse(path)
        path = parsed.path
    path = UID_PREFIX_RE.sub(r"\1", path)
    if not path.startswith("/"):
        path = "/" + path
    if path == "/":
        return "/datastore"
    if path == "/datastore" or path.startswith("/datastore/") or path == "/apiversion":
        return path
    return "/datastore" + path
