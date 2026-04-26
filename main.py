"""Terminal CLI for controlling a TELLO drone."""

from __future__ import annotations

import argparse
import shlex
import socket
from dataclasses import dataclass


@dataclass(frozen=True)
class TelloConfig:
    """Configuration values for TELLO UDP communication.

    Attributes:
        host: Drone IP address.
        port: Drone command port.
        local_port: Local UDP bind port.
        timeout_sec: Timeout for receiving command responses.
    """

    host: str = "192.168.10.1"
    port: int = 8889
    local_port: int = 9000
    timeout_sec: float = 7.0


class TelloTransport:
    """UDP transport layer for TELLO SDK commands."""

    def __init__(self, config: TelloConfig) -> None:
        """Initialize UDP socket and bind local port.

        Args:
            config: Communication parameters.
        """
        self._config = config
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("", config.local_port))
        self._sock.settimeout(config.timeout_sec)

    def close(self) -> None:
        """Close the UDP socket."""
        self._sock.close()

    def send_command(self, command: str) -> str:
        """Send a TELLO SDK command and return response text.

        Args:
            command: SDK command string.

        Returns:
            Response text returned by drone.
        """
        target = (self._config.host, self._config.port)
        self._sock.sendto(command.encode("utf-8"), target)
        while True:
            raw_response, sender = self._sock.recvfrom(1024)
            if sender[0] != self._config.host:
                continue
            return _decode_response(raw_response)


def _decode_response(raw_response: bytes) -> str:
    """Decode TELLO response bytes safely.

    Args:
        raw_response: Raw UDP payload.

    Returns:
        Decoded response text. For non-UTF-8 payloads, returns hex notation.
    """
    try:
        return raw_response.decode("utf-8").strip()
    except UnicodeDecodeError:
        return f"non-utf8-response:0x{raw_response.hex()}"


def _require_range(value: int, min_value: int, max_value: int, label: str) -> int:
    """Validate that integer is in the expected range.

    Args:
        value: Parsed integer value.
        min_value: Minimum accepted value.
        max_value: Maximum accepted value.
        label: Human readable field name.

    Returns:
        The same integer when valid.

    Raises:
        ValueError: If value is outside supported range.
    """
    if not min_value <= value <= max_value:
        raise ValueError(f"{label} must be between {min_value} and {max_value}.")
    return value


def build_sdk_command(user_input: str) -> str:
    """Translate CLI input into TELLO SDK command.

    Args:
        user_input: One line command input from terminal.

    Returns:
        SDK command string to send to drone.

    Raises:
        ValueError: If command format or values are invalid.
    """
    parts = shlex.split(user_input.strip())
    if not parts:
        raise ValueError("Command is empty.")

    command = parts[0].lower()
    simple_commands = {
        "takeoff",
        "land",
        "emergency",
        "battery?",
        "speed?",
        "time?",
    }
    if command in simple_commands and len(parts) == 1:
        return command

    if command == "flip" and len(parts) == 2 and parts[1] in {"l", "r", "f", "b"}:
        return f"flip {parts[1]}"

    if command in {"forward", "back", "left", "right", "up", "down"} and len(parts) == 2:
        distance = _require_range(int(parts[1]), 20, 500, "distance")
        return f"{command} {distance}"

    if command in {"cw", "ccw"} and len(parts) == 2:
        degree = _require_range(int(parts[1]), 1, 360, "degree")
        return f"{command} {degree}"

    if command == "speed" and len(parts) == 2:
        speed = _require_range(int(parts[1]), 10, 100, "speed")
        return f"speed {speed}"

    if command == "raw" and len(parts) >= 2:
        return " ".join(parts[1:])

    raise ValueError("Unsupported command. Type 'help' to show examples.")


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
    return parser.parse_args()


def main() -> None:
    """Run TELLO CLI entrypoint."""
    args = parse_args()
    config = TelloConfig(host=args.host, port=args.port, local_port=args.local_port)
    transport = TelloTransport(config)

    try:
        setup_response = transport.send_command("command")
        print(f"command -> {setup_response}")

        if args.command:
            sdk_command = build_sdk_command(args.command)
            result = transport.send_command(sdk_command)
            print(f"{sdk_command} -> {result}")
            return

        run_interactive(transport)
    finally:
        transport.close()


if __name__ == "__main__":
    main()
