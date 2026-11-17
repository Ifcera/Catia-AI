"""Assembly Design araçları: bileşen ekleme, konumlandırma, montaj kısıtları, güncelleme, montaj kontrolü.

R20'de doğrulananlar (docs/R20_API_NOTLARI.md):
- Products.AddComponentsFromFiles((yollar), "All") ve AddExternalComponent(açık belge) bileşen ekler.
- Position.GetComponents / SetComponents: 12 sayı = [X ekseni(3), Y ekseni(3), Z ekseni(3), orijin(3)] (ebeveyn koordinatında).
- Montaj bağlamında referans adı: "<Kök>/<Örnek>/!<öğe>":
    ana düzlem: "!xy plane" (parçadaki gerçek ad), bileşenin kendisi (fix için): "!<Kök>/<Örnek>/",
    yüzey: parça belgesinde Selection.Search ile alınan DisplayName ("Selection_RSur:(...)") aynen eklenir.
  Ürün belgesinin seçimiyle yüzey araması sonuç vermiyor; bu yüzden parça belgesinin topolojisi kullanılır
  (kimlikler parça düzeyindeki list_topology ile aynıdır).
- Kısıtlar root.Connections("CATIAConstraints") ile; root.Update() bileşenleri kısıtlara göre taşır.
- Çakışma: SPAWorkbench.Clashes.Add -> Compute -> Conflicts (Type 0=çakışma, 1=temas, 2=boşluk; Value mm).
"""
from __future__ import annotations

import math

import pywintypes

from ..catia.errors import ToolError, com_error_text
from ..catia.session import CatiaSession, r3, vec3
from ..catia.topology import Topology, describe_face
from .registry import DOC_PROP, FACE_SELECTOR, VEC3, tool

CST_TYPES = {"fix": 0, "offset": 1, "coincidence": 2, "angle": 6, "parallel": 8, "perpendicular": 11, "contact": 20}
CST_NAMES = {v: k for k, v in CST_TYPES.items()}
ORIENT = {"same": 0, "opposite": 1, "undefined": 2}
STATUS = {0: "OK", 1: "sağlanamadı", 2: "yanlış yön", 3: "yanlış değer", 4: "uygun olmayan geometri", 5: "kopuk"}
CONFLICT = {0: "çakışma", 1: "temas", 2: "boşluk"}
PRODUCT_PROP = {"type": "string"}
ELEMENT = {
    "type": "object",
    "description": "{instance, element: 'XY'|'YZ'|'ZX'|'component'|{plane: ad}|yüzey seçici (PARÇA koordinatında), body?}",
    "properties": {"instance": {"type": "string"}},
    "required": ["instance", "element"],
}
ELEMENT_B = {"type": "object", "description": "a ile aynı biçim."}


# ================================================================ yardımcılar
def product_document(s: CatiaSession, name: str | None):
    doc = s.document(name)
    try:
        _ = doc.Part
        raise ToolError(f"'{doc.Name}' bir CATPart; montaj araçları bir CATProduct ister.",
                        hint="new_product ile montaj oluşturun ya da document parametresine .CATProduct adı verin.")
    except (pywintypes.com_error, AttributeError):
        pass
    try:
        return doc, doc.Product
    except (pywintypes.com_error, AttributeError):
        raise ToolError(f"'{doc.Name}' bir montaj belgesi değil.") from None


def find_instance(root, name: str):
    prods = root.Products
    for i in range(1, prods.Count + 1):
        p = prods.Item(i)
        if p.Name == name:
            return p
    names = [prods.Item(i).Name for i in range(1, prods.Count + 1)]
    raise ToolError(f"Montajda '{name}' adlı bileşen yok.", hint=f"Bileşenler: {names}")


def component_part(s: CatiaSession, inst):
    try:
        pdoc = inst.ReferenceProduct.Parent
        return pdoc, pdoc.Part
    except (pywintypes.com_error, AttributeError):
        raise ToolError(f"'{inst.Name}' bir parça değil (alt montaj). Şimdilik yalnızca parça bileşenleri destekleniyor.") from None


