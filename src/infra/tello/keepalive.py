"""Keepalive sender for TELLO command inactivity safety limit."""

from __future__ import annotations

import socket
import threading
import time
from typing import Protocol


class TelloCommandSender(Protocol):
    """Protocol for objects that can send TELLO SDK commands."""

    def send_command(self, command: str) -> str:
        """Send one command to TELLO and return response text."""


class TelloKeepalive:
    """Send periodic TELLO commands while the drone is flying.

    TELLO lands automatically when no command is received for 15 seconds.
    This class keeps the command channel active by sending harmless read
    commands at a configurable interval while enabled.
    """

    def __init__(
        self,
        sender: TelloCommandSender,
        interval_sec: float = 8.0,
        command: str = "battery?",
    ) -> None:
        """Initialize keepalive sender.

        Args:
            sender: Command sender bound to TELLO.
            interval_sec: Keepalive interval in seconds.
            command: SDK command used as keepalive.
        """
        if interval_sec <= 0:
            raise ValueError("interval_sec must be greater than 0.")
        self._sender = sender
        self._interval_sec = interval_sec
        self._command = command
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_activity_sec = time.monotonic()

    def start(self) -> None:
        """Start keepalive loop if not running."""
        with self._lock:
            if self.running():
                return
            self._stop_event.clear()
            self._last_activity_sec = time.monotonic()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="tello-keepalive",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Stop keepalive loop if running."""
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stop_event.set()
        if thread is not None:
            thread.join(timeout=1)

    def running(self) -> bool:
        """Return whether keepalive loop is running."""
        return self._thread is not None

    def mark_activity(self) -> None:
        """Record recent command activity."""
        with self._lock:
            self._last_activity_sec = time.monotonic()

    def _run_loop(self) -> None:
        """Periodically send keepalive command.

        Socket errors are intentionally ignored so this loop can continue
        best-effort without aborting user command flow.
        """
        while not self._stop_event.wait(0.2):
            if self._idle_seconds() < self._interval_sec:
                continue
            try:
                self._sender.send_command(self._command)
                self.mark_activity()
            except (socket.timeout, OSError):
                self.mark_activity()

    def _idle_seconds(self) -> float:
        """Return seconds since last marked command activity."""
        with self._lock:
            return time.monotonic() - self._last_activity_sec
