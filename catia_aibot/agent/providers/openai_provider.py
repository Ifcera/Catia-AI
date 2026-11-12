"""OpenAI (ChatGPT) sağlayıcısı — resmî `openai` SDK, Responses API, function calling.

- store=False + include=["reasoning.encrypted_content"]: konuşma OpenAI'de saklanmaz; akıl yürütme öğeleri
  şifreli olarak geri gönderilir (her turda modelin önceki akıl yürütmesi korunur).
- Yanıt çıktı öğeleri (mesaj, akıl yürütme, function_call) değiştirilmeden bir sonraki isteğin girdisine eklenir.
- Araç çıktısı metin + görüntü (input_image, data URL) olarak function_call_output içinde döner.
- effort -> reasoning.effort (low/medium/high; xhigh/max -> high).
"""
from __future__ import annotations

import json

import openai

from .base import (REQUEST_TIMEOUT_S, RETRY_ATTEMPTS, Provider, ProviderError, StepResult, StepUsage, StopRequested,
                   ToolCall, ToolOutput, http_error_text, image_b64, transport_error_text)

EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


def _data_url(path) -> str:
    data, mime = image_b64(path)
    return f"data:{mime};base64,{data}"


def _dump(item) -> dict:
    return item.model_dump(mode="json", exclude_none=True) if hasattr(item, "model_dump") else dict(item)


class OpenAIProvider(Provider):
    name = "openai"
    label = "ChatGPT (OpenAI)"
    default_model = "gpt-5.2"

    def make_client(self):
        kw = {"timeout": REQUEST_TIMEOUT_S, "max_retries": RETRY_ATTEMPTS - 1}
        return openai.OpenAI(api_key=self.api_key, **kw) if self.api_key else openai.OpenAI(**kw)

    def add_user(self, pre, images, text):
        content = [{"type": "input_text", "text": "\n".join(pre)}] if pre else []
        content += [{"type": "input_image", "image_url": _data_url(p)} for p in images]
        content.append({"type": "input_text", "text": text})
        self.history.append({"role": "user", "content": content})

    @staticmethod
    def convert_tools(tools: list[dict]) -> list[dict]:
        return [{"type": "function", "name": t["name"], "description": t["description"],
                 "parameters": t["input_schema"], "strict": False} for t in tools]

    def _kwargs(self, system, tools):
        return dict(
            model=self.model,
            instructions=system,
            input=self.history,
            tools=self.convert_tools(tools),
            max_output_tokens=self.max_tokens,
            store=False,
            include=["reasoning.encrypted_content"],
            reasoning={"effort": EFFORT.get(self.effort, "high")},
        )

    def step(self, system, tools, emit, should_stop):
        if should_stop():
            raise StopRequested()
        try:
            with self.client.responses.stream(**self._kwargs(system, tools)) as stream:
                for ev in stream:
                    if should_stop():
                        raise StopRequested()
                    if getattr(ev, "type", "") == "response.output_text.delta":
                        emit("text_delta", text=ev.delta)
                resp = stream.get_final_response()
        except openai.APIStatusError as exc:
            raise ProviderError(http_error_text("OpenAI", exc.status_code, getattr(exc, "message", exc), self.model)) from None
        except openai.APIConnectionError as exc:  # APITimeoutError dahil
            raise ProviderError(transport_error_text("OpenAI", exc)) from None

        texts, calls, refusal = [], [], ""
        for item in resp.output or []:
            t = getattr(item, "type", None)
            if t == "message":
                for part in item.content or []:
                    pt = getattr(part, "type", None)
                    if pt == "output_text" and part.text.strip():
                        texts.append(part.text)
                    elif pt == "refusal":
                        refusal = getattr(part, "refusal", "") or "refusal"
            elif t == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError:
                    args = {"__unparseable_arguments__": item.arguments}
                calls.append(ToolCall(item.call_id, item.name, args))
        incomplete = getattr(getattr(resp, "incomplete_details", None), "reason", None)
        if refusal:
            stop = "refusal"
        elif getattr(resp, "status", "") == "incomplete" and incomplete == "max_output_tokens":
            stop = "max_tokens"
        elif calls:
            stop = "tool_use"
        else:
            stop = "end"
        u = getattr(resp, "usage", None)
        cached = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0 if u else 0
        usage = StepUsage((getattr(u, "input_tokens", 0) or 0) - cached, getattr(u, "output_tokens", 0) or 0, cached, 0) if u else StepUsage()
        return StepResult(texts, calls, stop, usage, refusal, native=[_dump(i) for i in resp.output or []])

    def accept(self, result: StepResult):
        self.history.extend(result.native)

    def add_tool_results(self, outputs: list[ToolOutput]):
        for o in outputs:
            if o.images:
                out = [{"type": "input_text", "text": o.text}]
                for p in o.images:
                    try:
                        out.append({"type": "input_image", "image_url": _data_url(p)})
                    except OSError:
                        pass
            else:
                out = o.text
            self.history.append({"type": "function_call_output", "call_id": o.call.id, "output": out})

    def list_models(self):
        return sorted(m.id for m in self.client.models.list())
