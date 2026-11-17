"""Sarmal ve süpürme tabanlı araçlar: helix, sweep (rib/slot), yay, tırtıl; malzeme atama.

R20'de doğrulananlar (docs/R20_API_NOTLARI.md):
- HybridShapeFactory.AddNewHelix(eksen doğrusu, ters, başlangıç noktası, hatve, yükseklik, saat yönü, açı, koniklik, dışa)
  boyu formülle birebir; saat_yönü=False sağ el sarımı (açı +X'ten +Y'ye doğru artar).
- Profil, eğriye dik düzlemde (AddNewPlaneNormal) çizilip AddNewRibFromRef ile süpürülünce hacim A × L ile birebir.
- Üç helix'i AddNewJoin ile birleştirip tek rib yapmak geçişlerdeki kırık yüzünden başarısız; her parça ayrı rib edilir
  (geçişlerde ~%0,01 örtüşme).
- AddNewSplit(XY, 0) düzlemin + tarafını bırakır; taşlanmış yay uçları böyle kesilir.
- AddNewSlotFromRef helix boyunca kanal açar; dairesel dizisi tam N kat malzeme kaldırır (çapraz tırtıl).
- Malzeme: <kurulum>/startup/materials/Catalog.CATMaterial, part.GetItem("CATMatManagerVBExt").ApplyMaterialOnBody.
"""
from __future__ import annotations

import math
from pathlib import Path

import pywintypes

from ..catia.errors import ToolError, com_error_text
from ..catia.session import CatiaSession, r3, vec3
from .features import _Op, _sketch
from .registry import BODY_PROP, DOC_PROP, VEC3, tool
from .sketch import _Builder

AXIS_VEC = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
# Eksene dik, sağ elli taban (e1 × e2 = eksen): başlangıç açısı e1'den e2'ye doğru ölçülür.
AXIS_BASIS = {"X": ((0, 1, 0), (0, 0, 1)), "Y": ((0, 0, 1), (1, 0, 0)), "Z": ((1, 0, 0), (0, 1, 0))}
SET_NAME = "Construction"


# =================================================================== yardımcılar
def construction_set(s: CatiaSession, doc, part):
    try:
        return part.HybridBodies.Item(SET_NAME)
    except pywintypes.com_error:
        hb = part.HybridBodies.Add()
        hb.Name = SET_NAME
        s.journal.record(f"{SET_NAME} setini sil", lambda d=doc, o=hb: s.delete_objects(d, [o]))
        return hb


def _add(s: CatiaSession, doc, part, hb, obj, name: str | None = None):
    hb.AppendHybridShape(obj)
    if name:
        try:
            obj.Name = name
        except pywintypes.com_error:
            pass
    s.journal.record(f"{obj.Name} sil", lambda d=doc, o=obj: s.delete_objects(d, [o]))
    try:
        part.UpdateObject(obj)
    except pywintypes.com_error as exc:
        raise ToolError(f"{obj.Name} oluşturulamadı: {com_error_text(exc)}") from None
    return obj


def hide(doc, objs) -> None:
    """Yardımcı geometriyi gizler (silmez; ağaçta düzenlenebilir kalır)."""
    sel = doc.Selection
    try:
        sel.Clear()
        for o in objs:
            sel.Add(o)
        if sel.Count:
            sel.VisProperties.SetShow(1)  # 1 = gizli
    except pywintypes.com_error:
        pass
    finally:
        sel.Clear()


def _hide_set(doc, hb) -> None:
    try:
        hide(doc, [hb.HybridShapes.Item(i) for i in range(1, hb.HybridShapes.Count + 1)])
    except pywintypes.com_error:
        pass


def _axis_key(axis: str) -> str:
    a = (axis or "Z").upper().lstrip("+")
    if a not in AXIS_VEC:
        raise ToolError(f"Bilinmeyen eksen '{axis}'.", hint="X, Y ya da Z.")
    return a


