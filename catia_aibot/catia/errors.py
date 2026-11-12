"""Asistana döndürülecek anlaşılır hata türleri."""
from __future__ import annotations

import pywintypes


class ToolError(Exception):
    """Araç çağrısının başarısız olduğunu anlatan, asistana gösterilecek hata.

    message: ne oldu (Türkçe, kısa)
    hint:    asistan bir sonraki adımda ne denemeli
    """

    def __init__(self, message: str, hint: str | None = None, **details):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details

    def to_dict(self) -> dict:
        d = {"ok": False, "error": self.message}
        if self.hint:
            d["hint"] = self.hint
        if self.details:
            d.update(self.details)
        return d


def com_error_text(exc: BaseException) -> str:
    """pywintypes.com_error içinden CATIA'nın kendi açıklamasını çıkarır."""
    if isinstance(exc, pywintypes.com_error):
        try:
            excepinfo = exc.args[2]
            if excepinfo and excepinfo[2]:
                src = excepinfo[1] or ""
                return f"{src}: {excepinfo[2]}".strip(": ").replace("\n", " ")
        except (IndexError, TypeError):
            pass
        return str(exc.args[1]) if len(exc.args) > 1 else str(exc)
    return str(exc)
