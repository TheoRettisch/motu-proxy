from unittest import TestCase

from motu_proxy.parser import extract_json_bytes, is_device_ack, join_response_frames, response_to_text
from motu_proxy.paths import normalize_path


class ParserTests(TestCase):
    def test_device_ack_detection(self) -> None:
        self.assertTrue(is_device_ack(bytes.fromhex("21 00 08 00 21 00 08 00")))
        self.assertFalse(is_device_ack(b"not an ack"))

    def test_join_response_frames_strips_motu_header(self) -> None:
        frame = b"NREK" + b"\x00" * 16 + b'{"value":"ok"}'
        self.assertEqual(join_response_frames([frame]), b'{"value":"ok"}')

    def test_extract_json_bytes_uses_first_object(self) -> None:
        self.assertEqual(extract_json_bytes(b"prefix {\"value\":\"ok\"} suffix"), b'{"value":"ok"}')

    def test_extract_json_bytes_ignores_trailing_binary_or_braces(self) -> None:
        self.assertEqual(extract_json_bytes(b'UTOM{"value":"ok"}@\x00h\x01}'), b'{"value":"ok"}')

    def test_extract_json_bytes_handles_braces_inside_strings(self) -> None:
        self.assertEqual(extract_json_bytes(b'{"value":"{ok}"} trailer }'), b'{"value":"{ok}"}')

    def test_response_to_text_can_pretty_print(self) -> None:
        self.assertIn('"value": "ok"', response_to_text(b'{"value":"ok"}'))


class PathTests(TestCase):
    def test_datastore_path_passes_through(self) -> None:
        self.assertEqual(normalize_path("/datastore/uid"), "/datastore/uid")

    def test_bare_path_gets_datastore_prefix(self) -> None:
        self.assertEqual(normalize_path("/uid"), "/datastore/uid")

    def test_uid_prefix_is_removed(self) -> None:
        self.assertEqual(normalize_path("/0001f2fffe00c719/datastore/uid"), "/datastore/uid")

    def test_root_maps_to_datastore(self) -> None:
        self.assertEqual(normalize_path("/"), "/datastore")

    def test_full_url_uses_path(self) -> None:
        self.assertEqual(normalize_path("http://127.0.0.1:1280/uid"), "/datastore/uid")
