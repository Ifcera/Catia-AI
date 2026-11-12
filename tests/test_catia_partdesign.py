"""Gerçek CATIA üzerinde Part Design araç testleri. CATIA açık değilse atlanır.

Beklenen hacimler elle hesaplanmıştır; tolerans %0.01. Yön/eksen hataları hacmi
değiştirecek şekilde seçilmiştir (ör. pattern yanlış eksende olsa delikler parçadan taşar).
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

TOL = 1e-4


def ok(r):
    assert r.ok, r.data
    return r.data


def close(a, b, tol=TOL):
    return abs(a - b) <= tol * max(abs(b), 1.0)


_N = [0]


def uniq(name: str) -> str:
    """Açık belgelerle (kullanıcının ya da asistanın) parça numarası çakışmasın diye benzersiz ad."""
    _N[0] += 1
    return f"T{os.getpid()}_{_N[0]}_{name}"


def block(t, w=50, h=30, d=10, name="Blok"):
    ok(t.call("new_part", name=uniq(name)))
    sk = ok(t.call("create_sketch", support="XY", elements=[{"type": "rectangle", "center": [0, 0], "width": w, "height": h}]))
    assert sk["fully_constrained"]
    return ok(t.call("pad", sketch=sk["sketch"], length=d))


def volume(t):
    return ok(t.call("measure", bbox=False))["volume_mm3"]


def test_acceptance_block_with_hole(msg):
    block(msg)
    ok(msg.call("hole", face={"axis": "+Z", "side": "max"}, position=[0, 0, 10], diameter=8, through_all=True))
    exp = 15000 - math.pi * 16 * 10
    chk = ok(msg.call("check_expectation", volume_mm3=exp, volume_tolerance_pct=0.01, bbox_size=[50, 30, 10]))
    assert chk["passed"], chk


def test_topology_faces_normals_and_edges(msg):
    block(msg)
    topo = ok(msg.call("list_topology", kind="both"))
    assert topo["face_count"] == 6 and topo["edge_count"] == 12
    normals = sorted(tuple(f["normal"]) for f in topo["faces"])
    assert normals == sorted([(0, 0, 1), (0, 0, -1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)])
    assert all(f["normal_confidence"] == "high" for f in topo["faces"])
    top = next(f for f in topo["faces"] if f["normal"] == [0, 0, 1])
    top_edges = [e for e in topo["edges"] if top["id"] in e["faces"]]
    assert len(top_edges) == 4 and all(len(e["faces"]) == 2 for e in topo["edges"])
    vertical = ok(msg.call("list_topology", kind="edges", edge_filter={"parallel_to": "Z"}))
    assert vertical["edge_count"] == 4


def test_fillet_vertical_edges_and_stable_ids(msg):
    block(msg)
    before = ok(msg.call("list_topology"))
    top_id = next(f["id"] for f in before["faces"] if f["normal"] == [0, 0, 1])
    r = ok(msg.call("fillet", edges={"parallel_to": "Z"}, radius=3))
    assert len(r["edges"]) == 4
    assert close(r["volume_mm3"], 15000 - 4 * (9 - math.pi * 9 / 4) * 10)
    after = ok(msg.call("list_topology", face_filter={"axis": "+Z", "side": "max"}))
    assert after["faces"][0]["id"] == top_id, "fillet sonrası üst yüzün kimliği değişmemeli"


def test_chamfer_top_edges(msg):
    block(msg)
    r = ok(msg.call("chamfer", edges={"on_face": {"axis": "+Z", "side": "max"}}, length=2, angle=45))
    # Üst 2 mm katman pahdan sonra kesik piramit: ∫0..2 (46+2t)(26+2t) dt = 2392 + 288 + 32/3
    layer = 2392 + 288 + 32 / 3
    exp = 15000 - (3000 - layer)
    assert close(r["volume_mm3"], exp), (r, exp)


def test_pocket_through_from_face_sketch(msg):
    block(msg)
    sk = ok(msg.call("create_sketch", support={"axis": "+Z", "side": "max"},
                     elements=[{"type": "circle", "center": [10, 5], "radius": 5}]))
    assert sk["local_axes"]["origin"][2] == 10 and sk["fully_constrained"]
    r = ok(msg.call("pocket", sketch=sk["sketch"], through_all=True))
    assert close(r["volume_mm3"], 15000 - math.pi * 25 * 10)


def test_zx_sketch_axes_shaft_and_groove(msg):
    ok(msg.call("new_part", name=uniq("Mil")))
    sk = ok(msg.call("create_sketch", support="ZX", elements=[
        {"type": "axis", "start": [0, 0], "end": [40, 0]},
        {"type": "rectangle", "corner": [0, 0], "width": 40, "height": 8},
    ]))
    assert sk["local_axes"]["H(u)"] == "+Z" and sk["local_axes"]["V(v)"] == "+X"
    assert sk["fully_constrained"]
    r = ok(msg.call("shaft", sketch=sk["sketch"]))
    assert close(r["volume_mm3"], math.pi * 64 * 40)
    g = ok(msg.call("create_sketch", support="ZX", elements=[
        {"type": "axis", "start": [0, 0], "end": [40, 0]},
        {"type": "rectangle", "corner": [10, 6], "width": 10, "height": 2},
    ]))
    r = ok(msg.call("groove", sketch=g["sketch"]))
    assert close(r["volume_mm3"], math.pi * 64 * 40 - math.pi * (64 - 36) * 10)
    bb = ok(msg.call("measure"))["bounding_box"]
    assert close(bb["size"][2], 40) and close(bb["size"][0], 16)


def test_rect_pattern_along_x(msg):
    block(msg)
    h = ok(msg.call("hole", face={"axis": "+Z", "side": "max"}, position=[-20, 0, 10], diameter=4, through_all=True))
    r = ok(msg.call("rect_pattern", feature=h["feature"], direction1="X", count1=5, spacing1=10))
    assert close(r["volume_mm3"], 15000 - 5 * math.pi * 4 * 10), r


def test_circ_pattern_about_z(msg):
    ok(msg.call("new_part", name=uniq("Disk")))
    sk = ok(msg.call("create_sketch", support="XY", elements=[{"type": "circle", "center": [0, 0], "radius": 30}]))
    ok(msg.call("pad", sketch=sk["sketch"], length=5))
    h = ok(msg.call("hole", face={"axis": "+Z", "side": "max"}, position=[20, 0, 5], diameter=4, through_all=True))
    r = ok(msg.call("circ_pattern", feature=h["feature"], axis="Z", count=6))
    assert close(r["volume_mm3"], math.pi * 900 * 5 - 6 * math.pi * 4 * 5), r


def test_mirror_body(msg):
    ok(msg.call("new_part", name=uniq("Ayna")))
    sk = ok(msg.call("create_sketch", support="XY", elements=[{"type": "rectangle", "corner": [5, 0], "width": 10, "height": 10}]))
    ok(msg.call("pad", sketch=sk["sketch"], length=5))
    r = ok(msg.call("mirror", plane="YZ"))
    assert close(r["volume_mm3"], 1000)
    bb = ok(msg.call("measure"))["bounding_box"]
    assert close(bb["min"][0], -15) and close(bb["max"][0], 15)


def test_profile_with_arcs_slot(msg):
    ok(msg.call("new_part", name=uniq("Kanal")))
    sk = ok(msg.call("create_sketch", support="XY", elements=[{
        "type": "profile", "id": "slot", "start": [-10, -5], "segments": [
            {"line_to": [10, -5]}, {"arc_to": [10, 5], "center": [10, 0]},
            {"line_to": [-10, 5]}, {"arc_to": [-10, -5], "center": [-10, 0]}]}]))
    assert sk["fully_constrained"], sk
    r = ok(msg.call("pad", sketch=sk["sketch"], length=4))
    assert close(r["volume_mm3"], (20 * 10 + math.pi * 25) * 4)


def test_offset_plane_and_named_plane_sketch(msg):
    ok(msg.call("new_part", name=uniq("Duzlem")))
    pl = ok(msg.call("create_plane", base="XY", offset=20))
    sk = ok(msg.call("create_sketch", support={"plane": pl["plane"]}, elements=[{"type": "circle", "radius": 5}]))
    ok(msg.call("pad", sketch=sk["sketch"], length=5))
    m = ok(msg.call("measure"))
    assert close(m["volume_mm3"], math.pi * 25 * 5)
    assert close(m["bounding_box"]["min"][2], 20)


def test_boolean_remove(msg):
    block(msg)
    ok(msg.call("new_body", name="Kesici"))
    sk = ok(msg.call("create_sketch", support="XY", body="Kesici", elements=[{"type": "circle", "radius": 5}]))
    ok(msg.call("pad", sketch=sk["sketch"], body="Kesici", length=10))
    r = ok(msg.call("boolean", body="Kesici", operation="remove"))
    assert close(r["volume_mm3"], 15000 - math.pi * 25 * 10)


def test_failed_feature_rolls_back(msg):
    block(msg)
    r = msg.call("fillet", edges={"on_face": {"axis": "+Z", "side": "max"}}, radius=50)
    assert not r.ok and r.data.get("rolled_back"), r.data
    st = ok(msg.call("get_design_state"))
    assert [f["name"] for f in st["bodies"][0]["features"]] == ["Pad.1"]
    assert st["up_to_date"]
    assert close(volume(msg), 15000)


def test_set_parameter_and_rollback_on_bad_value(msg):
    block(msg)
    r = ok(msg.call("set_parameter", name="Pad.1\\FirstLimit\\Length", value=20))
    assert r["old"] == "10mm" and close(volume(msg), 30000)
    f = ok(msg.call("fillet", edges={"parallel_to": "Z"}, radius=2))
    bad = msg.call("set_parameter", name=f"{f['feature']}\\Radius", value=40)
    assert not bad.ok, bad.data
    st = ok(msg.call("get_design_state"))
    assert st["up_to_date"], st
    assert close(volume(msg), 30000 - 4 * (4 - math.pi * 4 / 4) * 20)


def test_add_to_sketch_and_status(msg):
    ok(msg.call("new_part", name=uniq("Ekle")))
    sk = ok(msg.call("create_sketch", support="XY", elements=[{"type": "rectangle", "id": "r", "width": 40, "height": 20}]))
    rep = ok(msg.call("add_to_sketch", sketch=sk["sketch"], elements=[{"type": "circle", "id": "c", "center": [10, 0], "radius": 3}]))
    assert rep["fully_constrained"] and "c" in rep["elements"]
    r = ok(msg.call("pad", sketch=sk["sketch"], length=5))
    assert close(r["volume_mm3"], (800 - math.pi * 9) * 5)
    info = ok(msg.call("get_sketch", sketch=sk["sketch"]))
    assert all(c["status"] == "OK" for c in info["constraints"])


def test_measure_distance_and_screenshot(msg):
    block(msg)
    d = ok(msg.call("measure_distance", a={"face": {"axis": "+Z", "side": "max"}}, b={"face": {"axis": "-Z", "side": "min"}}))
    assert close(d["distance_mm"], 10)
    r = msg.call("screenshot", views=["iso", "front"], max_size=400)
    assert r.ok and len(r.images) == 2 and all(Path(p).exists() for p in r.images)


def test_save_and_step_inside_workspace_only(msg):
    block(msg, name="Kayit")
    s = ok(msg.call("save_document"))
    assert Path(s["path"]).exists()
    st = ok(msg.call("export_step", filename="kayit.stp"))
    assert st["size_bytes"] > 1000
    stl = ok(msg.call("export_stl", filename="kayit.stl"))
    assert stl["triangles"] and stl["triangles"] >= 12 and Path(stl["path"]).exists()
    bad = msg.call("save_document", filename="../disari.CATPart")
    assert not bad.ok and "dışında" in bad.data["error"]


def test_undo_last_message_closes_part(tools):
    tools.begin_message("geri alma testi")
    block(tools, name="GeriAl")
    tools.end_message()
    name = ok(tools.call("list_documents"))["active"]
    res = tools.undo_last_message()
    assert not res["errors"], res
    docs = [d["name"] for d in ok(tools.call("list_documents"))["documents"]]
    assert name not in docs


@pytest.mark.parametrize("support", ["XY", "YZ", "ZX"])
def test_origin_plane_circle_pad_volume(msg, support):
    ok(msg.call("new_part", name=uniq(f"P{support}")))
    sk = ok(msg.call("create_sketch", support=support, elements=[{"type": "circle", "center": [5, 7], "radius": 4}]))
    assert sk["fully_constrained"]
    r = ok(msg.call("pad", sketch=sk["sketch"], length=6, symmetric=True))
    assert close(r["volume_mm3"], math.pi * 16 * 6)
