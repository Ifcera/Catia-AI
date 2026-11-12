"""Gerçek CATIA üzerinde Assembly Design araç testleri (CATIA kapalıysa atlanır)."""
from __future__ import annotations

import math

import pytest

from test_catia_partdesign import close, ok, uniq


def make_parts(t):
    """Delikli blok (50x30x10, Ø8 orta delik) ve pim (Ø8 x 30, z=0..30); ikisi de kaydedilir."""
    blok = uniq("Blok")
    ok(t.call("new_part", name=blok))
    sk = ok(t.call("create_sketch", support="XY", elements=[{"type": "rectangle", "width": 50, "height": 30}]))
    ok(t.call("pad", sketch=sk["sketch"], length=10))
    ok(t.call("hole", face={"axis": "+Z", "side": "max"}, position=[0, 0, 10], diameter=8, through_all=True))
    bdoc = ok(t.call("save_document"))
    pim = uniq("Pim")
    ok(t.call("new_part", name=pim))
    sk = ok(t.call("create_sketch", support="XY", elements=[{"type": "circle", "radius": 4}]))
    ok(t.call("pad", sketch=sk["sketch"], length=30))
    pdoc = ok(t.call("save_document"))
    return bdoc, pdoc


def assemble(t):
    bdoc, pdoc = make_parts(t)
    ok(t.call("new_product", name=uniq("Montaj")))
    b = ok(t.call("add_component", source=bdoc["path"].split("\\")[-1], instance_name="Blok.1"))
    p = ok(t.call("add_component", source=pdoc["document"], instance_name="Pim.1"))  # açık belgeden
    return b, p


def test_add_components_and_list(msg):
    b, p = assemble(msg)
    assert b["instance"] == "Blok.1" and p["instance"] == "Pim.1" and p["count"] == 2
    lst = ok(msg.call("list_components"))
    assert [c["instance"] for c in lst["components"]] == ["Blok.1", "Pim.1"]
    assert lst["components"][1]["origin"] == [0, 0, 0]


def test_constraints_seat_pin_in_hole_and_check(msg):
    assemble(msg)
    ok(msg.call("set_component_position", instance="Pim.1", origin=[60, 40, 25], rotations=[{"axis": "X", "angle": 20}]))
    ok(msg.call("add_assembly_constraint", type="fix", a={"instance": "Blok.1", "element": "component"}))
    c = ok(msg.call("add_assembly_constraint", type="coincidence",
                    a={"instance": "Pim.1", "element": {"type": "cylindrical"}},
                    b={"instance": "Blok.1", "element": {"type": "cylindrical"}}))
    assert c["constraint"]["status"] == "OK", c
    c = ok(msg.call("add_assembly_constraint", type="contact",
                    a={"instance": "Pim.1", "element": {"axis": "Z", "side": "min"}},
                    b={"instance": "Blok.1", "element": {"axis": "Z", "side": "max"}}))
    pim = next(m for m in c["moved"] if m["instance"] == "Pim.1")
    assert pim["origin"] == [0, 0, 10] and pim["z_axis"] in ([0, 0, 1], [0, 0, -1])
    chk = ok(msg.call("check_assembly", expect_contacts=[["Blok.1", "Pim.1"]], fit_within=[60, 40, 45]))
    assert chk["passed"], chk
    bb = chk["bounding_box"]  # blok z 0..10, pim z 10..40
    assert bb["size"] == [50, 30, 40] and bb["min"] == [-25, -15, 0] and chk["fits_within"]["pass"]
    small = ok(msg.call("check_assembly", fit_within=[50, 30, 30]))
    assert not small["passed"] and not small["fits_within"]["pass"]
    assert chk["interference_count"] == 0 and chk["clash"]["method"] == "CATIA clash"
    assert close(chk["volume_mm3"], 15000 - math.pi * 16 * 10 + math.pi * 16 * 30, 1e-3)


def test_interference_detected(msg):
    assemble(msg)
    ok(msg.call("set_component_position", instance="Pim.1", origin=[10, 0, 0]))
    chk = ok(msg.call("check_assembly"))
    assert not chk["passed"] and chk["interference_count"] == 1
    c = chk["clash"]["conflicts"][0]
    assert c["type"] == "çakışma" and c["value_mm"] < -5


def test_angle_constraint_rotates(msg):
    assemble(msg)
    ok(msg.call("add_assembly_constraint", type="fix", a={"instance": "Blok.1", "element": "component"}))
    r = ok(msg.call("add_assembly_constraint", type="angle", value=30,
                    a={"instance": "Blok.1", "element": "YZ"}, b={"instance": "Pim.1", "element": "YZ"}))
    m = next(x for x in r["moved"] if x["instance"] == "Pim.1")
    assert abs(abs(m["x_axis"][0]) - math.cos(math.radians(30))) < 1e-3


def test_failed_constraint_rolls_back_positions(msg):
    assemble(msg)
    ok(msg.call("add_assembly_constraint", type="fix", a={"instance": "Blok.1", "element": "component"}))
    ok(msg.call("set_component_position", instance="Pim.1", origin=[60, 0, 0]))
    before = ok(msg.call("list_components"))
    # aynı iki düzlem arasında çelişen iki mesafe: ikincisi çözülemez
    ok(msg.call("add_assembly_constraint", type="offset", value=5,
                a={"instance": "Blok.1", "element": "XY"}, b={"instance": "Pim.1", "element": "XY"}))
    mid = ok(msg.call("list_components"))
    bad = msg.call("add_assembly_constraint", type="offset", value=40,
                   a={"instance": "Blok.1", "element": "XY"}, b={"instance": "Pim.1", "element": "XY"})
    assert not bad.ok and bad.data.get("rolled_back"), bad.data
    after = ok(msg.call("list_components"))
    assert len(after["constraints"]) == len(mid["constraints"])
    assert after["components"][1]["origin"] == mid["components"][1]["origin"]
    assert before["components"][1]["origin"] != mid["components"][1]["origin"]  # ilk offset gerçekten taşıdı


def test_same_part_twice(msg):
    bdoc, _ = make_parts(msg)
    ok(msg.call("new_product", name=uniq("Cift")))
    a = ok(msg.call("add_component", source=bdoc["document"]))
    b = ok(msg.call("add_component", source=bdoc["document"], origin=[0, 0, 20]))
    assert a["instance"] != b["instance"] and b["position"]["origin"] == [0, 0, 20]


def test_product_tool_rejects_part_document(msg):
    make_parts(msg)
    r = msg.call("add_component", source="x.CATPart")
    assert not r.ok and "CATProduct" in r.data["error"]
