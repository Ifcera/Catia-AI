"""Belge araçları: yeni parça, aç, kaydet, STEP, listele, etkinleştir, kapat."""
from __future__ import annotations

import pywintypes

from ..catia.errors import ToolError, com_error_text
from ..catia.session import CatiaSession
from .registry import DOC_PROP, tool


def _close_quietly(doc):
    try:
        doc.Close()
    except pywintypes.com_error:
        pass


def _part_numbers_in_use(s: CatiaSession) -> dict[str, str]:
    used = {}
    docs = s.app.Documents
    for i in range(1, docs.Count + 1):
        d = docs.Item(i)
        try:
            used[d.Product.PartNumber] = d.Name
        except (pywintypes.com_error, AttributeError):
            pass
    return used


@tool(
    "new_part",
    """Yeni CATPart; name = Part Number (montajda görünen ad).""",
    {"properties": {"name": {"type": "string", "description": "Parça numarası, ör. 'Taban'."}}, "required": ["name"]},
    category="document",
)
def new_part(s: CatiaSession, name: str):
    used = _part_numbers_in_use(s)
    if name in used:
        raise ToolError(f"'{name}' parça numarası açık '{used[name]}' belgesinde zaten kullanılıyor (CATIA yinelenene izin vermez).",
                        hint="Farklı bir ad verin ya da o belgeyle çalışın.")
    doc = s.app.Documents.Add("Part")
    s.journal.record(f"{doc.Name} belgesini kapat", lambda d=doc: _close_quietly(d))
    try:
        doc.Product.PartNumber = name
    except pywintypes.com_error as exc:
        raise ToolError(f"Parça numarası '{name}' verilemedi: {com_error_text(exc)}",
                        hint="Boşluk ve özel karakter içermeyen bir ad deneyin.") from None
    return {"document": doc.Name, "part_number": name, "bodies": ["PartBody"], "planes": ["XY", "YZ", "ZX"]}


@tool(
    "new_product",
    "Yeni boş bir CATProduct (montaj) oluşturur ve etkin yapar.",
    {"properties": {"name": {"type": "string"}}, "required": ["name"]},
    category="document",
)
def new_product(s: CatiaSession, name: str):
    doc = s.app.Documents.Add("Product")
    s.journal.record(f"{doc.Name} belgesini kapat", lambda d=doc: _close_quietly(d))
    doc.Product.PartNumber = name
    return {"document": doc.Name, "part_number": name}


@tool(
    "list_documents",
    "CATIA'da açık belgeleri, türlerini, kayıtlı yollarını ve etkin belgeyi listeler.",
    {"properties": {}},
    mutating=False,
    category="document",
)
def list_documents(s: CatiaSession):
    app = s.app
    docs = []
    for i in range(1, app.Documents.Count + 1):
        d = app.Documents.Item(i)
        try:
            path = d.FullName
        except pywintypes.com_error:
            path = None
        docs.append({"name": d.Name, "path": path, "saved": bool(d.Saved) if hasattr(d, "Saved") else None})
    try:
        active = app.ActiveDocument.Name
    except pywintypes.com_error:
        active = None
    return {"documents": docs, "active": active}


@tool(
    "activate_document",
    "Açık bir belgeyi etkin pencere yapar.",
    {"properties": {"document": DOC_PROP}, "required": ["document"]},
    mutating=False,
    category="document",
)
def activate_document(s: CatiaSession, document: str):
    doc = s.document(document)
    doc.Activate()
    return {"active": doc.Name}


@tool(
    "open_document",
    "Çalışma klasöründeki bir .CATPart/.CATProduct dosyasını açar.",
    {"properties": {"filename": {"type": "string", "description": "Çalışma klasörüne göre yol."}}, "required": ["filename"]},
    category="document",
)
def open_document(s: CatiaSession, filename: str):
    p = s.resolve_path(filename, must_exist=True)
    try:
        doc = s.app.Documents.Open(str(p))
    except pywintypes.com_error as exc:
        raise ToolError(f"Açılamadı: {com_error_text(exc)}") from None
    s.journal.record(f"{doc.Name} belgesini kapat", lambda d=doc: _close_quietly(d))
    return {"document": doc.Name, "path": str(p)}


