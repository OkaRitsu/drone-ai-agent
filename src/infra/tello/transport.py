"""UDP transport implementation for TELLO SDK commands."""

from __future__ import annotations

import socket
import threading

from src.infra.tello.config import TelloConfig
from src.infra.tello.protocol import decode_response


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
        self._send_lock = threading.Lock()

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
        with self._send_lock:
            self._sock.sendto(command.encode("utf-8"), target)
            while True:
                raw_response, sender = self._sock.recvfrom(1024)
                if sender[0] != self._config.host:
                    continue
                return decode_response(raw_response)

