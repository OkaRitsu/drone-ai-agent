"""TELLO command parser and validation for CLI input."""

from __future__ import annotations

import shlex


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

