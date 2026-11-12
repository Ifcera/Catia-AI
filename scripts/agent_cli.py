"""Agent'ı terminalden çalıştırır (arayüz gelene kadar).

    .venv\\Scripts\\python.exe scripts\\agent_cli.py                       # sohbet
    .venv\\Scripts\\python.exe scripts\\agent_cli.py -m "50x30x10 blok..."  # tek mesaj
Komutlar: /geri (son mesajı geri al), /maliyet, /cikis. Çalışırken Ctrl+C durdurur.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from catia_aibot.agent import PROVIDERS, Agent, AgentConfig, AgentEvent, get_api_key, make_provider  # noqa: E402
from catia_aibot.agent.claude_code import ClaudeCodeAgent, claude_status  # noqa: E402
from catia_aibot.tools import CatiaTools  # noqa: E402


def printer(e: AgentEvent) -> None:
    d = e.data
    if e.kind == "status":
        print(f"\n  … {d['text']} (adım {d['step']})", flush=True)
    elif e.kind == "text_delta":
        print(d["text"], end="", flush=True)
    elif e.kind == "assistant_text":
        print()
    elif e.kind == "tool_call":
        args = json.dumps(d["input"], ensure_ascii=False)
        print(f"\n  → {d['name']}({args[:160]}{'…' if len(args) > 160 else ''})", flush=True)
    elif e.kind == "tool_result":
        data = d["data"]
        if d["name"] == "check_expectation" and data.get("ok"):
            mark = "GEÇTİ" if data.get("passed") else "KALDI"
            details = "; ".join(f"{c['check']}={'✓' if c['pass'] else '✗'}" for c in data.get("checks", []))
            print(f"    ← kontrol {mark}: {details}")
        elif not d["ok"]:
            print(f"    ← HATA: {data.get('error')}  ipucu: {data.get('hint', '-')}")
        else:
            from catia_aibot.ui.labels import result_summary
            print(f"    ← ok {result_summary(d['name'], data)} ({d['seconds']} s)")
        for img in d["images"]:
            print(f"    🖼  {img}")
    elif e.kind == "plan":
        print("\n=== PLAN ===")
        print(json.dumps(d["plan"], ensure_ascii=False, indent=2))
        print("=== Onaylamak ya da değiştirmek için yazın ===")
    elif e.kind == "error":
        print(f"\n[HATA] {d['message']}")
    elif e.kind == "stopped":
        print("\n[Durduruldu]")


def run_turn(agent: Agent, text: str):
    result = {}

    def work():
        result["r"] = agent.send(text)

    th = threading.Thread(target=work, daemon=True)
    th.start()
    try:
        while th.is_alive():
            th.join(0.2)
    except KeyboardInterrupt:
        print("\n[Durduruluyor…]")
        agent.stop()
        th.join()
    r = result.get("r")
    u = agent.usage.as_dict(agent.model)
    if getattr(agent, "name", "") == "claude_code":
        rl = getattr(agent, "rate_limit", None) or {}
        util = rl.get("five_hour_utilization")
        cost = "abonelik" + (f", 5 saatlik pencere %{round(util * 100)} dolu" if util is not None else "")
    else:
        cost = f"≈ ${u['cost_usd']}" if u["cost_usd"] is not None else "tutar: sağlayıcı panelinden"
    print(f"\n[{r.status if r else '?'} | istek {u['requests']}, giriş {u['input']}, çıkış {u['output']}, "
          f"önbellek okuma {u['cache_read']} / yazma {u['cache_write']}, {cost}]")
    return r


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=str(ROOT / "work" / "agent"))
    ap.add_argument("-m", "--message", help="Tek mesaj gönder ve çık.")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--provider", default="claude_code", choices=["claude_code", *PROVIDERS],
                    help="claude_code: kendi Claude Code oturumunuz (abonelik, anahtar gerekmez).")
    ap.add_argument("--model", default=None, help="Boşsa sağlayıcının varsayılanı.")
    ap.add_argument("--list-models", action="store_true", help="Sağlayıcının erişilebilir modellerini listele ve çık.")
    ap.add_argument("--transcript", help="Bitince mesaj geçmişini bu JSON dosyasına yaz (çalışma klasörüne).")
    a = ap.parse_args()
    if a.list_models:
        for m in make_provider(a.provider, api_key=get_api_key(a.provider)).list_models():
            print(m)
        return 0
    Path(a.workspace).mkdir(parents=True, exist_ok=True)
    tools = CatiaTools(workspace=a.workspace)
    if a.provider == "claude_code":
        st = claude_status()
        if not st["logged_in"]:
            print("Claude Code kurulu değil ya da oturum açık değil. Terminalde 'claude' çalıştırıp giriş yapın.")
            tools.close()
            return 1
        agent = ClaudeCodeAgent(tools, a.workspace, on_event=printer, model=a.model, effort=a.effort)
        print(f"Sağlayıcı: {agent.label} ({st.get('plan')}) — model: {agent.model}")
    else:
        agent = Agent(tools, AgentConfig(provider=a.provider, model=a.model, effort=a.effort), on_event=printer)
        print(f"Sağlayıcı: {agent.provider.label} — model: {agent.model}")
    try:
        if a.message:
            print(f"Sen: {a.message}")
            run_turn(agent, a.message)
        else:
            while True:
                try:
                    text = input("\nSen: ").strip()
                except EOFError:
                    break
                if not text:
                    continue
                if text == "/cikis":
                    break
                if text == "/geri":
                    print(json.dumps(agent.undo_last_message(), ensure_ascii=False, indent=1))
                    continue
                if text == "/maliyet":
                    print(agent.usage.as_dict(agent.model))
                    continue
                run_turn(agent, text)
    finally:
        if a.transcript:
            out = Path(a.workspace) / a.transcript
            out.write_text(json.dumps(_serializable(agent.messages), ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"Geçmiş: {out}")
        if hasattr(agent, "close"):
            agent.close()
        tools.close()
    return 0


def _serializable(messages):
    def conv(x):
        if hasattr(x, "model_dump"):
            return conv(x.model_dump(mode="json", exclude_none=True))
        if isinstance(x, (bytes, bytearray)):
            return f"<{len(x)} bayt>"
        if isinstance(x, str) and x.startswith("data:image"):
            return "<görüntü>"
        if isinstance(x, dict):
            if x.get("type") == "image":
                return {"type": "image", "source": "<kısaltıldı>"}
            return {k: ("<kısaltıldı>" if k in ("data", "encrypted_content") and isinstance(v, str) and len(v) > 200 else conv(v))
                    for k, v in x.items()}
        if isinstance(x, list):
            return [conv(v) for v in x]
        return x
    return conv(messages)


if __name__ == "__main__":
    sys.exit(main())
