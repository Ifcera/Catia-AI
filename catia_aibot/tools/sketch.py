"""Sketch araçları.

Tasarım: asistan bir sketch'i TEK çağrıda (create_sketch) tüm öğeleriyle kurar.
auto_constrain=True iken her öğe, normal CATIA kısıtlarıyla tam kısıtlanır:
  - dikdörtgen: 2 yatay + 2 dikey, genişlik/yükseklik uzunlukları,
    konum: merkez eksen üzerindeyse simetri, değilse köşe noktasının eksenlere mesafesi
  - daire: yarıçap + merkezin eksenlere mesafesi (0 ise eksen üzerinde kısıtı)
  - profil (çoklu doğru/yay): her köşenin eksenlere mesafesi + her yayın yarıçapı
  - doğru/eksen/nokta: uç noktaların eksenlere mesafesi
  - yay: merkez + başlangıç noktası konumu + bitiş noktasının bir koordinatı
Böylece kullanıcı her ölçüyü CATIA'da çift tıklayıp değiştirebilir.

Koordinatlar sketch'in yerel (H, V) eksenlerindedir, mm. Sonuçta her sketch
için yerel eksenlerin global karşılığı döner (ör. ZX düzleminde H = +Z, V = +X).
"""
from __future__ import annotations

import math

import pywintypes

from ..catia.errors import ToolError, com_error_text
from ..catia.session import CatiaSession, vec3
from ..catia.topology import Topology, describe_face
from .registry import BODY_PROP, DOC_PROP, FACE_SELECTOR, VEC2, tool

CST = {
    "fix": 0, "distance": 1, "on": 2, "coincident": 2, "concentric": 3, "tangent": 4, "length": 5,
    "angle": 6, "parallel": 8, "horizontal": 10, "perpendicular": 11, "vertical": 13, "radius": 14,
    "symmetric": 15, "midpoint": 16,
}
MONO = {"fix", "length", "radius", "horizontal", "vertical"}
TRI = {"symmetric"}
STATUS = {0: "OK", 1: "sağlanamadı", 2: "yanlış yön/taraf", 3: "yanlış değer", 4: "uygun olmayan geometri", 5: "kopuk"}
DOF_REMOVED = {"distance": 1, "on": 1, "coincident": 2, "concentric": 2, "tangent": 1, "length": 1, "angle": 1,
               "parallel": 1, "horizontal": 1, "perpendicular": 1, "vertical": 1, "radius": 1, "symmetric": 2,
               "midpoint": 2, "fix": 2}
EPS = 1e-9


