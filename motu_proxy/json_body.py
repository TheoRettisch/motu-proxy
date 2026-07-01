"""Validation helpers for datastore JSON write bodies."""

from __future__ import annotations

import json
from typing import Any


class InvalidJsonBody(RuntimeError):
    pass


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def load_json_object(body: str) -> dict[str, Any]:
    try:
        value = json.loads(body, parse_constant=_reject_json_constant)
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidJsonBody(f"write body must be valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InvalidJsonBody("write body must be a JSON object")
    return value


def validate_json_body(body: str) -> None:
    load_json_object(body)
