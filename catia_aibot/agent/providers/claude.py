"""Anthropic Claude sağlayıcısı (resmî `anthropic` SDK, Messages API, streaming + tool use).

- Model varsayılanı claude-opus-5-5; adaptive thinking (Opus 5.5'te kapatılamaz), effort output_config ile.
- Araç girdileri eager_input_streaming ile akar; döngü her girdiyi şemayla doğrular.
- Üst düzey otomatik prompt önbelleği; araç listesi + sistem prompt'u sabit önek.
- Reddetmede sunucu tarafı yedek model: betas=[server-side-fallback-2026-07-01], fallbacks="default".
- Asistan yanıtı (düşünme blokları dahil) değiştirilmeden geri gönderilir.
"""
from __future__ import annotations

from pathlib import Path

import anthropic

from .base import (REQUEST_TIMEOUT_S, RETRY_ATTEMPTS, Provider, ProviderError, StepResult, StepUsage, StopRequested,
                   ToolCall, ToolOutput, http_error_text, image_b64, transport_error_text)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _image_block(path) -> dict:
    data, mime = image_b64(path)
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}


class ClaudeProvider(Provider):
    name = "anthropic"
    label = "Claude (Anthropic)"
    default_model = "claude-opus-5-5"

    def make_client(self):
        kw = {"timeout": REQUEST_TIMEOUT_S, "max_retries": RETRY_ATTEMPTS - 1}
        return anthropic.Anthropic(api_key=self.api_key, **kw) if self.api_key else anthropic.Anthropic(**kw)

    def add_user(self, pre, images, text):
        content = [{"type": "text", "text": "\n".join(pre)}] if pre else []
        content += [_image_block(p) for p in images]
        content.append({"type": "text", "text": text})
        self.history.append({"role": "user", "content": content})

    def _kwargs(self, system, tools):
        specs = [dict(t, eager_input_streaming=True) for t in tools]
        kw = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system}],
            tools=specs,
            messages=self.history,
            thinking={"type": "adaptive", "display": self.options.get("thinking_display", "summarized")},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
        )
        betas = list(self.options.get("extra_betas", []))
        if self.options.get("use_fallbacks", True):
            betas.append(FALLBACK_BETA)
            kw["fallbacks"] = "default"
        if betas:
            kw["betas"] = betas
        return kw

    def step(self, system, tools, emit, should_stop):
        json_retries = 0
        while True:
            if should_stop():
                raise StopRequested()
            try:
                with self.client.beta.messages.stream(**self._kwargs(system, tools)) as stream:
                    for ev in stream:
                        if should_stop():
                            raise StopRequested()
                        t = getattr(ev, "type", None)
                        if t == "text":
                            emit("text_delta", text=ev.text)
                        elif t == "thinking":
                            emit("thinking_delta", text=getattr(ev, "thinking", ""))
                    resp = stream.get_final_message()
                break
            except ValueError:
                # SDK'nın hiç ayrıştıramadığı araç girdisi: tool_use_id yok, turu yeniden iste (sınırlı).
                json_retries += 1
                if json_retries > 2:
                    raise ProviderError("Model geçersiz araç girdisi üretti (3 deneme).") from None
            except anthropic.APIStatusError as exc:
                raise ProviderError(http_error_text("Claude", exc.status_code, getattr(exc, "message", exc), self.model)) from None
            except anthropic.APIConnectionError as exc:  # APITimeoutError dahil
                raise ProviderError(transport_error_text("Claude", exc)) from None
        u = resp.usage
        usage = StepUsage(getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0,
                          getattr(u, "cache_read_input_tokens", 0) or 0, getattr(u, "cache_creation_input_tokens", 0) or 0)
        texts = [b.text for b in resp.content if getattr(b, "type", None) == "text" and b.text.strip()]
        calls = [ToolCall(b.id, b.name, b.input) for b in resp.content if getattr(b, "type", None) == "tool_use"]
        stop = {"end_turn": "end", "tool_use": "tool_use", "refusal": "refusal", "max_tokens": "max_tokens",
                "pause_turn": "pause", "stop_sequence": "end"}.get(resp.stop_reason, "end")
        detail = ""
        if stop == "refusal":
            det = getattr(resp, "stop_details", None)
            detail = getattr(det, "category", "") or ""
        if stop == "end" and calls:
            stop = "tool_use"
        return StepResult(texts, calls, stop, usage, detail, native=resp.content)

    def accept(self, result: StepResult):
        self.history.append({"role": "assistant", "content": result.native})

    def add_tool_results(self, outputs: list[ToolOutput]):
        blocks = []
        for o in outputs:
            content = [{"type": "text", "text": o.text}]
            for p in o.images:
                try:
                    content.append(_image_block(p))
                except OSError:
                    pass
            b = {"type": "tool_result", "tool_use_id": o.call.id, "content": content}
            if o.is_error:
                b["is_error"] = True
            blocks.append(b)
        self.history.append({"role": "user", "content": blocks})

    def list_models(self):
        return [m.id for m in self.client.models.list()]