def get_matrix(s: CatiaSession, inst) -> list[float]:
    return list(s.vb_array(inst.Position, "GetComponents", 12))


def set_matrix(inst, m: list[float]) -> None:
    inst.Position.SetComponents(tuple(float(x) for x in m))


def _rot(axis: str, deg: float):
    a = math.radians(deg)
    c, s_ = math.cos(a), math.sin(a)
    ax = axis.upper().lstrip("+")
    if ax == "X":
        return [[1, 0, 0], [0, c, -s_], [0, s_, c]]
    if ax == "Y":
        return [[c, 0, s_], [0, 1, 0], [-s_, 0, c]]
    if ax == "Z":
        return [[c, -s_, 0], [s_, c, 0], [0, 0, 1]]
    raise ToolError(f"Bilinmeyen dönme ekseni '{axis}'.", hint="X, Y ya da Z.")


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def rotation_matrix(rotations: list[dict] | None) -> list[list[float]]:
    m = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    for r in rotations or []:
        m = _matmul(_rot(r["axis"], float(r["angle"])), m)  # sırayla, sabit (ebeveyn) eksenler etrafında
    return m


def matrix_to_components(rot, origin) -> list[float]:
    # sütunlar: bileşenin X, Y, Z eksenlerinin ebeveyn koordinatındaki yönleri
    cols = [[rot[i][j] for i in range(3)] for j in range(3)]
    return cols[0] + cols[1] + cols[2] + [float(x) for x in origin]


def describe_position(m: list[float]) -> dict:
    return {"origin": vec3(m[9:12]), "x_axis": vec3(m[0:3]), "y_axis": vec3(m[3:6]), "z_axis": vec3(m[6:9])}


def positions(s: CatiaSession, root) -> dict[str, list[float]]:
    out = {}
    for i in range(1, root.Products.Count + 1):
        p = root.Products.Item(i)
        try:
            out[p.Name] = get_matrix(s, p)
        except ToolError:
            pass
    return out


def restore_positions(s: CatiaSession, root, snap: dict[str, list[float]]) -> None:
    for i in range(1, root.Products.Count + 1):
        p = root.Products.Item(i)
        if p.Name in snap:
            try:
                set_matrix(p, snap[p.Name])
            except pywintypes.com_error:
                pass


AXIS_TYPES = {"cylindrical", "conical"}


def element_reference(s: CatiaSession, root, spec: dict, use_axis: bool = False):
    """Montaj bağlamında referans: (reference, açıklama).
    use_axis: silindirik/konik yüzeyde yüzey yerine EKSENİ kullan. R20'de doğrulandı: iki silindirik yüzey arasında
    coincidence durum 4 (uygun olmayan geometri) verip hiçbir şeyi taşımıyor; "!Axis:(Selection_RSur:...)" ile durum OK."""
    inst = find_instance(root, spec["instance"])
    pdoc, part = component_part(s, inst)
    base = f"{root.Name}/{inst.Name}/!"
    el = spec.get("element")
    if el == "component":
        return root.CreateReferenceFromName(f"{base}{root.Name}/{inst.Name}/"), f"{inst.Name} (bileşen)"
    if isinstance(el, str) and el.upper() in ("XY", "YZ", "ZX", "XZ"):
        plane = s.origin_plane(part, el)
        return root.CreateReferenceFromName(base + plane.Name), f"{inst.Name} {el.upper()} düzlemi"
    if isinstance(el, dict) and "plane" in el:
        obj = s.find_object(part, el["plane"])
        return root.CreateReferenceFromName(base + obj.Name), f"{inst.Name} {el['plane']}"
    body = s.body(part, spec.get("body")) if spec.get("body") else part.MainBody
    face = Topology(s, pdoc, part, body).resolve_faces(el, many=False)[0]
    label = f"{inst.Name} {face.id} ({describe_face(face.info)})"
    if use_axis and face.info.get("type") in AXIS_TYPES:
        return root.CreateReferenceFromName(f"{base}Axis:({face.ref.DisplayName})"), label + " ekseni"
    return root.CreateReferenceFromName(base + face.ref.DisplayName), label


