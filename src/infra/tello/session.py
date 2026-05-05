"""TELLO SDK session recovery helpers."""

from __future__ import annotations

import socket
from typing import Protocol


class TelloCommandSender(Protocol):
    """Protocol for objects that can send TELLO SDK commands."""

    def send_command(self, command: str) -> str:
        """Send one command to TELLO and return response text."""


def reconnect_sdk_session(sender: TelloCommandSender) -> tuple[bool, str]:
    """Reconnect TELLO SDK session after drone reboot or battery swap.

    Args:
        sender: TELLO SDK command sender.

    Returns:
        Tuple of ``(ok, message)`` where message is a concise status summary.
    """
    try:
        command_response = sender.send_command("command")
        if not command_response.lower().startswith("ok"):
            return False, f"command failed: {command_response}"

        # Video stream state can remain stale after reboot/battery swap.
        # Force-reset stream mode before turning it back on.
        try:
            sender.send_command("streamoff")
        except socket.timeout:
            pass

        stream_response = sender.send_command("streamon")
        if not stream_response.lower().startswith("ok"):
            return False, f"streamon failed: {stream_response}"
    except socket.timeout:
        return False, "reconnect timeout"

    return True, "reconnected"
