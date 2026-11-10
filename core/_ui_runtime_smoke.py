"""Construct the real JARVIS Qt window without audio or network startup."""
import sys

from ui import JarvisUI
from PyQt6.QtWidgets import QApplication


def main() -> None:
    ui = JarvisUI(face_path="")
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("JarvisUI did not initialise QApplication.")
    app.processEvents()
    if not ui._win.isVisible():
        raise RuntimeError("The JARVIS main window did not become visible.")
    # Let Qt construct and paint the HUD once, without starting the live loop
    # (microphone, network session and background monitor remain untouched).
    ui._win.repaint()
    app.processEvents()
    ui._win.close()
    app.processEvents()
    print("JARVIS main window and HUD constructed successfully.")


if __name__ == "__main__":
    main()
