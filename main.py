"""Terminal CLI for controlling a TELLO drone."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import socket
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from src.app.dashboard import RerunDashboard
from src.infra.flight_logging import FlightLogConfig, FlightLogger
from src.infra.tello import (
    TelloConfig,
    TelloKeepalive,
    TelloStateReceiver,
    TelloTransport,
    build_sdk_command,
)
from src.mcp.server import run_server


@dataclass(frozen=True)
class TelloCliConfig:
    """Configuration for TELLO CLI and dashboard startup.

    Attributes:
        host: TELLO host IP.
        command_port: TELLO SDK command port.
        local_command_port: Local UDP bind port for SDK commands.
        state_port: Local UDP bind port for state telemetry.
        video_port: Local UDP port for TELLO video stream.
        log_dir: Root directory for flight logs.
        max_log_sessions: Maximum number of flight sessions to keep.
        state_sample_interval_sec: State sampling interval for flight logs.
        dashboard_state_interval_sec: State plot interval for the dashboard.
        dashboard_enabled: Whether to launch the Rerun dashboard in CLI mode.
        mcp_http_enabled: Whether to launch HTTP MCP server in this process.
        mcp_host: HTTP MCP bind host.
        mcp_port: HTTP MCP bind port.
        mcp_path: HTTP MCP endpoint path.
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
    dashboard_state_interval_sec: float = 0.2
    dashboard_enabled: bool = True
    mcp_http_enabled: bool = False
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000
    mcp_path: str = "/mcp"
    keepalive_interval_sec: float = 8.0
    keepalive_command: str = "battery?"


def print_help() -> None:
    """Print command examples for interactive mode."""
    print("Available commands:")
    print("  takeoff | land | emergency")
    print("  forward/back/left/right/up/down <20-500>")
    print("  cw/ccw <1-360>")
    print("  speed <10-100> | speed? | battery? | time?")
    print("  flip <l|r|f|b>")
    print("  raw <sdk command>  # send raw TELLO SDK command")
    print("  help | quit")


def run_interactive(
    transport: TelloTransport,
    flight_logger: FlightLogger,
    keepalive: TelloKeepalive | None = None,
) -> None:
    """Run interactive terminal loop for TELLO control.

    Args:
        transport: SDK command transport.
        flight_logger: Flight log manager.
    """
    print("TELLO interactive mode started. Type 'help' for commands.")
    while True:
        try:
            line = input("tello> ").strip()
        except EOFError:
            print()
            break

        if not line:
            continue
        if line.lower() in {"quit", "exit"}:
            break
        if line.lower() == "help":
            print_help()
            continue

        try:
            result = execute_command(
                transport,
                flight_logger,
                line,
                keepalive=keepalive,
            )
            if result["session_started"]:
                session_dir = result["session_started"]
                print(f"flight-log -> {session_dir}")

            if result["status"] == "error" and result["response"] == "timeout":
                print("No response from TELLO (timeout).")
            else:
                print(f"{result['sdk_command']} -> {result['response']}")

            if result["session_saved"]:
                print("flight-log -> saved")
        except ValueError as error:
            print(f"Input error: {error}")


def execute_command(
    transport: TelloTransport,
    flight_logger: FlightLogger,
    command: str,
    keepalive: TelloKeepalive | None = None,
) -> dict[str, Any]:
    """Execute one command with unified logging semantics.

    Args:
        transport: SDK command transport.
        flight_logger: Flight log manager.
        command: Human CLI command or raw TELLO SDK command.

    Returns:
        Command execution payload compatible with MCP response format.
    """
    sdk_command = build_sdk_command(command)
    session_started = None
    if sdk_command == "takeoff" and not flight_logger.session_active():
        session_started = str(flight_logger.start_session())

    if keepalive is not None:
        keepalive.mark_activity()

    try:
        response = transport.send_command(sdk_command)
        status = "ok"
    except socket.timeout:
        response = "timeout"
        status = "error"

    if flight_logger.session_active():
        flight_logger.log_command(
            command=sdk_command,
            response=response,
            status=status,
        )

    session_saved = False
    should_stop_keepalive = sdk_command in {
        "land",
        "emergency",
    } and response.lower().startswith("ok")
    if should_stop_keepalive and flight_logger.session_active():
        flight_logger.stop_session()
        session_saved = True
    if should_stop_keepalive and keepalive is not None:
        keepalive.stop()

    if (
        sdk_command == "takeoff"
        and response.lower().startswith("ok")
        and keepalive is not None
    ):
        keepalive.start()

    return {
        "command": command,
        "sdk_command": sdk_command,
        "status": status,
        "response": response,
        "session_started": session_started,
        "session_saved": session_saved,
    }


