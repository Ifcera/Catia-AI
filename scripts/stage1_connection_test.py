"""Aşama 1 — bağlantı testi.

Açık CATIA'ya bağlanır, sürümü okur, boş bir CATPart oluşturur ve hangi
çağrıların bu kurulumda çalıştığını raporlar. Dosya kaydetmez.

Çalıştırma:  .venv\\Scripts\\python.exe scripts\\stage1_connection_test.py
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pythoncom  # noqa: E402

from catia_aibot.catia.connection import CatiaNotRunning, connect, read_info  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(label, fn):
    try:
        value = fn()
        results.append((label, True, repr(value)))
        return value
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        results.append((label, False, msg))
        return None


def main() -> int:
    pythoncom.CoInitialize()
    try:
        catia = connect()
    except CatiaNotRunning as exc:
        print(f"HATA: {exc}")
        return 2

    info = read_info(catia)
    print("CATIA:", json.dumps(info.__dict__, ensure_ascii=False))

    check("Application.Visible", lambda: catia.Visible)
    old_alerts = check("Application.DisplayFileAlerts (okuma)", lambda: catia.DisplayFileAlerts)
    check("Application.DisplayFileAlerts = False", lambda: setattr(catia, "DisplayFileAlerts", False))
    check("Application.RefreshDisplay", lambda: catia.RefreshDisplay)
    check("Documents.Count", lambda: catia.Documents.Count)

    doc = check("Documents.Add('Part')", lambda: catia.Documents.Add("Part"))
    if doc is not None:
        part = check("PartDocument.Part", lambda: doc.Part)
        check("Document.Name", lambda: doc.Name)
        if part is not None:
            check("Part.Name", lambda: part.Name)
            check("Part.Bodies.Count", lambda: part.Bodies.Count)
            check("Part.MainBody.Name", lambda: part.MainBody.Name)
            check("Part.OriginElements.PlaneXY.Name", lambda: part.OriginElements.PlaneXY.Name)
            check("Part.Parameters.Count", lambda: part.Parameters.Count)
            check("Part.Update()", lambda: part.Update())
            check("Part.IsUpToDate(Part)", lambda: part.IsUpToDate(part))
        check("Document.Selection", lambda: doc.Selection.Count)
        check("ActiveWindow.Name", lambda: catia.ActiveWindow.Name)
        check("ActiveWindow.ActiveViewer.Viewpoint3D", lambda: catia.ActiveWindow.ActiveViewer.Viewpoint3D is not None)

    # Lisans yoklaması (çakışma analizi = SPA). Biçim belgelerde net değil; deneyerek bakıyoruz.
    sc = catia.SystemConfiguration
    for code in ("MD2", "PDG", "ASD", "SPA", "KIN", "HD2", "PD1", "MD1", "DMN"):
        check(f"SystemConfiguration.IsProductAuthorized('{code}')", lambda c=code: sc.IsProductAuthorized(c))
    check("SystemConfiguration.ProductCount", lambda: sc.ProductCount)

    # pycatia aynı oturuma bağlanabiliyor mu?
    def _pycatia():
        from pycatia import catia as pc_catia

        app = pc_catia()
        return {
            "name": app.name,
            "docs": app.documents.count,
            "release": app.system_configuration.release,
        }

    check("pycatia.catia() bağlantısı", _pycatia)

    if old_alerts is not None:
        check("DisplayFileAlerts eski haline", lambda: setattr(catia, "DisplayFileAlerts", old_alerts))

    print()
    width = max(len(r[0]) for r in results)
    for label, ok, val in results:
        print(f"[{'OK ' if ok else 'ERR'}] {label.ljust(width)}  {val}")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} çağrı başarılı.")
    critical = {"Documents.Add('Part')", "PartDocument.Part", "Part.Update()"}
    return 1 if any(r[0] in critical for r in failed) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(3)
