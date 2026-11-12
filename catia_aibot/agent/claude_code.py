"""Claude Code köprüsü: API anahtarı yerine kullanıcının kendi Claude Code oturumu (claude.ai aboneliği).

Uygulama CATIA araçlarını yerel bir MCP sunucusunda (CatiaMcpServer) açar ve bilgisayardaki `claude` komutunu
arka planda (headless, stream-json) bu sunucuya bağlı olarak başlatır. Mesajlar stdin'e, olaylar stdout'tan okunur.

Kısıtlar (gerçek CLI 2.1.322 ile doğrulandı):
- `--tools ""`: Claude Code'un yerleşik araçları (dosya yazma, komut, web) kapalı; yalnızca CATIA MCP araçları.
- `--strict-mcp-config` + `--mcp-config`: yalnızca bu uygulamanın MCP sunucusu yüklenir.
- `--setting-sources ""`: kullanıcının ayar dosyaları, eklentileri ve hook'ları yüklenmez.
- `--allowedTools mcp__catia` + `--permission-prompts none`: CATIA araçları onay sormadan çalışır, başka her şey reddedilir.
- `--system-prompt`: Claude Code'un kodlama odaklı varsayılan prompt'u yerine CATIA modelleme rehberi.

Durdurma süreci sonlandırır; sonraki mesaj aynı oturumu `--resume` ile sürdürür (konuşma korunur).
Kullanım claude.ai aboneliğinin limitlerinden düşer; `rate_limit_event` ile 5 saatlik pencere doluluğu bildirilir.
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from ..mcpserver import SERVER_NAME, CatiaMcpServer
from ..tools import CatiaTools
from .config import Usage
from .loop import AgentEvent, TurnResult
from .prompts import system_prompt
from .providers.base import image_b64

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

EXTRA_PROMPT = (
    "\n\n## Ortam\nAraçlara MCP üzerinden erişiyorsun; adları mcp__catia__<araç> biçimindedir (ör. mcp__catia__new_part). "
    "Dosya, komut ya da web araçların yok; tüm işi CATIA araçlarıyla yap."
)


def find_claude() -> str | None:
    return shutil.which("claude")


def claude_status(claude: str | None = None) -> dict:
    """Claude Code kurulu mu ve oturum açık mı? (Hesap ayrıntılarından yalnızca gerekenler döner.)"""
    exe = claude or find_claude()
    if not exe:
        return {"installed": False, "logged_in": False}
    try:
        out = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                             timeout=30, creationflags=CREATE_NO_WINDOW).stdout
        info = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return {"installed": True, "logged_in": False}
    return {"installed": True, "logged_in": bool(info.get("loggedIn")), "auth": info.get("authMethod"),
            "plan": info.get("subscriptionType")}


class ClaudeCodeAgent:
    name = "claude_code"
    label = "Claude Code (abonelik)"

    def __init__(self, tools: CatiaTools, workspace: str | Path, on_event: Callable[[AgentEvent], None] | None = None,
                 model: str | None = None, effort: str = "high", claude_path: str | None = None,
                 image_max_size: int = 768, turn_timeout: float = 3 * 3600.0):
        self.tools = tools
        self.workspace = Path(workspace)
        self.on_event = on_event or (lambda e: None)
        self.model_choice = model
        self.effort = effort
        self.claude = claude_path or find_claude()
        self.turn_timeout = turn_timeout
        self.usage = Usage()
        self.rate_limit: dict | None = None
        self.model: str = model or "(Claude Code varsayılanı)"
        self.session_id = str(uuid.uuid4())
        self._session_started = False
        self._proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()
        self._needs_restart = False
        self._events: queue.Queue = queue.Queue()
        self._stderr_tail: list[str] = []
        self._stop = threading.Event()
        self._pending_notes: list[str] = []
        self._first_message = True
        self.busy = False
        self.mcp = CatiaMcpServer(tools, on_event=lambda kind, data: self.emit(kind, **data), image_max_size=image_max_size)
        self.mcp.start()

    # ------------------------------------------------------------- olaylar
    def emit(self, kind: str, **data) -> None:
        try:
            self.on_event(AgentEvent(kind, data))
        except Exception:  # noqa: BLE001
            pass

    @property
    def messages(self) -> list:  # API agent'ıyla uyum için (geçmiş Claude Code'da tutulur)
        return []

    # ------------------------------------------------------------- süreç
    def _cmd(self) -> list[str]:
        if not self.claude:
            raise RuntimeError("Claude Code (claude komutu) bulunamadı.")
        cmd = [self.claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages",
               "--tools", "", "--strict-mcp-config", "--mcp-config", json.dumps(self.mcp.claude_mcp_config()),
               "--setting-sources", "", "--disable-slash-commands",
               "--allowedTools", f"mcp__{SERVER_NAME}", "--permission-prompts", "none",
               "--system-prompt", system_prompt() + EXTRA_PROMPT,
               "--effort", self.effort]
        if self.model_choice:
            cmd += ["--model", self.model_choice]
        cmd += ["--resume", self.session_id] if self._session_started else ["--session-id", self.session_id]
        return cmd

    def _start_process(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._events = queue.Queue()
        self._stderr_tail = []
        self._proc = subprocess.Popen(self._cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8", errors="replace", cwd=str(self.workspace),
                                      creationflags=CREATE_NO_WINDOW, bufsize=1)
        self._session_started = True
        proc, q = self._proc, self._events

        def read_out():
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    q.put(json.loads(line))
                except json.JSONDecodeError:
                    q.put({"type": "_raw", "text": line})
            q.put(None)

        def read_err():
            for line in proc.stderr:
                self._stderr_tail = (self._stderr_tail + [line.rstrip()])[-20:]

        threading.Thread(target=read_out, daemon=True, name="claude-stdout").start()
        threading.Thread(target=read_err, daemon=True, name="claude-stderr").start()

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _kill(self) -> None:
        with self._proc_lock:
            proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()

    # ------------------------------------------------------------- kontrol
    def stop(self) -> None:
        self._stop.set()
        self._needs_restart = True  # sonraki mesaj yeni süreçle aynı oturumu --resume ile sürdürür
        self._kill()
        # Kesilen turda çalışmış araçların sonucunu model görmemiş olabilir; bir sonraki mesajda bildir.
        self._pending_notes.append("[Not: Kullanıcı önceki turu durdurdu. O sırada bazı araçlar CATIA'da çalışmış "
                                   "olabilir; devam etmeden önce get_design_state ile güncel durumu oku.]")

    def undo_last_message(self) -> dict:
        res = self.tools.undo_last_message()
        if res.get("group"):
            self._pending_notes.append(
                f"[Not: Kullanıcı '{res['group']}' mesajında yapılan CATIA değişikliklerini geri aldı "
                f"({len(res['undone'])} işlem: {', '.join(res['undone'][:12])}). Model şu an o değişikliklerden önceki "
                "durumdadır; gerekirse get_design_state ile kontrol et.]")
        return res

    def reset(self) -> None:
        self._kill()
        self.session_id = str(uuid.uuid4())
        self._session_started = False
        self._first_message = True
        self._pending_notes = []

    def close(self) -> None:
        self._kill()
        self.mcp.stop()

    # ------------------------------------------------------------- tur
    def _user_line(self, text: str, images: list[str] | None, context: str | None) -> str:
        pre = []
        if self._first_message:
            ws = self.tools.run(lambda s: str(s.workspace) if s.workspace else "(seçilmedi)")
            pre.append(f"[Ortam: CATIA V5 R20. Çalışma klasörü: {ws}. Tarih: {time.strftime('%Y-%m-%d')}.]")
            self._first_message = False
        pre += self._pending_notes
        self._pending_notes = []
        if context:
            pre.append(f"[CATIA'da seçili öğeler (kullanıcı bağlam olarak ekledi): {context}]")
        content = [{"type": "text", "text": "\n".join(pre)}] if pre else []
        for p in images or []:
            data, mime = image_b64(p)
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
        content.append({"type": "text", "text": text})
        return json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False)

    def send(self, text: str, images: list[str] | None = None, context: str | None = None) -> TurnResult:
        if self.busy:
            raise RuntimeError("Agent zaten çalışıyor.")
        self.busy = True
        self._stop.clear()
        self.mcp.last_plan = None
        self.tools.begin_message(text[:60])
        self.emit("turn_start", text=text, provider=self.name, model=self.model)
        try:
            if self._needs_restart or not self._alive():
                self._kill()
                self._needs_restart = False
                self._start_process()
            proc = self._proc
            proc.stdin.write(self._user_line(text, images, context) + "\n")
            proc.stdin.flush()
            return self._read_turn()
        except (OSError, ValueError, RuntimeError) as exc:
            if self._stop.is_set():
                self.emit("stopped", where="model")
                return TurnResult("stopped")
            msg = f"Claude Code çalıştırılamadı: {exc}"
            self.emit("error", message=msg)
            return TurnResult("error", error=msg)
        finally:
            self.tools.end_message()
            self.busy = False
            self.emit("usage", **self._usage_dict())

    def _usage_dict(self) -> dict:
        d = self.usage.as_dict("")  # abonelik: dolar tutarı gösterilmez
        if self.rate_limit:
            d["rate_limit"] = self.rate_limit
        return d

    def _read_turn(self) -> TurnResult:
        texts: list[str] = []
        deadline = time.monotonic() + self.turn_timeout
        self.emit("status", text="Claude Code yanıtı bekleniyor…", step=1)
        while True:
            if self._stop.is_set():
                self.emit("stopped", where="model")
                return TurnResult("stopped", "\n".join(texts))
            try:
                ev = self._events.get(timeout=0.25)
            except queue.Empty:
                if time.monotonic() > deadline:
                    self._kill()
                    return self._fail(texts, f"Claude Code {int(self.turn_timeout)} sn içinde turu bitirmedi; durduruldu.")
                continue
            if ev is None:  # süreç kapandı
                if self._stop.is_set():
                    self.emit("stopped", where="model")
                    return TurnResult("stopped", "\n".join(texts))
                tail = " | ".join(self._stderr_tail[-5:]) or "(çıktı yok)"
                return self._fail(texts, f"Claude Code beklenmedik biçimde kapandı: {tail}")
            r = self._handle(ev, texts)
            if r is not None:
                return r

    def _fail(self, texts, msg) -> TurnResult:
        self.emit("error", message=msg)
        return TurnResult("error", "\n".join(texts), error=msg)

    def _handle(self, ev: dict, texts: list[str]) -> TurnResult | None:
        t = ev.get("type")
        if t == "system":
            st = ev.get("subtype")
            if st == "init":
                self.model = ev.get("model") or self.model
                bad = [s for s in ev.get("mcp_servers", []) if s.get("name") == SERVER_NAME and s.get("status") != "connected"]
                if bad or not any(s.get("name") == SERVER_NAME for s in ev.get("mcp_servers", [])):
                    self._kill()
                    return self._fail(texts, f"Claude Code CATIA MCP sunucusuna bağlanamadı: {ev.get('mcp_servers')}")
                extra = [x for x in ev.get("tools", []) if not x.startswith(f"mcp__{SERVER_NAME}__")]
                if extra:  # güvenlik: yerleşik araç açılmışsa çalıştırma
                    self._kill()
                    return self._fail(texts, f"Claude Code beklenmeyen araçlarla başladı ({extra[:5]}); güvenlik için durduruldu.")
            elif st in ("status", "api_retry"):
                self.emit("status", text=f"Claude Code: {ev.get('status') or st}", step=0)
        elif t == "stream_event":
            e = ev.get("event", {})
            if e.get("type") == "content_block_start" and (e.get("content_block") or {}).get("type") in ("thinking", "redacted_thinking"):
                self.emit("thinking_delta", text="")  # düşünme metni gizli olsa da arayüz "düşünüyor" gösterir
            if e.get("type") == "content_block_delta":
                d = e.get("delta", {})
                if d.get("type") == "text_delta":
                    self.emit("text_delta", text=d.get("text", ""))
                elif d.get("type") == "thinking_delta":
                    self.emit("thinking_delta", text=d.get("thinking", ""))
        elif t == "assistant":
            for b in ev.get("message", {}).get("content", []):
                if b.get("type") == "text" and b.get("text", "").strip():
                    texts.append(b["text"])
                    self.emit("assistant_text", text=b["text"])
        elif t == "rate_limit_event":
            info = ev.get("rate_limit_info", {})
            win = (info.get("unifiedWindows") or {}).get("five_hour") or {}
            self.rate_limit = {"status": info.get("status"), "five_hour_utilization": win.get("utilization"),
                               "resets_at": info.get("resetsAt")}
            self.emit("usage", **self._usage_dict())
        elif t == "result":
            u = ev.get("usage") or {}
            self.usage.requests += int(ev.get("num_turns") or 0)
            self.usage.input_tokens += u.get("input_tokens", 0) or 0
            self.usage.output_tokens += u.get("output_tokens", 0) or 0
            self.usage.cache_read_tokens += u.get("cache_read_input_tokens", 0) or 0
            self.usage.cache_write_tokens += u.get("cache_creation_input_tokens", 0) or 0
            if ev.get("is_error") or ev.get("subtype") != "success":
                detail = ev.get("result") or ev.get("subtype")
                return self._fail(texts, f"Claude Code turu hatayla bitti: {detail}")
            if self.mcp.last_plan is not None:
                return TurnResult("plan", "\n".join(texts), plan=self.mcp.last_plan)
            self.emit("done", text="\n".join(texts))
            return TurnResult("done", "\n".join(texts) or (ev.get("result") or ""))
        return None
