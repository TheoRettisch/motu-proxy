"""Validation helpers for datastore JSON write bodies."""

from __future__ import annotations

import json


class InvalidJsonBody(RuntimeError):
    pass


def validate_json_body(body: str) -> None:
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidJsonBody(f"write body must be valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise InvalidJsonBody("write body must be a JSON object")
