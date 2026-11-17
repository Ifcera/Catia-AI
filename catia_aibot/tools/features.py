"""Part Design feature araçları.

Her değiştirici araç:
  1. değişiklik öncesi gövde hacmini ve hatalı feature listesini okur,
  2. feature'ı oluşturur ve geri alma günlüğüne "sil" kaydı düşer,
  3. parçayı günceller; YENİ bir update hatası varsa ToolError fırlatır
     (registry bu çağrının değişikliklerini geri alır),
  4. feature adı + hacim değişimi döndürür; hacim beklenmedik biçimde
     değişmediyse uyarı ekler (ör. ters yöne pocket).
"""
from __future__ import annotations

import math

import pywintypes

from ..catia.errors import ToolError, com_error_text
from ..catia.measure import body_volume_mm3
from ..catia.session import CatiaSession, dot, r3
from ..catia.topology import AXES, Topology, axis_of
from .registry import BODY_PROP, DOC_PROP, EDGE_SELECTOR, FACE_SELECTOR, VEC3, tool
from .sketch import _Builder

LIMIT_MODES = {"dimension": 0, "next": 1, "last": 2, "plane": 3, "surface": 4, "thru_next": 5}
# Doğrulandı (R20): pattern yönü olarak ana düzlem verildiğinde ilk iç ekseni kullanılır.
PLANE_FOR_DIRECTION = {"X": "PlaneXY", "Y": "PlaneYZ", "Z": "PlaneZX"}
# Dairesel pattern / dönme ekseni için düzlemin normali kullanılır.
PLANE_FOR_NORMAL = {"X": "PlaneYZ", "Y": "PlaneZX", "Z": "PlaneXY"}


def _volume(s: CatiaSession, doc, part, body) -> float:
    return body_volume_mm3(s, doc, part, body)


class _Op:
    """Bir feature işleminin ortak önce/sonra mantığı."""

    def __init__(self, s: CatiaSession, document: str | None, body: str | None):
        self.s = s
        self.doc, self.part = s.part_document(document)
        self.body = s.body(self.part, body)
        self.part.InWorkObject = self.body
        self.v0 = _volume(s, self.doc, self.part, self.body)
        self.bad0 = set(s.failing_features(self.part))
        self.sf = self.part.ShapeFactory

    def track(self, obj, label: str | None = None):
        name = obj.Name
        self.s.journal.record(label or f"{name} sil", lambda d=self.doc, o=obj: self.s.delete_objects(d, [o]))
        self.s.created.add((self.doc.Name, name))
        return obj

    def finish(self, feat, expect: str | None = None, extra: dict | None = None) -> dict:
        bad = set(self.s.update(self.part)) - self.bad0
        if bad:
            raise ToolError(
                f"{feat.Name} güncellenemedi (update hatası: {sorted(bad)}). Değişiklik geri alındı.",
                hint="Ölçüleri, yönü (reverse) ve seçilen yüzey/kenarları kontrol edin.",
            )
        v1 = _volume(self.s, self.doc, self.part, self.body)
        out = {"feature": feat.Name, "body": self.body.Name, "volume_mm3": r3(v1), "volume_change_mm3": r3(v1 - self.v0)}
        dv = v1 - self.v0
        if expect == "add" and dv <= 1e-6:
            out["warning"] = "Hacim artmadı; feature malzeme eklemedi (yön ya da gövde yanlış olabilir)."
        if expect == "remove" and dv >= -1e-6:
            out["warning"] = "Hacim azalmadı; feature malzemeye değmiyor olabilir (reverse deneyin ya da konumu kontrol edin)."
        if extra:
            out.update(extra)
        return out


PROFILE_PROP = {"type": "object", "description": "sketch yerine satır içi {support, elements, constraints} (create_sketch biçimi)."}


def _profile_sketch(s: CatiaSession, op: "_Op", sketch: str | None, profile: dict | None):
    """Ya var olan sketch'i ya da satır içi profilden yeni bir sketch'i döndürür: (sketch, kısa rapor)."""
    if profile:
        from .sketch import create_sketch

        rep = create_sketch(s, profile["support"], profile.get("elements") or [], document=op.doc.Name,
                            body=op.body.Name, constraints=profile.get("constraints"), name=profile.get("name"))
        op.part.InWorkObject = op.body
        brief = {"sketch": rep["sketch"], "fully_constrained": rep["fully_constrained"], "local_axes": rep["local_axes"]}
        return _sketch(s, op.part, rep["sketch"]), brief
    if not sketch:
        raise ToolError("sketch ya da profile verin.")
    return _sketch(s, op.part, sketch), None