def constraint_info(cst) -> dict:
    d = {"name": None}
    try:
        d["name"] = cst.Name
    except pywintypes.com_error:
        return d
    for key, getter in (("type", lambda: CST_NAMES.get(int(cst.Type), int(cst.Type))),
                        ("status", lambda: STATUS.get(int(cst.Status), int(cst.Status))),
                        ("value", lambda: r3(cst.Dimension.Value))):
        try:
            d[key] = getter()
        except (pywintypes.com_error, AttributeError):
            pass  # R20: Fix kısıtında Type/Status okumak "catastrophic failure" veriyor
    if d.get("type") is None and d["name"] and d["name"].startswith("Fix"):
        d["type"] = "fix"
    return d


def all_constraints(root) -> list[dict]:
    cons = root.Connections("CATIAConstraints")
    return [constraint_info(cons.Item(i)) for i in range(1, cons.Count + 1)]


def _update(root) -> str | None:
    try:
        root.Update()
        return None
    except pywintypes.com_error as exc:
        return com_error_text(exc)


def _moved(before: dict, after: dict) -> list[dict]:
    out = []
    for name, m in after.items():
        b = before.get(name)
        if b and max(abs(x - y) for x, y in zip(b, m)) > 1e-6:
            out.append({"instance": name, **describe_position(m)})
    return out


# ===================================================================== araçlar
@tool(
    "add_component",
    """Montaja parça ekler: source açık belge adı ya da çalışma klasöründeki dosya; instance_name; origin. Aynı parça birden çok kez eklenebilir.""",
    {"properties": {"document": PRODUCT_PROP, "source": {"type": "string"}, "instance_name": {"type": "string"},
                    "origin": VEC3}, "required": ["source"]},
    category="assembly",
)
def add_component(s: CatiaSession, source: str, document=None, instance_name=None, origin=None):
    doc, root = product_document(s, document)
    prods = root.Products
    n0 = prods.Count
    src_doc = None
    try:
        src_doc = s.app.Documents.Item(source)
    except pywintypes.com_error:
        pass
    try:
        if src_doc is not None:
            try:
                _ = src_doc.Part
            except (pywintypes.com_error, AttributeError):
                raise ToolError(f"'{source}' bir parça belgesi değil.") from None
            inst = prods.AddExternalComponent(src_doc)
        else:
            p = s.resolve_path(source, must_exist=True)
            prods.AddComponentsFromFiles((str(p),), "All")
            if prods.Count == n0:
                raise ToolError(f"'{p.name}' eklenemedi.")
            inst = prods.Item(prods.Count)
    except pywintypes.com_error as exc:
        raise ToolError(f"Bileşen eklenemedi: {com_error_text(exc)}") from None
    s.journal.record(f"{inst.Name} bileşenini kaldır", lambda r=root, i=inst: r.Products.Remove(i.Name))
    if instance_name:
        try:
            inst.Name = instance_name
        except pywintypes.com_error:
            raise ToolError(f"Örnek adı '{instance_name}' verilemedi (aynı ad montajda olabilir).") from None
    if origin is not None:
        set_matrix(inst, [1, 0, 0, 0, 1, 0, 0, 0, 1, *origin])
    s.created.add((doc.Name, inst.Name))
    return {"instance": inst.Name, "part_number": inst.PartNumber, "count": prods.Count,
            "position": describe_position(get_matrix(s, inst))}


@tool(
    "list_components",
    """Bileşenler (ad, parça no, konum) ve montaj kısıtları (tür, durum, değer).""",
    {"properties": {"document": PRODUCT_PROP}},
    mutating=False,
    category="assembly",
)
def list_components(s: CatiaSession, document=None):
    doc, root = product_document(s, document)
    comps = []
    for i in range(1, root.Products.Count + 1):
        p = root.Products.Item(i)
        c = {"instance": p.Name, "part_number": p.PartNumber}
        try:
            c["file"] = p.ReferenceProduct.Parent.Name
        except (pywintypes.com_error, AttributeError):
            pass
        try:
            c.update(describe_position(get_matrix(s, p)))
        except ToolError:
            pass
        comps.append(c)
    return {"product": doc.Name, "components": comps, "constraints": all_constraints(root)}


