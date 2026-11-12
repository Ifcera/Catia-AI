"""Araç çağrılarının kullanıcıya gösterilecek Türkçe adları ve kısa özetleri (arayüzden bağımsız, test edilebilir)."""
from __future__ import annotations

from pathlib import Path

from ..i18n import t

TOOL_LABELS = {
    "new_part": "Yeni parça", "new_product": "Yeni montaj", "list_documents": "Belgeler", "activate_document": "Belge etkin",
    "open_document": "Belge açıldı", "save_document": "Kaydedildi", "export_step": "STEP dışa aktarıldı",
    "close_document": "Belge kapatıldı", "export_stl": "STL dışa aktarıldı", "create_sketch": "Sketch", "add_to_sketch": "Sketch'e ekleme",
    "get_sketch": "Sketch bilgisi", "pad": "Pad", "pocket": "Pocket", "shaft": "Shaft", "groove": "Groove",
    "hole": "Delik", "fillet": "Fillet", "chamfer": "Pah", "rect_pattern": "Doğrusal dizi",
    "circ_pattern": "Dairesel dizi", "mirror": "Ayna", "new_body": "Yeni gövde", "boolean": "Boolean",
    "create_plane": "Referans düzlem", "list_parameters": "Parametreler", "set_parameter": "Parametre değişti",
    "delete_feature": "Silindi", "set_feature_active": "Etkinlik", "get_design_state": "Ağaç okundu",
    "update_part": "Güncelleme", "measure": "Ölçüm", "measure_distance": "Mesafe", "list_topology": "Topoloji",
    "check_expectation": "Kontrol", "screenshot": "Görüntü", "get_selection": "Seçim", "present_plan": "Plan",
    "add_component": "Bileşen eklendi", "list_components": "Bileşenler", "set_component_position": "Konum",
    "add_assembly_constraint": "Montaj kısıtı", "update_product": "Montaj güncellendi", "check_assembly": "Montaj kontrolü",
    "remove_constraint": "Kısıt silindi",
}

# Okuma araçları sohbeti kalabalıklaştırmasın diye sönük gösterilir.
QUIET_TOOLS = {"list_documents", "get_design_state", "list_topology", "list_parameters", "get_sketch", "get_selection",
               "update_part", "list_components", "update_product"}

CHECK_NAMES = {"update": "Güncelleme", "volume_mm3": "Hacim", "bbox_size": "Boyut", "bbox_min": "Min köşe",
               "bbox_max": "Maks köşe"}


def label(name: str) -> str:
    return t(TOOL_LABELS.get(name, name))


def _n(x, nd=1) -> str:
    try:
        return f"{float(x):,.{nd}f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(x)


def input_summary(name: str, args: dict) -> str:
    a = args or {}
    if name == "new_part":
        return a.get("name", "")
    if name in ("pad", "pocket"):
        if a.get("through_all"):
            return f"{a.get('sketch', '')} · {t('sonuna kadar')}"
        v = a.get("length", a.get("depth"))
        return f"{a.get('sketch', '')}" + (f" · {v} mm" if v is not None else "")
    if name == "hole":
        return f"Ø{a.get('diameter')}" + (" · " + t("sonuna kadar") if a.get("through_all") else (f" · {a.get('depth')} mm" if a.get("depth") else ""))
    if name in ("fillet", "chamfer"):
        return f"{'R' if name == 'fillet' else ''}{a.get('radius', a.get('length'))} mm"
    if name == "create_sketch":
        sup = a.get("support")
        els = ", ".join(e.get("type", "?") for e in a.get("elements", [])[:4])
        return f"{sup if isinstance(sup, str) else t('yüzey')} · {els}"
    if name == "screenshot":
        return ", ".join(a.get("views") or ["iso"])
    if name == "set_parameter":
        return f"{a.get('name', '')} = {a.get('value')}"
    if name in ("rect_pattern", "circ_pattern"):
        return f"{a.get('feature', '')} × {a.get('count1', a.get('count'))}"
    return ""


