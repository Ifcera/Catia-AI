"""Gövde topolojisi: yüzeyleri ve kenarları listeleme, kalıcı kimlik verme, seçicileri çözme.

Nasıl çalışır (R20'de doğrulandı):
- Selection.Search("Topology.CGMFace,sel") / ("Topology.CGMEdge,sel") gövdenin
  o anki sonuç yüzey/kenarlarını verir. Her öğenin Reference.DisplayName'i
  "Selection_RSur:(Face:(Brp:(Pad.1;2);None:();Cf11:());Pad.1_ResultOUT;Z0;G3563)"
  biçimindedir. Sondaki "<Feature>_ResultOUT;..." kısmı ağaca feature
  eklendikçe değişir; baştaki "RSur:(Face:(...))" çekirdeği ise yüzey
  yaşadıkça aynı kalır. Bu çekirdek kalıcı anahtar olarak kullanılır.
- Anahtara oturum boyunca sabit kısa kimlik verilir (F1, F2..., E1, E2...),
  böylece asistan bir listelemeden sonraki çağrılarda aynı kimliği kullanabilir.
- Referans olarak arama sonucundaki Reference nesnesi kullanılır; CATIA
  bunu feature'a bağlarken kendi kalıcı BRep adına çevirir.
  (CreateReferenceFromBRepName ile kurulan referans R20'de ölçülemedi.)
- Kenar anahtarı iki komşu yüzeyin çekirdeğini içerir; bu sayede
  "şu iki yüzey arasındaki kenar" ya da "şu yüzeyin kenarları" seçilebilir.
- Sketch telleri (WireREdge) gövde kenarı değildir, elenir.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import pywintypes

from .errors import ToolError
from .session import CatiaSession, cross, dot, norm, r3, vec3

AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}

FACE_TYPES = {
    "PlanarFace": "planar",
    "CylindricalFace": "cylindrical",
    "ConicalFace": "conical",
    "SphericalFace": "spherical",
    "ToroidalFace": "toroidal",
}


def brep_key(display_name: str) -> str:
    s = display_name
    if s.startswith("Selection_"):
        s = s[len("Selection_"):]
    idx = s.find("_ResultOUT")
    if idx > 0:
        cut = s.rfind(";", 0, idx)
        if cut > 0:
            s = s[:cut]
    return s


def source_features(key: str) -> list[str]:
    seen: list[str] = []
    for name in re.findall(r"Brp:\(([^;()]+);", key):
        if name not in seen:
            seen.append(name)
    return seen


def face_core(face_key: str) -> str:
    # "RSur:(Face:(...))" -> "Face:(...)"  (kenar anahtarlarının içinde böyle geçer)
    if face_key.startswith("RSur:(") and face_key.endswith(")"):
        return face_key[len("RSur:("):-1]
    return face_key


def axis_of(name: str) -> tuple[float, float, float]:
    try:
        return AXES[name.upper().lstrip("+-")]
    except KeyError:
        raise ToolError(f"Bilinmeyen eksen '{name}'.", hint="X, Y veya Z kullanın.") from None


@dataclass
class Item:
    id: str
    key: str
    ref: Any
    info: dict = field(default_factory=dict)


class Topology:
    def __init__(self, session: CatiaSession, doc, part, body):
        self.s = session
        self.doc = doc
        self.part = part
        self.body = body
        self.spa = session.spa(doc)
        self._faces: list[Item] | None = None
        self._edges: list[Item] | None = None

    # ------------------------------------------------------------- arama
    def _search(self, what: str) -> list[tuple[Any, str]]:
        sel = self.doc.Selection
        keep = [sel.Item(i).Value for i in range(1, sel.Count + 1)]
        try:
            sel.Clear()
            sel.Add(self.body)
            sel.Search(f"Topology.{what},sel")
            return [(sel.Item(i).Reference, sel.Item(i).Type) for i in range(1, sel.Count + 1)]
        finally:
            sel.Clear()
            for v in keep:
                try:
                    sel.Add(v)
                except pywintypes.com_error:
                    pass

    def _id_for(self, key: str, prefix: str) -> str:
        dk = self.s.doc_key(self.doc)
        ids = self.s.topo_ids.setdefault(dk, {})
        if key not in ids:
            counters = self.s.topo_counters.setdefault(dk, {})
            counters[prefix] = counters.get(prefix, 0) + 1
            ids[key] = f"{prefix}{counters[prefix]}"
        return ids[key]

    # ------------------------------------------------------------- yüzeyler
    def faces(self) -> list[Item]:
        if self._faces is not None:
            return self._faces
        items: list[Item] = []
        for ref, typ in self._search("CGMFace"):
            key = brep_key(ref.DisplayName)
            if not key.startswith("RSur:(Face:"):
                continue
            m = self.spa.GetMeasurable(ref)
            info: dict = {"type": FACE_TYPES.get(typ, typ)}
            try:
                info["area_mm2"] = r3(m.Area * 1e6)
                info["center"] = vec3(self.s.vb_array(m, "GetCOG", 3))
            except (pywintypes.com_error, ToolError):
                info["center"] = None
            if typ == "PlanarFace":
                try:
                    p = self.s.vb_array(m, "GetPlane", 9)
                    info["_normal_raw"] = norm(cross(p[3:6], p[6:9]))
                except ToolError:
                    pass
            elif typ in ("CylindricalFace", "SphericalFace", "ConicalFace"):
                try:
                    info["radius"] = r3(m.Radius)
                except pywintypes.com_error:
                    pass
            info["features"] = source_features(key)
            items.append(Item(self._id_for(key, "F"), key, ref, info))
        self._orient_normals(items)
        self._faces = items
        return items

    def _orient_normals(self, items: list[Item]) -> None:
        """GetPlane normali dış yönü garanti etmez (R20'de alt yüz +Z döndü).
        Eksene hizalı yüzeylerde: tüm yüzey merkezlerinin en uç değerinde ise
        dış yön kesindir; değilse gövde ağırlık merkezine göre tahmin edilir."""
        centers = [it.info["center"] for it in items if it.info.get("center")]
        from .measure import body_cog as _body_cog

        try:
            body_cog = _body_cog(self.s, self.doc, self.part, self.body)
        except pywintypes.com_error:
            body_cog = None
        for it in items:
            n = it.info.pop("_normal_raw", None)
            c = it.info.get("center")
            if n is None or c is None:
                continue
            confidence = "estimated"
            k = max(range(3), key=lambda i: abs(n[i]))
            if abs(n[k]) > 0.999 and centers:
                hi = max(cc[k] for cc in centers)
                lo = min(cc[k] for cc in centers)
                sign = 1.0 if n[k] > 0 else -1.0
                if abs(c[k] - hi) < 1e-6 and hi - lo > 1e-6:
                    n = [x * sign for x in n]
                    confidence = "high"
                elif abs(c[k] - lo) < 1e-6 and hi - lo > 1e-6:
                    n = [-x * sign for x in n]
                    confidence = "high"
            if confidence == "estimated" and body_cog is not None:
                if dot(n, [c[i] - body_cog[i] for i in range(3)]) < 0:
                    n = [-x for x in n]
            it.info["normal"] = vec3(n)
            it.info["normal_confidence"] = confidence

    # ------------------------------------------------------------- kenarlar
    def edges(self) -> list[Item]:
        if self._edges is not None:
            return self._edges
        faces = self.faces()
        items: list[Item] = []
        for ref, typ in self._search("CGMEdge"):
            key = brep_key(ref.DisplayName)
            if not key.startswith("REdge:(Edge:"):
                continue  # sketch telleri vb.
            m = self.spa.GetMeasurable(ref)
            info: dict = {}
            try:
                gname = int(m.GeometryName)
            except pywintypes.com_error:
                gname = 0
            info["type"] = {10: "line", 9: "circle", 8: "curve"}.get(gname, "curve")
            try:
                info["length"] = r3(m.Length)  # R20: Measurable.Length mm döner (Volume m³, Area m² aksine)
            except pywintypes.com_error:
                pass
            try:
                pts = self.s.vb_array(m, "GetPointsOnCurve", 9)
                info["start"], info["mid"], info["end"] = vec3(pts[0:3]), vec3(pts[3:6]), vec3(pts[6:9])
                if info["type"] == "line":
                    info["direction"] = vec3(norm([pts[6] - pts[0], pts[7] - pts[1], pts[8] - pts[2]]))
            except ToolError:
                pass
            if info["type"] == "circle":
                try:
                    info["radius"] = r3(m.Radius)
                    info["center"] = vec3(self.s.vb_array(m, "GetCenter", 3))
                except (pywintypes.com_error, ToolError):
                    pass
            info["faces"] = [f.id for f in faces if face_core(f.key) in key]
            items.append(Item(self._id_for(key, "E"), key, ref, info))
        self._edges = items
        return items

    # ------------------------------------------------------------- dışa veri
    @staticmethod
    def public(it: Item) -> dict:
        d = {"id": it.id}
        d.update({k: v for k, v in it.info.items() if not k.startswith("_")})
        return d

    # ------------------------------------------------------------- seçiciler
    def resolve_faces(self, selector, many: bool = False) -> list[Item]:
        return self._resolve(selector, self.faces(), "face", many)

    def resolve_edges(self, selector, many: bool = True) -> list[Item]:
        return self._resolve(selector, self.edges(), "edge", many)

    def _by_id(self, ident: str, pool: list[Item], kind: str) -> Item:
        for it in pool:
            if it.id == ident:
                return it
        raise ToolError(
            f"{ident} kimlikli {('yüzey' if kind == 'face' else 'kenar')} gövdede artık yok (topoloji değişmiş olabilir).",
            hint="list_topology ile güncel listeyi alın ya da konum tabanlı seçici (near/axis/side) kullanın.",
        )

    def _resolve(self, selector, pool: list[Item], kind: str, many: bool) -> list[Item]:
        if isinstance(selector, str):
            return [self._by_id(selector, pool, kind)]
        if isinstance(selector, list):
            out: list[Item] = []
            for s in selector:
                for it in self._resolve(s, pool, kind, many=True):
                    if it not in out:
                        out.append(it)
            return out
        if not isinstance(selector, dict):
            raise ToolError(f"Geçersiz seçici: {selector!r}")
        if "id" in selector:
            return [self._by_id(selector["id"], pool, kind)]
        if "ids" in selector:
            return [self._by_id(i, pool, kind) for i in selector["ids"]]

        cand = list(pool)
        typ = selector.get("type")
        if typ:
            cand = [it for it in cand if it.info.get("type") == typ]
        feat = selector.get("feature")
        if feat:
            cand = [it for it in cand if feat in (it.info.get("features") or source_features(it.key))]
        if kind == "face" and selector.get("axis"):
            ax = axis_of(selector["axis"])
            cand = [it for it in cand if it.info.get("normal") and abs(dot(it.info["normal"], ax)) > 0.999]
            if selector["axis"].startswith(("+", "-")):
                sg = -1 if selector["axis"].startswith("-") else 1
                cand = [it for it in cand if dot(it.info["normal"], ax) * sg > 0]
        if kind == "edge":
            if selector.get("parallel_to"):
                ax = axis_of(selector["parallel_to"])
                cand = [it for it in cand if it.info.get("direction") and abs(dot(it.info["direction"], ax)) > 0.999]
            if selector.get("on_face") is not None:
                fids = {f.id for f in self.resolve_faces(selector["on_face"], many=True)}
                cand = [it for it in cand if fids & set(it.info.get("faces", []))]
            if selector.get("between") is not None:
                a, b = selector["between"]
                fa = {f.id for f in self.resolve_faces(a, many=True)}
                fb = {f.id for f in self.resolve_faces(b, many=True)}
                cand = [it for it in cand if fa & set(it.info["faces"]) and fb & set(it.info["faces"])]
        if selector.get("radius") is not None:
            cand = [it for it in cand if it.info.get("radius") is not None and abs(it.info["radius"] - selector["radius"]) < 1e-3]

        def pos(it):
            return it.info.get("center") or it.info.get("mid")

        side = selector.get("side")
        if side and cand:
            ax = axis_of(selector.get("axis") or selector.get("side_axis") or "Z")
            vals = [dot(pos(it), ax) for it in cand if pos(it)]
            target = max(vals) if side == "max" else min(vals)
            cand = [it for it in cand if pos(it) and abs(dot(pos(it), ax) - target) < 1e-4]
        near = selector.get("near")
        if near is not None and cand:
            cand.sort(key=lambda it: math.dist(pos(it), near) if pos(it) else 1e30)
            if not selector.get("all"):
                cand = cand[:1]
        if not cand:
            raise ToolError(
                f"Seçiciye uyan {('yüzey' if kind == 'face' else 'kenar')} yok: {selector}",
                hint="list_topology ile mevcut yüzey/kenarlara bakın.",
            )
        if not many and len(cand) > 1:
            raise ToolError(
                f"Seçici {len(cand)} {('yüzey' if kind == 'face' else 'kenar')} buldu, tek bir tane bekleniyordu: {[c.id for c in cand][:12]}",
                hint="near, side veya id ile daraltın.",
            )
        return cand


def describe_face(info: dict) -> str:
    parts = [info.get("type", "?")]
    n = info.get("normal")
    if n:
        k = max(range(3), key=lambda i: abs(n[i]))
        if abs(n[k]) > 0.999:
            parts.append(f"normal {'+' if n[k] > 0 else '-'}{'XYZ'[k]} @ {'xyz'[k]}={info['center'][k]}")
    if info.get("radius") is not None:
        parts.append(f"r={info['radius']}")
    return ", ".join(parts)
