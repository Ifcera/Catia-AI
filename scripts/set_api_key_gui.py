"""API anahtarı giriş penceresi (konsol gerektirmez).

Gizli bir alana girilen anahtar sağlayıcıya ücretsiz bir model listesi isteğiyle doğrulanır ve yalnızca
Windows Kimlik Bilgileri Yöneticisi'ne kaydedilir. Ekrana ya da dosyaya yazılmaz.
    .venv\\Scripts\\python.exe scripts\\set_api_key_gui.py --provider gemini
"""
from __future__ import annotations

import argparse
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catia_aibot.agent.keys import get_api_key, mask, set_api_key  # noqa: E402
from catia_aibot.agent.providers import PROVIDERS, make_provider  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=list(PROVIDERS), default="gemini")
    a = ap.parse_args()

    root = tk.Tk()
    root.title("CATIA AI — API anahtarı")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=14)
    frm.grid()

    prov = tk.StringVar(value=a.provider)
    ttk.Label(frm, text="Sağlayıcı").grid(row=0, column=0, sticky="w")
    box = ttk.Combobox(frm, textvariable=prov, values=list(PROVIDERS), state="readonly", width=14)
    box.grid(row=0, column=1, sticky="w")
    current = ttk.Label(frm, text="")
    current.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))

    ttk.Label(frm, text="API anahtarı").grid(row=2, column=0, sticky="w")
    key_var = tk.StringVar()
    entry = ttk.Entry(frm, textvariable=key_var, show="•", width=46)
    entry.grid(row=2, column=1, columnspan=2, sticky="we")
    status = ttk.Label(frm, text="", wraplength=380)
    status.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
    result = {"code": 1}

    def refresh(*_):
        current.config(text=f"{PROVIDERS[prov.get()].label} — kayıtlı anahtar: {mask(get_api_key(prov.get()))}")

    def save():
        key = key_var.get().strip()
        if not key:
            status.config(text="Anahtar boş.")
            return
        btn.config(state="disabled")
        status.config(text="Doğrulanıyor…")
        p = prov.get()

        def work():
            try:
                models = make_provider(p, api_key=key).list_models()
            except Exception as exc:  # noqa: BLE001 - her SDK'nın kendi hata türü var
                msg = f"Doğrulanamadı, kaydedilmedi: {exc.__class__.__name__}: {str(exc)[:160]}"
                root.after(0, lambda: (status.config(text=msg), btn.config(state="normal")))
                return
            set_api_key(key, p)
            default = PROVIDERS[p].default_model
            note = "varsayılan model listede var" if default in models else f"varsayılan '{default}' listede YOK"
            print(f"{PROVIDERS[p].label}: anahtar kaydedildi ({mask(key)}); {len(models)} model; {note}.")
            result["code"] = 0

            def done():
                key_var.set("")
                refresh()
                status.config(text=f"Kaydedildi ✓ — {len(models)} model erişilebilir; {note}. Pencereyi kapatabilirsiniz.")
                btn.config(state="normal")
            root.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    btn = ttk.Button(frm, text="Doğrula ve kaydet", command=save)
    btn.grid(row=3, column=2, sticky="e", pady=(8, 0))
    box.bind("<<ComboboxSelected>>", refresh)
    entry.bind("<Return>", lambda e: save())
    refresh()
    entry.focus_force()
    root.mainloop()
    return result["code"]


if __name__ == "__main__":
    sys.exit(main())