def _point_at(center, axis: str, radius: float, angle_deg: float, height: float):
    e1, e2 = AXIS_BASIS[axis]
    a = math.radians(angle_deg)
    ax = AXIS_VEC[axis]
    return [center[i] + radius * (math.cos(a) * e1[i] + math.sin(a) * e2[i]) + height * ax[i] for i in range(3)]


def _axis_line(s, doc, part, hb, center, axis: str):
    hsf = part.HybridShapeFactory
    o = _add(s, doc, part, hb, hsf.AddNewPointCoord(*map(float, center)))
    d = hsf.AddNewDirectionByCoord(*AXIS_VEC[axis])
    return _add(s, doc, part, hb, hsf.AddNewLinePtDir(s.ref(part, o), d, 0.0, 10.0, False))


def _helix(s, doc, part, hb, axis_line, start_xyz, pitch, height, left_handed, taper=0.0, name=None):
    hsf = part.HybridShapeFactory
    sp = _add(s, doc, part, hb, hsf.AddNewPointCoord(*map(float, start_xyz)))
    hx = hsf.AddNewHelix(s.ref(part, axis_line), False, s.ref(part, sp), float(pitch), float(height),
                         bool(left_handed), 0.0, float(taper), False)
    return _add(s, doc, part, hb, hx, name), sp


def _curve_length(s, doc, part, obj) -> float:
    return float(s.spa(doc).GetMeasurable(s.ref(part, obj)).Length)  # R20: mm


def profile_on_curve(s, op: _Op, hb, curve, point_obj, point_xyz, element: dict):
    """Eğrinin verilen noktasında eğriye dik düzlemde, merkezi o noktada olan tam kısıtlı bir profil sketch'i."""
    part, doc = op.part, op.doc
    hsf = part.HybridShapeFactory
    pl = _add(s, doc, part, hb, hsf.AddNewPlaneNormal(s.ref(part, curve), s.ref(part, point_obj)))
    part.InWorkObject = op.body
    sk = op.body.Sketches.Add(s.ref(part, pl))
    op.track(sk)
    ax = s.vb_array(sk, "GetAbsoluteAxisData", 9)
    rel = [point_xyz[i] - ax[i] for i in range(3)]
    u = sum(rel[i] * ax[3 + i] for i in range(3))
    v = sum(rel[i] * ax[6 + i] for i in range(3))
    meta = {"elements": {}, "dof": 0}
    b = _Builder(s, doc, part, sk, meta)
    try:
        el = dict(element)
        el["center"] = [u, v]
        b.add_element(el, 1, True)
    finally:
        b.close()
    s.sketch_meta[(doc.Name, sk.Name)] = meta
    part.InWorkObject = op.body
    return sk


def _rib(s, op: _Op, profile, path, remove: bool = False, pulling=None):
    """pulling: çekme yönü öğesi (ör. helix ekseni). Varsayılan 'keep angle' modu profili helix boyunca burulmayla
    döndürüyor (R20'de ölçüldü: 8,5 turda ~165°); diş gibi eksene göre sabit profil için çekme yönü verilmeli."""
    fn = op.sf.AddNewSlotFromRef if remove else op.sf.AddNewRibFromRef
    try:
        f = fn(s.ref(op.part, profile), s.ref(op.part, path))
        if pulling is not None:
            f.PullingDirElement = s.ref(op.part, pulling)
    except pywintypes.com_error as exc:
        raise ToolError(f"Süpürme oluşturulamadı: {com_error_text(exc)}") from None
    op.track(f)
    bad = set(s.update(op.part)) - op.bad0
    if bad:
        raise ToolError(f"{f.Name} güncellenemedi ({sorted(bad)}). Değişiklik geri alındı.",
                        hint="Profil eğriye göre çok büyük olabilir (ör. tel çapı hatveden büyük) ya da profil eğrinin ucunda değil.")
    return f


