"""Sağlayıcıdan bağımsız agent arayüzü.

Her sağlayıcı konuşma geçmişini KENDİ biçiminde tutar (Claude düşünme blokları, Gemini düşünce imzaları,
OpenAI akıl yürütme öğeleri bozulmadan geri gönderilmeli). Döngü yalnızca şu işlemleri kullanır:

    add_user(pre, images, text)      kullanıcı mesajı ekle (ön bilgi metinleri, görüntüler, asıl metin)
    step(system, tools, emit, stop)  bir model çağrısı -> StepResult (henüz geçmişe eklenmez)
    accept(result)                   yanıtı geçmişe ekle (refusal / max_tokens yanıtları eklenmez)
    add_tool_results(outputs)        araç sonuçlarını ekle (görüntülerle)
"""
from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ProviderError(Exception):
    """API/bağlantı hatası; mesaj kullanıcıya gösterilebilir."""


# Tek bir model isteği için üst sınır ve geçici hatalarda yeniden deneme (tüm sağlayıcılar).
REQUEST_TIMEOUT_S = 180.0
RETRY_ATTEMPTS = 3
RETRY_STATUS = (429, 500, 502, 503, 504)


def http_error_text(provider: str, code, message, model: str) -> str:
    msg = str(message)[:300]
    if code == 503:
        return (f"{provider}: '{model}' modeli şu an yoğun (503, birkaç kez denendi). Bu sağlayıcı tarafında geçici bir "
                "durum; birkaç dakika sonra tekrar deneyin ya da başka bir model/sağlayıcı seçin.")
    if code == 429:
        return f"{provider}: istek sınırı ya da kota aşıldı (429). Planınızın kotasını kontrol edin ya da biraz bekleyin. ({msg})"
    if code == 504:
        return f"{provider}: model zamanında yanıt vermedi (504). Tekrar deneyin. ({msg})"
    if code in (401, 403):
        return f"{provider}: anahtar geçersiz ya da bu modele yetkisi yok ({code}). Anahtarı kontrol edin."
    if code == 404:
        return f"{provider}: '{model}' modeli bulunamadı ya da artık sunulmuyor (404). Başka bir model seçin."
    return f"{provider} API hatası ({code}): {msg}"


def transport_error_text(provider: str, exc: Exception) -> str:
    name = exc.__class__.__name__
    if "Timeout" in name:
        return f"{provider}: istek {int(REQUEST_TIMEOUT_S)} sn içinde yanıt almadı ({name}). Bağlantınızı kontrol edip tekrar deneyin."
    return f"{provider}: bağlantı hatası ({name}: {str(exc)[:160]}). İnternet bağlantınızı kontrol edin."


class StopRequested(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    input: Any


@dataclass
class ToolOutput:
    call: ToolCall
    text: str
    images: list[Path] = field(default_factory=list)
    is_error: bool = False


@dataclass
class StepUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class StepResult:
    texts: list[str]
    tool_calls: list[ToolCall]
    stop: str  # end | tool_use | refusal | max_tokens | pause
    usage: StepUsage
    detail: str = ""
    native: Any = None  # sağlayıcının geçmişe ekleyeceği ham yanıt


def image_bytes(path: str | Path) -> tuple[bytes, str]:
    p = Path(path)
    return p.read_bytes(), ("image/png" if p.suffix.lower() == ".png" else "image/jpeg")


def image_b64(path: str | Path) -> tuple[str, str]:
    data, mime = image_bytes(path)
    return base64.standard_b64encode(data).decode("ascii"), mime


class Provider(ABC):
    name: str = "?"
    label: str = "?"
    default_model: str = ""

    def __init__(self, model: str | None, api_key: str | None, effort: str = "high", max_tokens: int = 32000,
                 client: Any = None, **options):
        self.model = model or self.default_model
        self.api_key = api_key
        self.effort = effort
        self.max_tokens = max_tokens
        self.options = options
        self._client = client
        self.history: list[Any] = []

    # ---- istemci
    @abstractmethod
    def make_client(self) -> Any: ...

    @property
    def client(self):
        if self._client is None:
            self._client = self.make_client()
        return self._client

    # ---- geçmiş
    def reset(self) -> None:
        self.history = []

    @abstractmethod
    def add_user(self, pre: list[str], images: list[Path], text: str) -> None: ...

    @abstractmethod
    def step(self, system: str, tools: list[dict], emit: Callable[..., None], should_stop: Callable[[], bool]) -> StepResult: ...

    @abstractmethod
    def accept(self, result: StepResult) -> None: ...

    @abstractmethod
    def add_tool_results(self, outputs: list[ToolOutput]) -> None: ...

    # ---- yardımcılar
    @abstractmethod
    def list_models(self) -> list[str]: ...

    def history_len(self) -> int:
        return len(self.history)