def _sketch(s: CatiaSession, part, name: str):
    sk = s.find_object(part, name)
    if s.typename(sk) != "Sketch":
        raise ToolError(f"'{name}' bir sketch değil.")
    return sk


def _set_limit(s, part, limit, mode: str | None, value: float | None, up_to_plane: str | None = None):
    if mode in (None, "dimension"):
        if value is not None:
            limit.Dimension.Value = float(value)
        return
    if mode not in LIMIT_MODES:
        raise ToolError(f"Bilinmeyen limit '{mode}'.", hint=f"{list(LIMIT_MODES)}")
    limit.LimitMode = LIMIT_MODES[mode]
    if mode == "plane":
        if not up_to_plane:
            raise ToolError("mode='plane' için up_to_plane gerekli.")
        target = s.origin_plane(part, up_to_plane) if up_to_plane.upper() in ("XY", "YZ", "ZX") else s.find_object(part, up_to_plane)
        limit.LimitingElement = s.ref(part, target)


def _direction_ref(s: CatiaSession, part, topo: Topology | None, spec, normal: bool = False):
    """'X'/'-Y' gibi eksen adı ya da kenar/yüzey seçicisini pattern referansına çevirir.
    Dönüş: (reference, reversed)"""
    if isinstance(spec, str) and spec.upper().lstrip("+-") in AXES:
        table = PLANE_FOR_NORMAL if normal else PLANE_FOR_DIRECTION
        plane = getattr(part.OriginElements, table[spec.upper().lstrip("+-")])
        return s.ref(part, plane), spec.startswith("-")
    if topo is None:
        raise ToolError(f"Yön çözülemedi: {spec}")
    if isinstance(spec, str) and spec.startswith("F") or isinstance(spec, dict) and spec.get("kind") == "face":
        return topo.resolve_faces(spec, many=False)[0].ref, False
    return topo.resolve_edges(spec, many=False)[0].ref, False


# ============================================================== sketch tabanlı
@tool(
    "pad",
    """Kapalı profilden pad: length; symmetric (toplam length), reverse, second_length, limit dimension|last|next|plane(up_to_plane).""",
    {"properties": {
        "document": DOC_PROP, "body": BODY_PROP, "sketch": {"type": "string"}, "profile": PROFILE_PROP,
        "length": {"type": "number"}, "second_length": {"type": "number"},
        "symmetric": {"type": "boolean"}, "reverse": {"type": "boolean"},
        "limit": {"enum": ["dimension", "last", "next", "plane"]}, "up_to_plane": {"type": "string"},
        "name": {"type": "string"},
    }},
)
def pad(s, sketch=None, profile=None, length=10.0, document=None, body=None, second_length=None, symmetric=False, reverse=False,
        limit=None, up_to_plane=None, name=None):
    op = _Op(s, document, body)
    sk, skrep = _profile_sketch(s, op, sketch, profile)
    if symmetric:
        f = op.track(op.sf.AddNewPad(sk, float(length) / 2))
        f.IsSymmetric = True
    else:
        f = op.track(op.sf.AddNewPad(sk, float(length)))
    _set_limit(s, op.part, f.FirstLimit, limit, None, up_to_plane)
    if second_length:
        f.SecondLimit.Dimension.Value = float(second_length)
    if reverse:
        f.DirectionOrientation = 1
    if name:
        f.Name = name
    return op.finish(f, "add", {"profile_sketch": skrep} if skrep else None)


@tool(
    "pocket",
    """Kapalı profille boşaltma: depth ya da through_all; yön sketch normalinin tersi (hacim azalmazsa reverse); symmetric, limit.""",
    {"properties": {
        "document": DOC_PROP, "body": BODY_PROP, "sketch": {"type": "string"}, "profile": PROFILE_PROP,
        "depth": {"type": "number"}, "through_all": {"type": "boolean"}, "symmetric": {"type": "boolean"},
        "reverse": {"type": "boolean"}, "limit": {"enum": ["dimension", "last", "next", "plane"]},
        "up_to_plane": {"type": "string"}, "name": {"type": "string"},
    }},
)
def pocket(s, sketch=None, profile=None, depth=10.0, document=None, body=None, through_all=False, symmetric=False, reverse=False,
           limit=None, up_to_plane=None, name=None):
    op = _Op(s, document, body)
    sk, skrep = _profile_sketch(s, op, sketch, profile)
    f = op.track(op.sf.AddNewPocket(sk, float(depth) / (2 if symmetric else 1)))
    if symmetric:
        f.IsSymmetric = True
    _set_limit(s, op.part, f.FirstLimit, "last" if through_all else limit, None, up_to_plane)
    if reverse:
        f.DirectionOrientation = 1
    if name:
        f.Name = name
    return op.finish(f, "remove", {"profile_sketch": skrep} if skrep else None)


