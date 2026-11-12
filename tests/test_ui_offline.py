"""Arayüz testleri — ekran dışı (offscreen) Qt, sahte agent; CATIA ve ağ gerektirmez."""
from __future__ import annotations

import os
import threading
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from catia_aibot.agent.loop import AgentEvent, TurnResult
from catia_aibot.ui import labels
from catia_aibot.ui.controller import AgentController
from catia_aibot.ui.theme import STYLE
from catia_aibot.ui.widgets import NoteRow, PlanCard, StepRow, UserBubble
from catia_aibot.ui.window import AssistantWindow

from test_agent_offline import FakeTools


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(STYLE)
    return a


class FakeAgent:
    """Bir tur boyunca birkaç olay yayınlayan sahte agent."""

    def __init__(self, emit, block: threading.Event | None = None):
        self.emit, self.block = emit, block
        self.sent, self.stopped, self.undone = [], False, 0

    def send(self, text, images=None, context=None):
        self.sent.append((text, images, context))
        self.emit(AgentEvent("turn_start", {"text": text}))
        self.emit(AgentEvent("tool_call", {"id": "a", "name": "pad", "input": {"sketch": "Sketch.1", "length": 10}}))
        if self.block:
            self.block.wait(5)
        self.emit(AgentEvent("tool_result", {"id": "a", "name": "pad", "ok": True, "images": [], "seconds": 0.1,
                                             "data": {"ok": True, "feature": "Pad.1", "volume_mm3": 15000, "volume_change_mm3": 15000}}))
        self.emit(AgentEvent("text_delta", {"text": "Tam"}))
        self.emit(AgentEvent("assistant_text", {"text": "Tamam."}))
        return TurnResult("done", "Tamam.")

    def stop(self):
        self.stopped = True
        if self.block:
            self.block.set()

    def undo_last_message(self):
        self.undone += 1
        return {"group": "blok", "undone": ["Pad.1 sil"], "errors": []}

    def reset(self):
        pass