class MainMcpRuntimeAdapter:
    """MCP runtime adapter over CLI-owned TELLO resources."""

    def __init__(
        self,
        transport: TelloTransport,
        state_receiver: TelloStateReceiver,
        dashboard: RerunDashboard,
        flight_logger: FlightLogger,
        log_dir: Path,
        keepalive: TelloKeepalive | None = None,
    ) -> None:
        """Initialize MCP runtime adapter.

        Args:
            transport: Shared SDK transport.
            state_receiver: Shared telemetry receiver.
            dashboard: Shared dashboard and video decoder.
            flight_logger: Shared flight logger.
            log_dir: Root directory that stores flight sessions.
        """
        self._transport = transport
        self._state_receiver = state_receiver
        self._dashboard = dashboard
        self._flight_logger = flight_logger
        self._log_dir = log_dir
        self._keepalive = keepalive

    def start(self) -> None:
        """No-op for shared runtime mode."""

    def stop(self) -> None:
        """No-op for shared runtime mode."""

    def get_state(self) -> dict[str, Any]:
        """Get latest telemetry state."""
        raw, parsed = self._state_receiver.get_latest()
        return {
            "raw": raw,
            "state": parsed or {},
            "has_state": parsed is not None,
        }

    def take_a_photo(self) -> Path | dict[str, Any]:
        """Save latest frame from dashboard decoder."""
        payload = self._dashboard.get_latest_jpeg_base64()
        if payload is None:
            return {
                "ok": False,
                "error": "frame_unavailable",
                "message": "No frame has been received yet.",
            }
        return _save_get_frame_log_base64(self._log_dir, payload["data_base64"])

    def load_image(self, filename: str) -> Path | dict[str, Any]:
        """Load a saved photo by file name."""
        if not filename:
            return {
                "ok": False,
                "error": "invalid_filename",
                "message": "filename must not be empty.",
            }

        image_dir = self._log_dir / "take_a_photo"
        candidate = (image_dir / filename).resolve()
        if candidate.parent != image_dir.resolve() or not candidate.exists():
            return {
                "ok": False,
                "error": "image_not_found",
                "message": f"Image not found: {filename}",
            }
        return candidate

    def send_command(self, command: str) -> dict[str, Any]:
        """Send one command via shared transport."""
        return execute_command(
            transport=self._transport,
            flight_logger=self._flight_logger,
            command=command,
            keepalive=self._keepalive,
        )

    def get_flight_log(
        self,
        session_id: str | None = None,
        command_limit: int = 200,
        state_limit: int = 200,
    ) -> dict[str, Any]:
        """Get one flight log session content."""
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
            "photos": _list_take_a_photo_paths(self._log_dir),
        }

    def _resolve_session_dir(self, session_id: str | None) -> Path | None:
        """Resolve session directory by ID or latest."""
        if not self._log_dir.exists():
            return None

        if session_id:
            session_dir = self._log_dir / session_id
            return session_dir if session_dir.is_dir() else None

        sessions = sorted((path for path in self._log_dir.iterdir() if path.is_dir()))
        if not sessions:
            return None
        return sessions[-1]


def _tail_csv(path: Path, limit: int) -> list[dict[str, str]]:
    """Read tail rows from CSV file.

    Args:
        path: CSV path.
        limit: Maximum row count.

    Returns:
        Tail rows, or empty list when file does not exist.
    """
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as fp:
        rows = list(csv.DictReader(fp))
    if limit <= 0:
        return rows
    return rows[-limit:]


def _save_get_frame_log_base64(log_root: Path, data_base64: str) -> Path:
    """Persist one photo response image as a log artifact.

    Args:
        log_root: Root log directory.
        data_base64: Base64 encoded JPEG payload.

    Returns:
        Saved image path.
    """
    save_dir = log_root / "take_a_photo"
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    output_path = save_dir / filename
    output_path.write_bytes(base64.b64decode(data_base64))
    return output_path


def _list_take_a_photo_paths(log_root: Path) -> list[str]:
    """List saved photo file paths under take_a_photo directory."""
    photo_dir = log_root / "take_a_photo"
    if not photo_dir.exists():
        return []
    return sorted(
        str(path.resolve())
        for path in photo_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".jpg"
    )


def run_mcp_http_server(
    runtime: MainMcpRuntimeAdapter,
    host: str,
    port: int,
    path: str,
) -> None:
    """Run shared-runtime MCP HTTP server and report startup failures.

    Args:
        runtime: Shared runtime adapter.
        host: Bind host.
        port: Bind port.
        path: Endpoint path.
    """
    try:
        run_server(
            runtime=runtime,
            transport="http",
            host=host,
            port=port,
            path=path,
            manage_runtime=False,
        )
    except Exception as error:
        print(f"MCP server error: {error}")


