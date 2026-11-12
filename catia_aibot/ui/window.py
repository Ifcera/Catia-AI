"""Asistan penceresi: CATIA'nın yanında duran dar, koyu temalı sohbet paneli."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ..agent.providers import PROVIDERS
from . import labels
from ..i18n import LANGUAGES, lang, set_lang, t
from .controller import BACKENDS, AgentController
from .key_dialog import KeyDialog
from .attachments import IMAGE_SUFFIXES, MAX_PER_MESSAGE, AttachmentError, prepare_image
from .widgets import (AssistantText, AttachmentBar, NoteRow, PlanCard, PromptEdit, StepRow, UserBubble, badge,
                      images_from_mime, set_badge)

DEFAULT_WIDTH = 440


class _ChatHolder(QWidget):
    """Sohbet akışının taşıyıcısı. Satır kaydıran etiketler en küçük yüksekliklerini şişirerek bildirdiği için
    kaydırma alanı içeriği gerçekte olduğundan uzun yapıyor ve sohbetin altında boşluk kalıyordu. Burada en küçük
    yükseklik bildirilmez; kaydırma alanı yüksekliği yerleşimin o genişlikteki gerçek yüksekliğinden (heightForWidth) alır."""

    def minimumSizeHint(self):  # noqa: N802
        return QSize(0, 0)

    def sizeHint(self):  # noqa: N802
        w = max(self.width(), 1)
        return QSize(w, self.heightForWidth(w))
EFFORTS = ["low", "medium", "high", "xhigh"]


class AssistantWindow(QWidget):
    def __init__(self, controller: AgentController | None = None, settings: QSettings | None = None):
        super().__init__()
        self.ctl = controller or AgentController()
        self.settings = settings or QSettings("CatiaAiBot", "Assistant")
        set_lang(str(self.settings.value("language", "tr")))
        self.setWindowTitle(t("CATIA Asistanı"))
        self.resize(DEFAULT_WIDTH, 900)
        self.setMinimumWidth(360)
        self._rows: dict[str, StepRow] = {}
        self._cur_text: AssistantText | None = None
        self._last_user: UserBubble | None = None
        self._plans: list[PlanCard] = []
        self._part = ""
        self._connected = False
        self._agent_label = t("Bağlı")
        self._attachments: list[str] = []
        self._phase_text, self._phase_since = "", 0.0
        self.setAcceptDrops(True)
        self._build()
        self._wire()
        self._load_settings()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.ctl.poll_catia)
        self._timer.start(5000)
        self.ctl.poll_catia()
        self._phase_timer = QTimer(self)
        self._phase_timer.timeout.connect(self._show_phase)
        self._phase_timer.start(1000)

    # ================================================================ kurulum
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # Başlık + rozetler
        head = QHBoxLayout()
        title_lb = QLabel(t("Asistan"))
        title_lb.setObjectName("title")
        head.addWidget(title_lb)
        self.catia_badge = badge("CATIA ●", "off")
        self.agent_badge = badge(t("Agent bağlı değil"), "off")
        head.addWidget(self.catia_badge)
        head.addWidget(self.agent_badge)
        head.addStretch(1)
        self.pin_btn = QPushButton("📌")
        self.pin_btn.setObjectName("icon")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip(t("Her zaman üstte"))
        self.dock_btn = QPushButton("⇥")
        self.dock_btn.setObjectName("icon")
        self.dock_btn.setToolTip(t("CATIA'nın yanına yasla"))
        self.gear_btn = QPushButton("⚙")
        self.gear_btn.setObjectName("icon")
        self.gear_btn.setCheckable(True)
        self.gear_btn.setChecked(True)
        self.gear_btn.setToolTip(t("Bağlantı ayarları"))
        for b in (self.pin_btn, self.dock_btn, self.gear_btn):
            head.addWidget(b)
        root.addLayout(head)

        # Ayarlar
        self.settings_panel = QFrame()
        self.settings_panel.setObjectName("panel")
        g = QGridLayout(self.settings_panel)
        g.setContentsMargins(10, 8, 10, 8)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(6)
        self.backend = QComboBox()
        for k, v in BACKENDS.items():
            self.backend.addItem(t(v), k)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.key_btn = QPushButton(t("Anahtar…"))
        self.effort = QComboBox()
        self.effort.addItems(EFFORTS)
        self.ws_label = QLabel("")
        self.ws_label.setObjectName("muted")
        self.ws_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.ws_btn = QPushButton(t("Seç…"))
        self.connect_btn = QPushButton(t("Bağlan"))
        self.connect_btn.setObjectName("primary")
        g.addWidget(QLabel(t("Sağlayıcı")), 0, 0)
        g.addWidget(self.backend, 0, 1, 1, 2)
        g.addWidget(QLabel("Model"), 1, 0)
        g.addWidget(self.model, 1, 1)
        g.addWidget(self.key_btn, 1, 2)
        g.addWidget(QLabel(t("Klasör")), 2, 0)
        g.addWidget(self.ws_label, 2, 1)
        g.addWidget(self.ws_btn, 2, 2)
        g.addWidget(QLabel(t("Özen")), 3, 0)
        g.addWidget(self.effort, 3, 1)
        g.addWidget(self.connect_btn, 3, 2)
        self.language = QComboBox()
        for k, v in LANGUAGES.items():
            self.language.addItem(v, k)
        self.language.setCurrentIndex(max(self.language.findData(lang()), 0))
        self.language.setToolTip("Dil / Language")
        g.addWidget(QLabel(t("Dil")), 4, 0)
        g.addWidget(self.language, 4, 1, 1, 2)
        self.backend_note = QLabel("")
        self.backend_note.setObjectName("muted")
        self.backend_note.setWordWrap(True)
        g.addWidget(self.backend_note, 5, 0, 1, 3)
        g.setColumnStretch(1, 1)
        root.addWidget(self.settings_panel)

        # İlerleme satırı
        self.progress = QLabel(t("Bağlanmak için ayarları seçip 'Bağlan'a basın."))
        self.progress.setObjectName("progress")
        self.progress.setWordWrap(True)
        root.addWidget(self.progress)

        # Sohbet akışı
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = _ChatHolder()
        self.chat = QVBoxLayout(holder)
        self.chat.setContentsMargins(0, 0, 4, 0)
        self.chat.setSpacing(6)
        self.chat.addStretch(1)
        self.scroll.setWidget(holder)
        root.addWidget(self.scroll, 1)
        intro = NoteRow(t("CATIA'da ne yapılmasını istediğinizi yazın (ör. \"50×30×10 mm blok, ortasına Ø8 delik\"). "
                          "Değişiklikler normal CATIA feature'ları olarak oluşur; her mesaj tek adımda geri alınabilir."))
        self._add(intro)

        # Giriş kutusu
        box = QFrame()
        box.setObjectName("inputBox")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(8, 6, 8, 6)
        bl.setSpacing(4)
        self.attach_bar = AttachmentBar()
        bl.addWidget(self.attach_bar)
        self.prompt = PromptEdit()
        self.prompt.setPlaceholderText(t("Ne yapılsın?  (Enter: gönder · Shift+Enter: yeni satır · fotoğraf: + / sürükle / Ctrl+V)"))
        self.prompt.setFixedHeight(72)
        bl.addWidget(self.prompt)
        row = QHBoxLayout()
        self.add_btn = QPushButton("+")
        self.add_btn.setObjectName("icon")
        self.add_btn.setFixedWidth(28)
        self.add_btn.setToolTip(t("Fotoğraf ekle (en fazla {n}). Pencereye sürükleyip bırakabilir "
                                  "ya da panodan Ctrl+V ile yapıştırabilirsiniz.").format(n=MAX_PER_MESSAGE))
        row.addWidget(self.add_btn)
        self.cb_view = QCheckBox(t("Görünümü ekle"))
        self.cb_view.setToolTip(t("CATIA'nın o anki görüntüsünü mesaja iliştir"))
        self.cb_sel = QCheckBox(t("Seçimi ekle"))
        self.cb_sel.setToolTip(t("CATIA'da seçili öğeleri bağlam olarak gönder"))
        row.addWidget(self.cb_view)
        row.addWidget(self.cb_sel)
        row.addStretch(1)
        self.undo_btn = QPushButton("↶")
        self.undo_btn.setObjectName("icon")
        self.undo_btn.setToolTip(t("Son mesajın CATIA'daki tüm değişikliklerini geri al"))
        self.send_btn = QPushButton("↑")
        self.send_btn.setObjectName("send")
        self.send_btn.setFixedSize(34, 34)
        self.send_btn.setToolTip(t("Gönder"))
        row.addWidget(self.undo_btn)
        row.addWidget(self.send_btn)
        bl.addLayout(row)
        root.addWidget(box)

        foot = QHBoxLayout()
        self.usage = QLabel("")
        self.usage.setObjectName("muted")
        self.new_btn = QPushButton(t("Yeni konuşma"))
        self.new_btn.setObjectName("link")
        foot.addWidget(self.usage, 1)
        foot.addWidget(self.new_btn)
        root.addLayout(foot)
        self._set_busy(False)

    def _wire(self):
        c = self.ctl
        c.event.connect(self._on_event)
        c.turn_finished.connect(self._on_turn_finished)
        c.connected.connect(self._on_connected)
        c.catia_status.connect(self._on_catia)
        c.models_listed.connect(self._on_models)
        c.notice.connect(lambda kind, text: self._add(NoteRow(text, kind)))
        c.busy_changed.connect(self._set_busy)
        c.user_attachment.connect(self._on_attachment)
        self.backend.currentIndexChanged.connect(self._on_backend_changed)
        self.key_btn.clicked.connect(self._open_keys)
        self.ws_btn.clicked.connect(self._pick_workspace)
        self.connect_btn.clicked.connect(self._connect)
        self.prompt.submit.connect(self._send)
        self.prompt.images_added.connect(self._add_images)
        self.add_btn.clicked.connect(self._pick_images)
        self.attach_bar.removed.connect(self._remove_attachment)
        self.send_btn.clicked.connect(self._send_or_stop)
        self.undo_btn.clicked.connect(self.ctl.undo_last)
        self.pin_btn.toggled.connect(self._set_on_top)
        self.dock_btn.clicked.connect(self._dock)
        self.gear_btn.toggled.connect(self.settings_panel.setVisible)
        self.new_btn.clicked.connect(self._new_conversation)
        self.language.currentIndexChanged.connect(self._on_language_changed)

    # ============================================================ ayarlar
    def _load_settings(self):
        s = self.settings
        default_ws = str(Path.home() / "Documents" / "CatiaAiBot")
        self.ws_label.setText(s.value("workspace", default_ws))
        self.ws_label.setToolTip(self.ws_label.text())
        i = self.backend.findData(s.value("backend", "claude_code"))
        self.backend.setCurrentIndex(max(i, 0))
        self.effort.setCurrentText(s.value("effort", "high"))
        self._on_backend_changed()
        on_top = s.value("on_top", False, type=bool)
        self.pin_btn.setChecked(on_top)
        geo = s.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    def _on_language_changed(self):
        code = self.language.currentData()
        self.settings.setValue("language", code)
        if code != lang():
            self._add(NoteRow("Dil değişikliği uygulamayı yeniden başlatınca geçerli olur. / "
                              "The language change takes effect after restarting the app.", "warn"))

    def _save_settings(self):
        s = self.settings
        s.setValue("backend", self.backend.currentData())
        s.setValue(f"model/{self.backend.currentData()}", self._model_value())
        s.setValue("workspace", self.ws_label.text())
        s.setValue("effort", self.effort.currentText())
        s.setValue("on_top", self.pin_btn.isChecked())
        s.setValue("geometry", self.saveGeometry())

    def _model_value(self) -> str:
        i = self.model.currentIndex()
        if i >= 0 and self.model.itemText(i) == self.model.currentText():
            return self.model.itemData(i) or ""
        return self.model.currentText().strip()

    def _on_backend_changed(self):
        b = self.backend.currentData()
        self.key_btn.setVisible(b != "claude_code")
        self.model.clear()
        if b == "claude_code":
            self.backend_note.setText(t("Bilgisayarınızdaki Claude Code oturumu kullanılır; API anahtarı gerekmez, "
                                        "kullanım claude.ai aboneliğinizden düşer."))
        else:
            self.backend_note.setText(t("{p} kullanımı sağlayıcı hesabınızdan ücretlendirilir.").format(p=t(BACKENDS[b])))
            self.model.addItem(PROVIDERS[b].default_model, PROVIDERS[b].default_model)
        self.ctl.list_models(b)

    def _on_models(self, backend: str, models: list):
        if backend != self.backend.currentData():
            return
        saved = self.settings.value(f"model/{backend}", "")
        self.model.clear()
        if backend == "claude_code":
            for m in models:
                self.model.addItem(m or t("(varsayılan)"), m)
        else:
            default = PROVIDERS[backend].default_model
            for m in [default] + [m for m in models if m != default]:
                self.model.addItem(m, m)
        if saved:
            i = self.model.findData(saved)
            if i >= 0:
                self.model.setCurrentIndex(i)
            else:
                self.model.setEditText(saved)

    def _open_keys(self):
        KeyDialog(self.backend.currentData(), self).exec()
        self.ctl.list_models(self.backend.currentData())

    def _pick_workspace(self):
        d = QFileDialog.getExistingDirectory(self, t("Çalışma klasörü"), self.ws_label.text())
        if d:
            self.ws_label.setText(d)
            self.ws_label.setToolTip(d)

    def _set_on_top(self, on: bool):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()

    def _dock(self):
        from .winutil import dock_beside_catia

        msg = dock_beside_catia(int(self.winId()), int(self.width() * self.devicePixelRatioF()))
        self._add(NoteRow(t(msg), "info" if "yaslandı" in msg else "error"))

    # ========================================================== bağlantı
    def _connect(self):
        self._save_settings()
        set_badge(self.agent_badge, t("Bağlanıyor…"), "busy")
        self.connect_btn.setEnabled(False)
        self.ctl.connect_backend(self.backend.currentData(), self._model_value() or None, self.effort.currentText(),
                                 self.ws_label.text())

    def _on_connected(self, info: dict):
        self.connect_btn.setEnabled(True)
        self._connected = bool(info.get("ok"))
        if self._connected:
            self._agent_label = info.get("label", t("Bağlı"))
            set_badge(self.agent_badge, self._agent_label, "ok")
            self.progress.setText(t("Hazır · {d}").format(d=info.get("detail")))
            self._add(NoteRow(t("Bağlandı: {d} · klasör: {w}").format(d=info.get("detail"), w=self.ws_label.text()), "ok"))
            self.gear_btn.setChecked(False)
            self.prompt.setFocus()
        else:
            set_badge(self.agent_badge, t("Bağlı değil"), "err")
            self.progress.setText(t("Bağlanamadı."))
            self._add(NoteRow(info.get("detail", t("Bağlanamadı.")), "error"))
        self._set_busy(self.ctl.busy)

    def _on_catia(self, ok: bool):
        set_badge(self.catia_badge, "CATIA ●" if ok else t("CATIA kapalı"), "ok" if ok else "err")

    # ============================================================== tur
    def _send_or_stop(self):
        if self.ctl.busy:
            self.ctl.stop()
            self.progress.setText(t("Durduruluyor…"))
        else:
            self._send()

    def _send(self, text: str | None = None):
        if self.ctl.busy:
            return
        from_prompt = text is None
        text = (text if text is not None else self.prompt.toPlainText()).strip()
        uploads = list(self._attachments) if from_prompt else []
        if not text and uploads:
            text = t("Ekteki görüntüye bak.")
        if not text:
            return
        if not self._connected:
            self._add(NoteRow(t("Önce 'Bağlan' ile bir sağlayıcıya bağlanın."), "error"))
            self.gear_btn.setChecked(True)
            return
        for p in self._plans:
            p.lock()
        self._last_user = UserBubble(text)
        self._add(self._last_user)
        if from_prompt:
            self.prompt.clear()
            self._attachments = []
            self.attach_bar.set_paths([])
        self._cur_text = None
        self.ctl.send(text, self.cb_view.isChecked(), self.cb_sel.isChecked(), images=uploads)

    # =========================================================== fotoğraflar
    def _attach_dir(self) -> Path:
        return Path(self.ws_label.text()) / "attachments"

    def _add_images(self, items: list):
        for item in items:
            if len(self._attachments) >= MAX_PER_MESSAGE:
                self._add(NoteRow(t("Bir mesaja en fazla {n} fotoğraf eklenebilir.").format(n=MAX_PER_MESSAGE), "warn"))
                break
            try:
                self._attachments.append(str(prepare_image(item, self._attach_dir())))
            except AttachmentError as exc:
                self._add(NoteRow(str(exc), "error"))
        self.attach_bar.set_paths(self._attachments)

    def _pick_images(self):
        pattern = " ".join(f"*{s}" for s in sorted(IMAGE_SUFFIXES))
        files, _ = QFileDialog.getOpenFileNames(self, t("Fotoğraf ekle"), str(Path.home() / "Pictures"),
                                                t("Görüntüler ({p})").format(p=pattern))
        if files:
            self._add_images(files)

    def _remove_attachment(self, path: str):
        self._attachments = [p for p in self._attachments if p != path]
        self.attach_bar.set_paths(self._attachments)

    def dragEnterEvent(self, e):  # noqa: N802
        if images_from_mime(e.mimeData()):
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        imgs = images_from_mime(e.mimeData())
        if imgs:
            e.acceptProposedAction()
            self._add_images(imgs)

    def _on_attachment(self, images: list, context: str):
        if self._last_user is not None and (images or context):
            self._last_user.add_attachments(images, context)
            self._scroll_bottom()

    def _set_busy(self, busy: bool):
        self.send_btn.setText("■" if busy else "↑")
        self.send_btn.setObjectName("stop" if busy else "send")
        self.send_btn.setToolTip(t("Durdur") if busy else t("Gönder"))
        self.send_btn.style().unpolish(self.send_btn)
        self.send_btn.style().polish(self.send_btn)
        self.undo_btn.setEnabled(not busy and self._connected)
        self.new_btn.setEnabled(not busy and self._connected)
        self.connect_btn.setEnabled(not busy)
        if self._connected:
            set_badge(self.agent_badge, t("Çalışıyor…") if busy else self._agent_label, "busy" if busy else "ok")

    # Uzun düşünme aşamalarında "takıldı mı?" sorusunu önlemek için o anki evre ve geçen süre gösterilir.
    def _set_phase(self, text: str):
        if text != self._phase_text:
            self._phase_text, self._phase_since = text, time.monotonic()
        self._show_phase()

    def _show_phase(self):
        if not self._phase_text or not self.ctl.busy:
            return
        sec = int(time.monotonic() - self._phase_since)
        el = t("{s} sn").format(s=sec) if sec < 60 else t("{m} dk {s} sn").format(m=sec // 60, s=f"{sec % 60:02d}")
        self.progress.setText(self._progress_text(f"{self._phase_text}… {el}"))

    def _on_turn_finished(self, result):
        self._phase_text = ""
        status = getattr(result, "status", "")
        self.progress.setText(t({"plan": "Plan onayınızı bekliyor.", "stopped": "Durduruldu.",
                                 "error": "Hata ile bitti.", "step_limit": "Adım sınırına ulaşıldı."}.get(status, "Hazır.")))
        self._cur_text = None

    def _new_conversation(self):
        self.ctl.new_conversation()
        self._part = ""

    # ============================================================ olaylar
    def _on_event(self, e):
        d = e.data
        k = e.kind
        if k == "turn_start":
            self._set_phase(t("Model yanıtı bekleniyor"))
        elif k == "status":
            self._set_phase(t("Model yanıtı bekleniyor"))
        elif k == "thinking_delta":
            self._set_phase(t("Model düşünüyor"))
        elif k == "text_delta":
            self._set_phase(t("Yanıt yazılıyor"))
            if self._cur_text is None:
                self._cur_text = AssistantText()
                self._add(self._cur_text)
            self._cur_text.append(d.get("text", ""))
            self._scroll_bottom()
        elif k == "assistant_text":
            if self._cur_text is None:
                self._cur_text = AssistantText()
                self._add(self._cur_text)
            self._cur_text.finalize(d.get("text", ""))
            self._cur_text = None
        elif k == "tool_call":
            self._cur_text = None
            name = d.get("name", "")
            if name == "new_part":
                self._part = (d.get("input") or {}).get("name", "")
            row = StepRow(name, d.get("input") or {})
            self._rows[d.get("id")] = row
            self._add(row)
            self._set_phase(t("{x} çalışıyor").format(x=labels.label(name)))
        elif k == "tool_result":
            self._set_phase(t("Model yanıtı bekleniyor"))
            row = self._rows.get(d.get("id"))
            if row is not None:
                row.finish(bool(d.get("ok")), d.get("data") or {}, d.get("images") or [], d.get("seconds"))
            self._scroll_bottom()
        elif k == "plan":
            card = PlanCard(d.get("plan") or {})
            card.approve.connect(lambda: self._send(t("Plan onaylandı, başla.")))
            card.revise.connect(self._revise_plan)
            self._plans.append(card)
            self._add(card)
        elif k == "usage":
            self.usage.setText(labels.usage_text(d, self.ctl.backend or ""))
        elif k == "error":
            self._add(NoteRow(d.get("message", t("Hata")), "error"))
        elif k == "stopped":
            self._add(NoteRow(t("Durduruldu. Yarım kalan değişiklikler '↶' ile geri alınabilir."), "warn"))

    def _progress_text(self, step: str) -> str:
        return (t("Parça: {p} · ").format(p=self._part) if self._part else "") + step

    def _revise_plan(self):
        self.prompt.setPlainText(t("Planda şunları değiştir: "))
        self.prompt.setFocus()
        c = self.prompt.textCursor()
        c.movePosition(c.MoveOperation.End)
        self.prompt.setTextCursor(c)

    # ========================================================== yardımcı
    def _add(self, w: QWidget):
        self.chat.insertWidget(self.chat.count() - 1, w)
        self._scroll_bottom()

    def _scroll_bottom(self):
        QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    def closeEvent(self, ev):  # noqa: N802
        self._save_settings()
        self._timer.stop()
        self.ctl.shutdown()
        super().closeEvent(ev)
