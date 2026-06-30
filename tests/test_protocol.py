from unittest import TestCase

from motu_proxy.fixtures import EXPECTED_GET_DATASTORE, EXPECTED_POST_HOST_OS
from motu_proxy.protocol import HostSequencer, build_get_frame, build_post_frame, crc32


class ProtocolTests(TestCase):
    def test_crc32_known_vector(self) -> None:
        self.assertEqual(crc32(b"123456789"), 0xCBF43926)

    def test_get_frame_matches_fixture(self) -> None:
        self.assertEqual(build_get_frame(0x24, 2, "/datastore"), EXPECTED_GET_DATASTORE)

    def test_post_frame_matches_fixture(self) -> None:
        self.assertEqual(
            build_post_frame(0x23, 2, "/datastore/host/os", '{"value": "win"}', header="PTTH"),
            EXPECTED_POST_HOST_OS,
        )

    def test_host_sequence_rolls_over_to_0x20(self) -> None:
        sequencer = HostSequencer(0x3E)
        self.assertEqual([sequencer.take(), sequencer.take(), sequencer.take()], [0x3E, 0x3F, 0x20])
