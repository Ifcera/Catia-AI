"""Okuma / kontrol araçları: ağaç, ölçüm, topoloji, beklenti kontrolü, ekran görüntüsü, seçim."""
from __future__ import annotations

import math
import time
from pathlib import Path

import pywintypes

from ..catia.errors import ToolError, com_error_text
from ..catia.measure import TMP_SET, body_properties, body_volume_mm3, bounding_box, mass_properties, min_distance
from ..catia.session import CatiaSession, norm, r3, vec3
from ..catia.topology import Topology, brep_key, describe_face
from .registry import BODY_PROP, DOC_PROP, EDGE_SELECTOR, FACE_SELECTOR, VEC3, ToolResult, tool


# ============================================================ tasarım durumu
@tool(
    "get_design_state",
    """Belgenin ağacını okur: gövdeler ve feature'ları (tür, güncel mi), sketch'ler, geometrik setler, çalışılan gövde,
update hataları ve gövde hacimleri. Bir işe başlamadan ve hata ayıklarken kullanın.""",
    {"properties": {"document": DOC_PROP, "include_volumes": {"type": "boolean", "default": True}}},
    mutating=False,
    category="inspect",
)
def get_design_state(s: CatiaSession, document=None, include_volumes=True):
    doc = s.document(document)
    try:
        part = doc.Part
    except (pywintypes.com_error, AttributeError):
        return {"document": doc.Name, "type": "product", "note": "Montaj belgesi; parça ağacı için bir CATPart verin."}
    try:
        in_work = part.InWorkObject.Name
    except pywintypes.com_error:
        in_work = None
    bodies = []
    errors = []
    for i in range(1, part.Bodies.Count + 1):
        b = part.Bodies.Item(i)
        feats = []
        for j in range(1, b.Shapes.Count + 1):
            sh = b.Shapes.Item(j)
            ok = bool(part.IsUpToDate(sh))
            feats.append({"name": sh.Name, "type": s.typename(sh), "ok": ok})
            if not ok:
                errors.append(sh.Name)
        sks = [b.Sketches.Item(j).Name for j in range(1, b.Sketches.Count + 1)]
        entry = {"name": b.Name, "features": feats, "sketches": sks}
        if include_volumes:
            entry["volume_mm3"] = r3(body_volume_mm3(s, doc, part, b))
        bodies.append(entry)
    sets = []
    for i in range(1, part.HybridBodies.Count + 1):
        hb = part.HybridBodies.Item(i)
        if hb.Name == TMP_SET:
            continue
        sets.append({"name": hb.Name, "items": [hb.HybridShapes.Item(j).Name for j in range(1, hb.HybridShapes.Count + 1)]})
    try:
        pn = doc.Product.PartNumber
    except pywintypes.com_error:
        pn = None
    return {
        "document": doc.Name, "type": "part", "part_number": pn, "in_work_object": in_work,
        "main_body": part.MainBody.Name, "bodies": bodies, "geometrical_sets": sets,
        "update_errors": errors, "up_to_date": not errors,
    }


@tool(
    "update_part",
    "Parçayı günceller ve güncellenemeyen feature'ları döndürür.",
    {"properties": {"document": DOC_PROP}},
    mutating=False,
    category="inspect",
)
def update_part(s: CatiaSession, document=None):
    doc, part = s.part_document(document)
    failing = s.update(part)
    return {"up_to_date": not failing, "update_errors": failing}


