"""Tur sayısını azaltan birleşik kullanımlar — gerçek CATIA (kapalıysa atlanır)."""
from __future__ import annotations

import math
from pathlib import Path

from test_catia_partdesign import close, ok, uniq


def test_pad_with_inline_profile_and_expect(msg):
    ok(msg.call("new_part", name=uniq("Tek")))
    r = ok(msg.call("pad", profile={"support": "XY", "elements": [{"type": "rectangle", "width": 50, "height": 30}]},
                    length=10, expect={"volume_mm3": 15000, "bbox_size": [50, 30, 10]}))
    assert r["profile_sketch"]["fully_constrained"] and r["check"] == {"passed": True}
    bad = ok(msg.call("pocket", profile={"support": {"axis": "+Z", "side": "max"},
                                         "elements": [{"type": "circle", "radius": 5}]},
                      through_all=True, expect={"volume_mm3": 99999}))
    assert bad["check"]["passed"] is False and bad["check"]["failed"][0]["check"] == "volume_mm3"
    assert close(bad["volume_mm3"], 15000 - math.pi * 25 * 10)  # özellik yine de oluştu, geri alınmadı


def test_hole_positions_in_one_call(msg):
    ok(msg.call("new_part", name=uniq("Dort")))
    ok(msg.call("pad", profile={"support": "XY", "elements": [{"type": "rectangle", "width": 80, "height": 40}]}, length=8))
    pos = [[x, y, 8] for x in (-30, 30) for y in (-12, 12)]
    r = ok(msg.call("hole", face={"axis": "+Z", "side": "max"}, positions=pos, diameter=5, through_all=True,
                    expect={"volume_mm3": 80 * 40 * 8 - 4 * math.pi * 6.25 * 8}))
    assert len(r["features"]) == 4 and r["check"]["passed"], r


def test_shaft_inline_profile_and_save_export(msg):
    ok(msg.call("new_part", name=uniq("MilTek")))
    r = ok(msg.call("shaft", profile={"support": "ZX", "elements": [
        {"type": "axis", "start": [0, 0], "end": [30, 0]},
        {"type": "rectangle", "corner": [0, 0], "width": 30, "height": 5}]},
        expect={"volume_mm3": math.pi * 25 * 30, "tol_pct": 0.1}))
    assert r["check"]["passed"], r
    sv = ok(msg.call("save_document", export=["stl", "step"]))
    assert Path(sv["stl"]["path"]).exists() and sv["stl"]["triangles"] > 10
    assert Path(sv["step"]["path"]).exists()


def test_blind_hole_depth_and_thread(msg):
    """Kör delikte depth uygulanır (R20 AddNewHoleFromSketch argümanı yok sayıyordu, 10 mm kalıyordu);
    diş boyu delikten uzun/eşitse kısaltılır, ince hatveli büyük diş (M20x1.5) update geçer."""
    ok(msg.call("new_part", name=uniq("KorDelik")))
    ok(msg.call("pad", profile={"support": "XY", "elements": [{"type": "rectangle", "width": 80, "height": 40}]}, length=30))
    top = {"axis": "+Z", "side": "max"}
    r = ok(msg.call("hole", face=top, position=[-20, 0, 30], diameter=8, depth=14))
    assert close(r["volume_change_mm3"], -math.pi * 16 * 14, tol=0.03), r  # 10 mm olsaydı ~-503
    ok(msg.call("hole", face=top, position=[15, 0, 30], depth=14, thread={"size": "M20", "pitch": 1.5}))
    params = {p["name"].split("\\")[-1]: p["value"] for p in
              ok(msg.call("list_parameters", filter="Hole.2"))["parameters"]}
    assert params["Depth"] == "14mm" and params["Tap depth"].replace(",", ".") == "12.5mm", params
