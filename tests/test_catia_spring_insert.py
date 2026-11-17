"""Yay, süpürme, dişli delik, tırtıl ve malzeme araçları — gerçek CATIA (kapalıysa atlanır)."""
from __future__ import annotations

import math
from pathlib import Path

from test_catia_partdesign import block, close, ok, uniq


def test_helix_length(msg):
    ok(msg.call("new_part", name=uniq("Helix")))
    h = ok(msg.call("create_helix", radius=5, pitch=3, height=24))
    assert close(h["length_mm"], 8 * math.hypot(2 * math.pi * 5, 3), 1e-5)
    assert h["start_point"] == [5, 0, 0]


def test_spring_closed_ground(msg):
    ok(msg.call("new_part", name=uniq("Yay")))
    r = ok(msg.call("spring", mean_diameter=10, wire_diameter=1, free_length=20, active_coils=5))
    assert close(r["volume_before_grinding_mm3"], r["expected_volume_before_grinding_mm3"], 2e-3), r
    assert r["volume_mm3"] < r["volume_before_grinding_mm3"]
    assert close(r["pitch_active"], 18 / 5) and r["outer_diameter"] == 11
    bb = ok(msg.call("measure"))["bounding_box"]
    assert abs(bb["size"][2] - 20) < 0.02 and abs(bb["min"][2]) < 0.02  # taşlanmış uçlar: 0..20
    assert abs(bb["size"][0] - 11) < 0.1 and abs(bb["size"][1] - 11) < 0.1


def test_spring_open_left_handed(msg):
    ok(msg.call("new_part", name=uniq("YaySol")))
    r = ok(msg.call("spring", outer_diameter=8, wire_diameter=0.8, free_length=15, pitch=2.5, end_type="open",
                    left_handed=True))
    assert close(r["volume_mm3"], r["expected_volume_before_grinding_mm3"], 2e-3), r
    bb = ok(msg.call("measure"))["bounding_box"]
    assert abs(bb["size"][2] - 15) < 0.05


def test_spring_rejects_overlapping_coils(msg):
    ok(msg.call("new_part", name=uniq("YayHata")))
    bad = msg.call("spring", mean_diameter=10, wire_diameter=2, free_length=12, active_coils=10)
    assert not bad.ok and "hatve" in bad.data["error"]
    st = ok(msg.call("get_design_state"))
    assert st["bodies"][0]["features"] == []


def test_sweep_square_profile_along_helix(msg):
    ok(msg.call("new_part", name=uniq("Kare")))
    h = ok(msg.call("create_helix", radius=6, pitch=4, turns=3))
    sk = ok(msg.call("create_sketch", support={"normal_to": h["helix"]},
                     elements=[{"type": "rectangle", "center": [0, 0], "width": 1, "height": 1}]))
    assert sk["fully_constrained"] and sk["path_point"] == [6, 0, 0]
    r = ok(msg.call("sweep", profile=sk["sketch"], path=h["helix"]))
    assert close(r["volume_mm3"], 1 * h["length_mm"], 2e-3), r


def test_threaded_hole_m2(msg):
    ok(msg.call("new_part", name=uniq("DisliDelik")))
    sk = ok(msg.call("create_sketch", support="XY", elements=[{"type": "circle", "radius": 1.8}]))
    ok(msg.call("pad", sketch=sk["sketch"], length=4))
    r = ok(msg.call("hole", face={"axis": "+Z", "side": "max"}, position=[0, 0, 4], through_all=True,
                    thread={"size": "M2"}))
    assert r["thread"]["pitch_mm"] == 0.4 and r["thread"]["tap_drill_mm"] == 1.6
    assert close(r["volume_mm3"], math.pi * (1.8 ** 2 - 0.8 ** 2) * 4)
    params = ok(msg.call("list_parameters", filter="Hole"))["parameters"]
    assert any("Thread" in p["name"] for p in params), params