@tool(
    "shaft",
    """'axis' öğeli profili eksen etrafında döndürür (angle°, varsayılan 360).""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "sketch": {"type": "string"}, "profile": PROFILE_PROP,
                    "angle": {"type": "number"}, "second_angle": {"type": "number"}, "name": {"type": "string"}},
     },
)
def shaft(s, sketch=None, profile=None, angle=360.0, second_angle=0.0, document=None, body=None, name=None):
    op = _Op(s, document, body)
    sk, skrep = _profile_sketch(s, op, sketch, profile)
    f = op.track(op.sf.AddNewShaft(sk))
    f.FirstAngle.Value = float(angle)
    f.SecondAngle.Value = float(second_angle)
    if name:
        f.Name = name
    return op.finish(f, "add", {"profile_sketch": skrep} if skrep else None)


@tool(
    "groove",
    """'axis' öğeli profille dönel boşaltma (angle°, varsayılan 360).""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "sketch": {"type": "string"}, "profile": PROFILE_PROP,
                    "angle": {"type": "number"}, "second_angle": {"type": "number"}, "name": {"type": "string"}},
     },
)
def groove(s, sketch=None, profile=None, angle=360.0, second_angle=0.0, document=None, body=None, name=None):
    op = _Op(s, document, body)
    sk, skrep = _profile_sketch(s, op, sketch, profile)
    f = op.track(op.sf.AddNewGroove(sk))
    f.FirstAngle.Value = float(angle)
    f.SecondAngle.Value = float(second_angle)
    if name:
        f.Name = name
    return op.finish(f, "remove", {"profile_sketch": skrep} if skrep else None)


