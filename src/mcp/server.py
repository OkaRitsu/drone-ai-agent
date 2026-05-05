"""MCP server for TELLO control and telemetry."""

from __future__ import annotations

import argparse
import csv
import json
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Sequence

from src.infra.flight_logging import FlightLogConfig, FlightLogger
from src.infra.tello import (
    TelloConfig,
    TelloKeepalive,
    TelloStateReceiver,
    TelloTransport,
    TelloVideoHub,
    build_sdk_command,
    reconnect_sdk_session,
)


@dataclass(frozen=True)
class TelloMcpConfig:
    """Configuration for TELLO MCP runtime.

    Attributes:
        host: TELLO host IP.
        command_port: TELLO command UDP port.
        local_command_port: Local UDP bind port for SDK commands.
        state_port: Local UDP bind port for state telemetry.
        video_port: Local UDP port for TELLO video stream.
        log_dir: Root path for flight logs.
        max_log_sessions: Maximum number of log sessions to keep.
        state_sample_interval_sec: State sampling interval for flight logs.
        keepalive_interval_sec: Keepalive command interval while flying.
        keepalive_command: SDK command used for keepalive.
    """

    host: str = "192.168.10.1"
    command_port: int = 8889
    local_command_port: int = 9000
    state_port: int = 8890
    video_port: int = 11111
    log_dir: Path = Path("logs")
    max_log_sessions: int = 20
    state_sample_interval_sec: float = 0.5
    keepalive_interval_sec: float = 8.0
    keepalive_command: str = "battery?"


def build_parser() -> argparse.ArgumentParser:
    """Build command line parser for the MCP server.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(description="TELLO MCP stdio server")
    parser.add_argument(
        "--host",
        default="192.168.10.1",
        help="TELLO host IP (default: 192.168.10.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8889,
        help="TELLO command port (default: 8889)",
    )
    parser.add_argument(
        "--local-port",
        type=int,
        default=9000,
        help="local UDP bind port (default: 9000)",
    )
    parser.add_argument(
        "--state-port",
        type=int,
        default=8890,
        help="local UDP bind port for TELLO state (default: 8890)",
    )
    parser.add_argument(
        "--video-port",
        type=int,
        default=11111,
        help="local UDP port for TELLO video stream (default: 11111)",
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="root directory for flight logs (default: logs)",
    )
    parser.add_argument(
        "--max-log-sessions",
        type=int,
        default=20,
        help="max number of flight log directories to keep (default: 20)",
    )
    parser.add_argument(
        "--state-sample-interval",
        type=float,
        default=0.5,
        help="state sampling interval seconds while flying (default: 0.5)",
    )
    parser.add_argument(
        "--keepalive-interval",
        type=float,
        default=8.0,
        help="keepalive command interval seconds while flying (default: 8.0)",
    )
    parser.add_argument(
        "--keepalive-command",
        choices=["battery?", "time?"],
        default="battery?",
        help="SDK command used for keepalive (default: battery?)",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse MCP server command line options.

    Args:
        argv: Optional argument list. Defaults to ``sys.argv`` when None.

    Returns:
        Parsed arguments.
    """
    return build_parser().parse_args(argv)


def config_from_args(args: argparse.Namespace) -> TelloMcpConfig:
    """Build MCP runtime configuration from parsed arguments.

    Args:
        args: Parsed command line options.

    Returns:
        MCP server runtime configuration.
    """
    return TelloMcpConfig(
        host=args.host,
        command_port=args.port,
        local_command_port=args.local_port,
        state_port=args.state_port,
        video_port=args.video_port,
        log_dir=Path(args.log_dir),
        max_log_sessions=args.max_log_sessions,
        state_sample_interval_sec=args.state_sample_interval,
        keepalive_interval_sec=args.keepalive_interval,
        keepalive_command=args.keepalive_command,
    )


