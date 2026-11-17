"""Gövde ölçümleri.

R20'de doğrulanan birimler: Measurable.Volume -> m³, Area -> m², GetCOG -> mm,
GetMinimumDistance -> mm, Inertia.Mass -> kg (varsayılan yoğunluk 1000 kg/m³).

Sınır kutusu için API'de doğrudan bir çağrı yok. Geçici bir geometrik sette
her eksen yönünde min/max Extremum oluşturulup ağırlık merkezi okunur
(extremum bir yüz olabileceğinden GetPoint değil GetCOG), ardından set silinir.
"""
from __future__ import annotations

import pywintypes

from .errors import ToolError, com_error_text
from .session import CatiaSession, r3, vec3

TMP_SET = "__aibot_tmp"


def has_solid_features(body) -> bool:
    try:
        return body.Shapes.Count > 0
    except pywintypes.com_error:
        return False


def _inertia_fallback(s: CatiaSession, doc, body) -> dict | None:
    """Measurable başarısızsa Inertia'dan hacim (kütle/yoğunluk) ve ağırlık merkezi."""
    try:
        inertia = s.spa(doc).Inertias.Add(body)
        mass, dens = inertia.Mass, inertia.Density
        if dens <= 0:
            return None
        out = {"volume_mm3": r3(mass / dens * 1e9), "source": "inertia"}
        try:
            out["center_of_gravity"] = vec3([c * 1000.0 for c in s.vb_array(inertia, "GetCOGPosition", 3)])
        except ToolError:
            pass
        return out
    except pywintypes.com_error:
        return None


def body_volume_mm3(s: CatiaSession, doc, part, body) -> float:
    # R20 tuzağı: feature'ı olmayan gövdeyi GetMeasurable ile ölçmek, o gövdenin
    # ölçümünü belge boyunca bozar (sonradan katı eklense bile "Volume failed").
    if not has_solid_features(body):
        return 0.0
    try:
        return s.spa(doc).GetMeasurable(s.ref(part, body)).Volume * 1e9
    except pywintypes.com_error:
        fb = _inertia_fallback(s, doc, body)
        return fb["volume_mm3"] if fb else 0.0


def body_cog(s: CatiaSession, doc, part, body) -> list[float] | None:
    if not has_solid_features(body):
        return None
    try:
        return list(s.vb_array(s.spa(doc).GetMeasurable(s.ref(part, body)), "GetCOG", 3))
    except ToolError:
        fb = _inertia_fallback(s, doc, body)
        return fb.get("center_of_gravity") if fb else None


def body_properties(s: CatiaSession, doc, part, body) -> dict:
    if not has_solid_features(body):
        return {"body": body.Name, "volume_mm3": 0.0, "note": "Gövdede feature yok."}
    m = s.spa(doc).GetMeasurable(s.ref(part, body))
    try:
        vol = m.Volume * 1e9
    except pywintypes.com_error:
        fb = _inertia_fallback(s, doc, body)
        if not fb or fb["volume_mm3"] <= 0:
            return {"body": body.Name, "volume_mm3": 0.0, "note": "Gövdede katı yok ya da güncel değil."}
        return {"body": body.Name, **fb, "note": "Yüzey alanı bu gövde için ölçülemedi (Inertia yedeği kullanıldı)."}
    out = {"body": body.Name, "volume_mm3": r3(vol)}
    try:
        out["area_mm2"] = r3(m.Area * 1e6)
    except pywintypes.com_error:
        pass
    try:
        out["center_of_gravity"] = vec3(s.vb_array(m, "GetCOG", 3))
    except ToolError:
        pass
    return out


def mass_properties(s: CatiaSession, doc, body) -> dict:
    spa = s.spa(doc)
    try:
        inertia = spa.Inertias.Add(body)
        return {"mass_kg": round(inertia.Mass, 6), "density_kg_m3": round(inertia.Density, 3)}
    except pywintypes.com_error as exc:
        return {"mass_error": com_error_text(exc)}