def _circ_pattern_about(s, op: _Op, feat, axis_line, count: int):
    ref = s.ref(op.part, axis_line)
    op.part.InWorkObject = op.body
    p = op.track(op.sf.AddNewCircPattern(feat, 1, int(count), 0.0, 360.0 / int(count), 1, 1, ref, ref, False, 0.0, True))
    return p


# ========================================================================= helix
@tool(
    "create_helix",
    """Helix ('Construction' seti): axis, center, radius, pitch, height|turns, start_angle°, left_handed, taper_angle.""",
    {"properties": {"document": DOC_PROP, "axis": {"enum": ["X", "Y", "Z"]}, "center": VEC3,
                    "radius": {"type": "number"}, "pitch": {"type": "number"}, "height": {"type": "number"},
                    "turns": {"type": "number"}, "start_angle": {"type": "number"}, "left_handed": {"type": "boolean"},
                    "taper_angle": {"type": "number"}, "name": {"type": "string"}},
     "required": ["radius", "pitch"]},
)
def create_helix(s, radius, pitch, document=None, axis="Z", center=None, height=None, turns=None, start_angle=0.0,
                 left_handed=False, taper_angle=0.0, name=None):
    doc, part = s.part_document(document)
    axis = _axis_key(axis)
    center = list(center or [0, 0, 0])
    if pitch <= 0 or radius <= 0:
        raise ToolError("radius ve pitch pozitif olmalı.")
    if height is None:
        if turns is None:
            raise ToolError("height ya da turns verin.")
        height = float(turns) * float(pitch)
    hb = construction_set(s, doc, part)
    in_work = part.InWorkObject
    line = _axis_line(s, doc, part, hb, center, axis)
    start = _point_at(center, axis, radius, start_angle, 0.0)
    hx, _ = _helix(s, doc, part, hb, line, start, pitch, height, left_handed, taper_angle, name)
    try:
        part.InWorkObject = in_work
    except pywintypes.com_error:
        pass
    L = _curve_length(s, doc, part, hx)
    turns_ = float(height) / float(pitch)
    return {"helix": hx.Name, "length_mm": r3(L), "turns": r3(turns_), "start_point": vec3(start),
            "expected_length_mm": r3(turns_ * math.hypot(2 * math.pi * radius, pitch)) if not taper_angle else None}