class TelloMcpRuntime:
    """Runtime container used by MCP tools."""

    def __init__(self, config: TelloMcpConfig) -> None:
        """Initialize runtime resources.

        Args:
            config: Runtime configuration values.
        """
        self._config = config
        self._transport = TelloTransport(
            TelloConfig(
                host=config.host,
                port=config.command_port,
                local_port=config.local_command_port,
            )
        )
        self._state_receiver = TelloStateReceiver(port=config.state_port)
        self._video_hub = TelloVideoHub(video_port=config.video_port)
        self._flight_logger = FlightLogger(
            config=FlightLogConfig(
                root_dir=config.log_dir,
                max_saved_sessions=config.max_log_sessions,
                state_sample_interval_sec=config.state_sample_interval_sec,
            ),
            state_provider=self._state_receiver.get_latest,
            video_recorder=self._video_hub,
        )
        self._keepalive = TelloKeepalive(
            sender=self._transport,
            interval_sec=config.keepalive_interval_sec,
            command=config.keepalive_command,
        )

    def start(self) -> None:
        """Start TELLO SDK mode, telemetry, and video receiver."""
        response = self._transport.send_command("command")
        if not response.lower().startswith("ok"):
            raise RuntimeError(f"Failed to enter SDK mode: {response}")

        stream_response = self._transport.send_command("streamon")
        if not stream_response.lower().startswith("ok"):
            raise RuntimeError(f"Failed to start TELLO stream: {stream_response}")

        self._state_receiver.start()
        self._video_hub.start()

    def stop(self) -> None:
        """Stop runtime resources."""
        if self._flight_logger.session_active():
            self._flight_logger.stop_session()

        self._video_hub.stop()
        self._state_receiver.stop()
        self._keepalive.stop()
        try:
            self._transport.send_command("streamoff")
        except socket.timeout:
            pass
        finally:
            self._transport.close()

    def get_state(self) -> dict[str, Any]:
        """Get latest TELLO state.

        Returns:
            Parsed and raw state payload.
        """
        raw, parsed = self._state_receiver.get_latest()
        return {
            "raw": raw,
            "state": parsed or {},
            "has_state": parsed is not None,
        }

    def take_a_photo(self) -> Path | dict[str, Any]:
        """Save latest camera frame as a JPEG file.

        Returns:
            Saved JPEG path, or an error payload when frame is unavailable.
        """
        jpeg_bytes = self._video_hub.get_latest_jpeg_bytes()
        if jpeg_bytes is None:
            return {
                "ok": False,
                "error": "frame_unavailable",
                "message": "No frame has been received yet.",
            }
        return _save_take_a_photo_bytes(self._config.log_dir, jpeg_bytes)

    def load_image(self, filename: str) -> Path | dict[str, Any]:
        """Load a photo saved by take_a_photo.

        Args:
            filename: File name under ``<log_dir>/take_a_photo``.

        Returns:
            Resolved image path or an error payload when unavailable.
        """
        if not filename:
            return {
                "ok": False,
                "error": "invalid_filename",
                "message": "filename must not be empty.",
            }

        image_dir = self._config.log_dir / "take_a_photo"
        candidate = (image_dir / filename).resolve()
        if candidate.parent != image_dir.resolve() or not candidate.exists():
            return {
                "ok": False,
                "error": "image_not_found",
                "message": f"Image not found: {filename}",
            }
        return candidate

    def send_command(self, command: str) -> dict[str, Any]:
        """Send a TELLO SDK command.

        Args:
            command: Human CLI command or raw SDK command.

        Returns:
            Command execution result.
        """
        if command.strip().lower() == "reconnect":
            self._keepalive.stop()
            ok, message = reconnect_sdk_session(self._transport)
            return {
                "command": command,
                "sdk_command": "reconnect",
                "status": "ok" if ok else "error",
                "response": message,
                "session_started": None,
                "session_saved": False,
            }

        sdk_command = build_sdk_command(command)
        session_started = None
        if sdk_command == "takeoff" and not self._flight_logger.session_active():
            session_started = str(self._flight_logger.start_session())

        self._keepalive.mark_activity()
        try:
            response = self._transport.send_command(sdk_command)
            status = "ok"
        except socket.timeout:
            ok, reconnect_message = reconnect_sdk_session(self._transport)
            if ok:
                try:
                    response = self._transport.send_command(sdk_command)
                    status = "ok"
                except socket.timeout:
                    response = "timeout"
                    status = "error"
            else:
                response = f"timeout ({reconnect_message})"
                status = "error"

        if self._flight_logger.session_active():
            self._flight_logger.log_command(
                command=sdk_command,
                response=response,
                status=status,
            )

        session_saved = False
        should_stop_keepalive = sdk_command in {
            "land",
            "emergency",
        } and response.lower().startswith("ok")
        if should_stop_keepalive and self._flight_logger.session_active():
            self._flight_logger.stop_session()
            session_saved = True
        if should_stop_keepalive:
            self._keepalive.stop()

        if sdk_command == "takeoff" and response.lower().startswith("ok"):
            self._keepalive.start()

        return {
            "command": command,
            "sdk_command": sdk_command,
            "status": status,
            "response": response,
            "session_started": session_started,
            "session_saved": session_saved,
        }

    def get_flight_log(
        self,
        session_id: str | None = None,
        command_limit: int = 200,
        state_limit: int = 200,
    ) -> dict[str, Any]:
        """Get one flight log session content.

        Args:
            session_id: Session directory name. Latest session is used when None.
            command_limit: Maximum command rows to return.
            state_limit: Maximum state rows to return.

        Returns:
            Session metadata and CSV rows.
        """
        session_dir = self._resolve_session_dir(session_id)
        if session_dir is None:
            return {
                "ok": False,
                "error": "log_not_found",
                "message": "No flight log session is available.",
            }

        metadata_path = session_dir / "metadata.json"
        commands_path = session_dir / "commands.csv"
        state_path = session_dir / "state.csv"

        metadata: dict[str, Any] = {}
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

        return {
            "ok": True,
            "session_id": session_dir.name,
            "session_dir": str(session_dir),
            "metadata": metadata,
            "commands": _tail_csv(commands_path, command_limit),
            "state": _tail_csv(state_path, state_limit),
            "photos": _list_take_a_photo_paths(self._config.log_dir),
        }

    def _resolve_session_dir(self, session_id: str | None) -> Path | None:
        """Resolve session directory by ID or latest.

        Args:
            session_id: Session directory name or None.

        Returns:
            Resolved path or None if no session exists.
        """
        root = self._config.log_dir
        if not root.exists():
            return None

        if session_id:
            session_dir = root / session_id
            return session_dir if session_dir.is_dir() else None

        sessions = sorted((path for path in root.iterdir() if path.is_dir()))
        if not sessions:
            return None
        return sessions[-1]