@tool(
    "set_component_position",
    """Bileşeni konumlar: origin + rotations [{axis, angle°}] (sırayla, montaj eksenleri). Kısıtlı bileşen update'te kısıtın yerine döner.""",
    {"properties": {"document": PRODUCT_PROP, "instance": {"type": "string"}, "origin": VEC3,
                    "rotations": {"type": "array", "items": {"type": "object", "properties": {
                        "axis": {"enum": ["X", "Y", "Z"]}, "angle": {"type": "number"}}, "required": ["axis", "angle"]}}},
     "required": ["instance", "origin"]},
    category="assembly",
)
def set_component_position(s: CatiaSession, instance: str, origin, document=None, rotations=None):
    doc, root = product_document(s, document)
    inst = find_instance(root, instance)
    old = get_matrix(s, inst)
    s.journal.record(f"{inst.Name} konumunu geri yükle", lambda i=inst, m=old: set_matrix(i, m))
    set_matrix(inst, matrix_to_components(rotation_matrix(rotations), origin))
    return {"instance": inst.Name, **describe_position(get_matrix(s, inst))}


@tool(
    "add_assembly_constraint",
    """Montaj kısıtı ekler ve günceller (bileşenler hareket eder): coincidence (düzlem/eksen; silindirde eksen) | contact | offset (value mm) | angle (value °) | parallel | perpendicular | fix (yalnız a). orientation same|opposite. Çözülemezse geri alınır.""",
    {"properties": {"document": PRODUCT_PROP, "type": {"enum": sorted(CST_TYPES)}, "a": ELEMENT, "b": ELEMENT_B,
                    "value": {"type": "number"}, "orientation": {"enum": ["same", "opposite", "undefined"]},
                    "name": {"type": "string"}},
     "required": ["type", "a"]},
    category="assembly",
)
def add_assembly_constraint(s: CatiaSession, type: str, a: dict, document=None, b=None, value=None, orientation=None,
                            name=None):
    doc, root = product_document(s, document)
    cons = root.Connections("CATIAConstraints")
    before = positions(s, root)
    code = CST_TYPES[type]
    if type == "fix":
        ra, da = element_reference(s, root, {**a, "element": "component"})
        cst = cons.AddMonoEltCst(code, ra)
        desc = [da]
    else:
        if b is None:
            raise ToolError(f"'{type}' kısıtı iki öğe ister (b eksik).")
        axis = type in ("coincidence", "angle", "parallel", "perpendicular")
        ra, da = element_reference(s, root, a, use_axis=axis)
        rb, db = element_reference(s, root, b, use_axis=axis)
        try:
            cst = cons.AddBiEltCst(code, ra, rb)
        except pywintypes.com_error as exc:
            raise ToolError(f"Kısıt oluşturulamadı: {com_error_text(exc)}",
                            hint="Öğe türleri bu kısıt için uygun mu? (ör. contact için iki yüzey, coincidence için eksen/düzlem)") from None
        desc = [da, db]
    cname = cst.Name

    def undo(r=root, n=cname, snap=before):
        try:
            r.Connections("CATIAConstraints").Remove(n)
        except pywintypes.com_error:
            pass
        restore_positions(s, r, snap)
        _update(r)

    s.journal.record(f"{cname} kısıtını sil", undo)
    if name:
        try:
            cst.Name = name
            cname = name
        except pywintypes.com_error:
            pass
    if value is not None:
        if type not in ("offset", "angle"):
            raise ToolError(f"'{type}' kısıtı değer almaz.")
        cst.Dimension.Value = float(value)
    elif type in ("offset", "angle"):
        raise ToolError(f"'{type}' kısıtı için value gerekli.")
    if orientation is not None:
        try:
            cst.Orientation = ORIENT[orientation]
        except pywintypes.com_error:
            pass
    err = _update(root)
    info = constraint_info(cst)
    if err or info.get("status") not in (None, "OK"):
        raise ToolError(f"Kısıt çözülemedi ({info.get('status') or err}); kısıt geri alındı.",
                        hint="Öğeleri, yön (orientation) ve değeri kontrol edin; çelişen bir kısıt olabilir.",
                        constraint=info)
    after = positions(s, root)
    return {"constraint": info, "between": desc, "moved": _moved(before, after)}


