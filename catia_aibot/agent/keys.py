"""API anahtarını işletim sisteminin kimlik deposunda (Windows Credential Manager) saklar.

Anahtar koda ya da düz metin dosyaya yazılmaz. Ortam değişkenleri (ENV_VARS) yalnızca
yedek okuma kaynağıdır; buraya hiçbir şey yazılmaz.
"""
from __future__ import annotations

import os

import keyring
import keyring.errors

SERVICE = "catia-aibot"
# Yalnızca okuma için yedek kaynaklar; buraya hiçbir şey yazılmaz.
ENV_VARS = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def get_api_key(provider: str = "anthropic") -> str | None:
    try:
        key = keyring.get_password(SERVICE, provider)
    except keyring.errors.KeyringError:
        key = None
    if key:
        return key
    for var in ENV_VARS.get(provider, ()):
        if os.environ.get(var):
            return os.environ[var]
    return None


def set_api_key(key: str, provider: str = "anthropic") -> None:
    key = key.strip()
    if not key:
        raise ValueError("Boş anahtar.")
    keyring.set_password(SERVICE, provider, key)


def delete_api_key(provider: str = "anthropic") -> None:
    try:
        keyring.delete_password(SERVICE, provider)
    except keyring.errors.PasswordDeleteError:
        pass


def has_api_key(provider: str = "anthropic") -> bool:
    return get_api_key(provider) is not None


def mask(key: str | None) -> str:
    if not key:
        return "(yok)"
    # Yalnızca son 4 karakter: anahtarın başı sağlayıcı önekinden fazlasını açık edebilir.
    return "…" + key[-4:] if len(key) > 12 else "…"
