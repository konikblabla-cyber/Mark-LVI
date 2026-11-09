"""Resource placement policy for local TTS inference."""
from __future__ import annotations


def select_tts_device(requested: str | None, cuda_available: bool) -> str:
    """Use CPU by default; CUDA requires explicit opt-in via setting or env."""
    choice = str(requested or "cpu").strip().lower()
    if choice in {"auto", "cuda"} and cuda_available:
        return "cuda"
    return "cpu"
