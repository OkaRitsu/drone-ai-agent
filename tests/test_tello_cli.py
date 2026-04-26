"""Unit tests for TELLO terminal command handling."""

from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from infra.flight_logging import FlightLogConfig, FlightLogger
from infra.tello.commands import build_sdk_command
from infra.tello.protocol import decode_response
from infra.tello.state import parse_state_payload
from infra.tello_cli import VideoWindow, build_video_stream_url, build_video_worker_command


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


class StateParseTest(unittest.TestCase):
    """Tests for TELLO state payload parsing."""

    def test_parse_state_payload(self) -> None:
        """Parses state string and converts numbers."""
        state = parse_state_payload("pitch:1;roll:-2;bat:93;baro:10.53;time:7;")
        self.assertEqual(state["pitch"], 1)
        self.assertEqual(state["roll"], -2)
        self.assertEqual(state["bat"], 93)
        self.assertEqual(state["baro"], 10.53)


class VideoWindowTest(unittest.TestCase):
    """Tests for TELLO OpenCV video worker launcher."""

    def test_build_video_stream_url(self) -> None:
        """Builds UDP URL with expected endpoint."""
        self.assertEqual(build_video_stream_url(11111), "udp://0.0.0.0:11111")

    def test_build_video_worker_command(self) -> None:
        """Builds subprocess command for video worker mode."""
        command = build_video_worker_command(11111, 19000, enable_display=False)
        self.assertIn("--video-worker-only", command)
        self.assertIn("--control-port", command)
        self.assertIn("19000", command)
        self.assertIn("--disable-display", command)

    def test_start_raises_when_opencv_not_installed(self) -> None:
        """Raises error when OpenCV package is not available."""
        with mock.patch("infra.tello_cli._reserve_local_udp_port", return_value=19000):
            window = VideoWindow(video_port=11111)
            with mock.patch(
                "infra.tello_cli.importlib.import_module",
                side_effect=ModuleNotFoundError("No module named 'cv2'"),
            ):
                with self.assertRaises(RuntimeError):
                    window.start()
            window.stop()

    def test_start_and_stop_with_mocked_subprocess(self) -> None:
        """Starts and stops spawned worker process."""
        with mock.patch("infra.tello_cli._reserve_local_udp_port", return_value=19000):
            window = VideoWindow(video_port=11111)
            process = mock.Mock()
            process.poll.return_value = None

            with mock.patch("infra.tello_cli.importlib.import_module", return_value=object()):
                with mock.patch("infra.tello_cli.subprocess.Popen", return_value=process):
                    window.start()
                    window.stop()

        process.terminate.assert_called_once()
        process.wait.assert_called_once()


class FlightLoggerTest(unittest.TestCase):
    """Tests for flight session log lifecycle."""

    def test_start_stop_and_retention(self) -> None:
        """Creates logs and prunes old session directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            video = mock.Mock()
            state_provider = mock.Mock(return_value=("bat:90;", {"bat": 90}))
            logger = FlightLogger(
                config=FlightLogConfig(
                    root_dir=root,
                    max_saved_sessions=1,
                    state_sample_interval_sec=0.01,
                ),
                state_provider=state_provider,
                video_recorder=video,
            )

            session_1 = logger.start_session()
            self.assertRegex(session_1.name, r"^\d{8}_\d{6}(_\d{2})?$")
            logger.log_command("takeoff", "ok")
            logger.stop_session()

            logger.start_session()
            logger.log_command("land", "ok")
            logger.stop_session()

            sessions = [p for p in root.iterdir() if p.is_dir()]
            self.assertEqual(len(sessions), 1)
            latest_commands = (sessions[0] / "commands.csv").read_text(encoding="utf-8")
            self.assertIn("land", latest_commands)
            latest_state = (sessions[0] / "state.csv").read_text(encoding="utf-8")
            self.assertIn("state_json", latest_state)
            video.start_recording.assert_called()
            video.stop_recording.assert_called()


if __name__ == "__main__":
    unittest.main()
