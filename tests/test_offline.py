"""CATIA gerektirmeyen testler: ayrıştırma, günlük, şemalar, yol koruması."""
from __future__ import annotations

import jsonschema
import pytest

from catia_aibot.catia.errors import ToolError
from catia_aibot.catia.journal import Journal
from catia_aibot.catia.session import CatiaSession
from catia_aibot.catia.topology import brep_key, face_core, source_features
from catia_aibot.tools import REGISTRY

FACE = "Selection_RSur:(Face:(Brp:(Pad.1;0:(Brp:(Sketch.1;4)));None:();Cf11:());EdgeFillet.1_ResultOUT;Z0;G3563)"
EDGE = ("Selection_REdge:(Edge:(Face:(Brp:(Pad.1;0:(Brp:(Sketch.1;4)));None:();Cf11:());"
        "Face:(Brp:(Pad.1;2);None:();Cf11:());None:(Limits1:();Limits2:());Cf11:());Pad.1_ResultOUT;Z0;G3563)")


def test_brep_key_strips_selection_and_result_suffix():
    assert brep_key(FACE) == "RSur:(Face:(Brp:(Pad.1;0:(Brp:(Sketch.1;4)));None:();Cf11:())"
    # Aynı yüzey farklı "son feature" bağlamında aynı anahtarı vermeli
    assert brep_key(FACE.replace("EdgeFillet.1", "Pocket.3")) == brep_key(FACE)


def test_source_features_and_edge_adjacency():
    k = brep_key(FACE)
    assert source_features(k) == ["Pad.1", "Sketch.1"]
    top = "RSur:(Face:(Brp:(Pad.1;2);None:();Cf11:())"
    assert face_core(top) in brep_key(EDGE)
    assert face_core(k) in brep_key(EDGE)


def test_journal_savepoint_and_undo_order():
    log = []
    j = Journal()
    j.begin("m1")
    j.record("a", lambda: log.append("a"))
    sp = j.savepoint()
    j.record("b", lambda: log.append("b"))
    j.record("c", lambda: log.append("c"))
    assert j.rollback_to(sp) == ["c", "b"]
    j.end()
    j.begin("m2")
    j.record("d", lambda: log.append("d"))
    j.end()
    label, undone, errors = j.undo_last()
    assert (label, undone, errors) == ("m2", ["d"], [])
    label, undone, _ = j.undo_last()
    assert label == "m1" and undone == ["a"]
    assert log == ["c", "b", "d", "a"]
    assert not j.can_undo()


def test_journal_continues_after_failing_undo():
    j = Journal()
    j.begin("m")
    j.record("ok", lambda: None)
    j.record("bad", lambda: 1 / 0)
    _, undone, errors = j.undo_last()
    assert undone == ["ok"] and len(errors) == 1


def test_all_tool_schemas_valid():
    assert len(REGISTRY.tools) >= 30
    for t in REGISTRY.tools.values():
        jsonschema.Draft202012Validator.check_schema(t.schema)
        assert t.description, t.name


def test_required_tools_present():
    need = {"new_part", "new_product", "open_document", "save_document", "export_step", "create_sketch", "pad", "pocket",
            "shaft", "groove", "hole", "fillet", "chamfer", "rect_pattern", "circ_pattern", "mirror", "set_parameter",
            "get_design_state", "measure", "list_topology", "check_expectation", "screenshot", "get_selection"}
    assert need <= set(REGISTRY.tools)


def test_workspace_path_guard(tmp_path):
    s = CatiaSession(tmp_path)
    assert s.resolve_path("a/b.CATPart").is_relative_to(tmp_path)
    assert s.resolve_path("x.txt", suffixes=(".stp",)).suffix == ".stp"
    with pytest.raises(ToolError):
        s.resolve_path("../disari.CATPart")
    with pytest.raises(ToolError):
        s.resolve_path("C:/Windows/kotu.CATPart")
    with pytest.raises(ToolError):
        CatiaSession(None).resolve_path("x.CATPart")
