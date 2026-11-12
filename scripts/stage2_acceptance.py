"""Aşama 2 kabul betiği.

50 x 30 x 10 mm blok + ortasında Ø8 mm boydan boya delik, yalnızca araç katmanı üzerinden.
Hacmi ve sınır kutusunu doğrular, ekran görüntüsü alır, CATPart ve STEP olarak kaydeder.

Çalıştırma (CATIA açıkken):
    .venv\\Scripts\\python.exe scripts\\stage2_acceptance.py [--workspace work\\stage2] [--undo]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from catia_aibot.tools import CatiaTools  # noqa: E402


def show(label, r):
    status = "OK " if r.ok else "ERR"
    print(f"[{status}] {label}: {json.dumps(r.data, ensure_ascii=False)[:600]}")
    if not r.ok:
        raise SystemExit(f"{label} başarısız")
    return r.data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", default=str(ROOT / "work" / "stage2"))
    ap.add_argument("--undo", action="store_true", help="Sonunda mesaj grubunu geri al (parçayı kapatır).")
    a = ap.parse_args()
    Path(a.workspace).mkdir(parents=True, exist_ok=True)

    t = CatiaTools(workspace=a.workspace)
    t.begin_message("50x30x10 blok + Ø8 delik")
    show("new_part", t.call("new_part", name="Blok50x30x10"))
    sk = show("create_sketch", t.call("create_sketch", support="XY",
                                      elements=[{"type": "rectangle", "id": "r", "center": [0, 0], "width": 50, "height": 30}]))
    assert sk["fully_constrained"], "sketch tam kısıtlı değil"
    show("pad", t.call("pad", sketch=sk["sketch"], length=10))
    faces = show("list_topology", t.call("list_topology", kind="faces"))
    for f in faces["faces"]:
        print("     ", f["id"], f["summary"], f.get("area_mm2"), f.get("normal_confidence"))
    show("hole", t.call("hole", face={"axis": "+Z", "side": "max"}, position=[0, 0, 10], diameter=8, through_all=True))

    expected = 50 * 30 * 10 - math.pi * 4 ** 2 * 10
    chk = show("check_expectation", t.call("check_expectation", volume_mm3=expected, volume_tolerance_pct=0.01,
                                           bbox_size=[50, 30, 10], bbox_min=[-25, -15, 0], bbox_max=[25, 15, 10]))
    for c in chk["checks"]:
        print("      ", "PASS" if c["pass"] else "FAIL", c)
    m = show("measure", t.call("measure", mass=True))
    shot = show("screenshot", t.call("screenshot", views=["iso", "top"]))
    show("save_document", t.call("save_document"))
    show("export_step", t.call("export_step", filename="Blok50x30x10.stp"))
    t.end_message()

    print("\nBeklenen hacim : %.3f mm³" % expected)
    print("Ölçülen hacim  : %.3f mm³" % m["volume_mm3"])
    print("Sınır kutusu   :", m["bounding_box"])
    print("Görüntüler     :", [i["path"] for i in shot["images"]])
    if a.undo:
        print("Geri alma      :", t.undo_last_message())
    t.close()
    print("\nKABUL:", "GEÇTİ" if chk["passed"] else "KALDI")
    return 0 if chk["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