class _Builder:
    """Bir sketch düzenleme oturumu (OpenEdition ... CloseEdition)."""

    def __init__(self, s: CatiaSession, doc, part, sketch, meta: dict):
        self.s, self.doc, self.part, self.sk = s, doc, part, sketch
        self.meta = meta  # {"elements": {id: {alt: catia_name}}, "dof": int}
        self.f = sketch.OpenEdition()
        self.cst = sketch.Constraints
        self.ax = sketch.AbsoluteAxis
        self.H = self.ax.HorizontalReference
        self.V = self.ax.VerticalReference
        self.objects: dict[str, object] = {}  # "id.alt" -> COM nesnesi
        self.created: list = []
        self.auto_csts = 0
        self.dof = 0

    def R(self, o):
        return self.part.CreateReferenceFromObject(o)

    # ------------------------------------------------------------- kısıt yardımcıları
    def add_cst(self, kind: str, objs: list, value: float | None = None):
        code = CST[kind]
        refs = [self.R(o) for o in objs]
        if len(refs) == 1:
            c = self.cst.AddMonoEltCst(code, refs[0])
        elif len(refs) == 2:
            c = self.cst.AddBiEltCst(code, refs[0], refs[1])
        else:
            c = self.cst.AddTriEltCst(code, refs[0], refs[1], refs[2])
        if value is not None:
            c.Dimension.Value = float(value)
        return c

    def dim_point(self, pt, u: float, v: float, which: str = "uv"):
        if "u" in which:
            if abs(u) < EPS:
                self.add_cst("on", [pt, self.V])
            else:
                self.add_cst("distance", [pt, self.V], abs(u))
            self.auto_csts += 1
        if "v" in which:
            if abs(v) < EPS:
                self.add_cst("on", [pt, self.H])
            else:
                self.add_cst("distance", [pt, self.H], abs(v))
            self.auto_csts += 1

    def point(self, u, v):
        p = self.f.CreatePoint(float(u), float(v))
        self.created.append(p)
        return p

    def line(self, a, b, pa, pb):
        ln = self.f.CreateLine(float(a[0]), float(a[1]), float(b[0]), float(b[1]))
        ln.StartPoint = pa
        ln.EndPoint = pb
        self.created.append(ln)
        return ln

    def arc(self, c, r, a0, a1, pc, ps, pe):
        arc = self.f.CreateCircle(float(c[0]), float(c[1]), float(r), float(a0), float(a1))
        arc.CenterPoint = pc
        arc.StartPoint = ps
        arc.EndPoint = pe
        self.created.append(arc)
        return arc

    def register(self, eid: str, alt: str, obj):
        self.objects[f"{eid}.{alt}" if alt else eid] = obj
        names = self.meta["elements"].setdefault(eid, {})
        names[alt or "_"] = obj.Name

    # ------------------------------------------------------------- öğeler
    def add_element(self, el: dict, idx: int, auto: bool) -> str:
        typ = el.get("type")
        eid = el.get("id") or f"{typ}{idx}"
        if eid in self.meta["elements"]:
            raise ToolError(f"Öğe kimliği '{eid}' bu sketch'te zaten var.")
        fn = getattr(self, f"_el_{typ}", None)
        if fn is None:
            raise ToolError(f"Bilinmeyen sketch öğesi '{typ}'.",
                            hint="rectangle, circle, profile, line, arc, point, axis kullanın.")
        fn(eid, el, auto)
        if el.get("construction"):
            for k, o in self.objects.items():
                if k == eid or k.startswith(eid + "."):
                    try:
                        o.Construction = True
                    except pywintypes.com_error:
                        pass
        return eid

    def _el_rectangle(self, eid, el, auto):
        w, h = float(el["width"]), float(el["height"])
        if w <= 0 or h <= 0:
            raise ToolError("Dikdörtgen genişlik/yüksekliği pozitif olmalı.")
        if "center" in el:
            cu, cv = el["center"]
        elif "corner" in el:
            cu, cv = el["corner"][0] + w / 2, el["corner"][1] + h / 2
        else:
            cu, cv = 0.0, 0.0
        xs = [(cu - w / 2, cv - h / 2), (cu + w / 2, cv - h / 2), (cu + w / 2, cv + h / 2), (cu - w / 2, cv + h / 2)]
        P = [self.point(*p) for p in xs]
        names = ["bottom", "right", "top", "left"]
        L = [self.line(xs[i], xs[(i + 1) % 4], P[i], P[(i + 1) % 4]) for i in range(4)]
        for i, p in enumerate(P):
            self.register(eid, f"p{i}", p)
        for n, ln in zip(names, L):
            self.register(eid, n, ln)
        self.dof += 8
        if not auto:
            return
        self.add_cst("horizontal", [L[0]]); self.add_cst("horizontal", [L[2]])
        self.add_cst("vertical", [L[1]]); self.add_cst("vertical", [L[3]])
        self.add_cst("length", [L[0]], w); self.add_cst("length", [L[3]], h)
        self.auto_csts += 6
        if abs(cu) < EPS:
            self.add_cst("symmetric", [P[0], P[1], self.V]); self.auto_csts += 1
        else:
            self.dim_point(P[0], xs[0][0], xs[0][1], "u")
        if abs(cv) < EPS:
            self.add_cst("symmetric", [P[0], P[3], self.H]); self.auto_csts += 1
        else:
            self.dim_point(P[0], xs[0][0], xs[0][1], "v")
        self.dof -= 8

    def _el_circle(self, eid, el, auto):
        cu, cv = el.get("center", [0.0, 0.0])
        r = float(el["radius"]) if "radius" in el else float(el["diameter"]) / 2
        if r <= 0:
            raise ToolError("Daire yarıçapı pozitif olmalı.")
        c = self.f.CreateClosedCircle(float(cu), float(cv), r)
        pc = self.point(cu, cv)
        c.CenterPoint = pc
        self.created.append(c)
        self.register(eid, "", c)
        self.register(eid, "center", pc)
        self.dof += 3
        if auto:
            self.add_cst("radius", [c], r); self.auto_csts += 1
            self.dim_point(pc, cu, cv)
            self.dof -= 3

    def _el_point(self, eid, el, auto):
        u, v = el["at"]
        p = self.point(u, v)
        self.register(eid, "", p)
        self.dof += 2
        if auto:
            self.dim_point(p, u, v)
            self.dof -= 2

    def _two_point_line(self, eid, el, auto):
        a, b = el["start"], el["end"]
        if math.dist(a, b) < 1e-6:
            raise ToolError("Doğrunun başlangıç ve bitişi aynı nokta.")
        pa, pb = self.point(*a), self.point(*b)
        ln = self.line(a, b, pa, pb)
        self.register(eid, "", ln)
        self.register(eid, "start", pa)
        self.register(eid, "end", pb)
        self.dof += 4
        if auto:
            self.dim_point(pa, *a)
            self.dim_point(pb, *b)
            self.dof -= 4
        return ln

    def _el_line(self, eid, el, auto):
        self._two_point_line(eid, el, auto)

    def _el_axis(self, eid, el, auto):
        ln = self._two_point_line(eid, el, auto)
        self.sk.CenterLine = ln

    def _el_arc(self, eid, el, auto):
        cu, cv = el["center"]
        r = float(el["radius"])
        a0, a1 = math.radians(el["start_angle"]), math.radians(el["end_angle"])
        s_ = (cu + r * math.cos(a0), cv + r * math.sin(a0))
        e_ = (cu + r * math.cos(a1), cv + r * math.sin(a1))
        pc, ps, pe = self.point(cu, cv), self.point(*s_), self.point(*e_)
        arc = self.arc((cu, cv), r, a0, a1, pc, ps, pe)
        self.register(eid, "", arc)
        for alt, p in (("center", pc), ("start", ps), ("end", pe)):
            self.register(eid, alt, p)
        self.dof += 5
        if auto:
            self.dim_point(pc, cu, cv)
            self.dim_point(ps, *s_)
            self.dim_point(pe, *e_, "u" if abs(math.sin(a1)) > abs(math.cos(a1)) else "v")
            self.dof -= 5

    def _el_profile(self, eid, el, auto):
        start = el["start"]
        segs = el["segments"]
        closed = el.get("closed", True)
        if not segs:
            raise ToolError("Profilde en az bir segment olmalı.")
        verts = [tuple(start)]
        for sg in segs:
            tgt = sg.get("line_to") or sg.get("arc_to")
            if tgt is None:
                raise ToolError("Profil segmenti 'line_to' ya da 'arc_to' içermeli.")
            verts.append(tuple(tgt))
        if closed:
            if math.dist(verts[0], verts[-1]) > 1e-6:
                # Kapatmak için son noktadan başa düz bir segment eklenir.
                segs = list(segs) + [{"line_to": list(verts[0])}]
                verts.append(verts[0])
            verts = verts[:-1]
        P = [self.point(*v) for v in verts]
        n = len(verts)
        for i, p in enumerate(P):
            self.register(eid, f"v{i}", p)
        self.dof += 2 * n
        arcs = []
        for i, sg in enumerate(segs):
            ia, ib = i, (i + 1) % n if closed else i + 1
            a, b = verts[ia], verts[ib]
            if math.dist(a, b) < 1e-6:
                raise ToolError(f"Profil segmenti {i + 1} sıfır uzunlukta.")
            if "line_to" in sg:
                obj = self.line(a, b, P[ia], P[ib])
            else:
                c = sg.get("center")
                if c is None:
                    raise ToolError(f"Yay segmenti {i + 1} için 'center' gerekli.")
                ra, rb = math.dist(a, c), math.dist(b, c)
                if abs(ra - rb) > 1e-4 * max(ra, 1):
                    raise ToolError(f"Yay segmenti {i + 1}: merkez iki uca eşit uzaklıkta değil ({ra:.4f} ≠ {rb:.4f}).")
                ccw = sg.get("ccw", True)
                ang_a = math.atan2(a[1] - c[1], a[0] - c[0])
                ang_b = math.atan2(b[1] - c[1], b[0] - c[0])
                pc = self.point(*c)
                if ccw:
                    a1 = ang_b if ang_b > ang_a else ang_b + 2 * math.pi
                    obj = self.arc(c, ra, ang_a, a1, pc, P[ia], P[ib])
                else:
                    a1 = ang_a if ang_a > ang_b else ang_a + 2 * math.pi
                    obj = self.arc(c, ra, ang_b, a1, pc, P[ib], P[ia])
                self.register(eid, f"c{i + 1}", pc)
                arcs.append((obj, ra))
                self.dof += 3  # merkez (2) + 1 serbestlik; uçlar tutarlılık ile bağlı
                self.dof -= 2
            self.register(eid, f"seg{i + 1}", obj)
        if auto:
            for p, v in zip(P, verts):
                self.dim_point(p, *v)
            for obj, ra in arcs:
                self.add_cst("radius", [obj], ra); self.auto_csts += 1
            self.dof -= 2 * n + len(arcs)

    # ------------------------------------------------------------- elle kısıt
    def lookup(self, name: str):
        if name in ("H", "H_AXIS"):
            return self.H
        if name in ("V", "V_AXIS"):
            return self.V
        if name in ("O", "ORIGIN"):
            return self.ax.Origin
        if name in self.objects:
            return self.objects[name]
        eid, _, alt = name.partition(".")
        if eid in self.meta["elements"]:
            cat = self.meta["elements"][eid].get(alt or "_")
            if cat:
                name = cat
        try:
            return self.sk.GeometricElements.Item(name)
        except pywintypes.com_error:
            known = sorted(set(self.objects) | {f"{e}.{a}" if a != "_" else e for e, d in self.meta["elements"].items() for a in d})
            raise ToolError(f"Sketch öğesi '{name}' bulunamadı.", hint=f"Bilinen öğeler: {known[:40]}") from None

    def add_manual(self, c: dict):
        kind = c.get("type")
        if kind not in CST:
            raise ToolError(f"Bilinmeyen kısıt türü '{kind}'.", hint=f"Geçerli: {sorted(CST)}")
        objs = [self.lookup(n) for n in c.get("elements", [])]
        need = 1 if kind in MONO else 3 if kind in TRI else 2
        if kind == "fix" and len(objs) == 1:
            need = 1
        if len(objs) != need:
            raise ToolError(f"'{kind}' kısıtı {need} öğe ister, {len(objs)} verildi.")
        val = c.get("value")
        cst = self.add_cst(kind, objs, val)
        self.dof -= DOF_REMOVED.get(kind, 1)
        return cst.Name

    def close(self):
        self.sk.CloseEdition()


