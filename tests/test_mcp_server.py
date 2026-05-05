from __future__ import annotations

"""Unit tests for MCP server helpers."""

import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.mcp.server import (
    TelloMcpRuntime,
    _append_mcp_io_log,
    _list_take_a_photo_paths,
    _save_take_a_photo_bytes,
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


class TakeAPhotoLogTest(unittest.TestCase):
    """Tests for take_a_photo image logging helper."""

    def test_save_take_a_photo_bytes_writes_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = _save_take_a_photo_bytes(Path(tmpdir), b"jpeg-bytes")
            self.assertTrue(output.exists())
            self.assertEqual(output.suffix, ".jpg")
            self.assertEqual(output.read_bytes(), b"jpeg-bytes")
            self.assertEqual(output.parent.name, "take_a_photo")

    def test_list_take_a_photo_paths_returns_sorted_jpg_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            photo_dir = Path(tmpdir) / "take_a_photo"
            photo_dir.mkdir(parents=True, exist_ok=True)
            second = photo_dir / "b.jpg"
            first = photo_dir / "a.jpg"
            ignored = photo_dir / "note.txt"
            second.write_bytes(b"2")
            first.write_bytes(b"1")
            ignored.write_text("x", encoding="utf-8")

            photos = _list_take_a_photo_paths(Path(tmpdir))
            self.assertEqual(photos, [str(first.resolve()), str(second.resolve())])


class McpIoLogTest(unittest.TestCase):
    """Tests for MCP request/response logging helper."""

    def test_append_mcp_io_log_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            _append_mcp_io_log(
                log_root=Path(tmpdir),
                tool_name="send_command",
                request={"command": "takeoff"},
                response={"status": "ok"},
                status="ok",
            )

            log_path = Path(tmpdir) / "mcp_io" / "requests_and_responses.jsonl"
            self.assertTrue(log_path.exists())
            payload = log_path.read_text(encoding="utf-8").strip()
            self.assertIn('"tool": "send_command"', payload)
            self.assertIn('"command": "takeoff"', payload)
            self.assertIn('"status": "ok"', payload)


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


class McpRuntimeReconnectTest(unittest.TestCase):
    """Tests for TELLO MCP runtime reconnect behavior."""

    def test_send_command_reconnect_calls_command_and_streamon(self) -> None:
        runtime = TelloMcpRuntime.__new__(TelloMcpRuntime)
        runtime._transport = mock.Mock()
        runtime._transport.send_command.side_effect = ["ok", "ok", "ok"]
        runtime._keepalive = mock.Mock()
        runtime._flight_logger = mock.Mock()

        result = runtime.send_command("reconnect")

        self.assertEqual(result["status"], "ok")
        runtime._keepalive.stop.assert_called_once()
        self.assertEqual(
            runtime._transport.send_command.call_args_list,
            [mock.call("command"), mock.call("streamoff"), mock.call("streamon")],
        )


@unittest.skipUnless(
    importlib.util.find_spec("fastmcp") is not None,
    "fastmcp is not installed",
)
class McpFrameToolTest(unittest.TestCase):
    """Tests for MCP camera tools response types."""

    def test_take_a_photo_returns_saved_filename_when_file_exists(self) -> None:
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

            def take_a_photo(self) -> Path:
                return image_path

            def load_image(self, filename: str) -> Path | dict[str, str | bool]:
                if filename == image_path.name:
                    return image_path
                return {
                    "ok": False,
                    "error": "image_not_found",
                    "message": f"Image not found: {filename}",
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
            tool = await app.get_tool("take_a_photo")
            result = tool.fn()
            self.assertIsInstance(result, dict)
            self.assertEqual(result["ok"], True)
            self.assertEqual(result["filename"], "frame.jpg")
            self.assertEqual(result["path"], str(image_path))

        asyncio.run(run_test())

    def test_take_a_photo_returns_error_payload_when_unavailable(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)

        class RuntimeStub:
            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def get_state(self) -> dict[str, str]:
                return {}

            def take_a_photo(self) -> dict[str, str | bool]:
                return {
                    "ok": False,
                    "error": "frame_unavailable",
                    "message": "No frame has been received yet.",
                }

            def load_image(self, filename: str) -> dict[str, str | bool]:
                return {
                    "ok": False,
                    "error": "image_not_found",
                    "message": f"Image not found: {filename}",
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
            tool = await app.get_tool("take_a_photo")
            result = tool.fn()
            self.assertIsInstance(result, dict)
            self.assertEqual(result["ok"], False)
            self.assertEqual(result["error"], "frame_unavailable")

        asyncio.run(run_test())

    def test_load_image_returns_fastmcp_image_when_found(self) -> None:
        from fastmcp.utilities.types import Image

        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        image_path = Path(tmpdir.name) / "snapshot.jpg"
        image_path.write_bytes(b"jpeg-bytes")

        class RuntimeStub:
            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

            def get_state(self) -> dict[str, str]:
                return {}

            def take_a_photo(self) -> Path:
                return image_path

            def load_image(self, filename: str) -> Path | dict[str, str]:
                if filename == image_path.name:
                    return image_path
                return {"ok": False, "error": "image_not_found"}

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
            tool = await app.get_tool("load_image")
            result = tool.fn(filename=image_path.name)
            self.assertIsInstance(result, Image)
            image_content = result.to_image_content()
            self.assertEqual(image_content.mimeType, "image/jpeg")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
