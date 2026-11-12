"""MCP sunucusu (gerçek MCP istemcisiyle, HTTP) ve Claude Code köprüsü (olay ayrıştırma, komut satırı).

Claude Code süreci başlatılmaz; ağ yalnızca 127.0.0.1.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from catia_aibot.agent.claude_code import ClaudeCodeAgent
from catia_aibot.mcpserver import CatiaMcpServer
from catia_aibot.tools import REGISTRY

from test_agent_offline import FakeTools


@pytest.fixture
def server(tmp_path):
    events = []
    srv = CatiaMcpServer(FakeTools(tmp_path), on_event=lambda k, d: events.append((k, d)), image_max_size=500)
    srv.start()
    srv.events = events
    yield srv
    srv.stop()


async def _session_call(srv, fn):
    client = create_mcp_http_client(headers={"Authorization": f"Bearer {srv.token}"})
    async with client:
        async with streamable_http_client(srv.url, http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as s:
                await s.initialize()
                return await fn(s)


def test_mcp_lists_all_tools_with_schemas(server):
    res = asyncio.run(_session_call(server, lambda s: s.list_tools()))
    names = {t.name for t in res.tools}
    assert names == set(REGISTRY.tools) | {"present_plan"}
    pad = next(t for t in res.tools if t.name == "pad")
    assert pad.input_schema["required"] == ["sketch"]


def test_mcp_call_returns_text_and_image_and_emits_events(server):
    res = asyncio.run(_session_call(server, lambda s: s.call_tool("screenshot", {"views": ["iso"], "max_size": 1200})))
    assert not res.is_error
    kinds = [c.type for c in res.content]
    assert kinds == ["text", "image"] and res.content[1].mime_type == "image/jpeg"
    assert json.loads(res.content[0].text)["ok"] is True
    called = server.tools.called[-1]
    assert called[0] == "screenshot" and called[1]["max_size"] == 500  # görüntü boyutu sınırı
    assert [k for k, _ in server.events] == ["tool_call", "tool_result"]


def test_mcp_invalid_input_and_tool_error_are_is_error(server):
    bad = asyncio.run(_session_call(server, lambda s: s.call_tool("pad", {"length": "on"})))
    assert bad.is_error and "şemaya uymuyor" in bad.content[0].text
    assert all(n != "pad" for n, _ in server.tools.called)
    err = asyncio.run(_session_call(server, lambda s: s.call_tool("fillet", {"edges": "E1", "radius": 50})))
    assert err.is_error and "update hatası" in err.content[0].text


def test_mcp_present_plan_recorded(server):
    plan = {"summary": "s", "parts": [{"name": "Taban", "method": "pad", "dimensions": "1x2x3"}]}
    res = asyncio.run(_session_call(server, lambda s: s.call_tool("present_plan", plan)))
    assert not res.is_error and server.last_plan == plan
    assert any(k == "plan" for k, _ in server.events)


def test_mcp_rejects_requests_without_token(server):
    req = urllib.request.Request(server.url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 401
    req.add_header("Authorization", "Bearer yanlis")
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    assert ei.value.code == 401


# ------------------------------------------------------------------ köprü
@pytest.fixture
def bridge(tmp_path):
    events = []
    a = ClaudeCodeAgent(FakeTools(tmp_path), tmp_path, on_event=events.append, claude_path="claude.exe")
    a.events = events
    yield a
    a.close()


def test_command_line_locks_down_tools(bridge):
    cmd = bridge._cmd()
    i = cmd.index("--tools")
    assert cmd[i + 1] == ""
    assert "--strict-mcp-config" in cmd and cmd[cmd.index("--setting-sources") + 1] == ""
    assert cmd[cmd.index("--allowedTools") + 1] == "mcp__catia"
    assert cmd[cmd.index("--permission-prompts") + 1] == "none"
    cfg = json.loads(cmd[cmd.index("--mcp-config") + 1])
    assert cfg["mcpServers"]["catia"]["url"] == bridge.mcp.url
    assert cfg["mcpServers"]["catia"]["headers"]["Authorization"] == f"Bearer {bridge.mcp.token}"
    assert "--session-id" in cmd and "--resume" not in cmd
    bridge._session_started = True
    assert "--resume" in bridge._cmd()  # durdurma sonrası aynı oturum sürer


def test_init_rejects_unexpected_builtin_tools(bridge):
    texts = []
    ok = bridge._handle({"type": "system", "subtype": "init", "model": "claude-opus-5-5",
                         "mcp_servers": [{"name": "catia", "status": "connected"}],
                         "tools": ["mcp__catia__pad"]}, texts)
    assert ok is None and bridge.model == "claude-opus-5-5"
    r = bridge._handle({"type": "system", "subtype": "init", "mcp_servers": [{"name": "catia", "status": "connected"}],
                        "tools": ["Bash", "mcp__catia__pad"]}, texts)
    assert r.status == "error" and "beklenmeyen araç" in r.error
    r = bridge._handle({"type": "system", "subtype": "init", "mcp_servers": [{"name": "catia", "status": "failed"}],
                        "tools": []}, texts)
    assert r.status == "error" and "bağlanamadı" in r.error


def test_stream_events_and_result(bridge):
    texts = []
    bridge._handle({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Mer"}}}, texts)
    bridge._handle({"type": "assistant", "message": {"content": [{"type": "text", "text": "Merhaba"}, {"type": "tool_use", "name": "x"}]}}, texts)
    bridge._handle({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": 1,
                                                                    "unifiedWindows": {"five_hour": {"utilization": 0.4}}}}, texts)
    r = bridge._handle({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "result": "Merhaba",
                        "usage": {"input_tokens": 5, "output_tokens": 7, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 50}}, texts)
    assert r.status == "done" and r.text == "Merhaba"
    kinds = [e.kind for e in bridge.events]
    assert kinds[:2] == ["text_delta", "assistant_text"] and "done" in kinds
    u = bridge._usage_dict()
    assert u["output"] == 7 and u["cache_read"] == 100 and u["cost_usd"] is None
    assert u["rate_limit"]["five_hour_utilization"] == 0.4


def test_result_error_and_plan(bridge):
    r = bridge._handle({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "limit"}, [])
    assert r.status == "error" and "limit" in r.error
    bridge.mcp.last_plan = {"summary": "s", "parts": []}
    r = bridge._handle({"type": "result", "subtype": "success", "is_error": False, "result": ""}, [])
    assert r.status == "plan" and r.plan["summary"] == "s"


def test_user_line_format(bridge, tmp_path):
    from PIL import Image
    p = tmp_path / "v.jpg"
    Image.new("RGB", (8, 8)).save(p)
    line = json.loads(bridge._user_line("blok yap", [str(p)], "Pad.1"))
    assert line["type"] == "user" and line["message"]["role"] == "user"
    c = line["message"]["content"]
    assert "Çalışma klasörü" in c[0]["text"] and "Pad.1" in c[0]["text"]
    assert c[1]["type"] == "image" and c[-1]["text"] == "blok yap"
    line2 = json.loads(bridge._user_line("devam", None, None))
    assert line2["message"]["content"] == [{"type": "text", "text": "devam"}]


def test_mcp_server_starts_without_console(tmp_path, monkeypatch):
    """pythonw (CATIA-Asistan.bat) altında sys.stdout/sys.stderr None'dır; uvicorn'un günlük yapılandırması
    bu durumda 'Unable to configure formatter default' ile çöküyordu."""
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    srv = CatiaMcpServer(FakeTools(tmp_path))
    srv.start()
    try:
        assert srv.port > 0
    finally:
        srv.stop()


def test_app_redirects_missing_streams(tmp_path, monkeypatch):
    import sys as _sys
    from catia_aibot.ui import app as appmod
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    monkeypatch.setattr("sys.excepthook", _sys.excepthook)
    appmod._ensure_streams()
    assert _sys.stdout is not None and _sys.stderr is not None
    print("günlüğe yazılabilir")
    _sys.stdout.flush()
    assert "günlüğe yazılabilir" in (tmp_path / "CatiaAiBot" / "app.log").read_text(encoding="utf-8")
    _sys.stdout.close()


def test_thinking_block_start_emits_thinking(bridge):
    bridge._handle({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "thinking"}}}, [])
    assert bridge.events[-1].kind == "thinking_delta"
