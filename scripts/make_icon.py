"""Uygulama logosunu üretir: catia_aibot/ui/assets/icon.png ve icon.ico (çok boyutlu).

Koyu yuvarlatılmış kare üzerinde izometrik mavi küp ve sağ üstte yapay zekâ kıvılcımı.
Çalıştırma:  .venv\\Scripts\\python.exe scripts\\make_icon.py
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "catia_aibot" / "ui" / "assets"
S = 1024  # çizim boyutu; sonra küçültülür


def _sparkle(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill) -> None:
    pts = []
    for i in range(8):
        a = math.pi / 4 * i - math.pi / 2
        rr = r if i % 2 == 0 else r * 0.28
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # Arka plan: dikey degrade, yuvarlatılmış kare
    bg = Image.new("RGBA", (S, S))
    top, bot = (32, 38, 52), (14, 17, 26)
    bd = ImageDraw.Draw(bg)
    for y in range(S):
        k = y / (S - 1)
        bd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * k) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([24, 24, S - 24, S - 24], radius=220, fill=255)
    img.paste(bg, (0, 0), mask)

    # İzometrik küp
    cx, cy, a = S * 0.47, S * 0.56, S * 0.25
    h = a * math.sqrt(3) / 2
    T = (cx, cy - a)
    L, R = (cx - h, cy - a / 2), (cx + h, cy - a / 2)
    C = (cx, cy)
    BL, BR, B = (cx - h, cy + a / 2), (cx + h, cy + a / 2), (cx, cy + a)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon([T, R, BR, B, BL, L], fill=(56, 140, 255, 150))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(40)))
    d = ImageDraw.Draw(img)
    d.polygon([T, R, C, L], fill=(126, 196, 255))      # üst yüz
    d.polygon([L, C, B, BL], fill=(46, 120, 230))      # sol yüz
    d.polygon([C, R, BR, B], fill=(24, 78, 170))       # sağ yüz
    edge = (210, 235, 255)
    w = int(S * 0.012)
    for p, q in [(T, R), (R, BR), (BR, B), (B, BL), (BL, L), (L, T), (L, C), (C, R), (C, B)]:
        d.line([p, q], fill=edge, width=w)
    # Üst yüzde delik (CAD özelliği çağrışımı)
    ex, ey = (T[0] + C[0]) / 2, (T[1] + C[1]) / 2
    rx, ry = a * 0.30, a * 0.30 / math.sqrt(3)
    d.ellipse([ex - rx, ey - ry, ex + rx, ey + ry], fill=(18, 52, 120), outline=edge, width=w // 2)

    # Yapay zekâ kıvılcımları
    _sparkle(d, S * 0.76, S * 0.25, S * 0.12, (255, 196, 64))
    _sparkle(d, S * 0.86, S * 0.42, S * 0.05, (255, 222, 140))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                                                       (64, 64), (128, 128), (256, 256)])
    print("yazıldı:", OUT)


if __name__ == "__main__":
    main()
