"""Her model isteğinde tekrar giden sabit öneki ölçer: sistem prompt'u + araç tanımları.

1) Yerel boyut: karakter sayıları (araç başına en büyükler dahil).
2) --live: boş bir Claude Code oturumunda tek "tamam" turu çalıştırıp gerçek token sayısını okur
   (aboneliğinizden çok küçük bir kullanım düşer). CATIA gerekmez.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from catia_aibot.agent.prompts import AGENT_TOOLS, system_prompt  # noqa: E402
from catia_aibot.tools import REGISTRY  # noqa: E402


def local_report() -> dict:
    specs = REGISTRY.specs() + list(AGENT_TOOLS.values())
    sizes = sorted(((len(json.dumps(s, ensure_ascii=False)), s["name"]) for s in specs), reverse=True)
    tools_chars = sum(n for n, _ in sizes)
    sp = len(system_prompt())
    print(f"araç sayısı: {len(specs)}  araç tanımları: {tools_chars:,} karakter  sistem prompt'u: {sp:,} karakter")
    print("en büyük araçlar:", ", ".join(f"{name} {n:,}" for n, name in sizes[:8]))
    return {"tools": len(specs), "tools_chars": tools_chars, "system_chars": sp}


def live() -> dict:
    from test_agent_offline import FakeTools
    from catia_aibot.agent.claude_code import ClaudeCodeAgent

    ws = Path(tempfile.mkdtemp(prefix="prefix_"))
    a = ClaudeCodeAgent(FakeTools(ws), ws, effort="low")
    try:
        r = a.send("Sadece 'tamam' yaz, araç çağırma.")
        u = a.usage
        total = u.input_tokens + u.cache_read_tokens + u.cache_write_tokens
        print(f"canlı ilk istek önek: {total:,} token (giriş {u.input_tokens}, önbellek okuma {u.cache_read_tokens:,}, "
              f"yazma {u.cache_write_tokens:,}) | {r.status}")
        return {"prefix_tokens": total}
    finally:
        a.close()


if __name__ == "__main__":
    out = local_report()
    if "--live" in sys.argv:
        out.update(live())
    print(json.dumps(out))