# ================================================================ delik
@tool(
    "hole",
    """Düzlemsel yüzeye dik delik; position (ya da positions, çoklu) global nokta, yüzeye izdüşürülür. through_all ya da depth. type: simple|counterbored(head_diameter,head_depth)|countersunk(head_diameter,head_angle). thread: {size:'M2', pitch?, depth?, left_handed?} (kozmetik diş; diameter yoksa kılavuz çapı).""",
    {"properties": {
        "document": DOC_PROP, "body": BODY_PROP, "face": FACE_SELECTOR, "position": VEC3,
        "positions": {"type": "array", "items": VEC3},
        "diameter": {"type": "number"}, "depth": {"type": "number"}, "through_all": {"type": "boolean"},
        "type": {"enum": ["simple", "counterbored", "countersunk"]},
        "head_diameter": {"type": "number"}, "head_depth": {"type": "number"}, "head_angle": {"type": "number"},
        "thread": {"type": "object", "properties": {"size": {"type": "string"}, "pitch": {"type": "number"},
                                                    "depth": {"type": "number"}, "left_handed": {"type": "boolean"}},
                   "required": ["size"]},
        "name": {"type": "string"},
    }, "required": ["face"]},
)
def hole(s, face, position=None, diameter=None, depth=None, through_all=False, type="simple", head_diameter=None,
         head_depth=None, head_angle=None, document=None, body=None, name=None, thread=None, positions=None):
    thr = _thread_spec(thread) if thread else None
    if diameter is None:
        if thr is None:
            raise ToolError("diameter ya da thread verin.")
        diameter = thr["tap_drill"]
    op = _Op(s, document, body)
    pts = [list(map(float, q)) for q in (positions or ([position] if position is not None else []))]
    if not pts:
        raise ToolError("position ya da positions verin.")
    if not through_all and depth is None:
        raise ToolError("depth ya da through_all=true verin.")
    face_sel = face
    holes, placed, extra = [], [], {}
    for k, q in enumerate(pts):
        topo = Topology(s, op.doc, op.part, op.body)  # her delikten sonra topoloji tazelenir; yüz kimliği kalıcıdır
        fc = topo.resolve_faces(face_sel, many=False)[0]
        face_sel = fc.id
        if fc.info.get("type") != "planar":
            raise ToolError(f"{fc.id} düzlemsel değil; delik düzlemsel yüzeye açılır.")
        n, c = fc.info.get("normal"), fc.info.get("center")
        p = list(q)
        if n and c:
            d = dot([p[i] - c[i] for i in range(3)], n)
            p = [p[i] - d * n[i] for i in range(3)]
        # R20'de AddNewHoleFromPoint ile kurulan deliğin dairesel pattern'ı kesmiyor (doğrulandı);
        # yüzeye açılan sketch'teki tam kısıtlı bir noktadan delik (AddNewHoleFromSketch) doğru çalışıyor
        # ve konum CATIA'da düzenlenebilir ölçü olarak kalıyor.
        sk = op.body.Sketches.Add(fc.ref)
        op.track(sk)
        ax = s.vb_array(sk, "GetAbsoluteAxisData", 9)
        rel = [p[i] - ax[i] for i in range(3)]
        u, v = dot(rel, ax[3:6]), dot(rel, ax[6:9])
        meta = {"elements": {}, "dof": 0}
        b = _Builder(s, op.doc, op.part, sk, meta)
        try:
            b.add_element({"type": "point", "id": "center", "at": [u, v]}, 1, True)
        finally:
            b.close()
        s.sketch_meta[(op.doc.Name, sk.Name)] = meta
        op.part.InWorkObject = op.body
        h = op.track(op.sf.AddNewHoleFromSketch(sk, float(depth or 10.0)))
        h.Type = {"simple": 0, "counterbored": 2, "countersunk": 3}[type]
        h.Diameter.Value = float(diameter)
        if through_all:
            h.BottomLimit.LimitMode = 2
        else:
            # R20: AddNewHoleFromSketch'in derinlik argümanı uygulanmıyor, delik 10 mm kalıyordu (doğrulandı)
            h.BottomLimit.Dimension.Value = float(depth)
        if type == "counterbored":
            if not (head_diameter and head_depth):
                raise ToolError("counterbored için head_diameter ve head_depth gerekli.")
            h.HeadDiameter.Value = float(head_diameter)
            h.HeadDepth.Value = float(head_depth)
        if type == "countersunk":
            if not head_diameter:
                raise ToolError("countersunk için head_diameter gerekli.")
            h.CounterSunkMode = 1
            h.HeadDiameter.Value = float(head_diameter)
            h.HeadAngle.Value = float(head_angle or 90.0)
        if thr is not None:
            h.ThreadingMode = 0  # catThreadedHoleThreading
            h.ThreadDiameter.Value = thr["nominal"]
            h.ThreadPitch.Value = thr["pitch"]
            tdepth = thr.get("depth") or (depth if not through_all else None)
            if tdepth and not through_all and float(tdepth) >= float(depth):
                # Kör delikte diş boyu delik derinliğine eşit/büyükse update hatası (R20'de doğrulandı)
                tdepth = round(float(depth) - max(thr["pitch"], 0.5), 3)
            if not tdepth:
                # Sonuna kadar delikte diş boyu verilmezse CATIA varsayılanı (10 mm) parçadan uzun kalıp update hatası
                # veriyor (R20'de doğrulandı). Diş boyu = gövdenin delik ekseni boyunca bu yüzden itibaren kalınlığı.
                depths = [dot([c[i] - f.info["center"][i] for i in range(3)], n)
                          for f in topo.faces() if f.info.get("center") and n and c]
                tdepth = max([x for x in depths if x > 1e-6], default=None)
            if tdepth:
                h.ThreadDepth.Value = float(tdepth)
            if thr.get("left_handed"):
                h.ThreadSide = 1
            extra["thread"] = {"size": thr["size"], "nominal_mm": thr["nominal"], "pitch_mm": thr["pitch"],
                               "tap_drill_mm": float(diameter), "cosmetic": True}
        if name:
            h.Name = name if len(pts) == 1 else f"{name}.{k + 1}"
        holes.append(h)
        placed.append([r3(x) for x in p])
        if len(pts) > 1:
            bad = set(s.update(op.part)) - op.bad0
            if bad:
                raise ToolError(f"{h.Name} güncellenemedi ({sorted(bad)}); tüm delikler geri alındı.")
    extra["face"] = face_sel
    if len(holes) == 1:
        extra["position"] = placed[0]
    else:
        extra["features"] = [h.Name for h in holes]
        extra["positions"] = placed
    return op.finish(holes[-1], "remove", extra)


# ISO metrik kaba hatve (mm)
METRIC_COARSE = {1.0: 0.25, 1.2: 0.25, 1.4: 0.3, 1.6: 0.35, 2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 3.5: 0.6, 4.0: 0.7, 5.0: 0.8,
                 6.0: 1.0, 8.0: 1.25, 10.0: 1.5, 12.0: 1.75, 14.0: 2.0, 16.0: 2.0, 20.0: 2.5, 24.0: 3.0}


