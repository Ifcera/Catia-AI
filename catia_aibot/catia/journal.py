"""Geri alma günlüğü.

Her değiştirici araç, yaptığı işin tersini (ör. oluşturduğu feature'ı silme,
eski parametre değerini geri yazma) günlüğe kaydeder. Kayıtlar mesaj başına
bir grupta toplanır:

- Bir araç çağrısı hata verirse, o çağrının kayıtları hemen ters sırada
  uygulanır (savepoint/rollback_to) -> parça çağrı öncesi duruma döner.
- "Son mesajı geri al", son grubun tüm kayıtlarını ters sırada uygular.

CATIA'nın kendi Undo komutu otomasyondan güvenilir biçimde kontrol
edilemediği için bu yaklaşım seçildi.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class UndoAction:
    description: str
    fn: Callable[[], None]


@dataclass
class Group:
    label: str
    actions: list[UndoAction] = field(default_factory=list)


class Journal:
    def __init__(self):
        self.groups: list[Group] = []
        self.current: Group | None = None

    # --- gruplar -----------------------------------------------------------
    def begin(self, label: str) -> None:
        if self.current is not None:
            self.end()
        self.current = Group(label)

    def end(self) -> None:
        if self.current is not None and self.current.actions:
            self.groups.append(self.current)
        self.current = None

    def _active(self) -> Group:
        if self.current is None:
            # Grup açılmadan çağrılan araçlar (ör. betik/test) kendi grubunu alır.
            self.current = Group("(adsız)")
        return self.current

    # --- kayıt ---------------------------------------------------------------
    def record(self, description: str, fn: Callable[[], None]) -> None:
        self._active().actions.append(UndoAction(description, fn))

    def savepoint(self) -> int:
        return len(self._active().actions)

    def rollback_to(self, sp: int) -> list[str]:
        g = self._active()
        undone, errors = self._apply(g.actions[sp:])
        del g.actions[sp:]
        return undone + [f"HATA: {e}" for e in errors]

    def undo_last(self) -> tuple[str | None, list[str], list[str]]:
        """Son grubu (açık grup boş değilse onu) geri alır."""
        if self.current is not None and self.current.actions:
            g = self.current
            self.current = None
        elif self.groups:
            g = self.groups.pop()
        else:
            return None, [], []
        undone, errors = self._apply(g.actions)
        return g.label, undone, errors

    @staticmethod
    def _apply(actions: list[UndoAction]) -> tuple[list[str], list[str]]:
        undone, errors = [], []
        for a in reversed(actions):
            try:
                a.fn()
                undone.append(a.description)
            except Exception as exc:  # noqa: BLE001 - geri almada devam et
                errors.append(f"{a.description}: {exc}")
        return undone, errors

    def can_undo(self) -> bool:
        return bool(self.groups) or bool(self.current and self.current.actions)
