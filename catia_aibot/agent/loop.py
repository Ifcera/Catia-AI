"""Sağlayıcıdan bağımsız agent döngüsü: LLM (Claude / ChatGPT / Gemini) + CATIA araç katmanı.

- Döngü: model çağrısı -> araç çağrıları doğrulanır ve çalıştırılır -> sonuçlar (görüntülerle) eklenir -> tekrar.
- Geçmiş her sağlayıcının kendi biçiminde ve yalnızca eklenerek büyür; model yanıtları değiştirilmeden
  geri gönderilir (düşünme blokları / imzalar / akıl yürütme öğeleri korunur).
- Her araç girdisi kendi JSON şemasıyla doğrulanır; refusal / max_tokens yanıtlarında araç çalıştırılmaz
  ve yanıt geçmişe eklenmez.
- Durdurma: model çağrısı sırasında (streaming destekleyen sağlayıcılarda anında) ya da araçlar arasında;
  her zaman her araç çağrısına bir sonuç yazılır, geçmiş geçerli kalır.
- present_plan turu bitirir; kullanıcının onayı bir sonraki mesajdır.

send() çağıranın iş parçacığında çalışır; CATIA araçları CatiaTools üzerinden COM iş parçacığında çalışır.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema

from ..tools import CatiaTools
from .config import AgentConfig, Usage
from .keys import get_api_key
from .prompts import AGENT_TOOLS, system_prompt
from .providers import Provider, ProviderError, StopRequested, ToolCall, ToolOutput, make_provider


@dataclass
class AgentEvent:
    kind: str  # turn_start | status | text_delta | thinking_delta | assistant_text | tool_call | tool_result | plan | usage | error | stopped | done
    data: dict = field(default_factory=dict)


@dataclass
class TurnResult:
    status: str  # done | plan | stopped | error | step_limit
    text: str = ""
    plan: dict | None = None
    error: str | None = None
    images: list[str] = field(default_factory=list)


class Agent:
    def __init__(self, tools: CatiaTools, config: AgentConfig | None = None, client: Any = None,
                 on_event: Callable[[AgentEvent], None] | None = None, provider: Provider | None = None):
        self.tools = tools
        self.config = config or AgentConfig()
        self.on_event = on_event or (lambda e: None)
        self.usage = Usage()
        self._stop = threading.Event()
        self._pending_notes: list[str] = []
        self._first_message = True
        self._tool_specs = [dict(s) for s in self.tools.specs()] + [dict(s) for s in AGENT_TOOLS.values()]
        self._schemas = {s["name"]: s["input_schema"] for s in self._tool_specs}
        self.provider = provider or self._make_provider(client)
        self.busy = False

    # ----------------------------------------------------------------- sağlayıcı
    def _make_provider(self, client=None) -> Provider:
        c = self.config
        return make_provider(c.provider, c.model, get_api_key(c.provider), effort=c.effort, max_tokens=c.max_tokens,
                             client=client, use_fallbacks=c.use_fallbacks, thinking_display=c.thinking_display,
                             extra_betas=c.extra_betas)

    def set_provider(self, provider: str, model: str | None = None, client: Any = None) -> None:
        """Sağlayıcı/model değiştirir. Geçmiş biçimleri farklı olduğu için konuşma yeniden başlar
        (CATIA'daki model olduğu gibi kalır)."""
        if self.busy:
            raise RuntimeError("Agent çalışırken sağlayıcı değiştirilemez.")
        self.config.provider, self.config.model = provider, model
        self.provider = self._make_provider(client)
        self._first_message = True
        self._pending_notes.append("[Not: Sağlayıcı değiştirildi; önceki konuşma bu modele aktarılmadı. "
                                   "CATIA'daki mevcut durumu get_design_state ile oku.]")

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def messages(self) -> list:
        return self.provider.history

    def emit(self, kind: str, **data) -> None:
        try:
            self.on_event(AgentEvent(kind, data))
        except Exception:  # noqa: BLE001 - arayüz hatası döngüyü durdurmasın
            pass

    # ----------------------------------------------------------------- kontrol
    def stop(self) -> None:
        self._stop.set()

    def undo_last_message(self) -> dict:
        res = self.tools.undo_last_message()
        if res.get("group"):
            self._pending_notes.append(
                f"[Not: Kullanıcı '{res['group']}' mesajında yapılan CATIA değişikliklerini geri aldı "
                f"({len(res['undone'])} işlem: {', '.join(res['undone'][:12])}). Model şu an o değişikliklerden önceki "
                "durumdadır; kimlikler ve adlar değişmiş olabilir, gerekirse get_design_state ile kontrol et.]")
        return res

    def reset(self) -> None:
        self.provider.reset()
        self._pending_notes = []
        self._first_message = True

    # ------------------------------------------------------------------ araçlar
    def _validate(self, name: str, args) -> str | None:
        schema = self._schemas.get(name)
        if schema is None:
            return f"Bilinmeyen araç: {name}"
        if not isinstance(args, dict):
            return "Girdi bir JSON nesnesi değil."
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as exc:
            path = "/".join(str(p) for p in exc.absolute_path) or "(kök)"
            return f"Girdi şemaya uymuyor ({path}): {exc.message}"
        return None

    def _run_tool(self, call: ToolCall) -> tuple[dict, Any, list[Path]]:
        name, args = call.name, call.input
        err = self._validate(name, args)
        if err:
            data = {"ok": False, "error": err, "hint": "Girdiyi araç şemasına göre düzeltip tekrar çağırın.",
                    "INVALID_INPUT": json.dumps(args, ensure_ascii=False, default=str)[:2000]}
            return data, data, []
        if name == "present_plan":
            data = {"ok": True, "status": "Plan kullanıcıya gösterildi; onay bekleniyor. Kullanıcı yanıt verene kadar başka işlem yapma."}
            return data, args, []
        if name == "screenshot":
            args = {**args, "max_size": min(int(args.get("max_size", self.config.image_max_size)), self.config.image_max_size)}
        r = self.tools.call(name, args)
        return r.data, r.data, list(r.images)

    def _output(self, call: ToolCall, data: dict, images: list[Path]) -> ToolOutput:
        text = json.dumps(data, ensure_ascii=False, default=str)
        limit = self.config.max_tool_result_chars
        if len(text) > limit:
            text = json.dumps({"ok": data.get("ok", True), "truncated": True,
                               "partial": text[:limit], "note": f"Sonuç {len(text)} karakterdi; daha dar filtreyle tekrar isteyin."},
                              ensure_ascii=False)
        return ToolOutput(call, text, images, is_error=not data.get("ok", True))

    # ------------------------------------------------------------------- tur
    def send(self, text: str, images: list[str] | None = None, context: str | None = None) -> TurnResult:
        if self.busy:
            raise RuntimeError("Agent zaten çalışıyor.")
        self.busy = True
        self._stop.clear()
        pre = []
        if self._first_message:
            ws = self.tools.run(lambda s: str(s.workspace) if s.workspace else "(seçilmedi)")
            pre.append(f"[Ortam: CATIA V5 R20. Çalışma klasörü: {ws}. Tarih: {time.strftime('%Y-%m-%d')}.]")
            self._first_message = False
        pre.extend(self._pending_notes)
        self._pending_notes = []
        if context:
            pre.append(f"[CATIA'da seçili öğeler (kullanıcı bağlam olarak ekledi): {context}]")
        if images:
            pre.append("[Ekteki görüntü kullanıcının o anki CATIA görünümüdür.]")
        self.provider.add_user(pre, [Path(p) for p in images or []], text)
        self.tools.begin_message(text[:60])
        self.emit("turn_start", text=text, provider=self.provider.name, model=self.provider.model)
        try:
            return self._loop()
        finally:
            self.tools.end_message()
            self.busy = False
            self.emit("usage", **self.usage.as_dict(self.provider.model))

    def _fail(self, texts, msg, images=None, status="error") -> TurnResult:
        self.emit("error", message=msg)
        return TurnResult(status, "\n".join(texts), error=msg, images=images or [])

    def _loop(self) -> TurnResult:
        texts: list[str] = []
        shots: list[str] = []
        malformed = 0
        for _step in range(self.config.max_steps):
            self.emit("status", text=f"{self.provider.label} yanıtı bekleniyor…", step=_step + 1)
            try:
                res = self.provider.step(system_prompt(), self._tool_specs, self.emit, self._stop.is_set)
            except StopRequested:
                self.emit("stopped", where="model")
                return TurnResult("stopped", "\n".join(texts), images=shots)
            except ProviderError as exc:
                return self._fail(texts, str(exc), shots)
            self.usage.add_step(res.usage)
            self.emit("usage", **self.usage.as_dict(self.provider.model))

            if res.stop == "refusal":
                return self._fail(texts, "Model isteği reddetti" + (f" ({res.detail})" if res.detail else "") + ".", shots)
            if res.stop == "max_tokens" and res.tool_calls:
                return self._fail(texts, "Yanıt çıktı sınırına takıldı; araç girdisi yarım kaldığı için çalıştırılmadı.", shots)
            if res.stop == "malformed":
                malformed += 1
                if malformed > 2:
                    return self._fail(texts, "Model üst üste geçersiz araç çağrısı üretti.", shots)
                continue

            self.provider.accept(res)
            for t in res.texts:
                texts.append(t)
                self.emit("assistant_text", text=t)
            if res.stop == "pause":
                continue
            if not res.tool_calls:
                self.emit("done", text="\n".join(texts))
                return TurnResult("done", "\n".join(texts), images=shots)

            outputs, plan, stopped = [], None, False
            for call in res.tool_calls:
                if stopped or self._stop.is_set():
                    stopped = True
                    outputs.append(ToolOutput(call, json.dumps({"ok": False, "error": "Kullanıcı durdurdu; araç çalıştırılmadı."},
                                                               ensure_ascii=False), [], True))
                    continue
                self.emit("tool_call", id=call.id, name=call.name, input=call.input)
                t0 = time.monotonic()
                try:
                    data, payload, imgs = self._run_tool(call)
                except Exception as exc:  # noqa: BLE001 - her çağrı bir sonuç almalı
                    data = {"ok": False, "error": f"Araç çalıştırılamadı: {exc.__class__.__name__}: {exc}"}
                    payload, imgs = data, []
                self.emit("tool_result", id=call.id, name=call.name, ok=bool(data.get("ok")), data=data,
                          images=[str(p) for p in imgs], seconds=round(time.monotonic() - t0, 2))
                shots.extend(str(p) for p in imgs)
                if call.name == "present_plan" and data.get("ok"):
                    plan = payload
                    self.emit("plan", plan=payload)
                outputs.append(self._output(call, data, imgs))
            self.provider.add_tool_results(outputs)
            if stopped:
                self.emit("stopped", where="tools")
                return TurnResult("stopped", "\n".join(texts), images=shots)
            if plan is not None:
                return TurnResult("plan", "\n".join(texts), plan=plan, images=shots)
        return self._fail(texts, f"Adım sınırına ({self.config.max_steps}) ulaşıldı.", shots, "step_limit")