def _thread_spec(thread: dict) -> dict:
    size = str(thread["size"]).strip().upper().replace(",", ".")
    if not size.startswith("M"):
        raise ToolError(f"Diş ölçüsü '{thread['size']}' anlaşılamadı.", hint="Metrik biçimde verin: 'M2', 'M3', 'M2.5'.")
    try:
        nominal = float(size[1:].split("X")[0])
    except ValueError:
        raise ToolError(f"Diş ölçüsü '{thread['size']}' anlaşılamadı.") from None
    pitch = thread.get("pitch")
    if pitch is None and "X" in size:
        pitch = float(size.split("X")[1])
    if pitch is None:
        if nominal not in METRIC_COARSE:
            raise ToolError(f"M{nominal:g} için kaba hatve tablomda yok.", hint="pitch'i açıkça verin.")
        pitch = METRIC_COARSE[nominal]
    return {"size": f"M{nominal:g}" + ("" if pitch == METRIC_COARSE.get(nominal) else f"x{pitch:g}"),
            "nominal": nominal, "pitch": float(pitch), "tap_drill": round(nominal - float(pitch), 3),
            "depth": thread.get("depth"), "left_handed": thread.get("left_handed", False)}


# ============================================================= dress-up
@tool(
    "fillet",
    """Kenarlara sabit yarıçaplı fillet.""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "edges": EDGE_SELECTOR, "radius": {"type": "number"},
                    "propagate_tangent": {"type": "boolean", "default": True}, "name": {"type": "string"}},
     "required": ["edges", "radius"]},
)
def fillet(s, edges, radius, propagate_tangent=True, document=None, body=None, name=None):
    op = _Op(s, document, body)
    items = Topology(s, op.doc, op.part, op.body).resolve_edges(edges, many=True)
    f = op.track(op.sf.AddNewSolidEdgeFilletWithConstantRadius(items[0].ref, 1 if propagate_tangent else 0, float(radius)))
    for it in items[1:]:
        f.AddObjectToFillet(it.ref)
    if name:
        f.Name = name
    return op.finish(f, "remove", {"edges": [it.id for it in items]})


@tool(
    "chamfer",
    """Kenarlara pah: length + angle (varsayılan 45°) ya da length2.""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "edges": EDGE_SELECTOR, "length": {"type": "number"},
                    "angle": {"type": "number"}, "length2": {"type": "number"}, "reverse": {"type": "boolean"},
                    "name": {"type": "string"}},
     "required": ["edges", "length"]},
)
def chamfer(s, edges, length, angle=45.0, length2=None, reverse=False, document=None, body=None, name=None):
    op = _Op(s, document, body)
    items = Topology(s, op.doc, op.part, op.body).resolve_edges(edges, many=True)
    mode = 0 if length2 is not None else 1
    f = op.track(op.sf.AddNewChamfer(items[0].ref, 0, mode, 1 if reverse else 0, float(length),
                                     float(length2 if length2 is not None else angle)))
    for it in items[1:]:
        f.AddElementToChamfer(it.ref)
    if name:
        f.Name = name
    return op.finish(f, "remove", {"edges": [it.id for it in items]})


# ============================================================ dönüşümler
@tool(
    "rect_pattern",
    """Feature'ın doğrusal dizisi: direction1/2 'X'|'-Y'… ya da doğrusal kenar; count orijinal dahil; spacing mm. CATIA yönü ters yorumlarsa araç ölçüp düzeltir.""",
    {"properties": {
        "document": DOC_PROP, "body": BODY_PROP, "feature": {"type": "string"},
        "direction1": {"anyOf": [{"type": "string"}, {"type": "object"}]}, "count1": {"type": "integer"}, "spacing1": {"type": "number"},
        "direction2": {"anyOf": [{"type": "string"}, {"type": "object"}]}, "count2": {"type": "integer"}, "spacing2": {"type": "number"},
        "name": {"type": "string"},
    }, "required": ["feature", "direction1", "count1", "spacing1"]},
)
def rect_pattern(s, feature, direction1, count1, spacing1, direction2=None, count2=1, spacing2=10.0,
                 document=None, body=None, name=None):
    op = _Op(s, document, body)
    src = s.find_object(op.part, feature)
    topo = Topology(s, op.doc, op.part, op.body)
    r1, rev1 = _direction_ref(s, op.part, topo, direction1)
    r2, rev2 = _direction_ref(s, op.part, topo, direction2) if direction2 else (r1, False)
    f = op.track(op.sf.AddNewRectPattern(src, int(count1), int(count2 or 1), float(spacing1), float(spacing2 or 10.0),
                                         1, 1, r1, r2, bool(rev1), bool(rev2), 0.0))
    if name:
        f.Name = name
    corrected = _verify_pattern_direction(s, op, src, f, topo, direction1, direction2 if count2 and count2 > 1 else None)
    out = op.finish(f)
    if corrected:
        out["direction_corrected"] = corrected
    return out