# ========================================================================= sweep
@tool(
    "sweep",
    """Profil sketch'ini eğri boyunca süpürür: add (rib) | remove (slot). Profil eğrinin başında ve ona dik olmalı: create_sketch(support={normal_to: eğri}).""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "profile": {"type": "string"}, "path": {"type": "string"},
                    "operation": {"enum": ["add", "remove"]}, "name": {"type": "string"}},
     "required": ["profile", "path"]},
)
def sweep(s, profile, path, operation="add", document=None, body=None, name=None):
    op = _Op(s, document, body)
    sk = _sketch(s, op.part, profile)
    curve = s.find_object(op.part, path)
    f = _rib(s, op, sk, curve, remove=(operation == "remove"))
    if name:
        f.Name = name
    return op.finish(f, "remove" if operation == "remove" else "add")


# ========================================================================= yay
@tool(
    "spring",
    """Basma yayı tek adımda: mean_diameter|outer_diameter, wire_diameter, free_length, active_coils|pitch, end_type closed_ground(varsayılan)|closed|open, closed_coils, left_handed, axis/center. Döner: hacim, beklenen hacim, tel boyu, katı boy, beklenen sınır kutusu.""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "mean_diameter": {"type": "number"},
                    "outer_diameter": {"type": "number"}, "wire_diameter": {"type": "number"},
                    "free_length": {"type": "number"}, "active_coils": {"type": "number"}, "pitch": {"type": "number"},
                    "end_type": {"enum": ["closed_ground", "closed", "open"]}, "closed_coils": {"type": "number"},
                    "left_handed": {"type": "boolean"}, "axis": {"enum": ["X", "Y", "Z"]}, "center": VEC3},
     "required": ["wire_diameter", "free_length"]},
)
def spring(s, wire_diameter, free_length, document=None, body=None, mean_diameter=None, outer_diameter=None,
           active_coils=None, pitch=None, end_type="closed_ground", closed_coils=1.0, left_handed=False, axis="Z",
           center=None):
    d = float(wire_diameter)
    if mean_diameter is None:
        if outer_diameter is None:
            raise ToolError("mean_diameter ya da outer_diameter verin.")
        mean_diameter = float(outer_diameter) - d
    D = float(mean_diameter)
    L = float(free_length)
    if d <= 0 or D <= d:
        raise ToolError(f"Geçersiz çaplar: ortalama çap ({D}) tel çapından ({d}) büyük olmalı.")
    axis = _axis_key(axis)
    center = list(center or [0, 0, 0])
    nc = float(closed_coils) if end_type != "open" else 0.0
    # Tel ekseninin (helix) toplam yüksekliği: taşlanmış uçta tel ekseni 0..L (uçlar d/2 taşlanır), diğerlerinde
    # tel uçları eksenden d/2 taşar, bu yüzden eksen 0..L-d.
    path_h = L if end_type == "closed_ground" else L - d
    active_h = path_h - 2 * nc * d
    if active_h <= 0:
        raise ToolError(f"Serbest boy çok kısa: kapalı turlar ({2 * nc:g} × {d} mm) boyu dolduruyor.",
                        hint="free_length'i artırın ya da closed_coils'i azaltın.")
    if active_coils is None and pitch is None:
        raise ToolError("active_coils ya da pitch verin.")
    p = float(pitch) if pitch is not None else active_h / float(active_coils)
    n_act = active_h / p
    if p <= d * 1.0001:
        raise ToolError(f"Aktif hatve ({p:.3f} mm) tel çapından ({d} mm) büyük olmalı; aksi halde sarımlar iç içe geçer.",
                        hint="Aktif tur sayısını azaltın ya da boyu artırın.")
    op = _Op(s, document, body)
    hb = construction_set(s, op.doc, op.part)
    line = _axis_line(s, op.doc, op.part, hb, center, axis)
    segments = ([(d, nc * d)] if nc else []) + [(p, active_h)] + ([(d, nc * d)] if nc else [])
    z, ang, total_len, ribs = 0.0, 0.0, 0.0, []
    sign = -1.0 if left_handed else 1.0
    for i, (pitch_i, h_i) in enumerate(segments):
        start = _point_at(center, axis, D / 2, ang, z)
        hx, sp = _helix(s, op.doc, op.part, hb, line, start, pitch_i, h_i, left_handed, name=f"YayHelix.{i + 1}")
        total_len += _curve_length(s, op.doc, op.part, hx)
        sk = profile_on_curve(s, op, hb, hx, sp, start, {"type": "circle", "id": "tel", "radius": d / 2})
        ribs.append(_rib(s, op, sk, hx).Name)
        z += h_i
        ang += sign * 360.0 * (h_i / pitch_i)
    vol_before = None
    try:
        from ..catia.measure import body_volume_mm3
        vol_before = body_volume_mm3(s, op.doc, op.part, op.body)
    except pywintypes.com_error:
        pass
    last = None
    if end_type == "closed_ground":
        hsf = op.part.HybridShapeFactory
        ax = AXIS_VEC[axis]
        base_plane = getattr(op.part.OriginElements, {"X": "PlaneYZ", "Y": "PlaneZX", "Z": "PlaneXY"}[axis])
        off0 = sum(center[i] * ax[i] for i in range(3))
        p0 = _add(s, op.doc, op.part, hb, hsf.AddNewPlaneOffset(s.ref(op.part, base_plane), abs(off0), off0 < 0)) if off0 else base_plane
        p1 = _add(s, op.doc, op.part, hb, hsf.AddNewPlaneOffset(s.ref(op.part, base_plane), abs(off0 + L), off0 + L < 0))
        op.part.InWorkObject = op.body
        last = op.track(op.sf.AddNewSplit(s.ref(op.part, p0), 0))
        op.part.InWorkObject = op.body
        last = op.track(op.sf.AddNewSplit(s.ref(op.part, p1), 1))
    feat = last or s.find_object(op.part, ribs[-1])
    _hide_set(op.doc, hb)
    expected_wire = math.pi * d * d / 4 * total_len
    out = op.finish(feat, "add", {
        "features": ribs + ([feat.Name] if last is not None else []),
        "pitch_active": r3(p), "active_coils": r3(n_act), "total_coils": r3(n_act + 2 * nc),
        "wire_length_mm": r3(total_len), "volume_before_grinding_mm3": r3(vol_before) if vol_before else None,
        "expected_volume_before_grinding_mm3": r3(expected_wire),
        "solid_height_mm": r3((n_act + 2 * nc + (0 if end_type == "closed_ground" else 1)) * d),
        "spring_index": r3(D / d), "outer_diameter": r3(D + d), "inner_diameter": r3(D - d),
        "expected_bbox_size": vec3([D + d if axis != k else L for k in ("X", "Y", "Z")]),
    })
    if end_type == "closed_ground":
        out["note"] = "Uçlar taşlandığı için son hacim, telin d/2 kadar kesilen uç parçaları kadar beklenenden azdır."
    return out


