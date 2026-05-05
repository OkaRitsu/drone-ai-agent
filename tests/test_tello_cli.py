"""Unit tests for TELLO terminal command handling."""

import socket
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from main import (
    _save_get_frame_log_base64,
    config_from_args,
    execute_command,
    parse_args,
    run_interactive,
)
from src.app.dashboard import PLOT_GROUPS, RerunDashboard, build_video_stream_url
from src.infra.flight_logging import STATE_CSV_FIELDS, FlightLogConfig, FlightLogger
from src.infra.tello.commands import build_sdk_command
from src.infra.tello.protocol import decode_response
from src.infra.tello.state import parse_state_payload


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
        dashboard = RerunDashboard(
            video_port=11111, state_provider=lambda: (None, None)
        )

        with mock.patch("src.app.dashboard.importlib.import_module") as import_module:
            import_module.side_effect = [
                object(),
                ModuleNotFoundError("No module named 'rerun'"),
            ]
            with self.assertRaises(RuntimeError):
                dashboard.start()

    def test_start_and_stop(self) -> None:
        dashboard = RerunDashboard(
            video_port=11111, state_provider=lambda: (None, None)
        )

        cv2_mock = mock.Mock()
        rr_mock = mock.Mock()

        with mock.patch("src.app.dashboard.importlib.import_module") as import_module:
            import_module.side_effect = [cv2_mock, rr_mock]
            with mock.patch("src.app.dashboard.threading.Thread") as thread_cls:
                video_thread = mock.Mock()
                state_thread = mock.Mock()
                thread_cls.side_effect = [video_thread, state_thread]
                dashboard.start()
                dashboard.stop()

        rr_mock.init.assert_called_once()
        video_thread.start.assert_called_once()
        state_thread.start.assert_called_once()

    def test_apply_opencv_log_level(self) -> None:
        dashboard = RerunDashboard(
            video_port=11111, state_provider=lambda: (None, None)
        )
        cv2_mock = mock.Mock()
        logging_mock = mock.Mock()
        cv2_mock.utils.logging = logging_mock
        logging_mock.LOG_LEVEL_SILENT = 0
        dashboard._cv2 = cv2_mock
        dashboard._apply_opencv_log_level()
        logging_mock.setLogLevel.assert_called_once_with(0)

    def test_log_scalar_with_scalars_fallback(self) -> None:
        dashboard = RerunDashboard(
            video_port=11111, state_provider=lambda: (None, None)
        )
        rr_mock = mock.Mock()
        rr_mock.Scalar = None
        del rr_mock.Scalar
        dashboard._rr = rr_mock
        dashboard._log_scalar("drone/state/bat", 90.0)
        rr_mock.Scalars.assert_called_once_with([90.0])
        rr_mock.log.assert_called_once()

    def test_set_time_now_uses_set_time(self) -> None:
        dashboard = RerunDashboard(
            video_port=11111, state_provider=lambda: (None, None)
        )
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
            self.assertIn("raw", latest_state)
            self.assertIn("bat", latest_state)
            for field in STATE_CSV_FIELDS:
                self.assertIn(field, latest_state)
            video.start_recording.assert_called()
            video.stop_recording.assert_called()


class InteractiveLoggingTest(unittest.TestCase):
    """Tests for interactive logging behavior on command timeouts."""

    def test_takeoff_timeout_does_not_abort_log_session(self) -> None:
        transport = mock.Mock()
        transport.send_command.side_effect = socket.timeout()

        flight_logger = mock.Mock()
        flight_logger.session_active.side_effect = [False, True]
        flight_logger.start_session.return_value = Path("logs/20260426_000000")

        with mock.patch("builtins.input", side_effect=["takeoff", "quit"]):
            run_interactive(transport, flight_logger)

        flight_logger.start_session.assert_called_once()
        flight_logger.log_command.assert_called_once_with(
            command="takeoff",
            response="timeout (reconnect timeout)",
            status="error",
        )
        flight_logger.stop_session.assert_not_called()


