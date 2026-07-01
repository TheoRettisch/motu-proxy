from unittest import TestCase

from motu_proxy.fixtures import EXPECTED_GET_DATASTORE, EXPECTED_POST_HOST_OS
from motu_proxy.protocol import (
    MAX_U16,
    HostSequencer,
    InvalidHostSequence,
    ProtocolFrameTooLarge,
    build_get_frame,
    build_post_frame,
    crc32,
    max_post_json_body_bytes,
    sized_word,
)


class ProtocolTests(TestCase):
    def test_crc32_known_vector(self) -> None:
        self.assertEqual(crc32(b"123456789"), 0xCBF43926)

    def test_get_frame_matches_fixture(self) -> None:
        self.assertEqual(build_get_frame(0x24, 2, "/datastore"), EXPECTED_GET_DATASTORE)

    def test_get_frame_can_forward_client_identifier(self) -> None:
        frame = build_get_frame(0x24, 2, "/datastore", client=1479701624)
        self.assertIn(sized_word("/datastore"), frame)
        self.assertIn(sized_word("client") + sized_word("1479701624"), frame)
        self.assertNotIn(b"/datastore?client", frame)

    def test_get_frame_can_forward_non_default_etag(self) -> None:
        frame = build_get_frame(0x24, 2, "/datastore", etag="5678")
        self.assertIn(sized_word("If-None-Match") + sized_word("5678"), frame)

    def test_post_frame_matches_fixture(self) -> None:
        self.assertEqual(
            build_post_frame(0x23, 2, "/datastore/host/os", '{"value": "win"}', header="PTTH"),
            EXPECTED_POST_HOST_OS,
        )

    def test_post_frame_can_forward_client_identifier(self) -> None:
        frame = build_post_frame(0x23, 2, "/datastore/host/os", '{"value":"linux"}', client=1479701624)
        self.assertIn(sized_word("/datastore/host/os"), frame)
        self.assertIn(sized_word("client") + sized_word("1479701624"), frame)
        self.assertNotIn(b"/datastore/host/os?client", frame)

    def test_post_frame_accepts_calculated_single_frame_limit(self) -> None:
        max_body = max_post_json_body_bytes("/datastore/host/os")
        frame = build_post_frame(0x23, 2, "/datastore/host/os", "x" * max_body)
        self.assertEqual(len(frame), MAX_U16)

    def test_post_frame_rejects_body_over_single_frame_limit(self) -> None:
        max_body = max_post_json_body_bytes("/datastore/host/os")
        with self.assertRaises(ProtocolFrameTooLarge):
            build_post_frame(0x23, 2, "/datastore/host/os", "x" * (max_body + 1))

    def test_host_sequence_rolls_over_to_0x20(self) -> None:
        sequencer = HostSequencer(0x3E)
        self.assertEqual([sequencer.take(), sequencer.take(), sequencer.take()], [0x3E, 0x3F, 0x20])

    def test_host_sequence_rejects_invalid_start(self) -> None:
        for seq_start in (0x1F, 0x40, 0x120):
            with (
                self.subTest(seq_start=seq_start),
                self.assertRaisesRegex(InvalidHostSequence, "0x20..0x3f"),
            ):
                HostSequencer(seq_start)
