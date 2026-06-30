"""Response helpers for the MOTU datastore protocol."""

from __future__ import annotations

import json


def is_device_ack(packet: bytes) -> bool:
    return len(packet) == 8 and packet[:4] == packet[4:] and packet[1] == 0 and packet[2:4] == b"\x08\x00"


def join_response_frames(frames: list[bytes]) -> bytes:
    pieces: list[bytes] = []
    for frame in frames:
        if frame.startswith((b"NREK", b"PTTH")) and len(frame) >= 20:
            pieces.append(frame[20:])
        else:
            pieces.append(frame)
    return b"".join(pieces)


def extract_json_bytes(response: bytes) -> bytes | None:
    start = response.find(b"{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(response)):
        byte = response[index]
        if in_string:
            if escape:
                escape = False
            elif byte == ord("\\"):
                escape = True
            elif byte == ord('"'):
                in_string = False
            continue
        if byte == ord('"'):
            in_string = True
        elif byte == ord("{"):
            depth += 1
        elif byte == ord("}"):
            depth -= 1
            if depth == 0:
                return response[start : index + 1]
    return None


def response_to_text(response: bytes, pretty: bool = True) -> str:
    json_bytes = extract_json_bytes(response)
    if json_bytes is not None:
        text = json_bytes.decode("utf-8", errors="replace")
        if pretty:
            try:
                return json.dumps(json.loads(text), indent=2, sort_keys=True)
            except json.JSONDecodeError:
                return text
        return text
    return response.decode("utf-8", errors="replace")
