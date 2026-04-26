"""Unit tests for TELLO terminal command handling."""

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.dashboard import PLOT_GROUPS, RerunDashboard, build_video_stream_url
from infra.flight_logging import FlightLogConfig, FlightLogger
from infra.tello.commands import build_sdk_command
from infra.tello.protocol import decode_response
from infra.tello.state import parse_state_payload


class BuildSdkCommandTest(unittest.TestCase):
    """Tests for CLI command to TELLO SDK command conversion."""

    def test_simple_command(self) -> None:
        self.assertEqual(build_sdk_command("takeoff"), "takeoff")

    def test_move_command_with_distance(self) -> None:
        self.assertEqual(build_sdk_command("forward 30"), "forward 30")

    def test_rotation_command_with_degree(self) -> None:
        self.assertEqual(build_sdk_command("cw 90"), "cw 90")

    def test_rejects_out_of_range_distance(self) -> None:
        with self.assertRaises(ValueError):
            build_sdk_command("forward 10")

    def test_rejects_unsupported_command(self) -> None:
        with self.assertRaises(ValueError):
            build_sdk_command("dance")

    def test_raw_command_passthrough(self) -> None:
        self.assertEqual(build_sdk_command("raw mon"), "mon")


class DecodeResponseTest(unittest.TestCase):
    """Tests for response byte decoding."""

    def test_decode_utf8_response(self) -> None:
        self.assertEqual(decode_response(b"ok\r\n"), "ok")

    def test_decode_non_utf8_response(self) -> None:
        self.assertEqual(
            decode_response(bytes([0xCC, 0x01])),
            "non-utf8-response:0xcc01",
        )


class StateParseTest(unittest.TestCase):
    """Tests for TELLO state payload parsing."""

    def test_parse_state_payload(self) -> None:
        state = parse_state_payload("pitch:1;roll:-2;bat:93;baro:10.53;time:7;")
        self.assertEqual(state["pitch"], 1)
        self.assertEqual(state["roll"], -2)
        self.assertEqual(state["bat"], 93)
        self.assertEqual(state["baro"], 10.53)

    def test_parse_state_payload_normalizes_keys(self) -> None:
        state = parse_state_payload("\x00bat:61;  TOF : 10;")
        self.assertEqual(state["bat"], 61)
        self.assertEqual(state["tof"], 10)


class DashboardTest(unittest.TestCase):
    """Tests for Rerun dashboard helpers."""

    def test_build_video_stream_url(self) -> None:
        self.assertEqual(build_video_stream_url(11111), "udp://0.0.0.0:11111")

    def test_plot_groups_definition(self) -> None:
        self.assertEqual(PLOT_GROUPS["battery_percent"], ["bat"])
        self.assertIn("yaw", PLOT_GROUPS["attitude_deg"])

    def test_start_raises_when_rerun_not_installed(self) -> None:
        dashboard = RerunDashboard(video_port=11111, state_provider=lambda: (None, None))

        with mock.patch("app.dashboard.importlib.import_module") as import_module:
            import_module.side_effect = [object(), ModuleNotFoundError("No module named 'rerun'")]
            with self.assertRaises(RuntimeError):
                dashboard.start()

    def test_start_and_stop(self) -> None:
        dashboard = RerunDashboard(video_port=11111, state_provider=lambda: (None, None))

        cv2_mock = mock.Mock()
        rr_mock = mock.Mock()

        with mock.patch("app.dashboard.importlib.import_module") as import_module:
            import_module.side_effect = [cv2_mock, rr_mock]
            with mock.patch("app.dashboard.threading.Thread") as thread_cls:
                video_thread = mock.Mock()
                state_thread = mock.Mock()
                thread_cls.side_effect = [video_thread, state_thread]
                dashboard.start()
                dashboard.stop()

        rr_mock.init.assert_called_once()
        video_thread.start.assert_called_once()
        state_thread.start.assert_called_once()

    def test_apply_opencv_log_level(self) -> None:
        dashboard = RerunDashboard(video_port=11111, state_provider=lambda: (None, None))
        cv2_mock = mock.Mock()
        logging_mock = mock.Mock()
        cv2_mock.utils.logging = logging_mock
        logging_mock.LOG_LEVEL_SILENT = 0
        dashboard._cv2 = cv2_mock
        dashboard._apply_opencv_log_level()
        logging_mock.setLogLevel.assert_called_once_with(0)

    def test_log_scalar_with_scalars_fallback(self) -> None:
        dashboard = RerunDashboard(video_port=11111, state_provider=lambda: (None, None))
        rr_mock = mock.Mock()
        rr_mock.Scalar = None
        del rr_mock.Scalar
        dashboard._rr = rr_mock
        dashboard._log_scalar("drone/state/bat", 90.0)
        rr_mock.Scalars.assert_called_once_with([90.0])
        rr_mock.log.assert_called_once()

    def test_set_time_now_uses_set_time(self) -> None:
        dashboard = RerunDashboard(video_port=11111, state_provider=lambda: (None, None))
        rr_mock = mock.Mock()
        dashboard._rr = rr_mock
        dashboard._set_time_now("time")
        rr_mock.set_time.assert_called_once()


class FlightLoggerTest(unittest.TestCase):
    """Tests for flight session log lifecycle."""

    def test_start_stop_and_retention(self) -> None:
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
