"""Configuration models for TELLO communication."""

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

