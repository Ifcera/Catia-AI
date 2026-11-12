"""Agent döngüsü testleri — sahte API istemcisi ve sahte araçlarla (CATIA ve ücret gerektirmez)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from catia_aibot.agent import Agent, AgentConfig
from catia_aibot.agent.providers.claude import FALLBACK_BETA
from catia_aibot.tools import REGISTRY, ToolResult


# ------------------------------------------------------------------ sahteler
def text(t):
    return NS(type="text", text=t)


def tool_use(i, name, inp):
    return NS(type="tool_use", id=f"toolu_{i}", name=name, input=inp)


def msg(*content, stop="end_turn", usage=None):
    return NS(content=list(content), stop_reason=stop, stop_details=None,
              usage=usage or NS(input_tokens=100, output_tokens=50, cache_read_input_tokens=1000, cache_creation_input_tokens=0))


class FakeStream:
    def __init__(self, final, events):
        self.final, self.events = final, events

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.final


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(json.loads(json.dumps(
            {k: v for k, v in kw.items() if k != "messages"}, default=str)) | {"n_messages": len(kw["messages"]),
                                                                             "messages": kw["messages"]})
        final = self.responses.pop(0)
        events = [NS(type="text", text=b.text) for b in final.content if b.type == "text"]
        return FakeStream(final, events)


class FakeTools:
    registry = REGISTRY

    def __init__(self, tmp_path: Path, on_call=None):
        self.tmp = tmp_path
        self.called: list[tuple[str, dict]] = []
        self.on_call = on_call
        self.groups: list[str] = []

    def specs(self, categories=None):
        return REGISTRY.specs(categories)

    def call(self, name, args=None, /, **kw):
        args = dict(args or {}, **kw)
        self.called.append((name, args))
        if self.on_call:
            self.on_call(name, args)
        if name == "screenshot":
            p = self.tmp / "shot.jpg"
            Image.new("RGB", (40, 30), "white").save(p)
            return ToolResult({"ok": True, "images": [str(p)]}, images=[p])
        if name == "fillet":
            return ToolResult({"ok": False, "error": "update hatası", "hint": "yarıçapı küçült"})
        return ToolResult({"ok": True, "feature": f"{name}.1"})

    def run(self, fn, *a):
        return fn(NS(workspace=self.tmp), *a)

    def begin_message(self, label):
        self.groups.append(label)

    def end_message(self):
        pass

    def undo_last_message(self):
        return {"group": self.groups[-1] if self.groups else None, "undone": ["Pad.1 sil", "Part1 kapat"], "errors": []}


def make(tmp_path, responses, on_call=None, **cfg):
    tools = FakeTools(tmp_path, on_call)
    client = FakeClient(responses)
    events = []
    agent = Agent(tools, AgentConfig(**cfg), client=client, on_event=events.append)
    return agent, tools, client, events


def check_history_valid(messages):
    """Her tool_use'un hemen sonraki kullanıcı mesajında bir tool_result'ı olmalı."""
    for i, m in enumerate(messages):
        if m["role"] != "assistant":
            continue
        ids = {b.id for b in m["content"] if getattr(b, "type", None) == "tool_use"}
        if not ids:
            continue
        nxt = messages[i + 1]
        assert nxt["role"] == "user"
        got = {b["tool_use_id"] for b in nxt["content"] if isinstance(b, dict) and b.get("type") == "tool_result"}
        assert ids == got, (ids, got)


# --------------------------------------------------------------------- testler
def test_tool_loop_and_request_shape(tmp_path):
    agent, tools, client, events = make(tmp_path, [
        msg(text("Parçayı açıyorum."), tool_use(1, "new_part", {"name": "Blok"}), stop="tool_use"),
        msg(text("Tamam.")),
    ])
    r = agent.send("blok yap")
    assert r.status == "done" and "Tamam." in r.text
    assert tools.called == [("new_part", {"name": "Blok"})]
    assert [m["role"] for m in agent.messages] == ["user", "assistant", "user", "assistant"]
    check_history_valid(agent.messages)
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and FALLBACK_BETA in kw["betas"]
    assert kw["thinking"]["type"] == "adaptive" and kw["output_config"]["effort"] == "high"
    assert kw["cache_control"] == {"type": "ephemeral"}
    assert all(t["eager_input_streaming"] for t in kw["tools"])
    assert any(t["name"] == "present_plan" for t in kw["tools"])
    # ilk kullanıcı mesajı ortam bilgisini taşır, sistem prompt'u sabittir
    first = agent.messages[0]["content"]
    assert "Çalışma klasörü" in first[0]["text"]
    assert {e.kind for e in events} >= {"turn_start", "text_delta", "tool_call", "tool_result", "done", "usage"}


def test_prefix_is_stable_across_requests(tmp_path):
    agent, _, client, _ = make(tmp_path, [
        msg(tool_use(1, "measure", {}), stop="tool_use"), msg(text("bitti")), msg(text("ikinci"))])
    agent.send("ölç")
    agent.send("tekrar")
    a, b, c = client.calls
    assert a["tools"] == b["tools"] == c["tools"]
    assert a["system"] == b["system"] == c["system"]
    # geçmiş yalnızca eklenerek büyür: önceki istek, sonrakinin önekidir
    assert a["n_messages"] < b["n_messages"] < c["n_messages"]


def test_invalid_input_is_not_executed(tmp_path):
    agent, tools, _, _ = make(tmp_path, [
        msg(tool_use(1, "pad", {"length": "on"}), stop="tool_use"),  # 'sketch' eksik, length tipi yanlış
        msg(text("düzelttim")),
    ])
    agent.send("pad")
    assert tools.called == []
    res = agent.messages[2]["content"][0]
    assert res["is_error"] is True and "şemaya uymuyor" in res["content"][0]["text"]


def test_tool_error_marked_is_error(tmp_path):
    agent, _, _, _ = make(tmp_path, [
        msg(tool_use(1, "fillet", {"edges": "E1", "radius": 50}), stop="tool_use"), msg(text("küçülttüm"))])
    agent.send("fillet")
    res = agent.messages[2]["content"][0]
    assert res["is_error"] is True and "update hatası" in res["content"][0]["text"]


def test_present_plan_ends_turn(tmp_path):
    plan = {"summary": "mancınık", "parts": [{"name": "Taban", "method": "pad", "dimensions": "200x100x10"}]}
    agent, tools, client, events = make(tmp_path, [msg(tool_use(1, "present_plan", plan), stop="tool_use")])
    r = agent.send("mancınık yap")
    assert r.status == "plan" and r.plan == plan
    assert len(client.calls) == 1, "plan sonrası model tekrar çağrılmamalı"
    assert any(e.kind == "plan" for e in events)
    check_history_valid(agent.messages)


def test_stop_during_tools_keeps_history_valid(tmp_path):
    holder = {}

    def on_call(name, args):
        if name == "new_part":
            holder["agent"].stop()

    agent, tools, client, events = make(tmp_path, [
        msg(tool_use(1, "new_part", {"name": "A"}), tool_use(2, "measure", {}), stop="tool_use")], on_call=on_call)
    holder["agent"] = agent
    r = agent.send("yap")
    assert r.status == "stopped"
    assert [n for n, _ in tools.called] == ["new_part"]
    check_history_valid(agent.messages)
    assert "durdurdu" in agent.messages[-1]["content"][1]["content"][0]["text"]
    assert len(client.calls) == 1


def test_refusal_not_appended(tmp_path):
    agent, tools, _, _ = make(tmp_path, [msg(tool_use(1, "new_part", {"name": "X"}), stop="refusal")])
    r = agent.send("x")
    assert r.status == "error" and tools.called == []
    assert [m["role"] for m in agent.messages] == ["user"]


def test_max_tokens_with_tool_use_not_executed(tmp_path):
    agent, tools, _, _ = make(tmp_path, [msg(tool_use(1, "new_part", {"name": "X"}), stop="max_tokens")])
    r = agent.send("x")
    assert r.status == "error" and tools.called == []


def test_screenshot_image_attached_and_capped(tmp_path):
    agent, tools, _, _ = make(tmp_path, [
        msg(tool_use(1, "screenshot", {"views": ["iso"], "max_size": 1280}), stop="tool_use"), msg(text("güzel"))],
        image_max_size=600)
    r = agent.send("göster")
    assert tools.called[0][1]["max_size"] == 600
    blocks = agent.messages[2]["content"][0]["content"]
    assert blocks[1]["type"] == "image" and blocks[1]["source"]["media_type"] == "image/jpeg"
    assert r.images and Path(r.images[0]).exists()


def test_user_attachments_and_undo_note(tmp_path):
    p = tmp_path / "view.jpg"
    Image.new("RGB", (10, 10)).save(p)
    agent, _, _, _ = make(tmp_path, [msg(text("ok")), msg(text("ok2"))])
    agent.send("bunu incele", images=[str(p)], context="Pad.1 (PlanarFace)")
    c = agent.messages[0]["content"]
    assert any(b.get("type") == "image" for b in c)
    assert "Pad.1" in c[0]["text"]
    res = agent.undo_last_message()
    assert res["undone"]
    agent.send("devam")
    note = agent.messages[2]["content"][0]["text"]
    assert "geri aldı" in note and "Çalışma klasörü" not in note


def test_usage_cost(tmp_path):
    agent, _, _, _ = make(tmp_path, [msg(text("ok"), usage=NS(input_tokens=1_000_000, output_tokens=100_000,
                                                               cache_read_input_tokens=2_000_000, cache_creation_input_tokens=0))])
    agent.send("x")
    u = agent.usage.as_dict("claude-opus-5-5")
    assert u["cost_usd"] == pytest.approx(4.0 + 2.0 + 0.4)


def test_fallbacks_can_be_disabled(tmp_path):
    agent, _, client, _ = make(tmp_path, [msg(text("ok"))], use_fallbacks=False)
    agent.send("x")
    assert "fallbacks" not in client.calls[0] and "betas" not in client.calls[0]