def test_knurl_straight_and_cross(msg):
    for style in ("straight", "cross"):
        ok(msg.call("new_part", name=uniq(f"Tirtil_{style}")))
        sk = ok(msg.call("create_sketch", support="XY", elements=[{"type": "circle", "radius": 1.8}]))
        v0 = ok(msg.call("pad", sketch=sk["sketch"], length=4))["volume_mm3"]
        r = ok(msg.call("knurl", diameter=3.6, start=0.5, end=3.5, count=12, style=style, groove_radius=0.15))
        assert r["volume_mm3"] < v0 and "warning" not in r, r
        assert len(r["features"]) == (2 if style == "straight" else 4)


def test_apply_material_brass(msg):
    block(msg)
    r = ok(msg.call("apply_material", material="pirinç"))
    assert r["material"] == "Brass" and 8000 < r["density_kg_m3"] < 8800
    assert close(r["mass_g"], 15000e-9 * r["density_kg_m3"] * 1000, 1e-3)
    bad = msg.call("apply_material", material="kryptonit")
    assert not bad.ok and "katalogda yok" in bad.data["error"]


def test_full_m2_heat_set_insert_and_stl(msg):
    ok(msg.call("new_part", name=uniq("InsertM2")))
    sk = ok(msg.call("create_sketch", support="ZX", elements=[
        {"type": "axis", "start": [0, 0], "end": [4, 0]},
        {"type": "profile", "start": [0, 0], "segments": [
            {"line_to": [0, 1.6]}, {"line_to": [3.6, 1.8]}, {"line_to": [4, 1.8]}, {"line_to": [4, 0]}]}]))
    ok(msg.call("shaft", sketch=sk["sketch"]))
    ok(msg.call("hole", face={"axis": "+Z", "side": "max"}, position=[0, 0, 4], through_all=True, thread={"size": "M2"}))
    k = ok(msg.call("knurl", diameter=3.6, start=1.0, end=3.4, count=10, style="cross"))
    m = ok(msg.call("apply_material", material="Brass"))
    assert k["volume_mm3"] > 10 and m["mass_g"] > 0
    stl = ok(msg.call("export_stl", filename="insert_m2.stl"))
    assert stl["triangles"] > 500 and Path(stl["path"]).exists()


def test_edge_lengths_are_mm(msg):
    block(msg)
    e = ok(msg.call("list_topology", kind="edges", edge_filter={"parallel_to": "X"}))
    assert all(abs(x["length"] - 50) < 1e-6 for x in e["edges"]), e["edges"][:2]


def test_model_thread_internal_and_external(msg):
    """Gerçek diş: kozmetik dişli kör delikten okunur; çekme yönüyle profil burulmaz, hacim Pappus ile eşleşir."""
    ok(msg.call("new_part", name=uniq("GercekDis")))
    ok(msg.call("pad", profile={"support": "XY", "elements": [{"type": "rectangle", "width": 60, "height": 40}]}, length=30))
    ok(msg.call("hole", face={"axis": "+Z", "side": "max"}, position=[-12, 0, 30], depth=14,
                thread={"size": "M20", "pitch": 1.5}, name="H20"))
    r = ok(msg.call("model_thread", hole="H20"))
    assert r["thread"]["pitch"] == 1.5 and r["thread"]["length"] == 12.5, r
    assert close(r["volume_change_mm3"], r["expected_volume_change_mm3"], 0.005), r
    ok(msg.call("shaft", profile={"support": "ZX", "elements": [
        {"type": "axis", "start": [0, 15], "end": [30, 15]}, {"type": "rectangle", "corner": [30, 15], "width": 20, "height": 4}]}))
    e = ok(msg.call("model_thread", size="M8", center=[15, 0, 50], axis=[0, 0, -1], length=10, external=True,
                    expect={"volume_mm3": 1}))
    assert close(e["volume_change_mm3"], e["expected_volume_change_mm3"], 0.005), e
    assert e["check"]["passed"] is False  # expect desteği çalışıyor