def pump(app, cond, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def make_window(app, tmp_path):
    ctl = AgentController()
    ctl.poll_catia = lambda: ctl.catia_status.emit(True)
    ctl.list_models = lambda b: ctl.models_listed.emit(b, ["", "opus"])
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    w = AssistantWindow(ctl, settings)
    return w, ctl


def widgets_of(w, cls):
    return [w.chat.itemAt(i).widget() for i in range(w.chat.count())
            if w.chat.itemAt(i).widget() is not None and isinstance(w.chat.itemAt(i).widget(), cls)]


def connect_fake(ctl, tmp_path, block=None):
    ctl.tools = FakeTools(tmp_path)
    ctl.workspace = tmp_path
    ctl.agent = FakeAgent(ctl._emit_event, block)
    ctl.backend = "claude_code"
    ctl.connected.emit({"ok": True, "backend": "claude_code", "label": "Claude Code · pro", "detail": "Claude Code · pro"})


def test_send_requires_connection(app, tmp_path):
    w, ctl = make_window(app, tmp_path)
    w.prompt.setPlainText("blok yap")
    w._send()
    app.processEvents()
    assert any("Bağlan" in n.findChildren(type(w.progress))[0].text() for n in widgets_of(w, NoteRow))
    assert not widgets_of(w, UserBubble)


def test_full_turn_flow_and_log(app, tmp_path):
    w, ctl = make_window(app, tmp_path)
    connect_fake(ctl, tmp_path)
    app.processEvents()
    assert w.agent_badge.text() == "Claude Code · pro"
    assert not w.gear_btn.isChecked()  # bağlanınca ayarlar paneli kapanır
    w.prompt.setPlainText("blok yap")
    w._send()
    assert pump(app, lambda: not ctl.busy and widgets_of(w, StepRow) and w.progress.text() == "Hazır.")
    rows = widgets_of(w, StepRow)
    assert len(rows) == 1 and rows[0].icon.text() == "✓" and "Pad.1" in rows[0].detail.text()
    assert widgets_of(w, UserBubble)[0] is not None
    assert w.send_btn.text() == "↑"
    log = list((tmp_path / "logs").glob("oturum_*.jsonl"))
    assert log and '"kind": "tool_result"' in log[0].read_text(encoding="utf-8")
    assert "text_delta" not in log[0].read_text(encoding="utf-8")


def test_stop_button_while_busy(app, tmp_path):
    w, ctl = make_window(app, tmp_path)
    block = threading.Event()
    connect_fake(ctl, tmp_path, block)
    app.processEvents()
    w._send("uzun iş")
    assert pump(app, lambda: ctl.busy and w.send_btn.text() == "■")
    assert not w.undo_btn.isEnabled()
    w._send_or_stop()
    assert pump(app, lambda: not ctl.busy)
    assert ctl.agent.stopped


def test_attach_view_and_selection(app, tmp_path):
    w, ctl = make_window(app, tmp_path)
    connect_fake(ctl, tmp_path)
    w.cb_view.setChecked(True)
    w.cb_sel.setChecked(True)
    w._send("bunu incele")
    assert pump(app, lambda: not ctl.busy and ctl.agent.sent)
    text, images, context = ctl.agent.sent[0]
    assert images and images[0].endswith("shot.jpg")
    names = [n for n, _ in ctl.tools.called]
    assert names[:2] == ["screenshot", "get_selection"]


def test_plan_card_approve_sends_message(app, tmp_path):
    w, ctl = make_window(app, tmp_path)
    connect_fake(ctl, tmp_path)
    app.processEvents()
    ctl.event.emit(AgentEvent("plan", {"plan": {"summary": "s", "parts": [{"name": "Taban", "method": "pad", "dimensions": "1"}]}}))
    app.processEvents()
    card = widgets_of(w, PlanCard)[0]
    card.ok_btn.click()
    assert pump(app, lambda: ctl.agent.sent and not ctl.busy)
    assert ctl.agent.sent[0][0] == "Plan onaylandı, başla."
    assert not card.ok_btn.isEnabled()


def test_undo_button(app, tmp_path):
    w, ctl = make_window(app, tmp_path)
    connect_fake(ctl, tmp_path)
    app.processEvents()
    w.undo_btn.click()
    assert pump(app, lambda: ctl.agent.undone == 1 and any("geri alındı" in n.findChildren(type(w.progress))[0].text()
                                                           for n in widgets_of(w, NoteRow)))


def _photo(path, size=(3000, 2000), mode="RGB"):
    from PIL import Image
    Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)).save(path)
    return str(path)


def test_prepare_image_resizes_and_flattens(tmp_path):
    from PIL import Image
    from catia_aibot.ui.attachments import MAX_EDGE, AttachmentError, prepare_image
    src = _photo(tmp_path / "buyuk.png", (4000, 1000), "RGBA")
    out = prepare_image(src, tmp_path / "att")
    with Image.open(out) as im:
        assert im.format == "JPEG" and im.mode == "RGB" and max(im.size) == MAX_EDGE
    assert Path(src).exists()  # asıl dosyaya dokunulmaz
    small = prepare_image(_photo(tmp_path / "k.jpg", (300, 200)), tmp_path / "att")
    with Image.open(small) as im:
        assert im.size == (300, 200)
    (tmp_path / "bozuk.png").write_bytes(b"not an image")
    with pytest.raises(AttachmentError):
        prepare_image(tmp_path / "bozuk.png", tmp_path / "att")
    with pytest.raises(AttachmentError):
        prepare_image(tmp_path / "yok.png", tmp_path / "att")


def test_attach_photos_send_and_clear(app, tmp_path):
    w, ctl = make_window(app, tmp_path)
    w.ws_label.setText(str(tmp_path))
    connect_fake(ctl, tmp_path)
    app.processEvents()
    w._add_images([_photo(tmp_path / "a.jpg"), _photo(tmp_path / "b.png")])
    assert len(w._attachments) == 2 and not w.attach_bar.isHidden()
    assert all(Path(p).parent == tmp_path / "attachments" for p in w._attachments)
    w._remove_attachment(w._attachments[0])
    assert len(w._attachments) == 1
    kept = w._attachments[0]
    w.prompt.setPlainText("")  # yalnız fotoğrafla gönderim de olur
    w._send()
    assert pump(app, lambda: ctl.agent.sent and not ctl.busy)
    text, images, _ = ctl.agent.sent[0]
    assert text == "Ekteki görüntüye bak." and images == [kept]
    assert w._attachments == []


