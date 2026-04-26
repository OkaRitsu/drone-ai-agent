"""Unit tests for TELLO terminal command handling."""

import unittest
from unittest import mock

from infra.tello.commands import build_sdk_command
from infra.tello.protocol import decode_response
from infra.tello_cli import VideoWindow, build_video_stream_url, build_video_viewer_command


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
        self.assertEqual(decode_response(b"ok\r\n"), "ok")

    def test_decode_non_utf8_response(self) -> None:
        """Returns hex text instead of raising decode errors."""
        self.assertEqual(
            decode_response(bytes([0xCC, 0x01])),
            "non-utf8-response:0xcc01",
        )


class VideoWindowTest(unittest.TestCase):
    """Tests for TELLO OpenCV video window launcher."""

    def test_build_video_stream_url(self) -> None:
        """Builds UDP URL with expected endpoint."""
        self.assertEqual(build_video_stream_url(11111), "udp://0.0.0.0:11111")

    def test_build_video_viewer_command(self) -> None:
        """Builds subprocess command for video viewer mode."""
        command = build_video_viewer_command(11111)
        self.assertIn("--video-viewer-only", command)
        self.assertEqual(command[-1], "11111")

    def test_start_raises_when_opencv_not_installed(self) -> None:
        """Raises error when OpenCV package is not available."""
        window = VideoWindow(video_port=11111)
        with mock.patch(
            "infra.tello_cli.importlib.import_module",
            side_effect=ModuleNotFoundError("No module named 'cv2'"),
        ):
            with self.assertRaises(RuntimeError):
                window.start()

    def test_start_and_stop_with_mocked_subprocess(self) -> None:
        """Starts and stops spawned viewer process."""
        window = VideoWindow(video_port=11111)
        process = mock.Mock()
        process.poll.return_value = None

        with mock.patch("infra.tello_cli.importlib.import_module", return_value=object()):
            with mock.patch("infra.tello_cli.subprocess.Popen", return_value=process):
                window.start()
                window.stop()

        process.terminate.assert_called_once()
        process.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