class McpRuntimeProtocol(Protocol):
    """Protocol for MCP tool runtimes."""

    def start(self) -> None:
        """Start runtime resources."""

    def stop(self) -> None:
        """Stop runtime resources."""

    def get_state(self) -> dict[str, Any]:
        """Get latest telemetry state."""

    def take_a_photo(self) -> Path | dict[str, Any]:
        """Save latest camera frame."""

    def load_image(self, filename: str) -> Path | dict[str, Any]:
        """Load one saved image."""

    def send_command(self, command: str) -> dict[str, Any]:
        """Send one drone command."""

    def get_flight_log(
        self,
        session_id: str | None = None,
        command_limit: int = 200,
        state_limit: int = 200,
    ) -> dict[str, Any]:
        """Get one flight log payload."""


def _tail_csv(path: Path, limit: int) -> list[dict[str, str]]:
    """Read the tail rows from CSV.

    Args:
        path: CSV file path.
        limit: Maximum row count.

    Returns:
        Tail rows. Empty list if file does not exist.
    """
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))
    if limit <= 0:
        return rows
    return rows[-limit:]


def _save_take_a_photo_bytes(log_root: Path, jpeg_bytes: bytes) -> Path:
    """Persist one take_a_photo response image as a log artifact.

    Args:
        log_root: Root log directory.
        jpeg_bytes: JPEG image bytes returned by take_a_photo.

    Returns:
        Saved image path.
    """
    save_dir = log_root / "take_a_photo"
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    output_path = save_dir / filename
    output_path.write_bytes(jpeg_bytes)
    return output_path


def _list_take_a_photo_paths(log_root: Path) -> list[str]:
    """List saved photo file paths under take_a_photo directory.

    Args:
        log_root: Root log directory.

    Returns:
        Sorted absolute image path list.
    """
    photo_dir = log_root / "take_a_photo"
    if not photo_dir.exists():
        return []
    return sorted(
        str(path.resolve())
        for path in photo_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".jpg"
    )