def _dir_vector(topo: Topology, spec) -> list[float] | None:
    if isinstance(spec, str) and spec.upper().lstrip("+-") in AXES:
        v = list(AXES[spec.upper().lstrip("+-")])
        return [-x for x in v] if spec.startswith("-") else v
    try:
        return topo.resolve_edges(spec, many=False)[0].info.get("direction")
    except ToolError:
        return None


def _verify_pattern_direction(s: CatiaSession, op: "_Op", src, pat, topo: Topology, d1, d2) -> list[str]:
    """CATIA'nın pattern yön işareti feature türüne göre değişiyor (R20'de pocket'ta +X, delikte -X
    çıktı; GetFirstDirection ikisinde de aynı değeri bildiriyor). Bu yüzden sonucu ölçerek doğrularız:
    yeni eklenen/çıkarılan malzemenin ağırlık merkezi, kaynak feature'ın merkezine göre istenen yönde
    olmalı. Değilse FirstOrientation/SecondOrientation çevrilir."""
    from ..catia.measure import body_cog

    spa = s.spa(op.doc)
    try:
        src_c = s.vb_array(spa.GetMeasurable(s.ref(op.part, src)), "GetCOG", 3)
    except (ToolError, pywintypes.com_error):
        return []
    c0 = body_cog(s, op.doc, op.part, op.body) if op.v0 > 0 else [0.0, 0.0, 0.0]
    if c0 is None:
        return []

    def shift():
        if s.update(op.part):
            return None
        v1 = _volume(s, op.doc, op.part, op.body)
        c1 = body_cog(s, op.doc, op.part, op.body)
        dv = v1 - op.v0
        if abs(dv) < 1e-6 or c1 is None:
            return None
        cen = [(c1[i] * v1 - c0[i] * op.v0) / dv for i in range(3)]
        return [cen[i] - src_c[i] for i in range(3)]

    fixed = []
    for label, spec, prop in (("direction1", d1, "FirstOrientation"), ("direction2", d2, "SecondOrientation")):
        vec = _dir_vector(topo, spec) if spec is not None else None
        if not vec:
            continue
        sh = shift()
        if sh is not None and dot(sh, vec) > 1e-6:
            continue
        old = bool(getattr(pat, prop))
        setattr(pat, prop, not old)
        sh2 = shift()
        if sh2 is not None and dot(sh2, vec) > 1e-6:
            fixed.append(f"{label} CATIA'da ters yorumlandı; {prop} çevrildi.")
        else:
            setattr(pat, prop, old)  # iki durumda da doğrulanamadı; ilk hâli koru
    return fixed


@tool(
    "circ_pattern",
    """Feature'ın dairesel dizisi: axis 'X'|'Y'|'Z' (orijinden) ya da silindirik yüzey/kenar; count orijinal dahil; angle_step yoksa 360/count.""",
    {"properties": {
        "document": DOC_PROP, "body": BODY_PROP, "feature": {"type": "string"},
        "axis": {"anyOf": [{"type": "string"}, {"type": "object"}]}, "count": {"type": "integer"},
        "angle_step": {"type": "number"}, "reverse": {"type": "boolean"}, "name": {"type": "string"},
    }, "required": ["feature", "axis", "count"]},
)
def circ_pattern(s, feature, axis, count, angle_step=None, reverse=False, document=None, body=None, name=None):
    op = _Op(s, document, body)
    src = s.find_object(op.part, feature)
    topo = Topology(s, op.doc, op.part, op.body)
    if isinstance(axis, dict) and axis.get("type") == "cylindrical" or isinstance(axis, str) and axis.startswith("F"):
        ref, rev = topo.resolve_faces(axis, many=False)[0].ref, False
    else:
        ref, rev = _direction_ref(s, op.part, topo, axis, normal=True)
    step = float(angle_step) if angle_step else 360.0 / int(count)
    f = op.track(op.sf.AddNewCircPattern(src, 1, int(count), 0.0, step, 1, 1, ref, ref, bool(rev) ^ bool(reverse), 0.0, True))
    if name:
        f.Name = name
    return op.finish(f)


