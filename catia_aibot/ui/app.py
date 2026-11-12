"""Uygulama girişi:  .venv\\Scripts\\pythonw.exe -m catia_aibot.ui   (ya da CATIA-Asistan.bat)"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path


def _app_log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "CatiaAiBot"
    base.mkdir(parents=True, exist_ok=True)
    return base / "app.log"


def _ensure_streams() -> None:
    """Konsolsuz (pythonw) çalışırken sys.stdout/sys.stderr None'dır; onlara yazan ya da isatty() soran
    kütüphaneler çöker. Bu durumda ikisini de uygulama günlüğüne yönlendir ve yakalanmayan hataları kaydet."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log = open(_app_log_path(), "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - süreç boyunca açık
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log
    logging.basicConfig(stream=log, level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    def hook(exc_type, exc, tb):
        log.write("".join(traceback.format_exception(exc_type, exc, tb)))
        log.flush()

    sys.excepthook = hook


def main() -> int:
    _ensure_streams()
    from PySide6.QtWidgets import QApplication

    from .theme import STYLE
    from .window import AssistantWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("CATIA Asistanı")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    icon = Path(__file__).with_name("assets") / "icon.ico"
    if icon.exists():
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(str(icon)))
    try:  # görev çubuğunda python simgesi yerine uygulama simgesi görünsün
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CatiaAiBot.Assistant")
    except (AttributeError, OSError):
        pass
    w = AssistantWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