def build_parser() -> argparse.ArgumentParser:
    """Build command line parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(description="TELLO terminal controller")
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
        "--command",
        help="send one command then exit (example: --command 'battery?')",
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
        "--dashboard-state-interval",
        type=float,
        default=0.2,
        help="state plot interval seconds for dashboard (default: 0.2)",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="disable rerun dashboard startup in CLI mode",
    )
    parser.add_argument(
        "--mcp-http",
        action="store_true",
        help="run MCP server over HTTP in this process",
    )
    parser.add_argument(
        "--mcp-host",
        default="127.0.0.1",
        help="HTTP MCP bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=8000,
        help="HTTP MCP bind port (default: 8000)",
    )
    parser.add_argument(
        "--mcp-path",
        default="/mcp",
        help="HTTP MCP endpoint path (default: /mcp)",
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
    """Parse command line options.

    Args:
        argv: Optional argument list. Defaults to ``sys.argv`` when None.

    Returns:
        Parsed arguments.
    """
    return build_parser().parse_args(argv)


def config_from_args(args: argparse.Namespace) -> TelloCliConfig:
    """Build runtime configuration from parsed CLI arguments.

    Args:
        args: Parsed command line options.

    Returns:
        Shared TELLO runtime configuration.
    """
    return TelloCliConfig(
        host=args.host,
        command_port=args.port,
        local_command_port=args.local_port,
        state_port=args.state_port,
        video_port=args.video_port,
        log_dir=Path(args.log_dir),
        max_log_sessions=args.max_log_sessions,
        state_sample_interval_sec=args.state_sample_interval,
        dashboard_state_interval_sec=args.dashboard_state_interval,
        dashboard_enabled=not args.no_dashboard,
        mcp_http_enabled=args.mcp_http,
        mcp_host=args.mcp_host,
        mcp_port=args.mcp_port,
        mcp_path=args.mcp_path,
        keepalive_interval_sec=args.keepalive_interval,
        keepalive_command=args.keepalive_command,
    )


def run_cli(config: TelloCliConfig, command: str | None = None) -> None:
    """Run TELLO dashboard and terminal CLI entrypoint.

    Args:
        config: Shared TELLO runtime configuration.
        command: Optional single command to execute before exiting.
    """
    tello_config = TelloConfig(
        host=config.host,
        port=config.command_port,
        local_port=config.local_command_port,
    )
    transport = TelloTransport(tello_config)
    state_receiver = TelloStateReceiver(port=config.state_port)
    dashboard = RerunDashboard(
        video_port=config.video_port,
        state_provider=state_receiver.get_latest,
        state_interval_sec=config.dashboard_state_interval_sec,
    )

    flight_logger = FlightLogger(
        config=FlightLogConfig(
            root_dir=config.log_dir,
            max_saved_sessions=config.max_log_sessions,
            state_sample_interval_sec=config.state_sample_interval_sec,
        ),
        state_provider=state_receiver.get_latest,
        video_recorder=dashboard,
    )
    keepalive = TelloKeepalive(
        sender=transport,
        interval_sec=config.keepalive_interval_sec,
        command=config.keepalive_command,
    )

    try:
        setup_response = transport.send_command("command")
        print(f"command -> {setup_response}")

        stream_response = transport.send_command("streamon")
        print(f"streamon -> {stream_response}")

        state_receiver.start()

        if config.dashboard_enabled:
            dashboard.start()
            print("Rerun dashboard started.")

        if config.mcp_http_enabled:
            mcp_runtime = MainMcpRuntimeAdapter(
                transport=transport,
                state_receiver=state_receiver,
                dashboard=dashboard,
                flight_logger=flight_logger,
                log_dir=config.log_dir,
                keepalive=keepalive,
            )
            mcp_thread = threading.Thread(
                target=run_mcp_http_server,
                args=(mcp_runtime, config.mcp_host, config.mcp_port, config.mcp_path),
                daemon=True,
            )
            mcp_thread.start()
            print(
                f"MCP HTTP server started on http://{config.mcp_host}:{config.mcp_port}{config.mcp_path}"
            )

        if command:
            result = execute_command(
                transport,
                flight_logger,
                command,
                keepalive=keepalive,
            )
            if result["session_started"]:
                print(f"flight-log -> {result['session_started']}")
            if result["status"] == "error" and result["response"] == "timeout":
                print("No response from TELLO (timeout).")
            else:
                print(f"{result['sdk_command']} -> {result['response']}")
            if result["session_saved"]:
                print("flight-log -> saved")
            return

        run_interactive(transport, flight_logger, keepalive=keepalive)
    except RuntimeError as error:
        print(f"Dashboard error: {error}")
    finally:
        if flight_logger.session_active():
            flight_logger.stop_session()
        keepalive.stop()

        dashboard.stop()
        state_receiver.stop()

        try:
            streamoff_response = transport.send_command("streamoff")
            print(f"streamoff -> {streamoff_response}")
        except socket.timeout:
            print("streamoff -> timeout")
        transport.close()


def main(argv: Sequence[str] | None = None) -> None:
    """Run TELLO CLI and dashboard entrypoint."""
    args = parse_args(argv)
    config = config_from_args(args)
    run_cli(config, command=args.command)


if __name__ == "__main__":
    main()