@tool(
    "save_document",
    """Belgeyi çalışma klasörüne kaydeder (üzerine yazar); export: ['stl','step'] aynı çağrıda dışa aktarır.""",
    {"properties": {"document": DOC_PROP, "filename": {"type": "string"},
                    "export": {"type": "array", "items": {"enum": ["stl", "step"]}}}},
    mutating=False,
    category="document",
)
def save_document(s: CatiaSession, document: str | None = None, filename: str | None = None, export=None):
    doc = s.document(document)
    ext = ".CATProduct" if doc.Name.lower().endswith(".catproduct") else ".CATPart"
    if filename is None:
        try:
            filename = doc.Product.PartNumber + ext
        except pywintypes.com_error:
            filename = doc.Name
    p = s.resolve_path(filename, suffixes=(ext,))
    try:
        doc.SaveAs(str(p))
    except pywintypes.com_error as exc:
        raise ToolError(f"Kaydedilemedi: {com_error_text(exc)}",
                        hint="Dosya başka bir programda açık olabilir ya da ad geçersiz.") from None
    out = {"document": doc.Name, "path": str(p)}
    for fmt in export or []:
        stem = str(p.with_suffix(""))
        r = export_stl(s, stem + ".stl", doc.Name) if fmt == "stl" else export_step(s, stem + ".stp", doc.Name)
        out[fmt] = {k: v for k, v in r.items() if k in ("path", "triangles", "size_bytes")}
    return out


@tool(
    "export_stl",
    """CATPart'ı çalışma klasörüne STL olarak aktarır (montaj değil); üçgen sayısı döner.""",
    {"properties": {"document": DOC_PROP, "filename": {"type": "string"}}, "required": ["filename"]},
    mutating=False,
    category="document",
)
def export_stl(s: CatiaSession, filename: str, document: str | None = None):
    doc = s.document(document)
    try:
        _ = doc.Part
    except (pywintypes.com_error, AttributeError):
        raise ToolError(f"'{doc.Name}' bir CATPart değil; STL yalnızca parçalardan aktarılır.",
                        hint="Montajdaki her parça için export_stl'yi parça belgesiyle çağırın.") from None
    p = s.resolve_path(filename, suffixes=(".stl",))
    try:
        doc.ExportData(str(p), "stl")
    except pywintypes.com_error as exc:
        raise ToolError(f"STL dışa aktarılamadı: {com_error_text(exc)}") from None
    if not p.exists() or p.stat().st_size == 0:
        raise ToolError("STL dosyası oluşmadı.")
    with open(p, "rb") as f:
        head = f.read(5)
    facets = None
    if head == b"solid":  # ASCII STL (R20'de doğrulandı)
        with open(p, "r", encoding="ascii", errors="replace") as f:
            facets = sum(1 for line in f if line.lstrip().startswith("facet"))
    return {"path": str(p), "size_bytes": p.stat().st_size, "triangles": facets}


@tool(
    "export_step",
    "Belgeyi çalışma klasörüne STEP (.stp) olarak dışa aktarır.",
    {"properties": {"document": DOC_PROP, "filename": {"type": "string"}}, "required": ["filename"]},
    mutating=False,
    category="document",
)
def export_step(s: CatiaSession, filename: str, document: str | None = None):
    doc = s.document(document)
    p = s.resolve_path(filename, suffixes=(".stp", ".step"))
    try:
        doc.ExportData(str(p), "stp")
    except pywintypes.com_error as exc:
        raise ToolError(f"STEP dışa aktarılamadı: {com_error_text(exc)}", hint="STEP lisansı olmayabilir.") from None
    if not p.exists():
        raise ToolError("STEP dosyası oluşmadı.", hint="CATIA'da STEP arayüzü lisansı olmayabilir.")
    return {"path": str(p), "size_bytes": p.stat().st_size}


@tool(
    "close_document",
    "Bir belgeyi kaydetmeden kapatır. Geri alınamaz; kaydedilmemiş değişiklikler kaybolur.",
    {"properties": {"document": DOC_PROP}, "required": ["document"]},
    mutating=False,
    category="document",
)
def close_document(s: CatiaSession, document: str):
    doc = s.document(document)
    name = doc.Name
    doc.Close()
    return {"closed": name}
