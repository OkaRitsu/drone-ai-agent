"""Terminal CLI for controlling a TELLO drone."""

from __future__ import annotations

import argparse
import socket
from pathlib import Path

from app.dashboard import RerunDashboard
from infra.flight_logging import FlightLogConfig, FlightLogger
from infra.tello import TelloConfig, TelloStateReceiver, TelloTransport, build_sdk_command


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


def run_interactive(transport: TelloTransport, flight_logger: FlightLogger) -> None:
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
            sdk_command = build_sdk_command(line)
            if sdk_command == "takeoff" and not flight_logger.session_active():
                session_dir = flight_logger.start_session()
                print(f"flight-log -> {session_dir}")

            response = transport.send_command(sdk_command)

            if flight_logger.session_active():
                flight_logger.log_command(command=sdk_command, response=response, status="ok")

            print(f"{sdk_command} -> {response}")

            if (
                sdk_command == "takeoff"
                and not response.lower().startswith("ok")
                and flight_logger.session_active()
            ):
                flight_logger.stop_session()
                print("flight-log -> aborted")

            if (
                sdk_command in {"land", "emergency"}
                and response.lower().startswith("ok")
                and flight_logger.session_active()
            ):
                flight_logger.stop_session()
                print("flight-log -> saved")

        except ValueError as error:
            print(f"Input error: {error}")
        except socket.timeout:
            print("No response from TELLO (timeout).")
            if flight_logger.session_active():
                flight_logger.log_command(command=line, response="timeout", status="error")
                if line.lower().strip().startswith("takeoff"):
                    flight_logger.stop_session()
                    print("flight-log -> aborted")


def parse_args() -> argparse.Namespace:
    """Parse command line options."""
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
        help="disable rerun dashboard startup",
    )
    return parser.parse_args()


def main() -> None:
    """Run TELLO CLI entrypoint."""
    args = parse_args()

    config = TelloConfig(host=args.host, port=args.port, local_port=args.local_port)
    transport = TelloTransport(config)
    state_receiver = TelloStateReceiver(port=args.state_port)
    dashboard = RerunDashboard(
        video_port=args.video_port,
        state_provider=state_receiver.get_latest,
        state_interval_sec=args.dashboard_state_interval,
    )

    flight_logger = FlightLogger(
        config=FlightLogConfig(
            root_dir=Path(args.log_dir),
            max_saved_sessions=args.max_log_sessions,
            state_sample_interval_sec=args.state_sample_interval,
        ),
        state_provider=state_receiver.get_latest,
        video_recorder=dashboard,
    )

    try:
        setup_response = transport.send_command("command")
        print(f"command -> {setup_response}")

        stream_response = transport.send_command("streamon")
        print(f"streamon -> {stream_response}")

        state_receiver.start()

        if not args.no_dashboard:
            dashboard.start()
            print("Rerun dashboard started.")

        if args.command:
            sdk_command = build_sdk_command(args.command)
            if sdk_command == "takeoff" and not flight_logger.session_active():
                session_dir = flight_logger.start_session()
                print(f"flight-log -> {session_dir}")
            result = transport.send_command(sdk_command)
            if flight_logger.session_active():
                flight_logger.log_command(command=sdk_command, response=result, status="ok")
            print(f"{sdk_command} -> {result}")
            if (
                sdk_command == "takeoff"
                and not result.lower().startswith("ok")
                and flight_logger.session_active()
            ):
                flight_logger.stop_session()
                print("flight-log -> aborted")
            return

        run_interactive(transport, flight_logger)
    except RuntimeError as error:
        print(f"Dashboard error: {error}")
    finally:
        if flight_logger.session_active():
            flight_logger.stop_session()

        dashboard.stop()
        state_receiver.stop()

        try:
            streamoff_response = transport.send_command("streamoff")
            print(f"streamoff -> {streamoff_response}")
        except socket.timeout:
            print("streamoff -> timeout")
        transport.close()


if __name__ == "__main__":
    main()
