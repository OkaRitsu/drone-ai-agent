"""Terminal CLI for controlling a TELLO drone."""

from __future__ import annotations

import argparse
import importlib
import socket
import subprocess
import sys
import time

from infra.tello import TelloConfig, TelloTransport, build_sdk_command


def build_video_stream_url(video_port: int) -> str:
    """Build TELLO video stream URL.

    Args:
        video_port: Local UDP port to receive TELLO video.

    Returns:
        Stream URL passed to OpenCV VideoCapture.
    """
    return f"udp://0.0.0.0:{video_port}"


class VideoWindow:
    """Handle launching and stopping the OpenCV streaming window."""

    def __init__(self, video_port: int) -> None:
        """Initialize video window launcher.

        Args:
            video_port: Local UDP port to receive TELLO video.
        """
        self._video_port = video_port
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        """Start OpenCV viewer process.

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
            build_video_viewer_command(self._video_port),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        """Stop OpenCV viewer process if running."""
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None


def build_video_viewer_command(video_port: int) -> list[str]:
    """Build command list to launch OpenCV video viewer subprocess.

    Args:
        video_port: Local UDP port to receive TELLO video.

    Returns:
        Subprocess command list.
    """
    return [
        sys.executable,
        "-m",
        "infra.tello_cli",
        "--video-viewer-only",
        "--video-port",
        str(video_port),
    ]


def run_video_viewer(video_port: int) -> int:
    """Run OpenCV video viewer in foreground.

    Args:
        video_port: Local UDP port to receive TELLO video.

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

    window_name = "TELLO Stream"
    created_window = False

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.01)
                continue

            if not created_window:
                cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
                created_window = True

            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
    finally:
        cap.release()
        if created_window:
            cv2.destroyWindow(window_name)
    return 0


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


def run_interactive(transport: TelloTransport) -> None:
    """Run interactive terminal loop for TELLO control.

    Args:
        transport: SDK command transport.
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
            response = transport.send_command(sdk_command)
            print(f"{sdk_command} -> {response}")
        except ValueError as error:
            print(f"Input error: {error}")
        except socket.timeout:
            print("No response from TELLO (timeout).")


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
        "--video-viewer-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> None:
    """Run TELLO CLI entrypoint."""
    args = parse_args()
    if args.video_viewer_only:
        raise SystemExit(run_video_viewer(args.video_port))

    config = TelloConfig(host=args.host, port=args.port, local_port=args.local_port)
    transport = TelloTransport(config)
    video_window = VideoWindow(video_port=args.video_port)

    try:
        setup_response = transport.send_command("command")
        print(f"command -> {setup_response}")

        if not args.no_video:
            stream_response = transport.send_command("streamon")
            print(f"streamon -> {stream_response}")
            video_window.start()
            print("TELLO stream window started.")

        if args.command:
            sdk_command = build_sdk_command(args.command)
            result = transport.send_command(sdk_command)
            print(f"{sdk_command} -> {result}")
            return

        run_interactive(transport)
    except RuntimeError as error:
        print(f"Video error: {error}")
    finally:
        video_window.stop()
        if not args.no_video:
            try:
                streamoff_response = transport.send_command("streamoff")
                print(f"streamoff -> {streamoff_response}")
            except socket.timeout:
                print("streamoff -> timeout")
        transport.close()


if __name__ == "__main__":
    main()