def _extremum_coord(s: CatiaSession, m, k: int) -> float:
    """Extremum sonucu yüz, doğru ya da nokta olabilir (ör. silindirin X uç değeri bir doğrudur).
    Uç kümesinin her noktası aynı k koordinatına sahiptir; okunabilen ilk noktayı kullan."""
    for method, n in (("GetCOG", 3), ("GetPoint", 3), ("GetPointsOnCurve", 9)):
        try:
            return s.vb_array(m, method, n)[k]
        except ToolError:
            continue
    raise ToolError("Sınır kutusu hesaplanamadı: extremum ölçülemedi.")


_NORMAL_PLANE = ("PlaneYZ", "PlaneZX", "PlaneXY")  # normali X, Y, Z olan ana düzlemler
_FAR = 10000.0


def _far_plane_coord(s: CatiaSession, spa, part, hsf, hb, ex, k: int, is_max: bool) -> float:
    base = s.ref(part, getattr(part.OriginElements, _NORMAL_PLANE[k]))
    pl = hsf.AddNewPlaneOffset(base, _FAR, not is_max)  # +F (max için) ya da -F (min için)
    hb.AppendHybridShape(pl)
    try:
        part.UpdateObject(pl)
        dist = spa.GetMeasurable(s.ref(part, pl)).GetMinimumDistance(s.ref(part, ex))
    except pywintypes.com_error as exc:
        raise ToolError(f"Sınır kutusu hesaplanamadı: {com_error_text(exc)}") from None
    return _FAR - dist if is_max else -_FAR + dist


def bounding_box(s: CatiaSession, doc, part, body) -> dict:
    spa = s.spa(doc)
    hsf = part.HybridShapeFactory
    in_work = None
    try:
        in_work = part.InWorkObject
    except pywintypes.com_error:
        pass
    hb = part.HybridBodies.Add()
    hb.Name = TMP_SET
    lo, hi = [0.0] * 3, [0.0] * 3
    try:
        body_ref = s.ref(part, body)
        for k, (x, y, z) in enumerate([(1, 0, 0), (0, 1, 0), (0, 0, 1)]):
            d = hsf.AddNewDirectionByCoord(x, y, z)
            for is_max in (1, 0):
                ex = hsf.AddNewExtremum(body_ref, d, is_max)
                hb.AppendHybridShape(ex)
                try:
                    part.UpdateObject(ex)
                except pywintypes.com_error as exc:
                    raise ToolError(f"Sınır kutusu hesaplanamadı: {com_error_text(exc)}") from None
                try:
                    coord = _extremum_coord(s, spa.GetMeasurable(s.ref(part, ex)), k)
                except ToolError:
                    # R20: sarmal gibi eğrisel gövdelerde extremum sonucu ölçülemiyor (GeometryName 0) ve gövdenin
                    # bütününe mesafe de reddediliyor; ama extremum -> uzak düzlem mesafesi doğru çalışıyor.
                    coord = _far_plane_coord(s, spa, part, hsf, hb, ex, k, bool(is_max))
                (hi if is_max else lo)[k] = coord
    finally:
        try:
            s.delete_objects(doc, [hb])
        except pywintypes.com_error:
            pass
        if in_work is not None:
            try:
                part.InWorkObject = in_work
            except pywintypes.com_error:
                pass
    return {"min": vec3(lo), "max": vec3(hi), "size": vec3([hi[i] - lo[i] for i in range(3)])}


def min_distance(s: CatiaSession, doc, ref_a, ref_b) -> float:
    m = s.spa(doc).GetMeasurable(ref_a)
    try:
        return r3(m.GetMinimumDistance(ref_b))
    except pywintypes.com_error as exc:
        raise ToolError(f"Mesafe ölçülemedi: {com_error_text(exc)}") from None
