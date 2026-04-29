from __future__ import annotations

"""Unit tests for MCP server helpers."""

import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest

from src.mcp.server import (
    _save_get_frame_log_bytes,
    _tail_csv,
    build_mcp_app,
    config_from_args,
    main,
    parse_args,
)


class TailCsvTest(unittest.TestCase):
    """Tests for CSV tail utility."""

    def test_tail_csv_returns_last_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "commands.csv"
            csv_path.write_text(
                "timestamp,command,status,response\n"
                "1,takeoff,ok,ok\n"
                "2,forward 30,ok,ok\n"
                "3,land,ok,ok\n",
                encoding="utf-8",
            )

            rows = _tail_csv(csv_path, limit=2)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["timestamp"], "2")
            self.assertEqual(rows[1]["command"], "land")

    def test_tail_csv_returns_empty_for_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            rows = _tail_csv(Path(tmpdir) / "missing.csv", limit=10)
            self.assertEqual(rows, [])


class GetFrameLogTest(unittest.TestCase):
    """Tests for get_frame image logging helper."""

    def test_save_get_frame_log_bytes_writes_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = _save_get_frame_log_bytes(Path(tmpdir), b"jpeg-bytes")
            self.assertTrue(output.exists())
            self.assertEqual(output.suffix, ".jpg")
            self.assertEqual(output.read_bytes(), b"jpeg-bytes")


class McpEntrypointTest(unittest.TestCase):
    """Tests for MCP server entrypoint argument handling."""

    def test_config_from_args_maps_options(self) -> None:
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
                "--keepalive-interval",
                "9.0",
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
        self.assertEqual(config.keepalive_interval_sec, 9.0)
        self.assertEqual(config.keepalive_command, "time?")

    def test_main_starts_stdio_server_with_config(self) -> None:
        from unittest import mock

        with mock.patch("src.mcp.server.run_stdio_server") as run_stdio_server:
            main(["--host", "127.0.0.1"])

        run_stdio_server.assert_called_once()
        self.assertEqual(run_stdio_server.call_args.args[0].host, "127.0.0.1")


@unittest.skipUnless(
    importlib.util.find_spec("fastmcp") is not None,
    "fastmcp is not installed",
)
class McpFrameToolTest(unittest.TestCase):
    """Tests for MCP get_frame tool response types."""

    def test_get_frame_returns_fastmcp_image_when_saved_file_exists(self) -> None:
        from fastmcp.utilities.types import Image
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        image_path = Path(tmpdir.name) / "frame.jpg"
        image_path.write_bytes(b"jpeg-bytes")

        class RuntimeStub:
            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def get_state(self) -> dict[str, str]:
                return {}

            def get_frame(self) -> Path:
                return image_path

            def send_command(self, command: str) -> dict[str, str]:
                return {"command": command}

            def get_flight_log(
                self,
                session_id: str | None = None,
                command_limit: int = 200,
                state_limit: int = 200,
            ) -> dict[str, str | int | None]:
                return {
                    "session_id": session_id,
                    "command_limit": command_limit,
                    "state_limit": state_limit,
                }

        async def run_test() -> None:
            app = build_mcp_app(RuntimeStub())
            tool = await app.get_tool("get_frame")
            result = tool.fn()
            self.assertIsInstance(result, Image)
            image_content = result.to_image_content()
            self.assertEqual(image_content.type, "image")
            self.assertEqual(image_content.mimeType, "image/jpeg")

        asyncio.run(run_test())

    def test_get_frame_returns_error_payload_when_unavailable(self) -> None:
        class RuntimeStub:
            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def get_state(self) -> dict[str, str]:
                return {}

            def get_frame(self) -> dict[str, str | bool]:
                return {
                    "ok": False,
                    "error": "frame_unavailable",
                    "message": "No frame has been received yet.",
                }

            def send_command(self, command: str) -> dict[str, str]:
                return {"command": command}

            def get_flight_log(
                self,
                session_id: str | None = None,
                command_limit: int = 200,
                state_limit: int = 200,
            ) -> dict[str, str | int | None]:
                return {
                    "session_id": session_id,
                    "command_limit": command_limit,
                    "state_limit": state_limit,
                }

        async def run_test() -> None:
            app = build_mcp_app(RuntimeStub())
            tool = await app.get_tool("get_frame")
            result = tool.fn()
            self.assertIsInstance(result, dict)
            self.assertEqual(result["ok"], False)
            self.assertEqual(result["error"], "frame_unavailable")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