# ======================================================================== tırtıl
@tool(
    "knurl",
    """Silindirik dış yüzeye tırtıl: diameter, start/end (eksen boyunca), count, style straight|diagonal|cross, angle°, groove_radius; axis/center.""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "diameter": {"type": "number"},
                    "start": {"type": "number"}, "end": {"type": "number"}, "count": {"type": "integer"},
                    "style": {"enum": ["straight", "diagonal", "cross"]}, "angle": {"type": "number"},
                    "groove_radius": {"type": "number"}, "axis": {"enum": ["X", "Y", "Z"]}, "center": VEC3},
     "required": ["diameter", "start", "end", "count"]},
)
def knurl(s, diameter, start, end, count, document=None, body=None, style="cross", angle=30.0, groove_radius=None,
          axis="Z", center=None):
    axis = _axis_key(axis)
    center = list(center or [0, 0, 0])
    Rr = float(diameter) / 2
    a0, a1 = sorted((float(start), float(end)))
    if a1 - a0 <= 0:
        raise ToolError("Tırtıl bölgesinin uzunluğu sıfır.")
    if count < 3:
        raise ToolError("count en az 3 olmalı.")
    gr = float(groove_radius) if groove_radius else max(0.05 * float(diameter), 0.1)
    op = _Op(s, document, body)
    hb = construction_set(s, op.doc, op.part)
    line = _axis_line(s, op.doc, op.part, hb, center, axis)
    feats = []
    hsf = op.part.HybridShapeFactory
    if style == "straight":
        # eksen boyunca düz bir doğru yol; ucunda dik düzlemde dairesel profil -> slot
        sp = _add(s, op.doc, op.part, hb, hsf.AddNewPointCoord(*_point_at(center, axis, Rr, 0.0, a0)))
        ep = _add(s, op.doc, op.part, hb, hsf.AddNewPointCoord(*_point_at(center, axis, Rr, 0.0, a1)))
        path = _add(s, op.doc, op.part, hb, hsf.AddNewLinePtPt(s.ref(op.part, sp), s.ref(op.part, ep)))
        paths = [(path, sp, _point_at(center, axis, Rr, 0.0, a0))]
    else:
        lead = math.pi * float(diameter) / math.tan(math.radians(float(angle)))
        paths = []
        for left in ((False, True) if style == "cross" else (False,)):
            start_xyz = _point_at(center, axis, Rr, 0.0, a0)
            hx, sp = _helix(s, op.doc, op.part, hb, line, start_xyz, lead, a1 - a0, left)
            paths.append((hx, sp, start_xyz))
    for path, sp, sxyz in paths:
        sk = profile_on_curve(s, op, hb, path, sp, sxyz, {"type": "circle", "id": "kanal", "radius": gr})
        slot = _rib(s, op, sk, path, remove=True)
        feats.append(slot.Name)
        feats.append(_circ_pattern_about(s, op, slot, line, count).Name)
    _hide_set(op.doc, hb)
    out = op.finish(s.find_object(op.part, feats[-1]), "remove",
                    {"features": feats, "groove_radius": r3(gr), "style": style})
    return out


# ================================================================ gerçek (modelli) diş
def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    if n < 1e-9:
        raise ToolError("Eksen vektörü sıfır.")
    return [x / n for x in v]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _perp(a):
    t = [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0]
    return _unit(_cross(a, t))


@tool(
    "model_thread",
    """Gerçek diş geometrisi (ISO 60° profil, helix boyunca slot): hole=delik feature adı verilirse eksen, çap, hatve, boy
deliğin (kozmetik) diş tanımından okunur. Ya da center (giriş yüzü merkezi), axis (malzemeye doğru vektör ya da 'X'|'-Z'…),
diameter (nominal), pitch|size, length; external=true dış diş (mil). STL'de görünür; kozmetik dişten ağırdır.""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "hole": {"type": "string"}, "size": {"type": "string"},
                    "diameter": {"type": "number"}, "pitch": {"type": "number"}, "length": {"type": "number"},
                    "center": VEC3, "axis": {"anyOf": [{"type": "string"}, VEC3]}, "external": {"type": "boolean"},
                    "left_handed": {"type": "boolean"}, "name": {"type": "string"}}},
)
def model_thread(s, document=None, body=None, hole=None, size=None, diameter=None, pitch=None, length=None, center=None,
                 axis=None, external=False, left_handed=None, name=None):
    from .features import _thread_spec

    op = _Op(s, document, body)
    if size:
        spec = _thread_spec({"size": size, "pitch": pitch})
        diameter, pitch = diameter or spec["nominal"], spec["pitch"]
    if hole:
        h = s.find_object(op.part, hole)
        center = list(s.vb_array(h, "GetOrigin", 3))
        axis_v = _unit(list(s.vb_array(h, "GetDirection", 3)))  # R20: malzemeye doğru (doğrulandı)
        threaded = False
        try:
            threaded = int(h.ThreadingMode) == 0
        except pywintypes.com_error:
            pass
        if threaded:
            diameter = diameter or h.ThreadDiameter.Value
            pitch = pitch or h.ThreadPitch.Value
            length = length or h.ThreadDepth.Value
            if left_handed is None:
                left_handed = int(h.ThreadSide) == 1
        bore = float(h.Diameter.Value)
        external = False
    else:
        if center is None or axis is None:
            raise ToolError("hole ya da center + axis verin.")
        if isinstance(axis, str):
            a = axis.strip().upper()
            sign = -1.0 if a.startswith("-") else 1.0
            axis_v = [sign * x for x in AXIS_VEC[_axis_key(a.lstrip("+-"))]]
        else:
            axis_v = _unit(list(map(float, axis)))
        bore = None
    if not (diameter and pitch and length):
        raise ToolError("diameter, pitch ve length gerekli (ya da kozmetik dişli bir hole).",
                        hint="size: 'M20x1.5' çap ve hatveyi verir.")
    D, P, L = float(diameter), float(pitch), float(length)
    H = 0.8660254 * P
    t30 = math.tan(math.radians(30))
    if not external:
        # iç diş: kök düzlüğü P/8 nominal çapta; profil delik boşluğuna taşar (temiz kesim), genişlik < P
        r_flat, w_flat = D / 2, P / 8
        r_open = r_flat - (0.95 * P - w_flat) / (2 * t30)
        minor = D - 1.0825 * P
        if bore is not None and bore / 2 <= r_open + 1e-3:
            raise ToolError(f"Delik çapı ({bore:g}) diş için çok küçük; en az ~{2 * r_open + 0.05:.2f} mm olmalı "
                            f"(kılavuz çapı ≈ {minor:.2f}).")
        if bore is not None and bore > D:
            raise ToolError(f"Delik çapı ({bore:g}) nominal diş çapından ({D:g}) büyük.")
    else:
        r_flat, w_flat = D / 2 - 0.6134 * P, P / 8
        r_open = r_flat + (0.95 * P - w_flat) / (2 * t30)
    w_open = w_flat + 2 * abs(r_flat - r_open) * t30
    hb = construction_set(s, op.doc, op.part)
    hsf = op.part.HybridShapeFactory
    c0 = [center[i] - axis_v[i] * 0.5 * P for i in range(3)]  # girişten yarım hatve önce başla (temiz çıkış)
    o = _add(s, op.doc, op.part, hb, hsf.AddNewPointCoord(*c0))
    line = _add(s, op.doc, op.part, hb, hsf.AddNewLinePtPt(
        s.ref(op.part, o), s.ref(op.part, _add(s, op.doc, op.part, hb, hsf.AddNewPointCoord(
            *[c0[i] + axis_v[i] * (L + P) for i in range(3)])))))
    rdir = _perp(axis_v)
    rc = (r_flat + r_open) / 2
    start = [c0[i] + rdir[i] * rc for i in range(3)]
    hx, sp = _helix(s, op.doc, op.part, hb, line, start, P, L + 0.5 * P, bool(left_handed))
    pl = _add(s, op.doc, op.part, hb, hsf.AddNewPlaneNormal(s.ref(op.part, hx), s.ref(op.part, sp)))
    op.part.InWorkObject = op.body
    sk = op.body.Sketches.Add(s.ref(op.part, pl))
    op.track(sk)
    ax = s.vb_array(sk, "GetAbsoluteAxisData", 9)

    def uv(r, a):
        q = [c0[i] + rdir[i] * r + axis_v[i] * a for i in range(3)]
        rel = [q[i] - ax[i] for i in range(3)]
        return [sum(rel[i] * ax[3 + i] for i in range(3)), sum(rel[i] * ax[6 + i] for i in range(3))]

    pts = [uv(r_open, -w_open / 2), uv(r_flat, -w_flat / 2), uv(r_flat, w_flat / 2), uv(r_open, w_open / 2)]
    meta = {"elements": {}, "dof": 0}
    b = _Builder(s, op.doc, op.part, sk, meta)
    try:
        b.add_element({"type": "profile", "id": "dis", "start": pts[0], "closed": True,
                       "segments": [{"line_to": q} for q in pts[1:]]}, 1, False)
    finally:
        b.close()
    s.sketch_meta[(op.doc.Name, sk.Name)] = meta
    op.part.InWorkObject = op.body
    slot = _rib(s, op, sk, hx, remove=True, pulling=line)
    if name:
        slot.Name = name
    _hide_set(op.doc, hb)
    # beklenen kaldırılan hacim (Pappus, malzeme sınırı: iç dişte delik çapı, dış dişte nominal çap); doğrulamak için
    r_lim = (bore / 2 if bore else r_open) if not external else D / 2
    a, b_ = sorted((r_flat, r_lim))
    n = 400
    per_turn = 2 * math.pi * sum((w_flat + 2 * abs(r - r_flat) * t30) * r * (b_ - a) / n
                                 for r in (a + (b_ - a) * (i + 0.5) / n for i in range(n)))
    out = op.finish(slot, "remove", {"thread": {"type": "external" if external else "internal", "diameter": D,
                                                "pitch": P, "length": r3(L), "turns": r3((L + 0.5 * P) / P),
                                                "left_handed": bool(left_handed), "axis": [r3(x) for x in axis_v]},
                                     "expected_volume_change_mm3": r3(-per_turn * L / P)})
    return out


