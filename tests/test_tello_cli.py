"""Unit tests for TELLO terminal command handling."""

import unittest

from main import _decode_response, build_sdk_command


class BuildSdkCommandTest(unittest.TestCase):
    """Tests for CLI command to TELLO SDK command conversion."""

    def test_simple_command(self) -> None:
        """Allows simple one word TELLO commands."""
        self.assertEqual(build_sdk_command("takeoff"), "takeoff")

    def test_move_command_with_distance(self) -> None:
        """Converts move command when distance is in valid range."""
        self.assertEqual(build_sdk_command("forward 30"), "forward 30")

    def test_rotation_command_with_degree(self) -> None:
        """Converts rotation command when degree is in valid range."""
        self.assertEqual(build_sdk_command("cw 90"), "cw 90")

    def test_rejects_out_of_range_distance(self) -> None:
        """Rejects move command when distance is too short."""
        with self.assertRaises(ValueError):
            build_sdk_command("forward 10")

    def test_rejects_unsupported_command(self) -> None:
        """Rejects command not included in parser support."""
        with self.assertRaises(ValueError):
            build_sdk_command("dance")

    def test_raw_command_passthrough(self) -> None:
        """Passes raw SDK command text after raw keyword."""
        self.assertEqual(build_sdk_command("raw mon"), "mon")


class DecodeResponseTest(unittest.TestCase):
    """Tests for response byte decoding."""

    def test_decode_utf8_response(self) -> None:
        """Decodes normal UTF-8/ASCII payload."""
        self.assertEqual(_decode_response(b"ok\r\n"), "ok")

    def test_decode_non_utf8_response(self) -> None:
        """Returns hex text instead of raising decode errors."""
        self.assertEqual(
            _decode_response(bytes([0xCC, 0x01])),
            "non-utf8-response:0xcc01",
        )


if __name__ == "__main__":
    unittest.main()