def _sketch_report(s: CatiaSession, part, sk, meta: dict) -> dict:
    axis = s.vb_array(sk, "GetAbsoluteAxisData", 9)
    bad = []
    count = sk.Constraints.Count
    for i in range(1, count + 1):
        c = sk.Constraints.Item(i)
        try:
            st = int(c.Status)
        except pywintypes.com_error:
            continue
        if st != 0:
            bad.append({"constraint": c.Name, "status": STATUS.get(st, st)})
    dirs = {}
    for label, vec in (("H", axis[3:6]), ("V", axis[6:9])):
        k = max(range(3), key=lambda i: abs(vec[i]))
        dirs[label] = f"{'+' if vec[k] > 0 else '-'}{'XYZ'[k]}" if abs(vec[k]) > 0.999 else vec3(vec)
    dof = meta.get("dof", 0)
    rep = {
        "sketch": sk.Name,
        "local_axes": {"origin": vec3(axis[0:3]), "H(u)": dirs["H"], "V(v)": dirs["V"]},
        "elements": meta["elements"],
        "constraint_count": count,
        "dof_estimate": dof,
        "fully_constrained": dof == 0 and not bad,
    }
    if bad:
        rep["constraint_problems"] = bad
    return rep


def _support(s: CatiaSession, doc, part, body, support):
    """Dönüş: (destek, açıklama, eğri başlangıç noktası xyz ya da None)."""
    if isinstance(support, str) and support.upper() in ("XY", "YZ", "ZX", "XZ"):
        return s.origin_plane(part, support), f"{support.upper()} ana düzlemi", None
    if isinstance(support, dict) and "plane" in support:
        obj = s.find_object(part, support["plane"])
        return s.ref(part, obj), f"düzlem {support['plane']}", None
    if isinstance(support, dict) and "normal_to" in support:
        from .advanced import _add, construction_set  # döngüsel içe aktarmayı önlemek için burada

        curve = s.find_object(part, support["normal_to"])
        hb = construction_set(s, doc, part)
        hsf = part.HybridShapeFactory
        at_end = support.get("at") == "end"
        pt = _add(s, doc, part, hb, hsf.AddNewPointOnCurveFromPercent(s.ref(part, curve), 1.0 if at_end else 0.0, False))
        pl = _add(s, doc, part, hb, hsf.AddNewPlaneNormal(s.ref(part, curve), s.ref(part, pt)))
        xyz = list(s.vb_array(s.spa(doc).GetMeasurable(s.ref(part, pt)), "GetPoint", 3))
        return s.ref(part, pl), f"{support['normal_to']} eğrisine dik düzlem ({'son' if at_end else 'başlangıç'} noktası)", xyz
    topo = Topology(s, doc, part, body)
    face = topo.resolve_faces(support, many=False)[0]
    if face.info.get("type") != "planar":
        raise ToolError(f"{face.id} düzlemsel değil ({face.info.get('type')}); sketch düzlemsel yüzeye açılır.")
    return face.ref, f"yüzey {face.id} ({describe_face(face.info)})", None