@tool(
    "update_product",
    "Montajı günceller ve kısıt durumlarını döndürür.",
    {"properties": {"document": PRODUCT_PROP}},
    mutating=False,
    category="assembly",
)
def update_product(s: CatiaSession, document=None):
    doc, root = product_document(s, document)
    err = _update(root)
    csts = all_constraints(root)
    bad = [c for c in csts if c.get("status") not in (None, "OK")]
    return {"updated": err is None, "error": err, "constraints": csts, "problems": bad}


def _clashes(s: CatiaSession, doc, clearance: float | None) -> dict:
    spa = s.spa(doc)
    clashes = spa.Clashes
    cl = clashes.Add()
    try:
        if clearance:
            cl.InterferenceType = 1  # catClashInterferenceTypeClearance
            cl.Clearance = float(clearance)
        cl.ComputationType = 0  # catClashComputationTypeBetweenAll
        cl.Compute()
        out = []
        for i in range(1, cl.Conflicts.Count + 1):
            c = cl.Conflicts.Item(i)
            item = {"type": CONFLICT.get(int(c.Type), int(c.Type)), "a": c.FirstProduct.Name, "b": c.SecondProduct.Name}
            try:
                item["value_mm"] = r3(c.Value)
            except pywintypes.com_error:
                pass
            out.append(item)
        return {"method": "CATIA clash", "conflicts": out}
    finally:
        try:
            clashes.Remove(clashes.Count)
        except pywintypes.com_error:
            pass


def _distance_fallback(s: CatiaSession, doc, root) -> dict:
    """Çakışma lisansı yoksa: bileşen çiftleri arası en kısa mesafe (0 = temas ya da girişim, ayırt edilemez)."""
    spa = s.spa(doc)
    insts = [root.Products.Item(i) for i in range(1, root.Products.Count + 1)]
    refs = {}
    for p in insts:
        try:
            refs[p.Name] = root.CreateReferenceFromName(f"{root.Name}/{p.Name}/!{root.Name}/{p.Name}/")
        except pywintypes.com_error:
            pass
    out = []
    names = list(refs)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            try:
                d = spa.GetMeasurable(refs[names[i]]).GetMinimumDistance(refs[names[j]])
            except pywintypes.com_error:
                continue
            if d < 1e-3:
                out.append({"type": "temas ya da çakışma", "a": names[i], "b": names[j], "distance_mm": r3(d)})
    return {"method": "mesafe (yedek; temas ile çakışma ayırt edilemez)", "conflicts": out}


def assembly_bbox(s: CatiaSession, root) -> dict | None:
    """Montajın sınır kutusu: her bileşenin (parça koordinatındaki) sınır kutusunun 8 köşesi montaj konumuna dönüştürülür.
    Eksene hizalı parçalarda kesin; döndürülmüş parçalarda biraz büyük (güvenli tarafta) çıkar."""
    from ..catia.measure import bounding_box, has_solid_features

    lo, hi = [math.inf] * 3, [-math.inf] * 3
    cache: dict[str, dict | None] = {}
    for i in range(1, root.Products.Count + 1):
        p = root.Products.Item(i)
        try:
            pdoc, part = component_part(s, p)
        except ToolError:
            continue
        if pdoc.Name not in cache:
            body = part.MainBody
            cache[pdoc.Name] = bounding_box(s, pdoc, part, body) if has_solid_features(body) else None
        bb = cache[pdoc.Name]
        if bb is None:
            continue
        m = get_matrix(s, p)
        X, Y, Z, O = m[0:3], m[3:6], m[6:9], m[9:12]
        for cx in (bb["min"][0], bb["max"][0]):
            for cy in (bb["min"][1], bb["max"][1]):
                for cz in (bb["min"][2], bb["max"][2]):
                    w = [O[k] + cx * X[k] + cy * Y[k] + cz * Z[k] for k in range(3)]
                    lo = [min(lo[k], w[k]) for k in range(3)]
                    hi = [max(hi[k], w[k]) for k in range(3)]
    if lo[0] == math.inf:
        return None
    return {"min": vec3(lo), "max": vec3(hi), "size": vec3([hi[k] - lo[k] for k in range(3)])}