# ================================================================== ölçüm
@tool(
    "measure",
    """Bir gövdeyi ölçer: hacim (mm³), yüzey alanı (mm²), ağırlık merkezi (mm), sınır kutusu (min/max/size, mm) ve isteğe bağlı
kütle (kg, CATIA'daki malzeme yoğunluğuyla; malzeme yoksa 1000 kg/m³).""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP,
                    "bbox": {"type": "boolean", "default": True}, "mass": {"type": "boolean", "default": False}}},
    mutating=False,
    category="inspect",
)
def measure(s: CatiaSession, document=None, body=None, bbox=True, mass=False):
    doc, part = s.part_document(document)
    b = s.body(part, body)
    out = body_properties(s, doc, part, b)
    if bbox and out.get("volume_mm3", 0) > 0:
        out["bounding_box"] = bounding_box(s, doc, part, b)
    if mass:
        out.update(mass_properties(s, doc, b))
    return out


def _target_ref(s: CatiaSession, doc, part, spec: dict):
    b = s.body(part, spec.get("body"))
    if "face" in spec:
        it = Topology(s, doc, part, b).resolve_faces(spec["face"], many=False)[0]
        return it.ref, it.id
    if "edge" in spec:
        it = Topology(s, doc, part, b).resolve_edges(spec["edge"], many=False)[0]
        return it.ref, it.id
    if "object" in spec:
        o = s.find_object(part, spec["object"])
        return s.ref(part, o), spec["object"]
    return s.ref(part, b), b.Name


@tool(
    "measure_distance",
    "İki öğe arasındaki en kısa mesafe (mm). a ve b: {face: seçici} | {edge: seçici} | {object: 'Pad.1'/'Plane.1'} | {body: 'PartBody'}.",
    {"properties": {"document": DOC_PROP, "a": {"type": "object"}, "b": {"type": "object"}}, "required": ["a", "b"]},
    mutating=False,
    category="inspect",
)
def measure_distance(s: CatiaSession, a, b, document=None):
    doc, part = s.part_document(document)
    ra, na = _target_ref(s, doc, part, a)
    rb, nb = _target_ref(s, doc, part, b)
    return {"a": na, "b": nb, "distance_mm": min_distance(s, doc, ra, rb)}


# ================================================================ topoloji
@tool(
    "list_topology",
    """Bir gövdenin yüzeylerini ve/veya kenarlarını kalıcı kimliklerle (F1.., E1..) listeler. Yüzey: tür, alan, merkez, dış normal
(normal_confidence: high | estimated), yarıçap, üreten feature'lar. Kenar: tür (line/circle), uzunluk, başlangıç/orta/bitiş, yön,
yarıçap/merkez, komşu yüzey kimlikleri. Kimlikler oturum boyunca aynı yüzeye bağlı kalır. Filtrelemek için face_filter/edge_filter
seçici nesnesi verin (ör. {axis:'+Z'} ya da {on_face:'F1'}).""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP, "kind": {"enum": ["faces", "edges", "both"], "default": "faces"},
                    "face_filter": {"type": "object"}, "edge_filter": {"type": "object"}, "limit": {"type": "integer", "default": 80}}},
    mutating=False,
    category="inspect",
)
def list_topology(s: CatiaSession, document=None, body=None, kind="faces", face_filter=None, edge_filter=None, limit=80):
    doc, part = s.part_document(document)
    b = s.body(part, body)
    topo = Topology(s, doc, part, b)
    out: dict = {"body": b.Name}
    if kind in ("faces", "both"):
        fs = topo.resolve_faces(face_filter, many=True) if face_filter else topo.faces()
        out["faces"] = [dict(Topology.public(f), summary=describe_face(f.info)) for f in fs[:limit]]
        out["face_count"] = len(fs)
    if kind in ("edges", "both"):
        es = topo.resolve_edges(edge_filter, many=True) if edge_filter else topo.edges()
        out["edges"] = [Topology.public(e) for e in es[:limit]]
        out["edge_count"] = len(es)
    return out