# ======================================================================= malzeme
ALIASES = {"pirinç": "Brass", "pirinc": "Brass", "çelik": "Steel", "celik": "Steel", "alüminyum": "Aluminium",
           "aluminyum": "Aluminium", "aluminum": "Aluminium", "bakır": "Copper", "bakir": "Copper", "bronz": "Bronze",
           "titanyum": "Titanium", "demir": "Iron", "çinko": "Zinc", "nikel": "Nickel", "altın": "Gold", "gümüş": "Silver",
           "kurşun": "Lead"}


def _catalog_path(s: CatiaSession) -> Path:
    exe = Path(s.app.FullName)  # ...\win_b64\code\bin\CNEXT.exe
    p = exe.parents[2] / "startup" / "materials" / "Catalog.CATMaterial"
    if not p.exists():
        raise ToolError("CATIA malzeme kataloğu bulunamadı.", hint=str(p))
    return p


def _with_catalog(s: CatiaSession, fn):
    app = s.app
    path = _catalog_path(s)
    already = None
    for i in range(1, app.Documents.Count + 1):
        d = app.Documents.Item(i)
        try:
            if Path(d.FullName).resolve() == path.resolve():
                already = d
        except (pywintypes.com_error, OSError):
            pass
    doc = already or app.Documents.Open(str(path))
    try:
        return fn(doc)
    finally:
        if already is None:
            try:
                doc.Close()
            except pywintypes.com_error:
                pass


