"""API anahtarı penceresi: gizli giriş, ücretsiz model listesi isteğiyle doğrulama, Windows kimlik deposuna kayıt."""
from __future__ import annotations

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from ..agent.keys import delete_api_key, get_api_key, mask, set_api_key
from ..agent.providers import PROVIDERS, make_provider
from ..i18n import t


class KeyDialog(QDialog):
    _done = Signal(bool, str)

    def __init__(self, provider: str, parent=None):
        super().__init__(parent)
        self.provider = provider
        label = PROVIDERS[provider].label
        self.setWindowTitle(t("{p} — API anahtarı").format(p=label))
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        self.current = QLabel(f"Kayıtlı anahtar: {mask(get_api_key(provider))}")
        self.current.setObjectName("muted")
        lay.addWidget(self.current)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(t("Anahtarı yapıştırın"))
        lay.addWidget(self.edit)
        note = QLabel(t("Anahtar yalnızca Windows Kimlik Bilgileri Yöneticisi'ne kaydedilir; dosyaya ya da koda yazılmaz."))
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.del_btn = QPushButton(t("Sil"))
        self.save_btn = QPushButton(t("Doğrula ve kaydet"))
        self.save_btn.setObjectName("primary")
        close = QPushButton(t("Kapat"))
        row.addWidget(self.del_btn)
        row.addStretch(1)
        row.addWidget(close)
        row.addWidget(self.save_btn)
        lay.addLayout(row)
        self.save_btn.clicked.connect(self._save)
        self.edit.returnPressed.connect(self._save)
        self.del_btn.clicked.connect(self._delete)
        close.clicked.connect(self.accept)
        self._done.connect(self._finished)

    def _save(self):
        key = self.edit.text().strip()
        if not key:
            self.status.setText(t("Anahtar boş."))
            return
        self.save_btn.setEnabled(False)
        self.status.setText(t("Doğrulanıyor…"))

        def work():
            try:
                models = make_provider(self.provider, api_key=key).list_models()
            except Exception as exc:  # noqa: BLE001
                self._done.emit(False, t("Doğrulanamadı, kaydedilmedi: {e}").format(e=f"{exc.__class__.__name__}: {str(exc)[:160]}"))
                return
            set_api_key(key, self.provider)
            self._done.emit(True, t("Kaydedildi ✓ — {n} model erişilebilir.").format(n=len(models)))
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, ok: bool, msg: str):
        self.status.setText(msg)
        self.save_btn.setEnabled(True)
        if ok:
            self.edit.clear()
            self.current.setText(f"Kayıtlı anahtar: {mask(get_api_key(self.provider))}")

    def _delete(self):
        delete_api_key(self.provider)
        self.current.setText(f"Kayıtlı anahtar: {mask(get_api_key(self.provider))}")
        self.status.setText(t("Anahtar silindi."))
