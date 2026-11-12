"""Kullanıcının eklediği fotoğrafları hazırlar: aç, gerekirse küçült, çalışma klasörüne JPEG olarak kopyala.

Asıl dosyaya dokunulmaz. Uzun kenar 1568 px ile sınırlanır (model daha büyüğünü zaten küçültür; gereksiz
yükleme ve token harcamasını önler). Saydam alanlar beyaz zemine düzleştirilir.
"""
from __future__ import annotations

import io
import time
from pathlib import Path

from PIL import Image, ImageOps

from ..i18n import t

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"}
MAX_EDGE = 1568
MAX_SOURCE_BYTES = 40 * 1024 * 1024
MAX_PER_MESSAGE = 5

_counter = [0]


class AttachmentError(ValueError):
    pass


def is_image_path(p: str | Path) -> bool:
    return Path(p).suffix.lower() in IMAGE_SUFFIXES


def prepare_image(source: str | Path | bytes, out_dir: str | Path) -> Path:
    """source: dosya yolu ya da görüntü baytları (panodan). Dönüş: hazırlanan JPEG'in yolu."""
    if isinstance(source, (str, Path)):
        p = Path(source)
        if not p.is_file():
            raise AttachmentError(t("Dosya bulunamadı: {n}").format(n=p.name))
        if p.stat().st_size > MAX_SOURCE_BYTES:
            raise AttachmentError(t("{n} çok büyük (en fazla {m} MB).").format(n=p.name, m=MAX_SOURCE_BYTES // (1024 * 1024)))
        name = p.stem
        opener = lambda: Image.open(p)  # noqa: E731
    else:
        name = "pano"
        opener = lambda: Image.open(io.BytesIO(source))  # noqa: E731
    try:
        with opener() as im:
            im.seek(0)  # çok kareli GIF/TIFF: ilk kare
            im = ImageOps.exif_transpose(im)
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, "white")
                bg.paste(im, mask=im.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            im.thumbnail((MAX_EDGE, MAX_EDGE))
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            _counter[0] += 1
            safe = "".join(c for c in name if c.isalnum() or c in "-_")[:40] or "foto"
            dst = out / f"ek_{time.strftime('%Y%m%d_%H%M%S')}_{_counter[0]}_{safe}.jpg"
            im.save(dst, "JPEG", quality=85)
            return dst
    except AttachmentError:
        raise
    except Exception as exc:  # noqa: BLE001 - bozuk / desteklenmeyen dosya
        raise AttachmentError(t("Görüntü açılamadı ({e}).").format(e=exc.__class__.__name__)) from None