_POINT_KEYS = ("center", "corner", "start", "end", "at")


def _shift_elements(elements: list, du: float, dv: float) -> list:
    """normal_to desteğinde öğe koordinatları eğri başlangıcına göredir; sketch'in yerel koordinatına kaydır."""
    def sh(p):
        return [p[0] + du, p[1] + dv]
    out = []
    for el in elements or []:
        e = dict(el)
        for k in _POINT_KEYS:
            if k in e and isinstance(e[k], list):
                e[k] = sh(e[k])
        if "segments" in e:
            segs = []
            for sg in e["segments"]:
                g = dict(sg)
                for k in ("line_to", "arc_to", "center"):
                    if k in g:
                        g[k] = sh(g[k])
                segs.append(g)
            e["segments"] = segs
        out.append(e)
    return out


def _run_builder(s, doc, part, sk, meta, elements, constraints, auto):
    b = _Builder(s, doc, part, sk, meta)
    try:
        for i, el in enumerate(elements or [], start=1):
            b.add_element(el, i + len(meta["elements"]), auto)
        names = [b.add_manual(c) for c in (constraints or [])]
        meta["dof"] = meta.get("dof", 0) + b.dof
    finally:
        try:
            b.close()
        except pywintypes.com_error:
            pass
    return b, names


