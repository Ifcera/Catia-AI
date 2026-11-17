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
    expect: bool = False  # True: girdide 'expect' kabul eder; iş bitince aynı çağrıda beklenti kontrolü yapılır

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
        expect = args.pop("expect", None) if tool.expect else None
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
            if expect:
                data["check"] = _run_expect(session, args, data, expect)
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


def _run_expect(session: CatiaSession, args: dict, data: dict, expect: dict) -> dict:
    """Ayrı bir check_expectation turu yerine, araç bitince aynı çağrıda beklenti kontrolü (token/tur tasarrufu).
    Yalnızca kalan kontrolleri ayrıntılı döndürür."""
    from .inspect_tools import check_expectation

    kw = {k: v for k, v in expect.items() if k in ("volume_mm3", "bbox_size", "bbox_min", "bbox_max")}
    if "tol_pct" in expect:
        kw["volume_tolerance_pct"] = expect["tol_pct"]
    if "tol_mm" in expect:
        kw["bbox_tolerance_mm"] = expect["tol_mm"]
    try:
        r = check_expectation(session, document=args.get("document"), body=data.get("body") or args.get("body"), **kw)
    except Exception as exc:  # noqa: BLE001 - kontrol hatası işlemi geri almaz
        return {"passed": False, "error": str(exc)}
    failed = [c for c in r["checks"] if not c["pass"]]
    out = {"passed": r["passed"]}
    if failed:
        out["failed"] = failed
    return out


REGISTRY = Registry()

EXPECT_PROP = {"type": "object", "description": "Aynı çağrıda kontrol: {volume_mm3, bbox_size, bbox_min, bbox_max, tol_pct, tol_mm}."}


def tool(name: str, description: str, schema: dict, mutating: bool = True, category: str = "part", expect: bool = False):
    """Bir fonksiyonu araç olarak kaydeden dekoratör. expect=True: şemaya 'expect' (aynı çağrıda kontrol) eklenir."""

    def deco(fn):
        full = {"type": "object", "additionalProperties": False, **schema}
        full.setdefault("properties", {})
        if expect:
            full["properties"] = {**full["properties"], "expect": EXPECT_PROP}
        REGISTRY.register(Tool(name, description.strip(), full, fn, mutating, category, expect))
        return fn

    return deco


# Şemalarda tekrar eden parçalar
# Ortak alanların anlamı rehberde (skill_catia.md) bir kez anlatılır; şemalarda tekrar edilmez (her istekte token).
DOC_PROP = {"type": "string"}
BODY_PROP = {"type": "string"}
VEC3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
VEC2 = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
FACE_SELECTOR = {"description": "Yüzey seçici (rehber).", "anyOf": [{"type": "string"}, {"type": "object"}]}
EDGE_SELECTOR = {"description": "Kenar seçici (rehber).", "anyOf": [{"type": "string"}, {"type": "object"}, {"type": "array"}]}