# ======================================================= beklenti kontrolü
@tool(
    "check_expectation",
    """Ölçülen değerleri beklenenle karşılaştırır: volume_mm3 (± volume_tolerance_pct, varsayılan %1), bbox_size [x,y,z] ve/veya
bbox_min/bbox_max (± bbox_tolerance_mm, varsayılan 0.05), update hatası olmaması. Her aşamadan sonra çağırın.""",
    {"properties": {"document": DOC_PROP, "body": BODY_PROP,
                    "volume_mm3": {"type": "number"}, "volume_tolerance_pct": {"type": "number"},
                    "bbox_size": VEC3, "bbox_min": VEC3, "bbox_max": VEC3, "bbox_tolerance_mm": {"type": "number"}}},
    mutating=False,
    category="inspect",
)
def check_expectation(s: CatiaSession, document=None, body=None, volume_mm3=None, volume_tolerance_pct=1.0,
                      bbox_size=None, bbox_min=None, bbox_max=None, bbox_tolerance_mm=0.05):
    doc, part = s.part_document(document)
    b = s.body(part, body)
    checks = []
    failing = s.update(part)
    checks.append({"check": "update", "pass": not failing, "detail": failing or "hatasız"})
    props = body_properties(s, doc, part, b)
    v = props.get("volume_mm3", 0.0)
    if volume_mm3 is not None:
        dev = (v - volume_mm3) / volume_mm3 * 100 if volume_mm3 else (0.0 if v == 0 else math.inf)
        checks.append({"check": "volume_mm3", "expected": volume_mm3, "measured": v, "deviation_pct": r3(dev),
                       "pass": abs(dev) <= volume_tolerance_pct})
    bb = None
    if bbox_size or bbox_min or bbox_max:
        bb = bounding_box(s, doc, part, b) if v > 0 else None
        for label, exp in (("bbox_size", bbox_size), ("bbox_min", bbox_min), ("bbox_max", bbox_max)):
            if exp is None:
                continue
            meas = bb[label.split("_")[1]] if bb else None
            ok = meas is not None and all(abs(meas[i] - exp[i]) <= bbox_tolerance_mm for i in range(3))
            checks.append({"check": label, "expected": exp, "measured": meas, "pass": ok})
    passed = all(c["pass"] for c in checks)
    return {"body": b.Name, "passed": passed, "checks": checks, **({"bounding_box": bb} if bb else {})}


# ============================================================ ekran görüntüsü
VIEWS = {
    #        bakış yönü (kameradan sahneye)   yukarı
    "iso": ((-1.0, -1.0, -1.0), (0.0, 0.0, 1.0)),
    "iso_back": ((1.0, 1.0, -1.0), (0.0, 0.0, 1.0)),
    "front": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "right": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, 1.0), (0.0, -1.0, 0.0)),
}


def _ortho_up(sight, up):
    sn = norm(sight)
    d = sum(a * b for a, b in zip(up, sn))
    return norm([up[i] - d * sn[i] for i in range(3)])


def _set_layout(win, value: int, tries: int = 4) -> bool:
    # R20: Layout ataması ara sıra RPC_E_SERVERFAULT (0x80010105) veriyor; kısa beklemeyle tekrar dene.
    for i in range(tries):
        try:
            win.Layout = value
            return True
        except pywintypes.com_error:
            time.sleep(0.25 * (i + 1))
    return False


