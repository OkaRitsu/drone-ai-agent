"""Terminal CLI for controlling a TELLO drone."""

from __future__ import annotations

import argparse
import importlib
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from infra.flight_logging import FlightLogConfig, FlightLogger
from infra.tello import TelloConfig, TelloStateReceiver, TelloTransport, build_sdk_command


def build_video_stream_url(video_port: int) -> str:
    """Build TELLO video stream URL.

    Args:
        video_port: Local UDP port to receive TELLO video.

    Returns:
        Stream URL passed to OpenCV VideoCapture.
    """
    return f"udp://0.0.0.0:{video_port}"


class VideoWindow:
    """Handle launching and controlling OpenCV video worker process."""

    def __init__(self, video_port: int, enable_display: bool = True) -> None:
        """Initialize video worker launcher.

        Args:
            video_port: Local UDP port to receive TELLO video.
            enable_display: Whether the worker should show display window.
        """
        self._video_port = video_port
        self._enable_display = enable_display
        self._control_port = _reserve_local_udp_port()
        self._process: subprocess.Popen[bytes] | None = None
        self._control_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def start(self) -> None:
        """Start OpenCV video worker process.

        Raises:
            RuntimeError: If OpenCV package is unavailable.
        """
        try:
            importlib.import_module("cv2")
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "opencv-python is not installed. Install it to show TELLO stream window."
            ) from error

        self._process = subprocess.Popen(
            build_video_worker_command(
                video_port=self._video_port,
                control_port=self._control_port,
                enable_display=self._enable_display,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def start_recording(self, output_path: Path) -> None:
        """Request video recording start for current stream.

        Args:
            output_path: Target file path for recorded video.
        """
        payload = {"type": "start_record", "path": str(output_path)}
        self._send_control(payload)

    def stop_recording(self) -> None:
        """Request video recording stop."""
        self._send_control({"type": "stop_record"})

    def stop(self) -> None:
        """Stop video worker process if running."""
        self._send_control({"type": "shutdown"})
        if self._process is None:
            self._control_sock.close()
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self._control_sock.close()

    def _send_control(self, payload: dict[str, str]) -> None:
        """Send control payload to video worker.

        Args:
            payload: JSON serializable control payload.
        """
        message = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        try:
            self._control_sock.sendto(message, ("127.0.0.1", self._control_port))
        except OSError:
            pass


def _reserve_local_udp_port() -> int:
    """Reserve and return an available local UDP port.

    Returns:
        Available UDP port number.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


def build_video_worker_command(video_port: int, control_port: int, enable_display: bool) -> list[str]:
    """Build command list to launch OpenCV video worker subprocess.

    Args:
        video_port: Local UDP port to receive TELLO video.
        control_port: UDP port for worker control messages.
        enable_display: Whether worker should show window.

    Returns:
        Subprocess command list.
    """
    command = [
        sys.executable,
        "-m",
        "infra.tello_cli",
        "--video-worker-only",
        "--video-port",
        str(video_port),
        "--control-port",
        str(control_port),
    ]
    if not enable_display:
        command.append("--disable-display")
    return command


def run_video_worker(video_port: int, control_port: int, enable_display: bool) -> int:
    """Run OpenCV video worker in foreground.

    Args:
        video_port: Local UDP port to receive TELLO video.
        control_port: UDP port for worker control messages.
        enable_display: Whether worker should show window.

    Returns:
        Process exit code.
    """
    cv2 = importlib.import_module("cv2")
    stream_url = build_video_stream_url(video_port)
    capture_backend = getattr(cv2, "CAP_FFMPEG", 0)
    cap = cv2.VideoCapture(stream_url, capture_backend)

    if not cap.isOpened():
        print("Video error: failed to open TELLO stream.")
        return 1

    control_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    control_sock.bind(("127.0.0.1", control_port))
    control_sock.settimeout(0.001)

    window_name = "TELLO Stream"
    created_window = False
    writer = None
    pending_record_path: Path | None = None

    try:
        while True:
            worker_command = _recv_worker_command(control_sock)
            if worker_command is not None:
                command_type = worker_command.get("type")
                if command_type == "shutdown":
                    break
                if command_type == "stop_record":
                    if writer is not None:
                        writer.release()
                        writer = None
                    pending_record_path = None
                if command_type == "start_record":
                    if writer is not None:
                        writer.release()
                        writer = None
                    pending_record_path = Path(worker_command["path"])

            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue

            if pending_record_path is not None and writer is None:
                fps = cap.get(getattr(cv2, "CAP_PROP_FPS", 5))
                if fps <= 0:
                    fps = 30.0
                height, width = frame.shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(pending_record_path), fourcc, fps, (width, height))

            if writer is not None:
                writer.write(frame)

            if enable_display:
                if not created_window:
                    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
                    created_window = True
                cv2.imshow(window_name, frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    break
    finally:
        cap.release()
        control_sock.close()
        if writer is not None:
            writer.release()
        if created_window:
            cv2.destroyWindow(window_name)
    return 0


def _recv_worker_command(control_sock: socket.socket) -> dict[str, str] | None:
    """Receive one worker command payload if available.

    Args:
        control_sock: Bound UDP socket for worker control.

    Returns:
        Parsed command payload or None.
    """
    try:
        data, _ = control_sock.recvfrom(4096)
    except socket.timeout:
        return None
    except OSError:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


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
        "--no-video",
        action="store_true",
        help="disable video stream window on startup",
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
        "--video-worker-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=0,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--disable-display",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> None:
    """Run TELLO CLI entrypoint."""
    args = parse_args()
    if args.video_worker_only:
        raise SystemExit(
            run_video_worker(
                video_port=args.video_port,
                control_port=args.control_port,
                enable_display=not args.disable_display,
            )
        )

    config = TelloConfig(host=args.host, port=args.port, local_port=args.local_port)
    transport = TelloTransport(config)
    video_window = VideoWindow(video_port=args.video_port, enable_display=not args.no_video)
    state_receiver = TelloStateReceiver(port=args.state_port)

    flight_logger = FlightLogger(
        config=FlightLogConfig(
            root_dir=Path(args.log_dir),
            max_saved_sessions=args.max_log_sessions,
            state_sample_interval_sec=args.state_sample_interval,
        ),
        state_provider=state_receiver.get_latest,
        video_recorder=video_window,
    )

    try:
        setup_response = transport.send_command("command")
        print(f"command -> {setup_response}")

        stream_response = transport.send_command("streamon")
        print(f"streamon -> {stream_response}")

        video_window.start()
        if not args.no_video:
            print("TELLO stream window started.")

        state_receiver.start()

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
        print(f"Video error: {error}")
    finally:
        if flight_logger.session_active():
            flight_logger.stop_session()

        state_receiver.stop()
        video_window.stop()

        try:
            streamoff_response = transport.send_command("streamoff")
            print(f"streamoff -> {streamoff_response}")
        except socket.timeout:
            print("streamoff -> timeout")
        transport.close()


if __name__ == "__main__":
    main()