# Kısa tutulur: her model isteğinde gönderilir. Alan tipleri açıklamada; öğe hataları _Builder'da anlaşılır biçimde döner.
ELEMENT_SCHEMA = {
    "type": "object",
    "description": (
        "Koordinatlar yerel [u,v] mm. rectangle{center|corner(sol-alt),width,height} circle{center,radius|diameter} "
        "profile{start,segments:[{line_to}|{arc_to,center,ccw}],closed=true} line{start,end} axis{start,end}(shaft ekseni) "
        "arc{center,radius,start_angle,end_angle(°)} point{at}; ortak: id, construction. Alt adlar: rectangle "
        "bottom/right/top/left/p0..p3, circle center, profile seg<i>/v<i>/c<i>."
    ),
    "properties": {"type": {"enum": ["rectangle", "circle", "profile", "line", "axis", "arc", "point"]}},
    "required": ["type"],
}
CONSTRAINT_SCHEMA = {
    "type": "object",
    "description": (
        "{type, elements, value(mm|°)}. elements: 'r1.top', 'c1.center', 'Line.3', eksen 'H'/'V', orijin 'O'. 1 öğe: length "
        "radius horizontal vertical fix; 2: distance on concentric tangent angle parallel perpendicular midpoint; 3: symmetric."
    ),
    "properties": {"type": {"enum": sorted(CST)}, "elements": {"type": "array", "items": {"type": "string"}}},
    "required": ["type", "elements"],
}


