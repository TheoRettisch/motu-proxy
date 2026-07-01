from unittest import TestCase

from motu_proxy.parser import (
    ResponseFrameError,
    datastore_payload,
    extract_datastore_body,
    extract_json_bytes,
    extract_response_etag,
    is_device_ack,
    join_response_frames,
    parse_response_frame,
    response_status_code,
    response_to_text,
)
from motu_proxy.paths import normalize_path
from motu_proxy.protocol import sized_word, u32

from tests.helpers import response_packet


class ParserTests(TestCase):
    def test_device_ack_detection(self) -> None:
        self.assertTrue(is_device_ack(bytes.fromhex("21 00 08 00 21 00 08 00")))
        self.assertFalse(is_device_ack(b"not an ack"))

    def test_parse_response_frame_validates_and_strips_payload(self) -> None:
        frame = parse_response_frame(response_packet(b'{"value":"ok"}'), expected_message_seq=2)
        self.assertEqual(frame.payload, b'{"value":"ok"}')
        self.assertTrue(frame.final)
        self.assertEqual(frame.segment_index, 0)

    def test_join_response_frames_concatenates_segments(self) -> None:
        frames = [
            response_packet(b'{"first":true}', final=False, segment_index=0, wrapper_seq=0x40),
            response_packet(b'{"second":true}', final=True, segment_index=1, wrapper_seq=0x41),
        ]
        self.assertEqual(join_response_frames(frames, expected_message_seq=2), b'{"first":true}{"second":true}')

    def test_response_frame_allows_padding_after_logical_wrapper(self) -> None:
        frame = parse_response_frame(response_packet(b'{"value":"ok"}', padding=b"\x00\x00"), expected_message_seq=2)
        self.assertEqual(frame.payload, b'{"value":"ok"}')

    def test_response_frame_rejects_crc_mismatch(self) -> None:
        packet = bytearray(response_packet(b'{"value":"ok"}'))
        packet[8] ^= 0xFF
        with self.assertRaisesRegex(ResponseFrameError, "CRC mismatch"):
            parse_response_frame(bytes(packet), expected_message_seq=2)

    def test_response_frame_rejects_message_sequence_mismatch(self) -> None:
        with self.assertRaisesRegex(ResponseFrameError, "message sequence"):
            parse_response_frame(response_packet(b'{"value":"ok"}', message_seq=3), expected_message_seq=2)

    def test_response_frame_rejects_bad_trailer(self) -> None:
        packet = bytearray(response_packet(b'{"value":"ok"}'))
        packet[-1] ^= 0xFF
        with self.assertRaisesRegex(ResponseFrameError, "trailer"):
            parse_response_frame(bytes(packet), expected_message_seq=2)

    def test_join_response_frames_rejects_segment_gap(self) -> None:
        frames = [
            response_packet(b"first", final=False, segment_index=0, wrapper_seq=0x40),
            response_packet(b"second", final=True, segment_index=2, wrapper_seq=0x41),
        ]
        with self.assertRaisesRegex(ResponseFrameError, "segment index"):
            join_response_frames(frames, expected_message_seq=2)

    def test_join_response_frames_rejects_final_flag_before_last_segment(self) -> None:
        frames = [
            response_packet(b"first", final=True, segment_index=0, wrapper_seq=0x40),
            response_packet(b"second", final=True, segment_index=1, wrapper_seq=0x41),
        ]
        with self.assertRaisesRegex(ResponseFrameError, "final flag"):
            join_response_frames(frames, expected_message_seq=2)

    def test_join_response_frames_requires_final_segment(self) -> None:
        with self.assertRaisesRegex(ResponseFrameError, "final segment"):
            join_response_frames([response_packet(b"first", final=False)], expected_message_seq=2)

    def test_extract_json_bytes_uses_first_object(self) -> None:
        self.assertEqual(extract_json_bytes(b"prefix {\"value\":\"ok\"} suffix"), b'{"value":"ok"}')

    def test_extract_json_bytes_ignores_trailing_binary_or_braces(self) -> None:
        self.assertEqual(extract_json_bytes(b'UTOM{"value":"ok"}@\x00h\x01}'), b'{"value":"ok"}')

    def test_extract_json_bytes_handles_braces_inside_strings(self) -> None:
        self.assertEqual(extract_json_bytes(b'{"value":"{ok}"} trailer }'), b'{"value":"{ok}"}')

    def test_response_to_text_can_pretty_print(self) -> None:
        self.assertIn('"value": "ok"', response_to_text(b'{"value":"ok"}'))

    def test_extract_response_etag_from_sized_header_words(self) -> None:
        metadata = (
            u32(200)
            + u32(3)
            + sized_word("Access-Control-Expose-Headers")
            + sized_word("ETag")
            + sized_word("Cache-Control")
            + sized_word("no-cache")
            + sized_word("ETag")
            + sized_word("5678")
        )
        payload = (
            b"UTOM"
            + u32(8)
            + u32(1)
            + u32(0)
            + u32(len(metadata))
            + metadata
            + b'{"value":"ok"}'
        )
        response = join_response_frames([response_packet(payload)], expected_message_seq=2)
        self.assertEqual(extract_response_etag(response), "5678")

    def test_extract_response_etag_from_http_header_text(self) -> None:
        response = b"HTTP/1.1 200 OK\r\nETag: 5678\r\nCache-Control: no-cache\r\n\r\n{}"
        self.assertEqual(extract_response_etag(response), "5678")

    def test_extract_response_etag_ignores_text_body_lines(self) -> None:
        response = b"HTTP/1.1 200 OK\r\nCache-Control: no-cache\r\n\r\nETag: body-value\n{}"
        self.assertIsNone(extract_response_etag(response))

    def test_extract_response_etag_ignores_non_http_text(self) -> None:
        self.assertIsNone(extract_response_etag(b"ETag: body-value\n{}"))

    def test_datastore_payload_keeps_json_body_and_etag(self) -> None:
        payload = datastore_payload(b"HTTP/1.1 200 OK\r\nETag: 5678\r\n\r\n{\"value\":\"ok\"}")
        self.assertEqual(payload.body, b'{"value":"ok"}')
        self.assertEqual(payload.etag, "5678")

    def test_datastore_payload_marks_text_304_not_modified(self) -> None:
        payload = datastore_payload(b"HTTP/1.1 304 Not Modified\r\nETag: 5678\r\n\r\n")
        self.assertTrue(payload.not_modified)
        self.assertEqual(payload.etag, "5678")
        self.assertEqual(payload.body, b"")

    def test_response_status_code_reads_utom_metadata_status(self) -> None:
        metadata = u32(304) + u32(1) + sized_word("ETag") + sized_word("5678")
        response = b"UTOM" + u32(8) + u32(1) + u32(0) + u32(len(metadata)) + metadata
        self.assertEqual(response_status_code(response), 304)

    def test_datastore_payload_does_not_truncate_raw_concatenated_json(self) -> None:
        response = b'{"first":true}{"second":true}'
        payload = datastore_payload(response)
        self.assertEqual(payload.body, response)

    def test_extract_datastore_body_strips_only_recognized_utom_envelope(self) -> None:
        metadata = u32(200) + u32(1) + sized_word("ETag") + sized_word("5678")
        response = b"UTOM" + u32(8) + u32(1) + u32(0) + u32(len(metadata)) + metadata + b'{"value":"ok"}'
        self.assertEqual(extract_datastore_body(response), b'{"value":"ok"}')


