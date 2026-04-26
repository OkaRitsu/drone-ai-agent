"""Protocol helpers for TELLO command/response handling."""


def decode_response(raw_response: bytes) -> str:
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