@tool(
    "mirror",
    """Çalışılan gövdenin o ana kadarki TAMAMINI aynalar; plane 'XY'|'YZ'|'ZX'|{plane}|düzlemsel yüzey.""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP,
                    "plane": {"anyOf": [{"type": "string"}, {"type": "object"}]}, "name": {"type": "string"}},
     "required": ["plane"]},
)
def mirror(s, plane, document=None, body=None, name=None):
    op = _Op(s, document, body)
    if isinstance(plane, str) and plane.upper() in ("XY", "YZ", "ZX", "XZ"):
        ref = s.ref(op.part, s.origin_plane(op.part, plane))
    elif isinstance(plane, dict) and "plane" in plane:
        ref = s.ref(op.part, s.find_object(op.part, plane["plane"]))
    else:
        ref = Topology(s, op.doc, op.part, op.body).resolve_faces(plane, many=False)[0].ref
    f = op.track(op.sf.AddNewMirror(ref))
    if name:
        f.Name = name
    return op.finish(f, "add")


# ========================================================= gövde / düzlem
@tool(
    "new_body",
    "Parçaya yeni bir katı gövde (Body) ekler ve onu çalışılan gövde yapar.",
    {"properties": {"document": DOC_PROP, "name": {"type": "string"}}},
)
def new_body(s, document=None, name=None):
    doc, part = s.part_document(document)
    b = part.Bodies.Add()
    s.journal.record(f"{b.Name} sil", lambda d=doc, o=b: s.delete_objects(d, [o]))
    if name:
        b.Name = name
    part.InWorkObject = b
    s.created.add((doc.Name, b.Name))
    return {"body": b.Name}


@tool(
    "boolean",
    "Bir gövdeyi çalışılan (hedef) gövdeyle birleştirir: operation add | remove | intersect.",
    {"properties": {"document": DOC_PROP, "body": {"type": "string", "description": "Uygulanacak (araç) gövde."},
                    "target": {"type": "string", "description": "Hedef gövde (varsayılan PartBody)."},
                    "operation": {"enum": ["add", "remove", "intersect"]}},
     "required": ["body", "operation"]},
)
def boolean(s, body, operation, target=None, document=None):
    doc, part = s.part_document(document)
    tool_body = s.body(part, body)
    tgt = s.body(part, target) if target else part.MainBody
    if tool_body.Name == tgt.Name:
        raise ToolError("Araç gövde ile hedef gövde aynı.")
    op = _Op(s, document, tgt.Name)
    fn = {"add": op.sf.AddNewAdd, "remove": op.sf.AddNewRemove, "intersect": op.sf.AddNewIntersect}[operation]
    f = op.track(fn(tool_body))
    return op.finish(f, {"add": "add", "remove": "remove"}.get(operation))


@tool(
    "create_plane",
    """Ana düzleme ya da düzlemsel yüzeye paralel, offset mm uzaklıkta düzlem ('Construction' seti).""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP,
                    "base": {"anyOf": [{"type": "string"}, {"type": "object"}], "description": "'XY'|'YZ'|'ZX' ya da yüzey seçici"},
                    "offset": {"type": "number"}, "name": {"type": "string"}},
     "required": ["base", "offset"]},
)
def create_plane(s, base, offset, document=None, body=None, name=None):
    doc, part = s.part_document(document)
    b = s.body(part, body)
    if isinstance(base, str) and base.upper() in ("XY", "YZ", "ZX", "XZ"):
        ref = s.ref(part, s.origin_plane(part, base))
    else:
        ref = Topology(s, doc, part, b).resolve_faces(base, many=False)[0].ref
    try:
        hb = part.HybridBodies.Item("Construction")
    except pywintypes.com_error:
        hb = part.HybridBodies.Add()
        hb.Name = "Construction"
        s.journal.record("Construction setini sil", lambda d=doc, o=hb: s.delete_objects(d, [o]))
    pl = part.HybridShapeFactory.AddNewPlaneOffset(ref, float(abs(offset)), offset < 0)
    hb.AppendHybridShape(pl)
    s.journal.record(f"{pl.Name} sil", lambda d=doc, o=pl: s.delete_objects(d, [o]))
    if name:
        pl.Name = name
    try:
        part.UpdateObject(pl)
    except pywintypes.com_error as exc:
        raise ToolError(f"Düzlem oluşturulamadı: {com_error_text(exc)}") from None
    part.InWorkObject = b
    return {"plane": pl.Name, "set": "Construction"}


# ============================================================ parametreler
def _params(part, filt: str | None):
    ps = part.Parameters
    out = []
    for i in range(1, ps.Count + 1):
        p = ps.Item(i)
        name = p.Name
        if filt and filt.lower() not in name.lower():
            continue
        out.append(p)
    return out