class KeepaliveControlTest(unittest.TestCase):
    """Tests for keepalive lifecycle integrated with command execution."""

    def test_takeoff_ok_starts_keepalive(self) -> None:
        transport = mock.Mock()
        transport.send_command.return_value = "ok"

        flight_logger = mock.Mock()
        flight_logger.session_active.side_effect = [False, True]
        flight_logger.start_session.return_value = Path("logs/20260426_000000")

        keepalive = mock.Mock()
        execute_command(
            transport=transport,
            flight_logger=flight_logger,
            command="takeoff",
            keepalive=keepalive,
        )

        keepalive.mark_activity.assert_called_once()
        keepalive.start.assert_called_once()
        keepalive.stop.assert_not_called()

    def test_land_ok_stops_keepalive_even_without_active_log_session(self) -> None:
        transport = mock.Mock()
        transport.send_command.return_value = "ok"

        flight_logger = mock.Mock()
        flight_logger.session_active.side_effect = [False, False]
        keepalive = mock.Mock()

        execute_command(
            transport=transport,
            flight_logger=flight_logger,
            command="land",
            keepalive=keepalive,
        )

        keepalive.mark_activity.assert_called_once()
        keepalive.stop.assert_called_once()
        keepalive.start.assert_not_called()

    def test_reconnect_command_reinitializes_sdk_session(self) -> None:
        transport = mock.Mock()
        transport.send_command.side_effect = ["ok", "ok", "ok"]

        flight_logger = mock.Mock()
        keepalive = mock.Mock()

        result = execute_command(
            transport=transport,
            flight_logger=flight_logger,
            command="reconnect",
            keepalive=keepalive,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["response"], "reconnected")
        keepalive.stop.assert_called_once()
        self.assertEqual(
            transport.send_command.call_args_list,
            [mock.call("command"), mock.call("streamoff"), mock.call("streamon")],
        )

    def test_timeout_auto_recovers_with_reconnect_then_retries(self) -> None:
        transport = mock.Mock()
        transport.send_command.side_effect = [
            socket.timeout(),
            "ok",
            "ok",
            "ok",
            "ok",
        ]

        flight_logger = mock.Mock()
        flight_logger.session_active.return_value = False
        keepalive = mock.Mock()

        result = execute_command(
            transport=transport,
            flight_logger=flight_logger,
            command="battery?",
            keepalive=keepalive,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["response"], "ok")
        self.assertEqual(
            transport.send_command.call_args_list,
            [
                mock.call("battery?"),
                mock.call("command"),
                mock.call("streamoff"),
                mock.call("streamon"),
                mock.call("battery?"),
            ],
        )


class TelloCliEntrypointTest(unittest.TestCase):
    """Tests for application entrypoint argument handling."""

    def test_config_from_args_maps_shared_options(self) -> None:
        args = parse_args(
            [
                "--host",
                "127.0.0.1",
                "--port",
                "9999",
                "--local-port",
                "9998",
                "--state-port",
                "9997",
                "--video-port",
                "9996",
                "--log-dir",
                "tmp-logs",
                "--max-log-sessions",
                "3",
                "--state-sample-interval",
                "0.1",
                "--dashboard-state-interval",
                "0.05",
                "--no-dashboard",
                "--mcp-http",
                "--mcp-host",
                "0.0.0.0",
                "--mcp-port",
                "18000",
                "--mcp-path",
                "/drone-mcp",
                "--keepalive-interval",
                "9.5",
                "--keepalive-command",
                "time?",
            ]
        )

        config = config_from_args(args)

        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.command_port, 9999)
        self.assertEqual(config.local_command_port, 9998)
        self.assertEqual(config.state_port, 9997)
        self.assertEqual(config.video_port, 9996)
        self.assertEqual(config.log_dir, Path("tmp-logs"))
        self.assertEqual(config.max_log_sessions, 3)
        self.assertEqual(config.state_sample_interval_sec, 0.1)
        self.assertEqual(config.dashboard_state_interval_sec, 0.05)
        self.assertFalse(config.dashboard_enabled)
        self.assertTrue(config.mcp_http_enabled)
        self.assertEqual(config.mcp_host, "0.0.0.0")
        self.assertEqual(config.mcp_port, 18000)
        self.assertEqual(config.mcp_path, "/drone-mcp")
        self.assertEqual(config.keepalive_interval_sec, 9.5)
        self.assertEqual(config.keepalive_command, "time?")


class GetFrameLogSaveTest(unittest.TestCase):
    """Tests for get_frame logging helper in main runtime."""

    def test_save_get_frame_log_base64_writes_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = _save_get_frame_log_base64(Path(tmpdir), "aGVsbG8=")
            self.assertTrue(output.exists())
            self.assertEqual(output.suffix, ".jpg")
            self.assertEqual(output.read_bytes(), b"hello")


if __name__ == "__main__":
    unittest.main()
