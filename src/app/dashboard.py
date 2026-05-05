"""Rerun dashboard integration for TELLO monitoring."""

from __future__ import annotations

import base64
import importlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

PLOT_GROUPS: dict[str, list[str]] = {
    "attitude_deg": ["pitch", "roll", "yaw"],
    "velocity_cm_s": ["vgx", "vgy", "vgz"],
    "acceleration_mg": ["agx", "agy", "agz"],
    "temperature_c": ["templ", "temph"],
    "distance_tof_cm": ["tof"],
    "height_cm": ["h"],
    "barometer_m": ["baro"],
    "battery_percent": ["bat"],
}


def build_video_stream_url(video_port: int) -> str:
    """Build TELLO video stream URL.

    Args:
        video_port: Local UDP port to receive TELLO video.

    Returns:
        Stream URL passed to OpenCV VideoCapture.
    """
    return f"udp://0.0.0.0:{video_port}"


class RerunDashboard:
    """Dashboard that streams TELLO video and state into Rerun."""

    def __init__(
        self,
        video_port: int,
        state_provider: Callable[[], tuple[str | None, dict[str, Any] | None]],
        state_interval_sec: float = 0.2,
        video_reconnect_interval_sec: float = 0.5,
    ) -> None:
        """Initialize dashboard runtime.

        Args:
            video_port: Local UDP port for TELLO video stream.
            state_provider: Callable returning latest raw and parsed state.
            state_interval_sec: Polling interval for dashboard state plots.
            video_reconnect_interval_sec: Delay before reopening camera stream.
        """
        self._video_port = video_port
        self._state_provider = state_provider
        self._state_interval_sec = state_interval_sec
        self._video_reconnect_interval_sec = video_reconnect_interval_sec

        self._cv2 = None
        self._rr = None

        self._stop_event = threading.Event()
        self._video_thread: threading.Thread | None = None
        self._state_thread: threading.Thread | None = None

        self._writer = None
        self._writer_lock = threading.Lock()
        self._recording_path: Path | None = None
        self._state_tick = 0
        self._latest_frame_bgr: Any | None = None
        self._latest_frame_ts: float | None = None
        self._frame_lock = threading.Lock()

    def start(self) -> None:
        """Start dashboard and spawn viewer.

        Raises:
            RuntimeError: If required packages are not installed.
        """
        self._configure_decoder_logging()

        try:
            self._cv2 = importlib.import_module("cv2")
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "opencv-python is not installed. Install it to decode TELLO stream."
            ) from error
        self._apply_opencv_log_level()

        try:
            self._rr = importlib.import_module("rerun")
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "rerun-sdk is not installed. Install it to open the dashboard."
            ) from error

        self._rr.init("drone_ai_agent", spawn=True)
        self._send_default_blueprint()
        self._stop_event.clear()

        self._video_thread = threading.Thread(target=self._run_video_loop, daemon=True)
        self._state_thread = threading.Thread(target=self._run_state_loop, daemon=True)

        self._video_thread.start()
        self._state_thread.start()

    def _send_default_blueprint(self) -> None:
        """Send default dashboard layout with separated motion/status views."""
        assert self._rr is not None
        if not hasattr(self._rr, "blueprint") or not hasattr(
            self._rr, "send_blueprint"
        ):
            return

        try:
            bp = self._rr.blueprint
            group_views = [
                bp.TimeSeriesView(
                    name=group_name,
                    origin="/",
                    contents=[f"drone/state_groups/{group_name}/**"],
                )
                for group_name in PLOT_GROUPS
            ]
            layout = bp.Blueprint(
                bp.Vertical(
                    bp.Horizontal(
                        bp.Spatial2DView(
                            name="camera",
                            origin="/",
                            contents=["/drone/camera"],
                        ),
                        bp.TextDocumentView(
                            name="state_raw",
                            origin="/",
                            contents=["/drone/state/raw"],
                        ),
                    ),
                    bp.Grid(
                        contents=group_views,
                        grid_columns=3,
                        name="state_plots",
                    ),
                    name="dashboard",
                ),
                auto_views=False,
            )
            self._rr.send_blueprint(layout, make_active=True, make_default=True)
        except Exception:
            return

    def _configure_decoder_logging(self) -> None:
        """Configure decoder logging level before OpenCV starts FFmpeg."""
        os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "8")

    def stop(self) -> None:
        """Stop dashboard background loops and close resources."""
        self._stop_event.set()

        if self._video_thread is not None:
            self._video_thread.join(timeout=2)
            self._video_thread = None

        if self._state_thread is not None:
            self._state_thread.join(timeout=2)
            self._state_thread = None

        self.stop_recording()

    def start_recording(self, output_path: Path) -> None:
        """Start recording incoming video stream to MP4 file.

        Args:
            output_path: Output file path.
        """
        with self._writer_lock:
            self._recording_path = output_path

    def stop_recording(self) -> None:
        """Stop recording and close writer if active."""
        with self._writer_lock:
            if self._writer is not None:
                self._writer.release()
                self._writer = None
            self._recording_path = None

    def get_latest_jpeg_base64(self) -> dict[str, Any] | None:
        """Get latest dashboard frame as base64 JPEG payload.

        Returns:
            Frame payload, or None when no frame is available yet.
        """
        if self._cv2 is None:
            return None

        with self._frame_lock:
            if self._latest_frame_bgr is None or self._latest_frame_ts is None:
                return None
            frame = self._latest_frame_bgr.copy()
            frame_ts = self._latest_frame_ts

        ok, encoded = self._cv2.imencode(".jpg", frame)
        if not ok:
            return None

        height, width = frame.shape[:2]
        return {
            "mime_type": "image/jpeg",
            "width": int(width),
            "height": int(height),
            "timestamp_unix": frame_ts,
            "data_base64": base64.b64encode(encoded.tobytes()).decode("ascii"),
        }

    def _run_video_loop(self) -> None:
        """Video thread: decode TELLO stream, log image, optionally record."""
        assert self._cv2 is not None
        assert self._rr is not None

        stream_url = build_video_stream_url(self._video_port)
        capture_backend = getattr(self._cv2, "CAP_FFMPEG", 0)
        cap = None

        try:
            while not self._stop_event.is_set():
                if cap is None:
                    cap = self._cv2.VideoCapture(stream_url, capture_backend)
                    if not cap.isOpened():
                        cap.release()
                        cap = None
                        time.sleep(self._video_reconnect_interval_sec)
                        continue

                ok, frame = cap.read()
                if not ok:
                    cap.release()
                    cap = None
                    time.sleep(self._video_reconnect_interval_sec)
                    continue

                with self._frame_lock:
                    self._latest_frame_bgr = frame
                    self._latest_frame_ts = time.time()

                self._set_time_now("time")
                rgb_frame = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)
                self._rr.log("drone/camera", self._rr.Image(rgb_frame))

                self._write_video_frame_if_needed(frame)
        finally:
            if cap is not None:
                cap.release()

    def _run_state_loop(self) -> None:
        """State thread: poll latest telemetry and log scalar series."""
        assert self._rr is not None

        while not self._stop_event.is_set():
            raw, state = self._state_provider()
            if state:
                self._state_tick += 1
                self._set_time_now("time")
                self._set_time_sequence("state_tick", self._state_tick)
                self._rr.log("drone/state/raw", self._rr.TextDocument(raw or ""))
                for key, value in state.items():
                    if isinstance(value, (int, float)):
                        self._log_scalar(f"drone/state/{key}", float(value))
                        for group_name, fields in PLOT_GROUPS.items():
                            if key in fields:
                                self._log_scalar(
                                    f"drone/state_groups/{group_name}/{key}",
                                    float(value),
                                )
            time.sleep(self._state_interval_sec)

    def _set_time_sequence(self, timeline: str, sequence: int) -> None:
        """Set timeline sequence for both old and new rerun APIs.

        Args:
            timeline: Timeline name.
            sequence: Sequence index.
        """
        assert self._rr is not None
        if hasattr(self._rr, "set_time"):
            self._rr.set_time(timeline, sequence=sequence)
            return
        if hasattr(self._rr, "set_time_sequence"):
            self._rr.set_time_sequence(timeline, sequence)

    def _set_time_now(self, timeline: str) -> None:
        """Set current wall time for both old and new rerun APIs.

        Args:
            timeline: Timeline name.
        """
        assert self._rr is not None
        now = time.time()
        if hasattr(self._rr, "set_time"):
            self._rr.set_time(timeline, timestamp=now)
            return
        self._set_time_sequence(timeline, int(now * 1000))

    def _log_scalar(self, path: str, value: float) -> None:
        """Log scalar with compatibility across rerun versions.

        Args:
            path: Entity path.
            value: Scalar value.
        """
        assert self._rr is not None
        if hasattr(self._rr, "Scalar"):
            self._rr.log(path, self._rr.Scalar(value))
            return
        if hasattr(self._rr, "Scalars"):
            self._rr.log(path, self._rr.Scalars([value]))

    def _apply_opencv_log_level(self) -> None:
        """Reduce OpenCV logger output as much as available in this build."""
        assert self._cv2 is not None
        logging_mod = getattr(getattr(self._cv2, "utils", None), "logging", None)
        if logging_mod is None:
            return
        set_level = getattr(logging_mod, "setLogLevel", None)
        silent_level = getattr(logging_mod, "LOG_LEVEL_SILENT", None)
        if callable(set_level) and silent_level is not None:
            set_level(silent_level)

    def _write_video_frame_if_needed(self, frame: Any) -> None:
        """Write one frame into current recording if requested.

        Args:
            frame: BGR image frame.
        """
        assert self._cv2 is not None

        with self._writer_lock:
            if self._recording_path is None:
                if self._writer is not None:
                    self._writer.release()
                    self._writer = None
                return

            if self._writer is None:
                height, width = frame.shape[:2]
                fourcc = self._cv2.VideoWriter_fourcc(*"mp4v")
                self._writer = self._cv2.VideoWriter(
                    str(self._recording_path),
                    fourcc,
                    30.0,
                    (width, height),
                )
            self._writer.write(frame)
