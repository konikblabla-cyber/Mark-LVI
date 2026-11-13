"""Offline policy tests for MARK's screen-only visual input."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from actions import screen_processor


class ScreenProcessorPolicyTests(unittest.TestCase):
    def test_screen_capture_is_the_only_capture_entry_point(self):
        self.assertTrue(callable(screen_processor._capture_screen))
        self.assertFalse(hasattr(screen_processor, "_capture_camera"))
        self.assertFalse(hasattr(screen_processor, "_detect_camera_index"))
        self.assertFalse(hasattr(screen_processor, "_cv2_backend"))
        self.assertFalse(hasattr(screen_processor, "cv2"))

    def test_capture_reads_only_the_primary_display(self):
        capture = MagicMock()
        capture.__enter__.return_value = capture
        capture.monitors = [None, {"left": 0, "top": 0, "width": 800, "height": 600}]
        capture.grab.return_value = SimpleNamespace(rgb=b"rgb-data", size=(800, 600))
        fake_mss = SimpleNamespace(
            mss=lambda: capture,
            tools=SimpleNamespace(to_png=lambda rgb, size: b"screen-png"),
        )
        with patch.object(screen_processor, "mss", fake_mss), patch.object(
            screen_processor, "_compress", return_value=(b"screen-jpeg", "image/jpeg")
        ) as compress:
            result = screen_processor._capture_screen()
        capture.grab.assert_called_once_with(capture.monitors[1])
        compress.assert_called_once_with(b"screen-png", "PNG")
        self.assertEqual(result, (b"screen-jpeg", "image/jpeg"))

    def test_runtime_and_hud_expose_no_webcam_capture_path(self):
        root = Path(__file__).resolve().parents[1]
        main_source = (root / "main.py").read_text(encoding="utf-8")
        ui_source = (root / "ui.py").read_text(encoding="utf-8")
        processor_source = Path(screen_processor.__file__).read_text(encoding="utf-8")
        requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("opencv", requirements)
        for forbidden in ("_capture_camera", "close_camera", "start_camera_stream", "stop_camera_stream"):
            self.assertNotIn(forbidden, main_source)
        for forbidden in ("_CameraPreview", "_cam_loop", "start_camera_stream", "stop_camera_stream"):
            self.assertNotIn(forbidden, ui_source)
        for forbidden in ("cv2", "VideoCapture", "camera_index", "CAP_DSHOW"):
            self.assertNotIn(forbidden, processor_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
