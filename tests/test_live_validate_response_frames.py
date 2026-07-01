from unittest import TestCase

from tools.live_validate_response_frames import (
    validate_response_packet,
    validate_response_sequence,
)

from tests.helpers import response_packet


class LiveResponseFrameValidationTests(TestCase):
    def test_rejects_unexpected_wrapper_type(self) -> None:
        packet = bytearray(response_packet(b"{}"))
        packet[1] = 0x99
        packet[-3] = 0x99

        check = validate_response_packet("/datastore", 1, bytes(packet), expected_message_seq=2)

        self.assertFalse(check.ok)
        self.assertIn("unexpected response wrapper type 0x99", check.errors)

    def test_rejects_invalid_short_wrapper_without_crashing(self) -> None:
        packet = bytes([0x40, 0x00, 0x04, 0x00]) + (b"x" * 24)

        check = validate_response_packet("/datastore", 1, packet, expected_message_seq=2)

        self.assertFalse(check.ok)
        self.assertIn("invalid response wrapper length 4", check.errors)
        self.assertIn("response body too short for MOTU fields: 0 bytes", check.errors)

    def test_rejects_segment_index_gap(self) -> None:
        check = validate_response_packet(
            "/datastore",
            1,
            response_packet(b"{}", segment_index=7),
            expected_message_seq=2,
        )

        validate_response_sequence([check])

        self.assertFalse(check.ok)
        self.assertIn("segment index 7 != expected 0", check.errors)

    def test_rejects_missing_final_segment_flag(self) -> None:
        check = validate_response_packet(
            "/datastore",
            1,
            response_packet(b"{}", final=False),
            expected_message_seq=2,
        )

        validate_response_sequence([check])

        self.assertFalse(check.ok)
        self.assertIn("missing final segment flag", check.errors)

    def test_rejects_final_flag_before_last_segment(self) -> None:
        first = validate_response_packet(
            "/datastore",
            1,
            response_packet(b"first", final=True, segment_index=0, wrapper_seq=0x40),
            expected_message_seq=2,
        )
        second = validate_response_packet(
            "/datastore",
            2,
            response_packet(b"second", final=True, segment_index=1, wrapper_seq=0x41),
            expected_message_seq=2,
        )

        validate_response_sequence([first, second])

        self.assertFalse(first.ok)
        self.assertIn("final flag set before last segment 0", first.errors)