def capture(s: CatiaSession, views: list[str], document=None, max_size=800, restore_view=True, tag="view") -> list[Path]:
    if document:
        s.document(document).Activate()
    win = s.app.ActiveWindow
    viewer = win.ActiveViewer
    vp = viewer.Viewpoint3D
    saved = None
    if restore_view:
        try:
            saved = (s.vb_array(vp, "GetOrigin", 3), s.vb_array(vp, "GetSightDirection", 3),
                     s.vb_array(vp, "GetUpDirection", 3), vp.FocusDistance, vp.FieldOfView)
        except (ToolError, pywintypes.com_error):
            saved = None
    try:
        old_layout = win.Layout
    except pywintypes.com_error:
        old_layout = None
    out_dir = s.resolve_path("captures/x.jpg").parent
    paths = []
    try:
        if old_layout is not None and not _set_layout(win, 1):  # yalnız geometri (ağaç görüntüyü kapatmasın)
            old_layout = None
        for v in views:
            if v != "current":
                if v not in VIEWS:
                    raise ToolError(f"Bilinmeyen görünüm '{v}'.", hint=f"{list(VIEWS) + ['current']}")
                sight, up = VIEWS[v]
                vp.PutSightDirection(tuple(norm(sight)))
                vp.PutUpDirection(tuple(_ortho_up(sight, up)))
                viewer.Reframe()
            viewer.Update()
            raw = out_dir / f"_raw_{int(time.time() * 1000)}.jpg"
            viewer.CaptureToFile(5, str(raw))
            final = out_dir / f"{tag}_{v}_{time.strftime('%H%M%S')}_{len(paths)}.jpg"
            _shrink(raw, final, max_size)
            paths.append(final)
    finally:
        if old_layout is not None:
            _set_layout(win, old_layout)
        if saved:
            try:
                vp.PutOrigin(tuple(saved[0]))
                vp.PutSightDirection(tuple(saved[1]))
                vp.PutUpDirection(tuple(saved[2]))
                vp.FocusDistance = saved[3]
                vp.FieldOfView = saved[4]
                viewer.Update()
            except pywintypes.com_error:
                pass
    return paths


def _shrink(src: Path, dst: Path, max_size: int):
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((max_size, max_size))
        im.save(dst, "JPEG", quality=82)
    try:
        src.unlink()
    except OSError:
        pass


@tool(
    "screenshot",
    """CATIA görüntüsünü alır. views: ['iso','front','back','left','right','top','bottom','iso_back','current'] (birden çok verilebilir;
her biri ayrı görüntü). Görünüm değiştirilip sığdırılır, sonra kullanıcının görünümü geri yüklenir. Görüntüler sohbette gösterilir
ve size iletilir; gereksiz görüntü almayın (her biri ücretlidir).""",
    {"properties": {"document": DOC_PROP, "views": {"type": "array", "items": {"type": "string"}, "default": ["iso"]},
                    "max_size": {"type": "integer", "default": 800}}},
    mutating=False,
    category="inspect",
)
def screenshot(s: CatiaSession, document=None, views=None, max_size=800):
    views = views or ["iso"]
    if len(views) > 4:
        raise ToolError("Tek çağrıda en fazla 4 görünüm.")
    max_size = max(200, min(int(max_size), 1280))
    paths = capture(s, views, document, max_size)
    from PIL import Image

    sizes = []
    for p in paths:
        with Image.open(p) as im:
            sizes.append(im.size)
    return ToolResult({"images": [{"view": v, "path": str(p), "size": sz} for v, p, sz in zip(views, paths, sizes)],
                       "note": "Görüntü boyutu CATIA görüntü alanıyla sınırlıdır; daha büyük görüntü için CATIA penceresini büyütün."},
                      images=paths)


# ================================================================== seçim
@tool(
    "get_selection",
    "Kullanıcının CATIA'da seçtiği öğeleri döndürür (ad, tür; yüzey/kenar ise topoloji kimliği).",
    {"properties": {"document": DOC_PROP}},
    mutating=False,
    category="inspect",
)
def get_selection(s: CatiaSession, document=None):
    doc = s.document(document)
    sel = doc.Selection
    items = []
    for i in range(1, sel.Count + 1):
        it = sel.Item(i)
        entry = {"type": it.Type}
        try:
            entry["name"] = it.Value.Name
        except pywintypes.com_error:
            pass
        try:
            key = brep_key(it.Reference.DisplayName)
            ids = s.topo_ids.get(s.doc_key(doc), {})
            if key in ids:
                entry["topology_id"] = ids[key]
            elif key.startswith(("RSur:", "REdge:")):
                entry["brep"] = key[:160]
        except pywintypes.com_error:
            pass
        items.append(entry)
    return {"document": doc.Name, "count": len(items), "items": items}