def test_paste_image_bytes_and_limit(app, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl
    from PySide6.QtGui import QImage
    from catia_aibot.ui.attachments import MAX_PER_MESSAGE
    w, ctl = make_window(app, tmp_path)
    w.ws_label.setText(str(tmp_path))
    img = QImage(120, 80, QImage.Format.Format_RGB32)
    img.fill(0x3366CC)
    mime = QMimeData()
    mime.setImageData(img)
    assert w.prompt.canInsertFromMimeData(mime)
    w.prompt.insertFromMimeData(mime)  # Ctrl+V
    assert len(w._attachments) == 1 and w.prompt.toPlainText() == ""
    files = QMimeData()
    files.setUrls([QUrl.fromLocalFile(_photo(tmp_path / f"p{i}.jpg", (50, 50))) for i in range(6)])
    w.prompt.insertFromMimeData(files)  # sürükle-bırak / dosya yapıştırma
    assert len(w._attachments) == MAX_PER_MESSAGE
    text_only = QMimeData()
    text_only.setText("merhaba")
    w.prompt.insertFromMimeData(text_only)
    assert "merhaba" in w.prompt.toPlainText()


def test_labels_summaries():
    assert labels.result_summary("pad", {"ok": True, "feature": "Pad.1", "volume_mm3": 15000, "volume_change_mm3": 15000}).startswith("Pad.1 · 15 000.0 mm³")
    assert "ipucu" not in labels.result_summary("pad", {"ok": False, "error": "x", "hint": "y"}) and "— y" in labels.result_summary("pad", {"ok": False, "error": "x", "hint": "y"})
    lines = labels.check_lines({"checks": [{"check": "volume_mm3", "expected": 10, "measured": 9, "deviation_pct": -10, "pass": False}]})
    assert lines[0][0] is False and "Hacim" in lines[0][1]
    assert labels.usage_text({"rate_limit": {"five_hour_utilization": 0.31}}, "claude_code") == "Abonelik · 5 saatlik kullanım %31"
    assert "≈ $0.120" in labels.usage_text({"input": 10, "cache_read": 0, "output": 5, "cost_usd": 0.12}, "anthropic")
    assert labels.selection_context({"ok": True, "count": 1, "document": "A.CATPart",
                                     "items": [{"name": "Pad.1", "type": "Pad"}]}) == "A.CATPart: Pad.1 (Pad)"
    assert labels.selection_context({"ok": True, "count": 0}) is None


def test_step_row_failure_and_check(app):
    r = StepRow("fillet", {"radius": 40})
    r.finish(False, {"ok": False, "error": "update hatası", "hint": "küçült"}, [], 0.2)
    assert r.icon.text() == "✗" and "küçült" in r.detail.text()
    c = StepRow("check_expectation", {})
    c.finish(True, {"ok": True, "passed": False, "checks": [{"check": "update", "pass": True}]}, [], 1.0)
    assert c.icon.text() == "❌" and "KALDI" in c.title.text()


def test_assembly_check_lines_and_summaries():
    data = {"passed": False, "updated": True, "constraints": [{"name": "Fix.1"}],
            "constraint_problems": [],
            "clash": {"conflicts": [{"type": "çakışma", "a": "Blok.1", "b": "Pim.1", "value_mm": -10.0},
                                    {"type": "temas", "a": "Blok.1", "b": "Kol.1", "expected": True}]},
            "missing_expected_contacts": [["Kol.1", "Mil.1"]]}
    lines = labels.assembly_check_lines(data)
    assert (False, "Çakışma: Blok.1 ↔ Pim.1 (-10.0 mm)") in lines
    assert (True, "Temas: Blok.1 ↔ Kol.1 (beklenen)") in lines
    assert (False, "Beklenen temas yok: Kol.1 ↔ Mil.1") in lines
    s = labels.result_summary("add_assembly_constraint", {"ok": True, "constraint": {"name": "Coincidence.2", "type": "coincidence"},
                                                         "moved": [{"instance": "Pim.1"}]})
    assert s == "Coincidence.2 · coincidence · taşındı: Pim.1"
    assert labels.label("check_assembly") == "Montaj kontrolü"


def test_progress_shows_phase_and_elapsed(app, tmp_path):
    w, ctl = make_window(app, tmp_path)
    connect_fake(ctl, tmp_path)
    app.processEvents()
    ctl.busy = True
    ctl.event.emit(AgentEvent("thinking_delta", {"text": ""}))
    app.processEvents()
    assert w.progress.text().startswith("Model düşünüyor… ") and w.progress.text().endswith("sn")
    w._phase_since -= 125
    w._show_phase()
    assert "2 dk 05 sn" in w.progress.text()
    ctl.event.emit(AgentEvent("tool_call", {"id": "z", "name": "pad", "input": {}}))
    app.processEvents()
    assert w.progress.text().startswith("Pad çalışıyor…")
    ctl.busy = False


LONG_MD = ("orijinden ölçtüğü için bunlar tek bir ölçüye bağlı değil:\n\n"
           + "\n".join(f"- **Kol boyu ({i}):** `Sk_Kepce` içindeki `Offset.{i}`, `.31`, `.33` ve `Length.5` (85). Ayrıca uzun bir açıklama satırı."
                       for i in range(6))
           + "\n\nİsterseniz kol boyunu tek ölçüden yönetilecek şekilde yeniden kısıtlayabilirim.\n\n"
           + "**Kol konumunu değiştirmek:** Montajdaki `Kol_Aci` kısıtı şu an 0°.")


def test_assistant_text_height_fits_content(app, tmp_path):
    w, ctl = make_window(app, tmp_path)
    w.resize(440, 900)
    w.show()
    connect_fake(ctl, tmp_path)
    ctl.event.emit(AgentEvent("assistant_text", {"text": LONG_MD}))
    for _ in range(10):
        app.processEvents()
    from catia_aibot.ui.widgets import AssistantText
    at = widgets_of(w, AssistantText)[-1]
    doc_h = at.view.document().size().height()
    assert abs(at.view.height() - doc_h) <= 4, (at.view.height(), doc_h)
    assert "Kol_Aci" in at.text()
    # pencere daraltılınca metin yeniden sarılır ve yükseklik yine belgeye eşit olur
    w.resize(380, 900)
    for _ in range(10):
        app.processEvents()
    assert abs(at.view.height() - at.view.document().size().height()) <= 4
    w.hide()


def test_no_blank_space_below_last_message(app, tmp_path):
    """Satır kaydıran etiketlerin şişirilmiş en küçük yüksekliği sohbetin altında boşluk bırakıyordu."""
    w, ctl = make_window(app, tmp_path)
    w.resize(440, 700)
    w.show()
    connect_fake(ctl, tmp_path)
    for i in range(12):
        ctl.notice.emit("info", f"Uzun bir not satırı {i}: " + "kelime " * 30)
        ctl.event.emit(AgentEvent("assistant_text", {"text": LONG_MD}))
    for _ in range(20):
        app.processEvents()
    holder = w.scroll.widget()
    last = w.chat.itemAt(w.chat.count() - 2).widget()
    assert holder.height() > w.scroll.viewport().height()  # içerik kaydırılacak kadar uzun
    assert holder.height() - (last.y() + last.height()) <= 8, (holder.height(), last.y() + last.height())
    w.hide()


def test_english_language(app, tmp_path):
    from catia_aibot import i18n
    from catia_aibot.agent.prompts import system_prompt
    from catia_aibot.ui import labels

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    settings.setValue("language", "en")
    ctl = AgentController()
    ctl.poll_catia = lambda: ctl.catia_status.emit(True)
    ctl.list_models = lambda b: ctl.models_listed.emit(b, [""])
    try:
        w = AssistantWindow(ctl, settings)
        assert w.windowTitle() == "CATIA Assistant"
        assert w.connect_btn.text() == "Connect"
        assert w.backend.itemText(0) == "Claude Code (subscription)"
        assert labels.label("hole") == "Hole"
        assert "reply to the user in English" in system_prompt()
        w.language.setCurrentIndex(w.language.findData("tr"))
        assert settings.value("language") == "tr"
        w.hide()
    finally:
        i18n.set_lang("tr")
    assert labels.label("hole") == "Delik"
    assert "LANGUAGE:" not in system_prompt()
