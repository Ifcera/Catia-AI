"""Sohbet akışı bileşenleri."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy, QTextBrowser, QVBoxLayout,
                               QWidget)

from . import labels
from ..i18n import t
from .theme import C


class WrapLabel(QLabel):
    """Satır kaydıran QLabel, en küçük yüksekliğini dar bir varsayılan genişliğe göre (gerçekten büyük) bildirir;
    kaydırma alanı bu şişirilmiş değerlerin toplamını kullandığı için sohbetin altında boşluk kalıyordu.
    Burada en küçük yükseklik, etiketin o anki genişliğindeki gerçek yüksekliğidir."""

    def minimumSizeHint(self):  # noqa: N802
        s = super().minimumSizeHint()
        if self.wordWrap() and self.width() > 0:
            return QSize(0, self.heightForWidth(self.width()))
        return s

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.wordWrap() and e.size().width() != e.oldSize().width():
            self.updateGeometry()


def _label(text: str = "", obj: str | None = None, wrap: bool = True, markdown: bool = False) -> QLabel:
    lb = WrapLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    lb.setTextFormat(Qt.TextFormat.MarkdownText if markdown else Qt.TextFormat.PlainText)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    return lb


class ImageThumb(QLabel):
    """Tıklanınca görüntüyü varsayılan görüntüleyicide açan küçük resim."""

    def __init__(self, path: str, width: int = 240):
        super().__init__()
        self.path = path
        pm = QPixmap(path)
        if not pm.isNull():
            self.setPixmap(pm.scaledToWidth(min(width, pm.width()), Qt.TransformationMode.SmoothTransformation))
        else:
            self.setText(Path(path).name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(t("{n} — açmak için tıklayın").format(n=Path(path).name))
        self.setStyleSheet(f"border: 1px solid {C['border']}; border-radius: 4px;")

    def mousePressEvent(self, ev):  # noqa: N802
        if os.path.exists(self.path):
            os.startfile(self.path)  # noqa: S606 - kullanıcının kendi görüntüsü


def images_row(paths: list[str], width: int = 240) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 4, 0, 0)
    lay.setSpacing(4)
    for p in paths:
        lay.addWidget(ImageThumb(p, width), alignment=Qt.AlignmentFlag.AlignLeft)
    return w


class UserBubble(QFrame):
    def __init__(self, text: str):
        super().__init__()
        self.setObjectName("bubbleUser")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(10, 7, 10, 7)
        self.lay.addWidget(_label(text))

    def add_attachments(self, images: list[str], context: str):
        if context:
            self.lay.addWidget(_label(t("Seçim: {c}").format(c=context), "muted"))
        if images:
            self.lay.addWidget(images_row(images, 220))


class AutoTextView(QTextBrowser):
    """Yüksekliği içeriğe tam oturan salt-okunur metin görünümü.

    Satır kaydıran QLabel, uzun Markdown metinlerde (listeler, kod) yüksekliği fazla hesaplayıp altta boşluk bırakıyordu;
    burada yükseklik doğrudan belgenin o genişlikteki boyundan alınır."""

    def __init__(self):
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setOpenExternalLinks(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; }")
        self.document().setDocumentMargin(2)
        self.document().contentsChanged.connect(self._fit)

    def _fit(self):
        self.document().setTextWidth(max(self.viewport().width(), 50))
        h = int(self.document().size().height()) + 2
        if self.height() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()


class AssistantText(QFrame):
    """Akış sırasında düz metin biriktirir, tamamlanınca Markdown olarak gösterir."""

    def __init__(self):
        super().__init__()
        self.setObjectName("bubbleAssistant")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = AutoTextView()
        lay.addWidget(self.view)
        self.buf = ""

    def append(self, delta: str):
        self.buf += delta
        self.view.setPlainText(self.buf)

    def finalize(self, text: str):
        self.buf = text
        self.view.setMarkdown(text)

    def text(self) -> str:
        return self.view.toPlainText()


class StepRow(QFrame):
    """Bir araç çağrısı: çalışırken ⏳, bitince ✓ / ✗ ve kısa özet."""

    def __init__(self, name: str, args: dict):
        super().__init__()
        self.setObjectName("step")
        self.name = name
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.icon = QLabel("⏳")
        self.icon.setFixedWidth(18)
        self.title = QLabel(f"<b>{labels.label(name)}</b>")
        self.title.setTextFormat(Qt.TextFormat.RichText)
        summary = labels.input_summary(name, args)
        self.detail = _label(summary, "muted")
        top.addWidget(self.icon)
        top.addWidget(self.title)
        top.addWidget(self.detail, 1)
        self.secs = QLabel("")
        self.secs.setObjectName("muted")
        top.addWidget(self.secs)
        lay.addLayout(top)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(24, 0, 0, 2)
        self.body.setSpacing(1)
        lay.addLayout(self.body)
        if name in labels.QUIET_TOOLS:
            self.setStyleSheet("QFrame#step { background: transparent; border: none; }")

    def finish(self, ok: bool, data: dict, images: list[str], seconds: float | None):
        if self.name in ("check_expectation", "check_assembly") and ok:
            passed = bool(data.get("passed"))
            self.icon.setText("✅" if passed else "❌")
            what = t("Montaj kontrolü") if self.name == "check_assembly" else t("Kontrol")
            self.title.setText(f"<b>{what} {t('geçti') if passed else t('KALDI')}</b>")
            lines = labels.assembly_check_lines(data) if self.name == "check_assembly" else labels.check_lines(data)
            for good, txt in lines:
                lb = _label(("✓ " if good else "✗ ") + txt)
                lb.setStyleSheet(f"color: {C['ok'] if good else C['err']};")
                self.body.addWidget(lb)
            self.detail.setText("")
        else:
            self.icon.setText("✓" if ok else "✗")
            self.icon.setStyleSheet(f"color: {C['ok'] if ok else C['err']}; font-weight: 700;")
            summ = labels.result_summary(self.name, data)
            if summ:
                self.detail.setText(summ)
            if not ok:
                self.detail.setStyleSheet(f"color: {C['err']};")
            if ok and data.get("warning"):
                w = _label("⚠ " + data["warning"])
                w.setStyleSheet(f"color: {C['warn']};")
                self.body.addWidget(w)
        if seconds is not None:
            self.secs.setText(t("{s} sn").format(s=f"{seconds:.1f}"))
        if images:
            self.body.addWidget(images_row(images))


class NoteRow(QFrame):
    def __init__(self, text: str, kind: str = "info"):
        super().__init__()
        self.setObjectName("note")
        color = {"error": C["err"], "ok": C["ok"], "info": C["muted"], "warn": C["warn"]}.get(kind, C["muted"])
        self.setStyleSheet(f"QFrame#note {{ border-left: 3px solid {color}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 2, 4, 2)
        lb = _label(text)
        lb.setStyleSheet(f"color: {color};")
        lay.addWidget(lb)


class PlanCard(QFrame):
    approve = Signal()
    revise = Signal()

    def __init__(self, plan: dict):
        super().__init__()
        self.setObjectName("plan")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.addWidget(QLabel(t("<b>Plan</b> — onayınızı bekliyor")))
        if plan.get("summary"):
            lay.addWidget(_label(plan["summary"]))
        from html import escape

        items = []
        for p in plan.get("parts", []):
            q = f" ×{p['quantity']}" if p.get("quantity", 1) not in (None, 1) else ""
            vol = f" · ≈{labels._n(p['expected_volume_mm3'], 0)} mm³" if p.get("expected_volume_mm3") else ""
            bb = f" · {' × '.join(labels._n(v, 0) for v in p['expected_bbox_size'])} mm" if p.get("expected_bbox_size") else ""
            items.append(f"<li style='margin-bottom:4px'><b>{escape(str(p.get('name', '')))}</b>{q} — "
                         f"{escape(str(p.get('dimensions', '')))}{vol}{bb}<br>"
                         f"<span style='color:{C['muted']}'>{escape(str(p.get('method', '')))}</span></li>")
        if items:
            lb = WrapLabel(f"<ol style='margin-left:-20px'>{''.join(items)}</ol>")
            lb.setTextFormat(Qt.TextFormat.RichText)
            lb.setWordWrap(True)
            lay.addWidget(lb)
        if plan.get("assembly"):
            lb = WrapLabel(f"<b>{t('Montaj:')}</b> {escape(plan['assembly'])}")
            lb.setTextFormat(Qt.TextFormat.RichText)
            lb.setWordWrap(True)
            lay.addWidget(lb)
        for q in plan.get("questions") or []:
            lay.addWidget(_label(f"❓ {q}"))
        btns = QHBoxLayout()
        self.ok_btn = QPushButton(t("Onayla ve başla"))
        self.ok_btn.setObjectName("primary")
        self.edit_btn = QPushButton(t("Değiştir…"))
        btns.addWidget(self.ok_btn)
        btns.addWidget(self.edit_btn)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.ok_btn.clicked.connect(self._ok)
        self.edit_btn.clicked.connect(self.revise.emit)

    def _ok(self):
        self.ok_btn.setEnabled(False)
        self.edit_btn.setEnabled(False)
        self.approve.emit()

    def lock(self):
        self.ok_btn.setEnabled(False)
        self.edit_btn.setEnabled(False)


def images_from_mime(mime) -> list:
    """Bir yapıştırma/bırakma içeriğinden görüntüleri çıkarır: dosya yolları (str) ya da PNG baytları (bytes)."""
    from .attachments import is_image_path

    out: list = []
    if mime.hasUrls():
        for u in mime.urls():
            if u.isLocalFile() and is_image_path(u.toLocalFile()):
                out.append(u.toLocalFile())
    if not out and mime.hasImage():
        img = mime.imageData()
        if img is not None and not img.isNull():
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice

            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            out.append(bytes(ba.data()))
    return out


class PromptEdit(QPlainTextEdit):
    """Enter gönderir, Shift+Enter yeni satır. Görüntü yapıştırma (Ctrl+V) ve bırakma desteklenir."""

    submit = Signal()
    images_added = Signal(list)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
            return
        super().keyPressEvent(e)

    def canInsertFromMimeData(self, source):  # noqa: N802
        return bool(images_from_mime(source)) or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):  # noqa: N802
        imgs = images_from_mime(source)
        if imgs:
            self.images_added.emit(imgs)
            return
        super().insertFromMimeData(source)


class AttachmentBar(QWidget):
    """Gönderilmeyi bekleyen fotoğrafların küçük resimleri; her birinde kaldır (×)."""

    removed = Signal(str)

    def __init__(self):
        super().__init__()
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.lay.addStretch(1)
        self.setVisible(False)

    def set_paths(self, paths: list[str]):
        while self.lay.count() > 1:
            w = self.lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for p in paths:
            chip = QFrame()
            chip.setObjectName("step")
            cl = QHBoxLayout(chip)
            cl.setContentsMargins(3, 3, 3, 3)
            cl.setSpacing(2)
            thumb = QLabel()
            pm = QPixmap(p)
            if not pm.isNull():
                thumb.setPixmap(pm.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            thumb.setToolTip(Path(p).name)
            x = QPushButton("×")
            x.setObjectName("icon")
            x.setFixedSize(20, 20)
            x.setToolTip(t("Kaldır"))
            x.clicked.connect(lambda _=False, path=p: self.removed.emit(path))
            cl.addWidget(thumb)
            cl.addWidget(x, alignment=Qt.AlignmentFlag.AlignTop)
            self.lay.insertWidget(self.lay.count() - 1, chip)
        self.setVisible(bool(paths))


def badge(text: str, state: str = "off") -> QLabel:
    lb = QLabel(text)
    set_badge(lb, text, state)
    return lb


def set_badge(lb: QLabel, text: str, state: str) -> None:
    lb.setText(text)
    lb.setObjectName({"ok": "badgeOk", "busy": "badgeBusy", "err": "badgeErr"}.get(state, "badge"))
    lb.style().unpolish(lb)
    lb.style().polish(lb)
