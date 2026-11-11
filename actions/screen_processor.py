"""Explicit, one-shot computer display capture for MARK.

This module has no webcam, camera discovery, video stream, or camera dependency.
The only capture entry point reads the computer display after the user asks for
visual help. The resulting frame is handled by the screen-inspection path.
"""
from __future__ import annotations

import io

try:
    import mss
    import mss.tools
except ImportError:
    mss = None

try:
    import PIL.Image
except ImportError:
    PIL = None


_IMG_MAX_W = 1280
_IMG_MAX_H = 720
_JPEG_Q = 82


def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if PIL is None:
        return img_bytes, f"image/{source_format.lower()}"
    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as exc:
        print(f"[Vision] Image compression failed: {type(exc).__name__}")
        return img_bytes, f"image/{source_format.lower()}"


def _capture_screen() -> tuple[bytes, str]:
    """Capture the primary computer display; never opens a camera device."""
    if mss is None:
        raise RuntimeError("mss is not installed. Run: pip install mss")
    with mss.mss() as capture:
        monitors = capture.monitors
        target = monitors[1] if len(monitors) > 1 else monitors[0]
        shot = capture.grab(target)
        png = mss.tools.to_png(shot.rgb, shot.size)
    return _compress(png, "PNG")


__all__ = ["_capture_screen"]