@tool(
    "create_sketch",
    """Sketch'i tüm öğeleriyle tek çağrıda kurar; auto_constrain ile tam kısıtlar. Yerel eksenler sonuçta local_axes
(XY: u=+X,v=+Y; YZ: u=+Y,v=+Z; ZX: u=+Z,v=+X). constraint_problems dönerse geri alınmıştır. Sketch'i yalnız sweep ya da
elle kısıt için ayrı kurun; pad/pocket/shaft/groove 'profile' ile sketch'i kendisi kurar.""",
    {
        "properties": {
            "document": DOC_PROP, "body": BODY_PROP,
            "support": {"description": "'XY'|'YZ'|'ZX' | {plane: ad} | {normal_to: eğri, at: start|end} ([0,0]=eğri üstü) | yüzey seçici",
                        "anyOf": [{"type": "string"}, {"type": "object"}]},
            "elements": {"type": "array", "items": ELEMENT_SCHEMA},
            "constraints": {"type": "array", "items": CONSTRAINT_SCHEMA},
            "auto_constrain": {"type": "boolean"},
            "name": {"type": "string"},
        },
        "required": ["support", "elements"],
    },
    category="sketch",
)
def create_sketch(s: CatiaSession, support, elements: list, document: str | None = None, body: str | None = None,
                  constraints: list | None = None, auto_constrain: bool = True, name: str | None = None):
    doc, part = s.part_document(document)
    b = s.body(part, body)
    sup, sup_desc, path_xyz = _support(s, doc, part, b, support)
    part.InWorkObject = b
    sk = b.Sketches.Add(sup)
    s.journal.record(f"{sk.Name} sil", lambda d=doc, o=sk: s.delete_objects(d, [o]))
    if name:
        sk.Name = name
    path_uv = None
    if path_xyz is not None:
        ax = s.vb_array(sk, "GetAbsoluteAxisData", 9)
        rel = [path_xyz[i] - ax[i] for i in range(3)]
        path_uv = [sum(rel[i] * ax[3 + i] for i in range(3)), sum(rel[i] * ax[6 + i] for i in range(3))]
        elements = _shift_elements(elements, *path_uv)
    meta = {"elements": {}, "dof": 0}
    _run_builder(s, doc, part, sk, meta, elements, constraints, auto_constrain)
    s.sketch_meta[(doc.Name, sk.Name)] = meta
    s.created.add((doc.Name, sk.Name))
    failing = s.update(part)
    rep = _sketch_report(s, part, sk, meta)
    rep["support"] = sup_desc
    if path_uv is not None:
        rep["path_start_local"] = vec3(path_uv + [0])[:2]
        rep["path_point"] = vec3(path_xyz)
    if rep.get("constraint_problems") or sk.Name in failing:
        raise ToolError("Sketch kısıtları çözülemedi; sketch geri alındı.", hint="Ölçüleri ve konumları kontrol edin.",
                        constraint_problems=rep.get("constraint_problems"), failing=failing)
    return rep


