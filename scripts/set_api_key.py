"""API anahtarını Windows Kimlik Bilgileri Yöneticisi'ne kaydeder (4. aşamadaki arayüz gelene kadar).

Anahtar ekranda görünmeden (getpass) okunur, sağlayıcıya ücretsiz bir model listesi isteğiyle doğrulanır ve
yalnızca işletim sisteminin kimlik deposuna yazılır.
    .venv\\Scripts\\python.exe scripts\\set_api_key.py --provider gemini      # anthropic | openai | gemini
    .venv\\Scripts\\python.exe scripts\\set_api_key.py --provider gemini --delete
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catia_aibot.agent.keys import delete_api_key, get_api_key, mask, set_api_key  # noqa: E402
from catia_aibot.agent.providers import PROVIDERS, make_provider  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=list(PROVIDERS), default="anthropic")
    ap.add_argument("--delete", action="store_true")
    a = ap.parse_args()
    label = PROVIDERS[a.provider].label
    if a.delete:
        delete_api_key(a.provider)
        print(f"{label} anahtarı silindi.")
        return 0
    print(f"{label} — mevcut anahtar: {mask(get_api_key(a.provider))}")
    key = getpass.getpass(f"{label} API anahtarı (yazdıklarınız görünmez): ").strip()
    if not key:
        print("İptal edildi.")
        return 1
    try:
        models = make_provider(a.provider, api_key=key).list_models()  # ücretsiz doğrulama çağrısı
    except Exception as exc:  # noqa: BLE001 - her SDK'nın kendi hata türü var
        print(f"Anahtar doğrulanamadı ({exc.__class__.__name__}: {str(exc)[:200]}). Kaydedilmedi.")
        return 1
    set_api_key(key, a.provider)
    default = PROVIDERS[a.provider].default_model
    print(f"Kaydedildi: {mask(key)} — {len(models)} model erişilebilir. Varsayılan '{default}' "
          f"{'listede var' if default in models else 'LİSTEDE YOK (--model ile başka bir model seçin)'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
