"""Flight log management for takeoff-to-land sessions."""

from __future__ import annotations

import csv
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol

STATE_CSV_FIELDS = [
    "pitch",
    "roll",
    "yaw",
    "vgx",
    "vgy",
    "vgz",
    "templ",
    "temph",
    "tof",
    "h",
    "bat",
    "baro",
    "time",
    "agx",
    "agy",
    "agz",
]


class VideoRecorder(Protocol):
    """Protocol for video recorder operations used by flight logger."""

    def start_recording(self, output_path: Path) -> None:
        """Start recording video to output path."""

    def stop_recording(self) -> None:
        """Stop active video recording."""


@dataclass(frozen=True)
class FlightLogConfig:
    """Configuration for flight log storage and retention.

    Attributes:
        root_dir: Root directory containing per-flight log directories.
        max_saved_sessions: Maximum number of session directories to keep.
        state_sample_interval_sec: Sampling interval for state snapshots.
    """

    root_dir: Path = Path("logs")
    max_saved_sessions: int = 20
    state_sample_interval_sec: float = 0.5


class FlightLogger:
    """Manage takeoff-to-land logs for commands, state, and video."""

    def __init__(
        self,
        config: FlightLogConfig,
        state_provider: Callable[[], tuple[str | None, dict[str, Any] | None]],
        video_recorder: VideoRecorder,
    ) -> None:
        """Initialize flight logger.

        Args:
            config: Log storage configuration.
            state_provider: Callable returning latest (raw, parsed) TELLO state.
            video_recorder: Recorder used to save flight video.
        """
        self._config = config
        self._state_provider = state_provider
        self._video_recorder = video_recorder

        self._session_dir: Path | None = None
        self._command_fp = None
        self._command_writer: csv.writer | None = None
        self._state_fp = None
        self._state_writer: csv.DictWriter | None = None
        self._state_thread: threading.Thread | None = None
        self._state_stop = threading.Event()
        self._lock = threading.Lock()

    def session_active(self) -> bool:
        """Return whether a flight log session is currently active."""
        return self._session_dir is not None

    def start_session(self) -> Path:
        """Start a new flight session log.

        Returns:
            Created session directory path.
        """
        with self._lock:
            if self._session_dir is not None:
                return self._session_dir

            self._config.root_dir.mkdir(parents=True, exist_ok=True)
            self._prune_old_logs()

            self._session_dir = self._create_session_dir()

            self._command_fp = (self._session_dir / "commands.csv").open(
                "w", encoding="utf-8", newline=""
            )
            self._command_writer = csv.writer(self._command_fp)
            self._command_writer.writerow(["timestamp", "command", "status", "response"])

            self._state_fp = (self._session_dir / "state.csv").open(
                "w", encoding="utf-8", newline=""
            )
            self._state_writer = csv.DictWriter(
                self._state_fp,
                fieldnames=["timestamp", "raw", *STATE_CSV_FIELDS],
            )
            self._state_writer.writeheader()

            metadata = {
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "session_dir": str(self._session_dir),
            }
            (self._session_dir / "metadata.json").write_text(
                json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8"
            )

            self._video_recorder.start_recording(self._session_dir / "video.mp4")
            self._state_stop.clear()
            self._state_thread = threading.Thread(target=self._state_loop, daemon=True)
            self._state_thread.start()
            return self._session_dir

    def stop_session(self) -> None:
        """Stop current flight session and finalize files."""
        with self._lock:
            if self._session_dir is None:
                return

            self._state_stop.set()
            if self._state_thread is not None:
                self._state_thread.join(timeout=2)
                self._state_thread = None

            self._video_recorder.stop_recording()

            if self._command_fp is not None:
                self._command_fp.close()
                self._command_fp = None
                self._command_writer = None
            if self._state_fp is not None:
                self._state_fp.close()
                self._state_fp = None
                self._state_writer = None

            self._session_dir = None

    def log_command(self, command: str, response: str, status: str = "ok") -> None:
        """Append executed command record to current session.

        Args:
            command: Command sent to TELLO.
            response: Response text returned from transport.
            status: Result status label such as `ok` or `error`.
        """
        if self._command_fp is None or self._command_writer is None:
            return
        self._command_writer.writerow(
            [
                datetime.now().isoformat(timespec="milliseconds"),
                command,
                status,
                response,
            ]
        )
        self._command_fp.flush()

    def _state_loop(self) -> None:
        """Background loop sampling latest state while session is active."""
        while not self._state_stop.is_set():
            raw, parsed = self._state_provider()
            if self._state_fp is not None and self._state_writer is not None and parsed is not None:
                row: dict[str, Any] = {
                    "timestamp": datetime.now().isoformat(timespec="milliseconds"),
                    "raw": raw or "",
                }
                for key in STATE_CSV_FIELDS:
                    value = parsed.get(key)
                    row[key] = "" if value is None else value
                self._state_writer.writerow(row)
                self._state_fp.flush()
            time.sleep(self._config.state_sample_interval_sec)

    def _prune_old_logs(self) -> None:
        """Delete oldest session directories when retention is exceeded."""
        if self._config.max_saved_sessions <= 0:
            return
        sessions = [p for p in self._config.root_dir.iterdir() if p.is_dir()]
        if len(sessions) < self._config.max_saved_sessions:
            return
        sessions.sort(key=lambda p: p.stat().st_mtime)
        remove_count = len(sessions) - self._config.max_saved_sessions + 1
        for path in sessions[:remove_count]:
            for child in sorted(path.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            path.rmdir()

    def _create_session_dir(self) -> Path:
        """Create unique session directory under root dir.

        Returns:
            Created session directory path.
        """
        base_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        primary = self._config.root_dir / base_name
        if not primary.exists():
            primary.mkdir(parents=True, exist_ok=False)
            return primary

        suffix = 1
        while True:
            candidate = self._config.root_dir / f"{base_name}_{suffix:02d}"
            if not candidate.exists():
                candidate.mkdir(parents=True, exist_ok=False)
                return candidate
            suffix += 1