def result_summary(name: str, data: dict) -> str:
    if not data.get("ok", False):
        hint = data.get("hint")
        return f"{data.get('error', t('hata'))}" + (f" — {hint}" if hint else "")
    if "feature" in data and "volume_mm3" in data:
        s = f"{data['feature']} · {_n(data['volume_mm3'])} mm³ ({float(data.get('volume_change_mm3', 0)):+,.1f})".replace(",", " ")
        if data.get("direction_corrected"):
            s += " · " + t("yön düzeltildi")
        return s
    if name == "create_sketch" or name == "add_to_sketch":
        return f"{data.get('sketch', '')} · " + (t("tam kısıtlı") if data.get("fully_constrained") else t("serbestlik≈{n}").format(n=data.get("dof_estimate")))
    if name == "new_part":
        return data.get("document", "")
    if name == "measure":
        bb = (data.get("bounding_box") or {}).get("size")
        return f"{_n(data.get('volume_mm3'))} mm³" + (f" · {' × '.join(_n(v) for v in bb)} mm" if bb else "")
    if name == "measure_distance":
        return f"{_n(data.get('distance_mm'), 3)} mm"
    if name in ("save_document", "export_step", "export_stl"):
        return Path(data.get("path", "")).name
    if name == "set_parameter":
        return f"{data.get('old')} → {data.get('new')}"
    if name == "check_expectation":
        return t("GEÇTİ") if data.get("passed") else t("KALDI")
    if name == "list_topology":
        parts = []
        if "face_count" in data:
            parts.append(t("{n} yüz").format(n=data["face_count"]))
        if "edge_count" in data:
            parts.append(t("{n} kenar").format(n=data["edge_count"]))
        return ", ".join(parts)
    if name == "get_design_state":
        n = sum(len(b.get("features", [])) for b in data.get("bodies", []))
        return t("{n} feature").format(n=n) + ("" if data.get("up_to_date", True) else t(" · update hatası var"))
    if name == "add_component":
        return f"{data.get('instance', '')} ({data.get('part_number', '')})"
    if name == "add_assembly_constraint":
        c = data.get("constraint") or {}
        moved = ", ".join(m["instance"] for m in data.get("moved", []))
        return f"{c.get('name', '')} · {c.get('type', '')}" + (t(" · taşındı: {m}").format(m=moved) if moved else "")
    if name == "set_component_position":
        return f"{data.get('instance', '')} → {data.get('origin')}"
    if name == "check_assembly":
        return t("GEÇTİ") if data.get("passed") else t("KALDI")
    if name == "create_plane":
        return data.get("plane", "")
    if name == "new_body":
        return data.get("body", "")
    if data.get("warning"):
        return data["warning"]
    return ""


def check_lines(data: dict) -> list[tuple[bool, str]]:
    out = []
    for c in data.get("checks", []):
        name = t(CHECK_NAMES.get(c.get("check"), c.get("check")))
        if c.get("check") == "update":
            txt = f"{name}: {t('hatasız') if c['pass'] else c.get('detail')}"
        elif c.get("check") == "volume_mm3":
            txt = f"{name}: {_n(c.get('measured'))} / {t('beklenen')} {_n(c.get('expected'))} mm³ (%{_n(c.get('deviation_pct'), 2)})"
        else:
            m = c.get("measured")
            txt = f"{name}: {[round(v, 2) for v in m] if m else '-'} / {t('beklenen')} {c.get('expected')}"
        out.append((bool(c.get("pass")), txt))
    return out


def assembly_check_lines(data: dict) -> list[tuple[bool, str]]:
    out = [(bool(data.get("updated")), t("Güncelleme") + ": " + (t("hatasız") if data.get("updated") else str(data.get("update_error"))))]
    probs = data.get("constraint_problems") or []
    out.append((not probs, t("Kısıtlar: {n} adet").format(n=len(data.get("constraints", []))) +
                ("" if not probs else t(" · sorunlu: ") + ", ".join(f"{p.get('name')} ({p.get('status')})" for p in probs))))
    conflicts = (data.get("clash") or {}).get("conflicts", [])
    clashes = [c for c in conflicts if c.get("type") == "çakışma"]
    out.append((not clashes, t("Çakışma yok") if not clashes else
                t("Çakışma: ") + "; ".join(f"{c['a']} ↔ {c['b']} ({c.get('value_mm')} mm)" for c in clashes)))
    for c in conflicts:
        if c.get("type") == "temas":
            out.append((True, t("Temas: {a} ↔ {b}").format(a=c["a"], b=c["b"]) + (t(" (beklenen)") if c.get("expected") else "")))
    for pair in data.get("missing_expected_contacts") or []:
        out.append((False, t("Beklenen temas yok: {a} ↔ {b}").format(a=pair[0], b=pair[1])))
    return out


def usage_text(u: dict, backend: str) -> str:
    if not u:
        return ""
    if backend == "claude_code":
        rl = u.get("rate_limit") or {}
        util = rl.get("five_hour_utilization")
        s = t("Abonelik")
        if util is not None:
            s += t(" · 5 saatlik kullanım %{p}").format(p=round(util * 100))
        return s
    tok = t("{i} giriş / {o} çıkış token").format(i=f"{u.get('input', 0) + u.get('cache_read', 0):,}",
                                                 o=f"{u.get('output', 0):,}").replace(",", ".")
    cost = u.get("cost_usd")
    return tok + (f" · ≈ ${cost:.3f}" if cost is not None else "")


def selection_context(data: dict) -> str | None:
    if not data.get("ok") or not data.get("count"):
        return None
    items = []
    for it in data.get("items", [])[:10]:
        s = it.get("name") or it.get("type", "?")
        if it.get("topology_id"):
            s += f" [{it['topology_id']}]"
        if it.get("type") and it.get("name"):
            s += f" ({it['type']})"
        items.append(s)
    return f"{data.get('document')}: " + "; ".join(items)
