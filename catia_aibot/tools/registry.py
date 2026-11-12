"""Arayüzden bağımsız araç kaydı.

Her araç: ad, açıklama, JSON Schema girdi şeması ve bir işleyici. İşleyici
(session, **args) alır ve dict döndürür. Kayıt aynı araçları Anthropic tool-use
biçiminde ya da ileride bir MCP sunucusunda sunmak için kullanılabilir.

call() sarmalayıcısı şunları yapar:
- geri alma günlüğüne savepoint koyar; hata olursa o çağrının değişikliklerini geri alır,
- CATIA diyaloglarını bekçiyle izler ve kapatır,
- COM hatalarını anlaşılır metne çevirir,
- sonucu {"ok": True, ...} / {"ok": False, "error", "hint"} biçiminde döndürür.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pywintypes

from ..catia.dialogs import DialogGuard
from ..catia.errors import ToolError, com_error_text
from ..catia.session import CatiaSession


@dataclass
class ToolResult:
    data: dict
    images: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.data.get("ok"))


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[..., Any]
    mutating: bool = True
    category: str = "part"

    def anthropic_spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}


class Registry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"Araç iki kez kaydedildi: {tool.name}")
        self.tools[tool.name] = tool

    def specs(self, categories: set[str] | None = None) -> list[dict]:
        return [t.anthropic_spec() for t in self.tools.values() if categories is None or t.category in categories]

    def call(self, session: CatiaSession, name: str, args: dict | None = None, guard_dialogs: bool = True) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult({"ok": False, "error": f"Bilinmeyen araç: {name}"})
        args = dict(args or {})
        sp = session.journal.savepoint() if tool.mutating else None
        guard = DialogGuard() if guard_dialogs else None
        try:
            if guard:
                guard.__enter__()
            try:
                out = tool.handler(session, **args)
            finally:
                if guard:
                    guard.__exit__(None, None, None)
            images: list[Path] = []
            if isinstance(out, ToolResult):
                data, images = out.data, out.images
            else:
                data = out or {}
            data = {"ok": True, **data}
            if guard and guard.dialogs:
                data["dialogs_closed"] = guard.summary()
            return ToolResult(data, images)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, ToolError):
                err = exc.to_dict()
            elif isinstance(exc, pywintypes.com_error):
                err = {"ok": False, "error": f"CATIA hatası: {com_error_text(exc)}"}
            elif isinstance(exc, TypeError) and "argument" in str(exc):
                err = {"ok": False, "error": f"Geçersiz parametre: {exc}", "hint": "Araç şemasına bakın."}
            else:
                err = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}", "trace": traceback.format_exc(limit=3)}
            if sp is not None:
                undone = session.journal.rollback_to(sp)
                if undone:
                    err["rolled_back"] = undone
            if guard and guard.dialogs:
                err["dialogs_closed"] = guard.summary()
            return ToolResult(err)


REGISTRY = Registry()


def tool(name: str, description: str, schema: dict, mutating: bool = True, category: str = "part"):
    """Bir fonksiyonu araç olarak kaydeden dekoratör."""

    def deco(fn):
        full = {"type": "object", "additionalProperties": False, **schema}
        full.setdefault("properties", {})
        REGISTRY.register(Tool(name, description.strip(), full, fn, mutating, category))
        return fn

    return deco


# Şemalarda tekrar eden parçalar
DOC_PROP = {"type": "string", "description": "Belge adı (ör. 'Taban.CATPart'). Boşsa etkin belge."}
BODY_PROP = {"type": "string", "description": "Gövde adı (ör. 'PartBody'). Boşsa çalışılan (in-work) gövde."}
VEC3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
VEC2 = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
FACE_SELECTOR = {
    "description": (
        "Yüzey seçici. Ya list_topology'den bir kimlik ('F3') ya da nesne: "
        "{type: planar|cylindrical|..., axis: 'X'|'Y'|'Z'|'+Z'|'-Z' (düzlem normali), side: 'max'|'min' (eksen boyunca en uçtaki), "
        "near: [x,y,z] (merkezi bu noktaya en yakın), feature: 'Pad.1' (bu feature'dan gelen), radius: r}."
    ),
    "anyOf": [{"type": "string"}, {"type": "object"}],
}
EDGE_SELECTOR = {
    "description": (
        "Kenar seçici. Kimlik ('E5'), kimlik listesi ya da nesne: {type: line|circle, parallel_to: 'X'|'Y'|'Z', "
        "on_face: <yüzey seçici>, between: [<yüzey>, <yüzey>], near: [x,y,z], all: true (near ile hepsini sırala), radius: r}. "
        "Nesne seçici uyan TÜM kenarları seçer."
    ),
    "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "array"}],
}