@tool(
    "check_assembly",
    """Montaj kontrolü: kısıt durumu, çakışma (value_mm negatif = girişim), beklenen temaslar (expect_contacts), clearance, sınır kutusu ve fit_within [x,y,z], kütle.""",
    {"properties": {"document": PRODUCT_PROP, "clearance": {"type": "number"},
                    "expect_contacts": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                    "fit_within": VEC3}},
    mutating=False,
    category="assembly",
)
def check_assembly(s: CatiaSession, document=None, clearance=None, expect_contacts=None, fit_within=None):
    doc, root = product_document(s, document)
    err = _update(root)
    csts = all_constraints(root)
    problems = [c for c in csts if c.get("status") not in (None, "OK")]
    try:
        clash = _clashes(s, doc, clearance)
    except pywintypes.com_error as exc:
        clash = _distance_fallback(s, doc, root)
        clash["clash_error"] = com_error_text(exc)
    expected = {frozenset(p) for p in (expect_contacts or [])}
    for c in clash["conflicts"]:
        c["expected"] = frozenset((c["a"], c["b"])) in expected
    interferences = [c for c in clash["conflicts"] if c["type"] == "çakışma"]
    out = {
        "product": doc.Name, "updated": err is None, "update_error": err,
        "components": root.Products.Count, "constraints": csts, "constraint_problems": problems,
        "clash": clash, "interference_count": len(interferences),
        "missing_expected_contacts": [list(p) for p in expected
                                      if not any(frozenset((c["a"], c["b"])) == p for c in clash["conflicts"])],
    }
    try:
        out["bounding_box"] = assembly_bbox(s, root)
    except (ToolError, pywintypes.com_error) as exc:
        out["bounding_box_error"] = str(exc)
    if fit_within is not None and out.get("bounding_box"):
        size = out["bounding_box"]["size"]
        out["fits_within"] = {"limit": fit_within, "pass": all(size[k] <= fit_within[k] + 1e-6 for k in range(3))}
    try:
        inertia = s.spa(doc).Inertias.Add(root)
        out["mass_kg"] = round(inertia.Mass, 6)
        out["volume_mm3"] = r3(inertia.Mass / inertia.Density * 1e9) if inertia.Density else None
    except pywintypes.com_error:
        pass
    out["passed"] = (err is None and not problems and not interferences and not out["missing_expected_contacts"]
                     and (out.get("fits_within") or {}).get("pass", True))
    return out


@tool(
    "remove_constraint",
    "Bir montaj kısıtını siler (bu oturumda eklenenler ya da kullanıcı onaylıysa). Bileşenler yerinde kalır.",
    {"properties": {"document": PRODUCT_PROP, "name": {"type": "string"}}, "required": ["name"]},
    category="assembly",
)
def remove_constraint(s: CatiaSession, name: str, document=None):
    doc, root = product_document(s, document)
    cons = root.Connections("CATIAConstraints")
    names = [cons.Item(i).Name for i in range(1, cons.Count + 1)]
    if name not in names:
        raise ToolError(f"'{name}' adlı kısıt yok.", hint=f"Kısıtlar: {names}")
    cons.Remove(name)
    err = _update(root)
    return {"removed": name, "update_error": err, "note": "Kısıt silme geri alınamaz; gerekirse yeniden ekleyin."}
