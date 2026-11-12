"""OpenAI ve Gemini sağlayıcıları — sahte istemcilerle (ağ ve ücret yok)."""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

from google.genai import errors, types
from PIL import Image

from catia_aibot.agent import Agent, AgentConfig
from catia_aibot.agent.providers import GeminiProvider, OpenAIProvider
from catia_aibot.tools import REGISTRY

from test_agent_offline import FakeTools


# ============================================================== OpenAI
class Item(NS):
    def model_dump(self, **kw):
        def conv(v):
            if isinstance(v, NS):
                return {k: conv(x) for k, x in vars(v).items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v
        return conv(self)


def o_text(t):
    return Item(type="message", role="assistant", content=[NS(type="output_text", text=t)])


def o_call(cid, name, args):
    return Item(type="function_call", call_id=cid, name=name, arguments=args if isinstance(args, str) else json.dumps(args))


def o_resp(*items, status="completed", reason=None):
    return NS(output=list(items), status=status, incomplete_details=NS(reason=reason) if reason else None,
              usage=NS(input_tokens=1000, output_tokens=200, input_tokens_details=NS(cached_tokens=600)))


class OStream:
    def __init__(self, resp):
        self.resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for it in self.resp.output:
            if it.type == "message" and it.content[0].type == "output_text":
                yield NS(type="response.output_text.delta", delta=it.content[0].text)

    def get_final_response(self):
        return self.resp


def openai_agent(tmp_path, resps, **cfg):
    fake = NS(calls=[], queue=list(resps))

    def stream(**kw):
        fake.calls.append(kw | {"input": list(kw["input"])})
        return OStream(fake.queue.pop(0))

    fake.responses = NS(stream=stream)
    tools = FakeTools(tmp_path)
    prov = OpenAIProvider(None, "test", client=fake)
    events = []
    return Agent(tools, AgentConfig(provider="openai", **cfg), provider=prov, on_event=events.append), tools, fake, events


def test_openai_tool_loop_request_shape_and_images(tmp_path):
    agent, tools, fake, events = openai_agent(tmp_path, [
        o_resp(o_text("Başlıyorum"), o_call("c1", "new_part", {"name": "Blok"}), o_call("c2", "screenshot", {"views": ["iso"]})),
        o_resp(o_text("Bitti.")),
    ])
    r = agent.send("blok yap")
    assert r.status == "done" and [n for n, _ in tools.called] == ["new_part", "screenshot"]
    kw = fake.calls[0]
    assert kw["store"] is False and "reasoning.encrypted_content" in kw["include"]
    assert kw["reasoning"] == {"effort": "high"} and kw["instructions"]
    assert all(t["type"] == "function" and "parameters" in t for t in kw["tools"])
    hist = agent.messages
    outs = [h for h in hist if isinstance(h, dict) and h.get("type") == "function_call_output"]
    assert [o["call_id"] for o in outs] == ["c1", "c2"]
    assert isinstance(outs[0]["output"], str)
    assert outs[1]["output"][1]["type"] == "input_image" and outs[1]["output"][1]["image_url"].startswith("data:image/jpeg;base64,")
    # model çıktı öğeleri değiştirilmeden geçmişe eklenir
    assert any(h.get("type") == "function_call" and h.get("call_id") == "c1" for h in hist if isinstance(h, dict))
    assert agent.usage.cache_read_tokens == 1200 and agent.usage.input_tokens == 800
    assert agent.usage.as_dict(agent.model)["cost_usd"] is None


def test_openai_unparseable_arguments_rejected(tmp_path):
    agent, tools, _, _ = openai_agent(tmp_path, [o_resp(o_call("c1", "pad", "{bozuk")), o_resp(o_text("ok"))])
    agent.send("pad")
    assert tools.called == []
    out = [h for h in agent.messages if isinstance(h, dict) and h.get("type") == "function_call_output"][0]
    assert "şemaya uymuyor" in out["output"]


def test_openai_refusal_and_max_tokens(tmp_path):
    ref = Item(type="message", role="assistant", content=[NS(type="refusal", refusal="yapamam")])
    agent, tools, _, _ = openai_agent(tmp_path, [o_resp(ref)])
    assert agent.send("x").status == "error" and len(agent.messages) == 1
    agent2, tools2, _, _ = openai_agent(tmp_path, [o_resp(o_call("c1", "new_part", {"name": "A"}), status="incomplete", reason="max_output_tokens")])
    assert agent2.send("x").status == "error" and tools2.called == []


# ============================================================== Gemini
def g_resp(*parts, finish=types.FinishReason.STOP):
    content = types.Content(role="model", parts=list(parts))
    return NS(candidates=[NS(finish_reason=finish, content=content)], prompt_feedback=None,
              usage_metadata=NS(prompt_token_count=1000, candidates_token_count=100, thoughts_token_count=50, cached_content_token_count=400))


def g_call(cid, name, args, sig=None):
    return types.Part(function_call=types.FunctionCall(id=cid, name=name, args=args), thought_signature=sig)


def gemini_agent(tmp_path, resps, raise_first=None):
    fake = NS(calls=[], queue=list(resps), raised=False)

    def generate_content(model, contents, config):
        fake.calls.append({"model": model, "contents": list(contents), "config": config})
        if raise_first and not fake.raised:
            fake.raised = True
            raise raise_first
        return fake.queue.pop(0)

    fake.models = NS(generate_content=generate_content)
    tools = FakeTools(tmp_path)
    prov = GeminiProvider(None, "test", client=fake)
    events = []
    return Agent(tools, AgentConfig(provider="gemini"), provider=prov, on_event=events.append), tools, fake, events


def test_gemini_tool_loop_preserves_signature_and_images(tmp_path):
    first = g_resp(types.Part.from_text(text="Başlıyorum"), g_call("id1", "new_part", {"name": "Blok"}, sig=b"imza"),
                   g_call("id2", "screenshot", {"views": ["iso"]}))
    agent, tools, fake, _ = gemini_agent(tmp_path, [first, g_resp(types.Part.from_text(text="Bitti."))])
    r = agent.send("blok yap")
    assert r.status == "done" and [n for n, _ in tools.called] == ["new_part", "screenshot"]
    cfg = fake.calls[0]["config"]
    assert cfg.automatic_function_calling.disable is True
    assert cfg.thinking_config.thinking_level.name == "HIGH" if hasattr(cfg.thinking_config.thinking_level, "name") else True
    decl = cfg.tools[0].function_declarations
    assert len(decl) == len(REGISTRY.tools) + 1 and decl[0].parameters_json_schema
    hist = agent.messages
    assert hist[1] is first.candidates[0].content and hist[1].parts[1].thought_signature == b"imza"
    fr = [p.function_response for p in hist[2].parts]
    assert [f.id for f in fr] == ["id1", "id2"] and fr[0].response["output"]["ok"] is True
    assert fr[1].parts and fr[1].parts[0].inline_data.mime_type == "image/jpeg"
    assert agent.usage.output_tokens == 300 and agent.usage.cache_read_tokens == 800


def test_gemini_retries_without_thinking_when_unsupported(tmp_path):
    err = errors.ClientError(400, {"error": {"code": 400, "message": "thinking_level is not supported for this model", "status": "INVALID_ARGUMENT"}})
    agent, _, fake, _ = gemini_agent(tmp_path, [g_resp(types.Part.from_text(text="ok"))], raise_first=err)
    assert agent.send("x").status == "done"
    assert fake.calls[0]["config"].thinking_config is not None and fake.calls[1]["config"].thinking_config is None


def test_gemini_safety_and_malformed(tmp_path):
    agent, tools, _, _ = gemini_agent(tmp_path, [g_resp(g_call("a", "new_part", {"name": "X"}), finish=types.FinishReason.SAFETY)])
    assert agent.send("x").status == "error" and tools.called == [] and len(agent.messages) == 1
    agent2, tools2, fake2, _ = gemini_agent(tmp_path, [g_resp(finish=types.FinishReason.MALFORMED_FUNCTION_CALL),
                                                       g_resp(types.Part.from_text(text="düzelttim"))])
    assert agent2.send("x").status == "done" and len(fake2.calls) == 2


def test_all_tool_schemas_convert_for_every_provider():
    specs = REGISTRY.specs()
    assert len(OpenAIProvider.convert_tools(specs)) == len(specs)
    for s in specs:
        types.FunctionDeclaration(name=s["name"], description=s["description"], parameters_json_schema=s["input_schema"])


def test_gemini_overload_ends_turn_with_clear_message(tmp_path):
    err = errors.ServerError(503, {"error": {"code": 503, "message": "This model is currently experiencing high demand.", "status": "UNAVAILABLE"}})
    agent, tools, fake, events = gemini_agent(tmp_path, [], raise_first=err)
    r = agent.send("x")
    assert r.status == "error" and "yoğun (503" in r.error and tools.called == []
    assert any(e.kind == "status" for e in events)
    # geçmiş geçerli kalır: yalnızca kullanıcı mesajı
    assert len(agent.messages) == 1


def test_gemini_client_has_timeout_and_retry():
    from catia_aibot.agent.providers.base import REQUEST_TIMEOUT_S
    c = GeminiProvider(None, "test").make_client()
    opts = c._api_client._http_options
    assert opts.timeout == int(REQUEST_TIMEOUT_S * 1000)
    assert opts.retry_options.attempts == 3 and 503 in opts.retry_options.http_status_codes