def _iter_materials(cat):
    for i in range(1, cat.Families.Count + 1):
        f = cat.Families.Item(i)
        for k in range(1, f.Materials.Count + 1):
            yield f.Name, f.Materials.Item(k)


@tool(
    "list_materials",
    "CATIA malzeme kataloğundaki malzemeleri listeler (family ile süzülebilir, ör. 'Metal').",
    {"properties": {"family": {"type": "string"}}},
    mutating=False,
)
def list_materials(s, family=None):
    def work(cat):
        out = {}
        for fam, m in _iter_materials(cat):
            if family and fam.lower() != family.lower():
                continue
            out.setdefault(fam, []).append(m.Name)
        return out
    return {"materials": _with_catalog(s, work)}


@tool(
    "apply_material",
    """CATIA kataloğundan malzeme atar (Brass/pirinç, Steel/çelik, Aluminium…); yoğunluk ve kütle döner.""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "material": {"type": "string"}}, "required": ["material"]},
)
def apply_material(s, material, document=None, body=None):
    doc, part = s.part_document(document)
    b = s.body(part, body)
    want = ALIASES.get(material.strip().lower(), material.strip())
    try:
        mgr = part.GetItem("CATMatManagerVBExt")
    except pywintypes.com_error as exc:
        raise ToolError(f"Malzeme yöneticisine erişilemedi: {com_error_text(exc)}") from None

    def work(cat):
        found = None
        for fam, m in _iter_materials(cat):
            if m.Name.lower() == want.lower():
                found = (fam, m)
                break
        if found is None:
            names = [m.Name for fam, m in _iter_materials(cat) if fam == "Metal"]
            raise ToolError(f"'{material}' katalogda yok.", hint=f"Metal ailesi: {names[:25]} (list_materials ile hepsi)")
        mgr.ApplyMaterialOnBody(b, found[1], 0)
        return found[0], found[1].Name

    fam, name = _with_catalog(s, work)

    def undo(m=mgr, body=b):
        try:
            m.ApplyMaterialOnBody(body, None, 0)
        except pywintypes.com_error:
            pass

    s.journal.record(f"{b.Name} malzemesini kaldır", undo)
    s.update(part)
    out = {"body": b.Name, "material": name, "family": fam}
    try:
        inertia = s.spa(doc).Inertias.Add(b)
        out["density_kg_m3"] = round(inertia.Density, 2)
        out["mass_g"] = round(inertia.Mass * 1000, 4)
    except pywintypes.com_error:
        pass
    return out
