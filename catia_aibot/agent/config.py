"""Agent ayarları ve fiyat tablosu."""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-opus-5-5"

# $ / 1M token — yalnızca doğrulanmış fiyatlar (Anthropic, 2026-11). Önbellek yazma = 5 dk TTL (1.25x giriş).
# Diğer sağlayıcılarda fiyat tablosu tutulmuyor: arayüz token sayısını gösterir, tutarı sağlayıcının panelinden kontrol edin.
PRICES = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.10, "cache_write": 2.5},
    "claude-fable-5-2": {"input": 8.0, "output": 40.0, "cache_read": 0.20, "cache_write": 10.0},
}

MODELS = list(PRICES)


@dataclass
class AgentConfig:
    provider: str = DEFAULT_PROVIDER  # anthropic | openai | gemini
    model: str | None = None          # None -> sağlayıcının varsayılanı
    # Opus 5.5'te varsayılan 'medium'; çok adımlı CAD işinde 'high' seçildi. Diğer sağlayıcılara eşlenir.
    effort: str = "high"
    max_tokens: int = 32000
    max_steps: int = 80               # bir kullanıcı mesajı başına en fazla model çağrısı
    image_max_size: int = 768         # ekran görüntüsü uzun kenarı (px)
    max_tool_result_chars: int = 12000
    use_fallbacks: bool = True        # Claude: reddetmede sunucu tarafı yedek model
    thinking_display: str = "summarized"
    extra_betas: list[str] = field(default_factory=list)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 0

    def add_step(self, u) -> None:
        self.requests += 1
        self.input_tokens += u.input_tokens
        self.output_tokens += u.output_tokens
        self.cache_read_tokens += u.cache_read_tokens
        self.cache_write_tokens += u.cache_write_tokens

    def cost_usd(self, model: str) -> float | None:
        p = PRICES.get(model)
        if p is None:
            return None
        return (self.input_tokens * p["input"] + self.output_tokens * p["output"]
                + self.cache_read_tokens * p["cache_read"] + self.cache_write_tokens * p["cache_write"]) / 1e6

    def as_dict(self, model: str) -> dict:
        cost = self.cost_usd(model)
        return {"requests": self.requests, "input": self.input_tokens, "output": self.output_tokens,
                "cache_read": self.cache_read_tokens, "cache_write": self.cache_write_tokens,
                "cost_usd": round(cost, 4) if cost is not None else None}