@tool(
    "add_to_sketch",
    "Var olan bir sketch'e öğe ve/veya kısıt ekler (create_sketch ile aynı biçim).",
    {
        "properties": {
            "document": DOC_PROP, "sketch": {"type": "string"},
            "elements": {"type": "array", "items": {"type": "object"}},
            "constraints": {"type": "array", "items": {"type": "object"}},
            "auto_constrain": {"type": "boolean"},
        },
        "required": ["sketch"],
    },
    category="sketch",
)
def add_to_sketch(s: CatiaSession, sketch: str, document: str | None = None, elements: list | None = None,
                  constraints: list | None = None, auto_constrain: bool = True):
    doc, part = s.part_document(document)
    sk = s.find_object(part, sketch)
    if s.typename(sk) != "Sketch":
        raise ToolError(f"'{sketch}' bir sketch değil.")
    meta = s.sketch_meta.setdefault((doc.Name, sk.Name), {"elements": {}, "dof": 0})
    before_cst = sk.Constraints.Count
    before_geo = sk.GeometricElements.Count
    snapshot = {k: dict(v) for k, v in meta["elements"].items()}
    dof0 = meta.get("dof", 0)
    b, _ = _run_builder(s, doc, part, sk, meta, elements, constraints, auto_constrain)

    def undo(d=doc, sk=sk, created=list(b.created), n_cst=before_cst):
        f = sk.OpenEdition()
        try:
            for i in range(sk.Constraints.Count, n_cst, -1):
                sk.Constraints.Remove(i)
        except pywintypes.com_error:
            pass
        sk.CloseEdition()
        s.delete_objects(d, created)
        meta["elements"] = snapshot
        meta["dof"] = dof0

    s.journal.record(f"{sk.Name} eklemelerini geri al", undo)
    failing = s.update(part)
    rep = _sketch_report(s, part, sk, meta)
    rep["added_geometry"] = sk.GeometricElements.Count - before_geo
    if rep.get("constraint_problems") or sk.Name in failing:
        raise ToolError("Eklenen kısıtlar çözülemedi; ekleme geri alındı.",
                        constraint_problems=rep.get("constraint_problems"), failing=failing)
    return rep


@tool(
    "get_sketch",
    "Bir sketch'in yerel eksenlerini, öğe adlarını, kısıt sayısını/durumunu ve tahmini serbestlik derecesini verir.",
    {"properties": {"document": DOC_PROP, "sketch": {"type": "string"}}, "required": ["sketch"]},
    mutating=False,
    category="sketch",
)
def get_sketch(s: CatiaSession, sketch: str, document: str | None = None):
    doc, part = s.part_document(document)
    sk = s.find_object(part, sketch)
    meta = s.sketch_meta.get((doc.Name, sk.Name), {"elements": {}, "dof": None})
    rep = _sketch_report(s, part, sk, {"elements": meta["elements"], "dof": meta.get("dof")})
    geo = []
    for i in range(1, sk.GeometricElements.Count + 1):
        g = sk.GeometricElements.Item(i)
        geo.append(g.Name)
    rep["geometry"] = geo
    csts = []
    for i in range(1, sk.Constraints.Count + 1):
        c = sk.Constraints.Item(i)
        item = {"name": c.Name, "status": STATUS.get(int(c.Status), c.Status)}
        try:
            item["value"] = round(c.Dimension.Value, 6)
        except pywintypes.com_error:
            pass
        csts.append(item)
    rep["constraints"] = csts
    if meta.get("dof") is None:
        rep["dof_estimate"] = None
        rep["fully_constrained"] = None
        rep["note"] = "Bu sketch asistan dışında oluşturulmuş; serbestlik tahmini yok."
    return rep

