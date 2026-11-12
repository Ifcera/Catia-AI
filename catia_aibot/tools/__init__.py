"""Araç katmanı. Arayüzden bağımsızdır; agent döngüsü, betikler ve ileride bir MCP sunucusu aynı kaydı kullanır.

Kullanım (herhangi bir iş parçacığından):

    from catia_aibot.tools import CatiaTools
    t = CatiaTools(workspace="C:/proje")
    t.begin_message("blok yap")
    r = t.call("new_part", name="Blok")
    ...
    t.end_message()
    t.undo_last_message()
"""
from __future__ import annotations

from pathlib import Path

from ..catia.com_thread import ComThread
from ..catia.session import CatiaSession
from . import assembly, document, features, inspect_tools, sketch  # noqa: F401  (araçları kaydeder)
from .registry import REGISTRY, Registry, Tool, ToolResult


class CatiaTools:
    """Oturumu ve COM iş parçacığını birlikte tutan ince sarmalayıcı."""

    def __init__(self, workspace: str | Path | None = None, registry: Registry = REGISTRY):
        self.registry = registry
        self.thread = ComThread()
        self.session: CatiaSession = self.thread.call(lambda: CatiaSession(workspace))

    def set_workspace(self, path: str | Path) -> None:
        self.thread.call(lambda: setattr(self.session, "workspace", Path(path).resolve()))

    def call(self, tool_name: str, args: dict | None = None, /, **kwargs) -> ToolResult:
        """call("pad", {"sketch": "Sketch.1"}) ya da call("pad", sketch="Sketch.1")."""
        merged = dict(args or {}, **kwargs)
        return self.thread.call(self.registry.call, self.session, tool_name, merged, timeout=600)

    def submit(self, tool_name: str, args: dict | None = None, /):
        return self.thread.submit(self.registry.call, self.session, tool_name, dict(args or {}))

    def run(self, fn, *args, timeout: float | None = 300):
        """Oturumla birlikte keyfi bir fonksiyonu COM iş parçacığında çalıştırır: fn(session, *args)."""
        return self.thread.call(fn, self.session, *args, timeout=timeout)

    def begin_message(self, label: str) -> None:
        self.thread.call(self.session.journal.begin, label)

    def end_message(self) -> None:
        self.thread.call(self.session.journal.end)

    def undo_last_message(self) -> dict:
        def _undo(s: CatiaSession):
            label, undone, errors = s.journal.undo_last()
            for i in range(1, s.app.Documents.Count + 1):
                try:
                    s.app.Documents.Item(i).Part.Update()
                except Exception:  # noqa: BLE001
                    pass
            return {"group": label, "undone": undone, "errors": errors}

        return self.thread.call(_undo, self.session)

    def specs(self, categories: set[str] | None = None) -> list[dict]:
        return self.registry.specs(categories)

    def close(self) -> None:
        self.thread.shutdown()


__all__ = ["CatiaTools", "REGISTRY", "Registry", "Tool", "ToolResult"]
