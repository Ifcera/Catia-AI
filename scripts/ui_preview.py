"""Arayüzü ekrana açmadan örnek bir konuşmayla çizer ve PNG kaydeder (görsel kontrol için).

    set QT_QPA_PLATFORM=offscreen & .venv\\Scripts\\python.exe scripts\\ui_preview.py out.png
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_aibot.agent.loop import AgentEvent  # noqa: E402
from catia_aibot.ui.controller import AgentController  # noqa: E402
from catia_aibot.ui.theme import STYLE  # noqa: E402
from catia_aibot.ui.window import AssistantWindow  # noqa: E402


class PreviewController(AgentController):
    """Hiçbir şeye bağlanmayan denetleyici (yalnızca sinyaller)."""

    def poll_catia(self):
        self.catia_status.emit(True)

    def list_models(self, backend):
        self.models_listed.emit(backend, ["", "opus", "sonnet"])


def demo(w: AssistantWindow, ctl: PreviewController, shot: str | None):
    ctl.connected.emit({"ok": True, "backend": "claude_code", "label": "Claude Code · pro", "model": "varsayılan",
                        "detail": "Claude Code · pro · varsayılan"})
    ctl.backend = "claude_code"
    ev = lambda k, **d: ctl.event.emit(AgentEvent(k, d))  # noqa: E731
    w._send_text_only = True
    w._last_user = None
    from catia_aibot.ui.widgets import UserBubble
    ub = UserBubble("50 × 30 × 10 mm blok yap, ortasından boydan boya 8 mm delik aç, kontrol et ve kaydet.")
    w._add(ub)
    ev("turn_start", text="...")
    ev("plan", plan={"summary": "Tek parça: dikdörtgen blok + ortada boydan boya delik.",
                     "parts": [{"name": "Blok", "method": "XY'de ortalı dikdörtgen sketch → pad 10 mm → üst yüze Ø8 delik",
                                "dimensions": "50 × 30 × 10 mm, Ø8", "expected_volume_mm3": 14497.35,
                                "expected_bbox_size": [50, 30, 10]}]})
    ev("tool_call", id="1", name="new_part", input={"name": "Blok"})
    ev("tool_result", id="1", name="new_part", ok=True, data={"ok": True, "document": "Blok.CATPart"}, images=[], seconds=0.6)
    ev("tool_call", id="2", name="create_sketch", input={"support": "XY", "elements": [{"type": "rectangle"}]})
    ev("tool_result", id="2", name="create_sketch", ok=True, data={"ok": True, "sketch": "Sketch.1", "fully_constrained": True}, images=[], seconds=0.4)
    ev("tool_call", id="3", name="pad", input={"sketch": "Sketch.1", "length": 10})
    ev("tool_result", id="3", name="pad", ok=True, data={"ok": True, "feature": "Pad.1", "volume_mm3": 15000, "volume_change_mm3": 15000}, images=[], seconds=0.3)
    ev("tool_call", id="4", name="fillet", input={"edges": {"parallel_to": "Z"}, "radius": 40})
    ev("tool_result", id="4", name="fillet", ok=False, data={"ok": False, "error": "EdgeFillet.1 güncellenemedi (update hatası). Değişiklik geri alındı.", "hint": "Yarıçapı küçültün."}, images=[], seconds=0.5)
    ev("tool_call", id="5", name="hole", input={"diameter": 8, "through_all": True})
    ev("tool_result", id="5", name="hole", ok=True, data={"ok": True, "feature": "Hole.1", "volume_mm3": 14497.345, "volume_change_mm3": -502.655}, images=[], seconds=0.7)
    ev("tool_call", id="6", name="check_expectation", input={})
    ev("tool_result", id="6", name="check_expectation", ok=True, data={"ok": True, "passed": True, "checks": [
        {"check": "update", "pass": True}, {"check": "volume_mm3", "expected": 14497.35, "measured": 14497.345, "deviation_pct": 0.0, "pass": True},
        {"check": "bbox_size", "expected": [50, 30, 10], "measured": [50.0, 30.0, 10.0], "pass": True}]}, images=[], seconds=1.2)
    img = sorted((ROOT / "work").rglob("view_iso_*.jpg"))
    ev("tool_call", id="7", name="screenshot", input={"views": ["iso"]})
    ev("tool_result", id="7", name="screenshot", ok=True, data={"ok": True}, images=[str(img[-1])] if img else [], seconds=0.3)
    sys.path.insert(0, str(ROOT / "tests"))
    from test_ui_offline import LONG_MD
    ev("assistant_text", text=LONG_MD)
    ev("assistant_text", text="Blok hazır ve kaydedildi.\n\n| | Beklenen | Ölçülen |\n|---|---|---|\n| Hacim | 14497,35 mm³ | 14497,345 mm³ |\n| Boyut | 50 × 30 × 10 | 50 × 30 × 10 |")
    ev("usage", requests=8, input=12, output=1363, cache_read=109003, cache_write=3308, cost_usd=None,
       rate_limit={"five_hour_utilization": 0.31})
    ctl.turn_finished.emit(type("R", (), {"status": "done"})())


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    settings = QSettings(str(Path(tempfile.gettempdir()) / "catia_aibot_preview.ini"), QSettings.Format.IniFormat)
    ctl = PreviewController()
    w = AssistantWindow(ctl, settings)
    w.resize(440, 1250)
    w.show()
    demo(w, ctl, None)
    w.ws_label.setText(str(Path(tempfile.gettempdir()) / "catia_aibot_preview_ws"))
    probe = ROOT / "work" / "agent_cc" / "probe_foto.png"
    pics = [str(probe)] if probe.exists() else []
    pics += [str(p) for p in sorted((ROOT / "work").rglob("view_iso_*.jpg"))[-1:]]
    if pics:
        w._add_images(pics)
    for _ in range(20):
        app.processEvents()
    out = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "work" / "ui_preview.png")
    w.scroll.verticalScrollBar().setValue(0)
    app.processEvents()
    w.grab().save(out)
    sb = w.scroll.verticalScrollBar()
    sb.setValue(sb.maximum())
    app.processEvents()
    w.grab().save(out.replace(".png", "_alt.png"))
    print("kaydedildi:", out)


if __name__ == "__main__":
    main()