def _sanitize_for_log(value: Any) -> Any:
    """Convert runtime values into JSON serializable payloads.

    Args:
        value: Arbitrary runtime value.

    Returns:
        JSON-serializable representation.
    """
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _sanitize_for_log(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_for_log(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize_for_log(v) for v in value]
    return str(value)


def _append_mcp_io_log(
    log_root: Path,
    tool_name: str,
    request: dict[str, Any],
    response: Any,
    status: str,
) -> None:
    """Append one MCP request/response record.

    Args:
        log_root: MCP log root directory.
        tool_name: Invoked MCP tool name.
        request: Tool request payload.
        response: Tool response payload.
        status: Execution status string.
    """
    log_dir = log_root / "mcp_io"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "requests_and_responses.jsonl"
    record = {
        "timestamp": datetime.now().isoformat(timespec="milliseconds"),
        "tool": tool_name,
        "request": _sanitize_for_log(request),
        "response": _sanitize_for_log(response),
        "status": status,
    }
    with log_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def _resolve_runtime_log_root(runtime: Any) -> Path:
    """Resolve runtime log root for MCP request/response logging.

    Args:
        runtime: Runtime object used by MCP app.

    Returns:
        Resolved log root path.
    """
    config = getattr(runtime, "_config", None)
    if config is not None and hasattr(config, "log_dir"):
        return Path(config.log_dir)

    log_dir = getattr(runtime, "_log_dir", None)
    if log_dir is not None:
        return Path(log_dir)
    return Path("logs")


def build_mcp_app(runtime: McpRuntimeProtocol) -> Any:
    """Build FastMCP app bound to a runtime instance.

    Args:
        runtime: Runtime backend that implements MCP tool behavior.

    Returns:
        Configured FastMCP app instance.
    """
    try:
        from fastmcp import FastMCP
        from fastmcp.utilities.types import Image
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "fastmcp package is not installed. Run `uv sync` first."
        ) from error

    mcp = FastMCP("drone_ai_agent")
    log_root = _resolve_runtime_log_root(runtime)

    def _run_tool(tool_name: str, request: dict[str, Any], fn: Any) -> Any:
        """Execute MCP tool and persist request/response log."""
        try:
            response = fn()
            _append_mcp_io_log(
                log_root=log_root,
                tool_name=tool_name,
                request=request,
                response=response,
                status="ok",
            )
            return response
        except Exception as error:
            _append_mcp_io_log(
                log_root=log_root,
                tool_name=tool_name,
                request=request,
                response={"error": str(error)},
                status="error",
            )
            raise

    @mcp.tool()
    def get_state() -> dict[str, Any]:
        """Get current drone state telemetry."""
        return _run_tool("get_state", {}, runtime.get_state)

    @mcp.tool()
    def take_a_photo() -> Any:
        """Save the latest camera frame from drone as a JPEG image."""

        def _impl() -> Any:
            frame = runtime.take_a_photo()
            if isinstance(frame, Path):
                return {
                    "ok": True,
                    "filename": frame.name,
                    "path": str(frame),
                }
            return frame

        return _run_tool("take_a_photo", {}, _impl)

    @mcp.tool()
    def load_image(filename: str) -> Any:
        """Load one saved image file by filename."""

        def _impl() -> Any:
            image_path = runtime.load_image(filename)
            if isinstance(image_path, Path):
                return Image(path=str(image_path))
            return image_path

        return _run_tool("load_image", {"filename": filename}, _impl)

    @mcp.tool()
    def send_command(command: str) -> dict[str, Any]:
        """Send one command to the drone."""
        return _run_tool(
            "send_command",
            {"command": command},
            lambda: runtime.send_command(command),
        )

    @mcp.tool()
    def get_flight_log(
        session_id: str | None = None,
        command_limit: int = 200,
        state_limit: int = 200,
    ) -> dict[str, Any]:
        """Get one flight log session."""
        return _run_tool(
            "get_flight_log",
            {
                "session_id": session_id,
                "command_limit": command_limit,
                "state_limit": state_limit,
            },
            lambda: runtime.get_flight_log(
                session_id=session_id,
                command_limit=command_limit,
                state_limit=state_limit,
            ),
        )

    return mcp


def run_server(
    config: TelloMcpConfig | None = None,
    runtime: McpRuntimeProtocol | None = None,
    transport: str = "stdio",
    host: str | None = None,
    port: int | None = None,
    path: str | None = None,
    manage_runtime: bool = True,
) -> None:
    """Run MCP server with configurable transport and runtime.

    Args:
        config: Runtime config used only when ``runtime`` is None.
        runtime: Optional shared runtime backend.
        transport: MCP transport (e.g. ``stdio`` or ``http``).
        host: Bind host for HTTP/SSE transports.
        port: Bind port for HTTP/SSE transports.
        path: HTTP endpoint path for HTTP/SSE transports.
        manage_runtime: Whether to call runtime start/stop in this function.
    """
    active_runtime: McpRuntimeProtocol
    if runtime is None:
        cfg = config or TelloMcpConfig()
        active_runtime = TelloMcpRuntime(cfg)
    else:
        active_runtime = runtime

    mcp = build_mcp_app(active_runtime)

    if manage_runtime:
        active_runtime.start()
    try:
        run_kwargs: dict[str, Any] = {"transport": transport}
        if host is not None:
            run_kwargs["host"] = host
        if port is not None:
            run_kwargs["port"] = port
        if path is not None:
            run_kwargs["path"] = path
        mcp.run(**run_kwargs)
    finally:
        if manage_runtime:
            active_runtime.stop()


def run_stdio_server(config: TelloMcpConfig | None = None) -> None:
    """Run MCP server over stdio transport.

    Args:
        config: Optional runtime configuration override.
    """
    run_server(config=config, transport="stdio", manage_runtime=True)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the TELLO MCP server entrypoint."""
    run_stdio_server(config_from_args(parse_args(argv)))


if __name__ == "__main__":
    main()