def _short(name: str) -> str:
    # "Part1\PartBody\Pad.1\FirstLimit\Length" -> "PartBody\Pad.1\FirstLimit\Length"
    return name.split("\\", 1)[1] if "\\" in name else name


@tool(
    "list_parameters",
    """Parça parametreleri ve değerleri; filter ad içinde arar (ör. 'Pad.1').""",
    {"properties": {"document": DOC_PROP, "filter": {"type": "string"}, "limit": {"type": "integer", "default": 60}}},
    mutating=False,
)
def list_parameters(s, document=None, filter=None, limit=60):
    doc, part = s.part_document(document)
    out = []
    for p in _params(part, filter):
        if p.Name.endswith("\\Activity") or "AbsoluteAxis" in p.Name:
            continue
        try:
            val = p.ValueAsString()
        except pywintypes.com_error:
            val = None
        out.append({"name": _short(p.Name), "value": val})
        if len(out) >= limit:
            break
    return {"parameters": out, "truncated": len(out) >= limit}


@tool(
    "set_parameter",
    """Parametre değerini değiştirir (ad tam ya da son kısmı, ör. 'Pad.1\\FirstLimit\\Length'; değer sayı ya da '25mm'). Update hatasında eski değer geri yazılır.""",
    {"properties": {"document": DOC_PROP, "name": {"type": "string"},
                    "value": {"anyOf": [{"type": "number"}, {"type": "string"}, {"type": "boolean"}]}},
     "required": ["name", "value"]},
)
def set_parameter(s, name, value, document=None):
    doc, part = s.part_document(document)
    matches = [p for p in _params(part, None) if p.Name == name or _short(p.Name) == name or p.Name.endswith("\\" + name)]
    if not matches:
        raise ToolError(f"'{name}' parametresi bulunamadı.", hint="list_parameters ile adları alın.")
    if len(matches) > 1:
        raise ToolError(f"'{name}' birden fazla parametreye uyuyor.", hint=str([_short(p.Name) for p in matches][:10]))
    p = matches[0]
    old = p.ValueAsString()
    bad0 = set(s.failing_features(part))
    s.journal.record(f"{_short(p.Name)} = {old}", lambda p=p, old=old, part=part: (p.ValuateFromString(old), part.Update()))
    if isinstance(value, str):
        p.ValuateFromString(value)
    else:
        p.Value = value
    bad = set(s.update(part)) - bad0
    if bad:
        raise ToolError(f"Yeni değerle update hatası: {sorted(bad)}. Eski değer ({old}) geri yazıldı.")
    return {"parameter": _short(p.Name), "old": old, "new": p.ValueAsString()}


@tool(
    "delete_feature",
    """Bu oturumda asistanın oluşturduğu nesneyi siler (geri alınamaz); kullanıcınınkiler için set_feature_active.""",
    {"properties": {"document": DOC_PROP, "name": {"type": "string"}}, "required": ["name"]},
    mutating=False,
)
def delete_feature(s, name, document=None):
    doc, part = s.part_document(document)
    if (doc.Name, name) not in s.created:
        raise ToolError(f"'{name}' asistan tarafından oluşturulmadı; silinmesine izin yok.",
                        hint="Geçici olarak kapatmak için set_feature_active(active=false) kullanın.")
    obj = s.find_object(part, name)
    s.delete_objects(doc, [obj])
    s.created.discard((doc.Name, name))
    failing = s.update(part)
    return {"deleted": name, "update_errors": failing, "note": "Silme geri alınamaz."}


@tool(
    "set_feature_active",
    "Bir feature'ı etkisizleştirir/etkinleştirir (CATIA Deactivate/Activate). Geri alınabilir.",
    {"properties": {"document": DOC_PROP, "name": {"type": "string"}, "active": {"type": "boolean"}},
     "required": ["name", "active"]},
)
def set_feature_active(s, name, active, document=None):
    doc, part = s.part_document(document)
    target = [p for p in _params(part, None) if p.Name.endswith(f"\\{name}\\Activity")]
    if not target:
        raise ToolError(f"'{name}' için Activity parametresi yok.")
    p = target[0]
    old = bool(p.Value)
    s.journal.record(f"{name} etkinliği = {old}", lambda p=p, old=old, part=part: (setattr(p, "Value", old), part.Update()))
    p.Value = bool(active)
    failing = s.update(part)
    return {"feature": name, "active": bool(active), "update_errors": failing}
