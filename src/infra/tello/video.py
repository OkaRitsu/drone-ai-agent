"""Video stream utilities for TELLO."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any


def build_video_stream_url(video_port: int) -> str:
    """Build TELLO video stream URL.

    Args:
        video_port: Local UDP port to receive TELLO video stream.

    Returns:
        OpenCV-compatible stream URL.
    """
    return f"udp://0.0.0.0:{video_port}"


class TelloVideoHub:
    """Receive TELLO video stream and provide latest frame/recording."""

    def __init__(self, video_port: int = 11111, reconnect_interval_sec: float = 0.5) -> None:
        """Initialize video hub.

        Args:
            video_port: Local UDP port bound by OpenCV.
            reconnect_interval_sec: Delay before reopening stream when disconnected.
        """
        self._video_port = video_port
        self._reconnect_interval_sec = reconnect_interval_sec

        self._cv2: Any | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        self._latest_frame_bgr = None
        self._latest_frame_ts: float | None = None
        self._frame_lock = threading.Lock()

        self._writer = None
        self._recording_path: Path | None = None
        self._writer_lock = threading.Lock()

    def start(self) -> None:
        """Start background stream receiver."""
        if self._thread is not None:
            return

        os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "8")
        try:
            import cv2  # type: ignore
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "opencv-python is not installed. Install it to use get_frame."
            ) from error

        self._cv2 = cv2
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop background stream receiver."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self.stop_recording()

    def start_recording(self, output_path: Path) -> None:
        """Start recording frames to MP4.

        Args:
            output_path: Output file path.
        """
        with self._writer_lock:
            self._recording_path = output_path

    def stop_recording(self) -> None:
        """Stop active recording."""
        with self._writer_lock:
            self._recording_path = None
            if self._writer is not None:
                self._writer.release()
                self._writer = None

    def get_latest_jpeg_bytes(self) -> bytes | None:
        """Get latest frame as JPEG bytes.

        Returns:
            Encoded JPEG bytes or None if frame is unavailable.
        """
        if self._cv2 is None:
            return None

        with self._frame_lock:
            if self._latest_frame_bgr is None or self._latest_frame_ts is None:
                return None
            frame = self._latest_frame_bgr.copy()

        ok, encoded = self._cv2.imencode(".jpg", frame)
        if not ok:
            return None
        return encoded.tobytes()

    def _run(self) -> None:
        """Capture loop that updates latest frame and recording output."""
        assert self._cv2 is not None
        capture = None

        try:
            while not self._stop_event.is_set():
                if capture is None:
                    capture = self._open_capture()
                    if capture is None:
                        time.sleep(self._reconnect_interval_sec)
                        continue

                ok, frame = capture.read()
                if not ok:
                    capture.release()
                    capture = None
                    continue

                with self._frame_lock:
                    self._latest_frame_bgr = frame
                    self._latest_frame_ts = time.time()

                self._write_video_frame_if_needed(frame)
        finally:
            if capture is not None:
                capture.release()

    def _open_capture(self) -> Any | None:
        """Open OpenCV capture for TELLO UDP stream.

        Returns:
            Opened capture object or None on failure.
        """
        assert self._cv2 is not None
        backend = getattr(self._cv2, "CAP_FFMPEG", 0)
        capture = self._cv2.VideoCapture(build_video_stream_url(self._video_port), backend)
        if not capture.isOpened():
            capture.release()
            return None
        return capture

    def _write_video_frame_if_needed(self, frame: Any) -> None:
        """Write one frame to recorder when recording is active.

        Args:
            frame: BGR frame from OpenCV capture.
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
                    (int(width), int(height)),
                )
            self._writer.write(frame)
