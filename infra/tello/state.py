"""State telemetry receiver for TELLO."""

from __future__ import annotations

import socket
import threading
from typing import Any


def parse_state_payload(payload: str) -> dict[str, Any]:
    """Parse TELLO state payload text into a dictionary.

    Args:
        payload: Raw state payload separated by ';' and ':'.

    Returns:
        Parsed state dictionary with int/float coercion when possible.
    """
    result: dict[str, Any] = {}
    for part in payload.strip().split(";"):
        if not part or ":" not in part:
            continue
        key, value = part.split(":", 1)
        value = value.strip()
        result[key] = _coerce_number(value)
    return result


def _coerce_number(value: str) -> Any:
    """Convert payload value to int/float when possible.

    Args:
        value: String value from state payload.

    Returns:
        Converted numeric value or original string.
    """
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


class TelloStateReceiver:
    """Receive and store latest TELLO state telemetry from UDP."""

    def __init__(self, port: int = 8890, timeout_sec: float = 0.5) -> None:
        """Initialize state receiver.

        Args:
            port: Local UDP port for TELLO state packets.
            timeout_sec: Socket timeout for thread loop.
        """
        self._port = port
        self._timeout_sec = timeout_sec
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._latest_raw: str | None = None
        self._latest_state: dict[str, Any] | None = None

    def start(self) -> None:
        """Start background telemetry receiver thread."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("", self._port))
        self._sock.settimeout(self._timeout_sec)

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background receiver and close socket."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None

        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def get_latest(self) -> tuple[str | None, dict[str, Any] | None]:
        """Get the latest raw and parsed state.

        Returns:
            Tuple of (raw_payload, parsed_state_dict).
        """
        with self._lock:
            if self._latest_state is None:
                return self._latest_raw, None
            return self._latest_raw, dict(self._latest_state)

    def _run(self) -> None:
        """Worker loop for receiving telemetry packets."""
        assert self._sock is not None
        while not self._stop_event.is_set():
            try:
                payload, _ = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break

            raw = payload.decode("utf-8", errors="ignore").strip()
            parsed = parse_state_payload(raw)
            with self._lock:
                self._latest_raw = raw
                self._latest_state = parsed

