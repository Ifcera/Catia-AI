"""Google Gemini sağlayıcısı — resmî `google-genai` SDK, generate_content + function calling.

- Araç şemaları parameters_json_schema ile olduğu gibi verilir (JSON Schema).
- Otomatik fonksiyon çağırma kapalı; araçları döngü çalıştırır.
- Model yanıtı (Content) değiştirilmeden geçmişe eklenir: düşünce imzaları (thought_signature) korunur,
  aksi halde Gemini 3 fonksiyon çağrısı turlarında 400 döner.
- Araç çıktısı FunctionResponse.response (JSON) + FunctionResponse.parts (görüntü) olarak döner.
- effort -> thinking_config.thinking_level; model desteklemiyorsa düşünme ayarı olmadan tekrar denenir.
"""
from __future__ import annotations

import json

from google import genai
from google.genai import errors, types

from .base import (REQUEST_TIMEOUT_S, RETRY_ATTEMPTS, RETRY_STATUS, Provider, ProviderError, StepResult, StepUsage,
                   StopRequested, ToolCall, ToolOutput, http_error_text, image_bytes, transport_error_text)

LEVEL = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "xhigh": "HIGH", "max": "HIGH"}
REFUSAL = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT"}


class GeminiProvider(Provider):
    name = "gemini"
    label = "Gemini (Google)"
    default_model = "gemini-3.8-flash"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._tools_cache: tuple[int, list] | None = None
        self._thinking_ok = True

    def make_client(self):
        # Zaman aşımı + sınırlı yeniden deneme: aksi halde 503 "yüksek talep" sırasında istek çok uzun
        # süre sessizce bekleyebiliyor (2026-11-12'de 28 dk takılma yaşandı).
        http = types.HttpOptions(
            timeout=int(REQUEST_TIMEOUT_S * 1000),
            retry_options=types.HttpRetryOptions(attempts=RETRY_ATTEMPTS, initial_delay=2.0, max_delay=15.0,
                                                 http_status_codes=list(RETRY_STATUS)))
        return genai.Client(api_key=self.api_key, http_options=http) if self.api_key else genai.Client(http_options=http)

    def add_user(self, pre, images, text):
        parts = [types.Part.from_text(text="\n".join(pre))] if pre else []
        for p in images:
            data, mime = image_bytes(p)
            parts.append(types.Part.from_bytes(data=data, mime_type=mime))
        parts.append(types.Part.from_text(text=text))
        self.history.append(types.Content(role="user", parts=parts))

    def _tools(self, tools):
        key = id(tools)
        if self._tools_cache is None or self._tools_cache[0] != key:
            decls = [types.FunctionDeclaration(name=t["name"], description=t["description"],
                                               parameters_json_schema=t["input_schema"]) for t in tools]
            self._tools_cache = (key, [types.Tool(function_declarations=decls)])
        return self._tools_cache[1]

    def _config(self, system, tools):
        cfg = dict(
            system_instruction=system,
            tools=self._tools(tools),
            max_output_tokens=self.max_tokens,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if self._thinking_ok:
            cfg["thinking_config"] = types.ThinkingConfig(thinking_level=LEVEL.get(self.effort, "HIGH"))
        return types.GenerateContentConfig(**cfg)

    def step(self, system, tools, emit, should_stop):
        if should_stop():
            raise StopRequested()
        for attempt in range(2):
            try:
                resp = self.client.models.generate_content(model=self.model, contents=self.history,
                                                           config=self._config(system, tools))
                break
            except errors.ClientError as exc:
                if attempt == 0 and self._thinking_ok and "thinking" in str(exc).lower():
                    self._thinking_ok = False  # bu model thinking_level desteklemiyor
                    continue
                raise ProviderError(http_error_text("Gemini", getattr(exc, "code", None), getattr(exc, "message", exc), self.model)) from None
            except errors.APIError as exc:
                raise ProviderError(http_error_text("Gemini", getattr(exc, "code", None), getattr(exc, "message", exc), self.model)) from None
            except Exception as exc:  # noqa: BLE001 - httpx zaman aşımı / bağlantı hataları
                raise ProviderError(transport_error_text("Gemini", exc)) from None
        if should_stop():
            raise StopRequested()

        um = getattr(resp, "usage_metadata", None)
        cached = (getattr(um, "cached_content_token_count", 0) or 0) if um else 0
        usage = StepUsage(
            ((getattr(um, "prompt_token_count", 0) or 0) - cached) if um else 0,
            ((getattr(um, "candidates_token_count", 0) or 0) + (getattr(um, "thoughts_token_count", 0) or 0)) if um else 0,
            cached, 0)
        cands = getattr(resp, "candidates", None) or []
        if not cands:
            reason = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
            return StepResult([], [], "refusal", usage, str(getattr(reason, "name", reason) or "boş yanıt"))
        cand = cands[0]
        finish = getattr(cand.finish_reason, "name", str(cand.finish_reason or ""))
        content = cand.content or types.Content(role="model", parts=[])
        texts, calls = [], []
        for i, part in enumerate(content.parts or []):
            if part.function_call is not None:
                fc = part.function_call
                calls.append(ToolCall(fc.id or f"gemini_call_{len(self.history)}_{i}", fc.name, dict(fc.args or {})))
            elif part.text and not part.thought and part.text.strip():
                texts.append(part.text)
                emit("text_delta", text=part.text)
        if finish in REFUSAL:
            stop = "refusal"
        elif finish == "MAX_TOKENS":
            stop = "max_tokens"
        elif finish == "MALFORMED_FUNCTION_CALL":
            # Geçersiz çağrı: geçmişe eklemeden modelden düzeltmesini iste.
            stop = "malformed"
        elif calls:
            stop = "tool_use"
        else:
            stop = "end"
        return StepResult(texts, calls, stop, usage, finish, native=content)

    def accept(self, result: StepResult):
        if result.native is not None:
            self.history.append(result.native)

    def add_tool_results(self, outputs: list[ToolOutput]):
        parts = []
        for o in outputs:
            try:
                payload = json.loads(o.text)
            except json.JSONDecodeError:
                payload = o.text
            response = {"error": payload} if o.is_error else {"output": payload}
            img_parts = []
            for p in o.images:
                try:
                    data, mime = image_bytes(p)
                    img_parts.append(types.FunctionResponsePart(inline_data=types.FunctionResponseBlob(mime_type=mime, data=data)))
                except OSError:
                    pass
            fr = types.FunctionResponse(
                id=None if o.call.id.startswith("gemini_call_") else o.call.id,
                name=o.call.name, response=response, parts=img_parts or None)
            parts.append(types.Part(function_response=fr))
        self.history.append(types.Content(role="user", parts=parts))

    def list_models(self):
        out = []
        for m in self.client.models.list():
            if "generateContent" in (m.supported_actions or []):
                out.append(m.name.removeprefix("models/"))
        return sorted(out)