class PathTests(TestCase):
    def test_datastore_path_passes_through(self) -> None:
        self.assertEqual(normalize_path("/datastore/uid"), "/datastore/uid")

    def test_meters_path_passes_through(self) -> None:
        self.assertEqual(normalize_path("/meters"), "/meters")

    def test_meters_and_datastore_normalization_are_independent(self) -> None:
        self.assertEqual(normalize_path("/meters"), "/meters")
        self.assertEqual(normalize_path("/uid"), "/datastore/uid")
        self.assertEqual(normalize_path("/datastore/meters"), "/datastore/meters")

    def test_datastore_prefix_must_be_complete_path_segment(self) -> None:
        self.assertEqual(normalize_path("/datastore-foo"), "/datastore/datastore-foo")

    def test_bare_path_gets_datastore_prefix(self) -> None:
        self.assertEqual(normalize_path("/uid"), "/datastore/uid")

    def test_uid_prefix_is_removed(self) -> None:
        self.assertEqual(normalize_path("/0001f2fffe00c719/datastore/uid"), "/datastore/uid")

    def test_root_maps_to_datastore(self) -> None:
        self.assertEqual(normalize_path("/"), "/datastore")

    def test_full_url_uses_path(self) -> None:
        self.assertEqual(normalize_path("http://127.0.0.1:1280/uid"), "/datastore/uid")
