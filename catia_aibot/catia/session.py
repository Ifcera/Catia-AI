"""CATIA oturumu: bağlantı, çalışma klasörü, geri alma günlüğü ve ortak yardımcılar.

Bu sınıfın tüm metodları COM iş parçacığında (ComThread) çağrılmalıdır.
"""
from __future__ import annotations

import math
from pathlib import Path

import pywintypes

from .connection import CatiaNotRunning, connect
from .errors import ToolError, com_error_text
from .journal import Journal

UNIT_M3_TO_MM3 = 1e9
UNIT_M2_TO_MM2 = 1e6

ORIGIN_PLANES = {"XY": "PlaneXY", "YZ": "PlaneYZ", "ZX": "PlaneZX", "XZ": "PlaneZX"}

_VB_ARRAY = """Public Function f(o)
  Dim a({n})
  o.{method} a
  f = a
End Function
"""
_VB_TYPENAME = """Public Function f(o)
  f = TypeName(o)
End Function
"""


class CatiaSession:
    def __init__(self, workspace: str | Path | None = None):
        self._app = None
        self.workspace: Path | None = Path(workspace).resolve() if workspace else None
        self.journal = Journal()
        # Topoloji kimlikleri: belge adı -> {brep_anahtarı: "F3"} ve sayaçlar
        self.topo_ids: dict[str, dict[str, str]] = {}
        self.topo_counters: dict[str, dict[str, int]] = {}
        # Asistanın oluşturduğu nesneler: (belge, ad)
        self.created: set[tuple[str, str]] = set()
        # Asistanın kurduğu sketch öğe adları: (belge, sketch) -> {"elements": {...}, "dof": int}
        self.sketch_meta: dict[tuple[str, str], dict] = {}

    # ------------------------------------------------------------------ bağlantı
    @property
    def app(self):
        if self._app is not None:
            try:
                _ = self._app.Name
                return self._app
            except pywintypes.com_error:
                self._app = None
        try:
            self._app = connect()
        except CatiaNotRunning as exc:
            raise ToolError(str(exc), hint="Kullanıcıdan CATIA'yı açmasını isteyin.") from None
        try:
            self._app.DisplayFileAlerts = False
        except pywintypes.com_error:
            pass
        return self._app

    def is_connected(self) -> bool:
        try:
            _ = self.app
            return True
        except ToolError:
            return False

    # --------------------------------------------------------------- belgeler
    def document(self, name: str | None = None):
        app = self.app
        if name:
            try:
                return app.Documents.Item(name)
            except pywintypes.com_error:
                names = [app.Documents.Item(i).Name for i in range(1, app.Documents.Count + 1)]
                raise ToolError(f"'{name}' adlı açık belge yok.", hint=f"Açık belgeler: {names}") from None
        try:
            return app.ActiveDocument
        except pywintypes.com_error:
            raise ToolError("CATIA'da açık belge yok.", hint="Önce new_part ile bir parça oluşturun.") from None

    def part_document(self, name: str | None = None):
        doc = self.document(name)
        try:
            part = doc.Part
        except (pywintypes.com_error, AttributeError):
            raise ToolError(
                f"'{doc.Name}' bir CATPart değil.",
                hint="Parça işlemleri için document parametresine bir .CATPart adı verin.",
            ) from None
        return doc, part

    def body(self, part, name: str | None = None):
        if name:
            try:
                return part.Bodies.Item(name)
            except pywintypes.com_error:
                names = [part.Bodies.Item(i).Name for i in range(1, part.Bodies.Count + 1)]
                raise ToolError(f"'{name}' adlı gövde yok.", hint=f"Gövdeler: {names}") from None
        try:
            obj = part.InWorkObject
            if self.typename(obj) == "Body":
                return obj
            parent = obj.Parent
            for _ in range(3):
                if self.typename(parent) == "Body":
                    return parent
                parent = parent.Parent
        except pywintypes.com_error:
            pass
        return part.MainBody

    def find_object(self, part, name: str):
        try:
            obj = part.FindObjectByName(name)
        except pywintypes.com_error:
            obj = None
        if obj is None:  # R20: bulunamayan ad için None döner
            raise ToolError(f"'{name}' adlı nesne parçada bulunamadı.", hint="get_design_state ile ağaca bakın.")
        return obj

    def origin_plane(self, part, key: str):
        attr = ORIGIN_PLANES.get(key.upper())
        if not attr:
            raise ToolError(f"Bilinmeyen ana düzlem '{key}'.", hint="XY, YZ veya ZX kullanın.")
        return getattr(part.OriginElements, attr)

    # ---------------------------------------------------------- COM yardımcıları
    def vb_array(self, obj, method: str, n: int) -> tuple:
        """Dizi çıktı parametreli metodlar (GetCOG, GetPlane...) için VBScript köprüsü."""
        try:
            return tuple(self.app.SystemService.Evaluate(_VB_ARRAY.format(n=n - 1, method=method), 0, "f", [obj]))
        except pywintypes.com_error as exc:
            raise ToolError(f"{method} okunamadı: {com_error_text(exc)}") from None

    def typename(self, obj) -> str:
        try:
            return str(self.app.SystemService.Evaluate(_VB_TYPENAME, 0, "f", [obj]))
        except pywintypes.com_error:
            return "?"

    def spa(self, doc):
        return doc.GetWorkbench("SPAWorkbench")

    @staticmethod
    def ref(part, obj):
        return part.CreateReferenceFromObject(obj)

    def _still_exists(self, doc, obj) -> bool:
        try:
            name = obj.Name
        except pywintypes.com_error:
            return False
        try:
            part = doc.Part
        except (pywintypes.com_error, AttributeError):
            return True
        try:
            # R20: bulunamayan ad için hata değil None döner.
            return part.FindObjectByName(name) is not None
        except pywintypes.com_error:
            return False

    def delete_objects(self, doc, objs, missing_ok: bool = True) -> None:
        """Nesneleri siler. missing_ok: zaten silinmiş nesneler (ör. Pad silinince
        CATIA'nın birlikte sildiği sketch'i) hata saymaz."""
        sel = doc.Selection
        sel.Clear()
        try:
            for o in objs:
                try:
                    sel.Add(o)
                except pywintypes.com_error:
                    if not missing_ok:
                        raise
            if sel.Count:
                try:
                    sel.Delete()
                except pywintypes.com_error:
                    for o in objs:
                        sel.Clear()
                        try:
                            sel.Add(o)
                            sel.Delete()
                        except pywintypes.com_error:
                            if not missing_ok or self._still_exists(doc, o):
                                raise
        finally:
            sel.Clear()

    # ----------------------------------------------------------------- update
    def failing_features(self, part) -> list[str]:
        bad: list[str] = []
        try:
            for i in range(1, part.Bodies.Count + 1):
                b = part.Bodies.Item(i)
                for j in range(1, b.Shapes.Count + 1):
                    s = b.Shapes.Item(j)
                    if not part.IsUpToDate(s):
                        bad.append(s.Name)
                for j in range(1, b.Sketches.Count + 1):
                    s = b.Sketches.Item(j)
                    if not part.IsUpToDate(s):
                        bad.append(s.Name)
            for i in range(1, part.HybridBodies.Count + 1):
                hb = part.HybridBodies.Item(i)
                for j in range(1, hb.HybridShapes.Count + 1):
                    s = hb.HybridShapes.Item(j)
                    if not part.IsUpToDate(s):
                        bad.append(s.Name)
        except pywintypes.com_error:
            pass
        return bad

    def update(self, part) -> list[str]:
        """Parçayı günceller. Başarısızsa güncellenemeyen feature adlarını döndürür."""
        try:
            part.Update()
            return []
        except pywintypes.com_error:
            bad = self.failing_features(part)
            return bad or ["(bilinmeyen feature)"]

    # -------------------------------------------------------- çalışma klasörü
    def resolve_path(self, filename: str, suffixes: tuple[str, ...] = (), must_exist: bool = False) -> Path:
        if self.workspace is None:
            raise ToolError("Çalışma klasörü seçilmemiş.", hint="Kullanıcıdan bir proje klasörü seçmesini isteyin.")
        p = Path(filename)
        if not p.is_absolute():
            p = self.workspace / p
        p = p.resolve()
        try:
            p.relative_to(self.workspace)
        except ValueError:
            raise ToolError(
                f"'{filename}' çalışma klasörünün dışında.",
                hint=f"Yalnızca {self.workspace} altına yazılabilir; göreli bir dosya adı verin.",
            ) from None
        if suffixes and p.suffix.lower() not in [s.lower() for s in suffixes]:
            p = p.with_suffix(suffixes[0])
        if must_exist and not p.exists():
            raise ToolError(f"Dosya bulunamadı: {p.name}")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def doc_key(self, doc) -> str:
        return doc.Name


def r6(x: float) -> float:
    return round(float(x), 6)


def r3(x: float) -> float:
    v = round(float(x), 3)
    return 0.0 if v == 0 else v


def vec3(v) -> list[float]:
    return [r3(c) for c in v]


def norm(v):
    length = math.sqrt(sum(c * c for c in v))
    return [c / length for c in v] if length > 1e-12 else list(v)


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))
