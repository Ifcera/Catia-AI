# CATIA V5 R20 (B20, win_b64) — gerçek kurulumda doğrulanan API davranışları

Her madde bu makinedeki CATIA'da deneyle doğrulandı (2026-11-12). "Varsayım" yok; doğrulanamayanlar ayrıca belirtilmiştir.

## Bağlantı ve genel
- `pythoncom.GetActiveObject("CATIA.Application")` + `win32com.client.dynamic.Dispatch` (geç bağlama) çalışıyor; gencache gerekmiyor.
- `pycatia 0.10.1` aynı oturuma bağlanabiliyor; araç katmanı doğrudan win32com kullanıyor.
- Dizi çıktı parametreli metodlar (`GetCOG`, `GetPlane`, `GetPointsOnCurve`, `GetAbsoluteAxisData`, `GetCenter`, `GetOrigin`,
  `GetSightDirection`...) win32com'dan doğrudan çağrılamıyor → `SystemService.Evaluate` ile VBScript köprüsü (`session.vb_array`).
- `TypeName(o)` de aynı köprüyle okunuyor.
- `SystemConfiguration.IsProductAuthorized(x)` **uydurma kodlara bile True** dönüyor → lisans tespiti için kullanılamaz.
- `DisplayFileAlerts = False` okunup yazılabiliyor.
- `Part.FindObjectByName(ad)` bulunamayan ad için **hata değil `None`** döndürüyor.
- Yinelenen `Product.PartNumber` (aynı numara iki açık belgede) reddediliyor.

## Ölçüm (SPAWorkbench)
- Birimler: `Measurable.Volume` m³, `Area` m²; `Length`, `GetCOG`, `GetPoint`, `GetMinimumDistance`, `Radius` mm (Length ilk notta yanlışlıkla m yazılmıştı); `Inertia.Mass` kg (varsayılan yoğunluk 1000).
- **Tuzak:** feature'ı olmayan (boş) bir gövdeyi `GetMeasurable` ile ölçmek, o gövdenin ölçümünü belge boyunca bozuyor
  (sonradan katı eklense bile "Volume failed"). Çözüm: boş gövde ölçülmez; yine de bozulursa Inertia yedeği (kütle/yoğunluk).
- `GetPlane` normali dış yönü **garanti etmiyor** (alt yüz +Z döndü). Dış yön sınır/ağırlık merkezi ile tahmin ediliyor.
- Silindirik yüzde `GetAxis` / `GetDirection` sıfır dönüyor; `Radius` ve `GetCOG` doğru.
- Bir feature'ın kendi ölçümü (`GetMeasurable(ref(Hole.1))`) o feature'ın araç gövdesini veriyor (ör. delik silindiri).
- Sınır kutusu için doğrudan API yok → geçici geometrik sette 6 `Extremum` (+/−X,Y,Z), sonra silinir.
  Extremum sonucu yüz, doğru ya da nokta olabilir: `GetCOG` → `GetPoint` → `GetPointsOnCurve` sırasıyla denenir.

## Topoloji
- `Selection.Search("Topology.CGMFace,sel")` / `"Topology.CGMEdge,sel"` gövdenin güncel yüz/kenarlarını veriyor.
- `Reference.DisplayName` örneği: `Selection_RSur:(Face:(Brp:(Pad.1;2);None:();Cf11:());Pad.1_ResultOUT;Z0;G3563)`.
  Sondaki `<Feature>_ResultOUT;...` ağaç büyüdükçe değişiyor; baştaki `RSur:(Face:(...))` çekirdeği yüz yaşadıkça sabit (fillet sonrası doğrulandı).
- Kenar adı iki komşu yüzün çekirdeğini içeriyor → komşuluk metinden çıkarılabiliyor.
- Kenar aramasında sketch telleri (`WireREdge`) de geliyor; gövde kenarları `REdge:(Edge:` ile başlıyor.
- Seçimden gelen Reference, sketch desteği, delik yüzü, fillet/chamfer kenarı olarak doğrudan kullanılabiliyor.
- `CreateReferenceFromBRepName` ham adla başarısız; `WithTemporaryBody;...` bayraklarıyla referans dönüyor ama ölçülemiyor (alan 0) → kullanılmıyor.

## Sketch
- `AddMonoEltCst(10 = yatay)` CATIA'da "Parallelism" adıyla, Type=8 olarak saklanıyor (normal).
- `Constraint.Status`: 0 = OK, 1 = sağlanamadı ... (`CatConstraintStatus`).
- Serbestlik derecesi (DOF) için otomasyon API'si bulunmadı → araç kendi tahminini tutuyor (`dof_estimate`).
- Yerel eksenler: XY → H=+X, V=+Y; YZ → H=+Y, V=+Z; **ZX → H=+Z, V=+X**. Yüzeye açılan sketch'in orijini parça orijininin izdüşümü.
- `Sketch.CenterLine = line` shaft/groove ekseni olarak çalışıyor.

## Feature'lar
- Pad / Pocket (`LimitMode=2` sonuna kadar) / Shaft / Groove / Fillet (`AddObjectToFillet`) / Chamfer / Mirror (tüm gövde) / Boolean doğrulandı.
- `Pad.IsSymmetric`, `DirectionOrientation` var.
- Update hatası: `Part.Update()` istisna fırlatıyor, `IsUpToDate(feature)=False` hatalı feature'ı gösteriyor; diyalog açılmadı.
- Katıya değmeyen pocket **hata vermiyor** → araç hacim değişimine bakıp uyarıyor.
- Pad silinince (`Selection.Delete`) sketch'i de siliniyor.
- **Delik:** `AddNewHoleFromPoint` ile kurulan deliğin dairesel pattern'ı hiç kesmiyor; `AddNewHoleFromSketch`
  (yüzeydeki sketch'te tam kısıtlı nokta) doğru çalışıyor → araç bunu kullanıyor.
- **Pattern yönü:** ana düzlem verildiğinde ilk iç ekseni kullanılıyor (XY→X, YZ→Y, ZX→Z); dairesel pattern'da düzlemin normali eksen.
  Yön işareti feature türüne göre değişiyor (pocket'ta +X, delikte −X) ve `GetFirstDirection` ikisinde de aynı değeri bildiriyor
  → araç sonucu ağırlık merkezi kaymasıyla ölçüp gerekirse `FirstOrientation`'ı çeviriyor. Eksen doğrusu (`LinePtDir`) dikdörtgen pattern yönü olarak update hatası veriyor.

## Montaj (Assembly Design)
- `Products.AddComponentsFromFiles((yollar,), "All")` ve `AddExternalComponent(açık_belge)` bileşen ekliyor; `Products.Remove(ad)` kaldırıyor.
- `Position.GetComponents` (12 değer, VBScript köprüsü) / `SetComponents`: [X ekseni, Y ekseni, Z ekseni, orijin] — ebeveyn koordinatında.
- Montaj bağlamında referans adları (`root.CreateReferenceFromName`):
  - ana düzlem: `"<Kök>/<Örnek>/!xy plane"` (parçadaki düzlemin adı)
  - bileşenin kendisi (Fix için): `"<Kök>/<Örnek>/!<Kök>/<Örnek>/"` (`"!<Örnek>"` başarısız)
  - yüzey: parça belgesinde `Selection.Search` ile alınan `DisplayName` (`Selection_RSur:(...)`) aynen eklenir; ölçüm montajda doğru.
  - **silindir ekseni:** `"!Axis:(Selection_RSur:(...))"`. İki silindirik yüzeyin kendisi arasında coincidence durum 4
    (uygun olmayan geometri) verip hiçbir şeyi taşımıyor; eksen biçimiyle durum 0 ve bileşen eksene geliyor.
- Ürün belgesinin seçimiyle (`sel.Add(örnek); Search("Topology.CGMFace,sel")`) yüzey **dönmüyor** → parça belgesinin topolojisi kullanılıyor.
- Kısıtlar `root.Connections("CATIAConstraints")`: Fix=0, Offset(Distance)=1, Coincidence(On)=2, Angle=6, Contact(SurfContact)=20.
  `root.Update()` bileşenleri taşıyor. Fix kısıtında `Type`/`Status` okumak "catastrophic failure" veriyor (adı okunabiliyor).
- Çakışma analizi bu kurulumda **lisanslı ve çalışıyor**: `SPAWorkbench.Clashes.Add()` → `ComputationType=0` → `Compute()` →
  `Conflicts`: Type 0 = çakışma (Value negatif = girişim, ör. −10.014 mm), 1 = temas (Value 0), 2 = boşluk. `Clashes.Remove` ile kayıt silinir.
- `Inertias.Add(root)` montajın toplam kütlesini veriyor.

## Sarmal, süpürme, diş, malzeme
- `HybridShapeFactory.AddNewHelix(eksen doğrusu, ters, başlangıç noktası, hatve, yükseklik, saat_yönü, açı, koniklik, dışa)`:
  boy formülle birebir (n·√((2πr)²+p²)); `saat_yönü=False` sağ el sarımı.
- Profil eğriye dik düzlemde (`AddNewPlaneNormal(eğri, nokta)`) çizilip `ShapeFactory.AddNewRibFromRef(profil, eğri)` ile
  süpürülünce hacim A·L ile birebir; `AddNewSlotFromRef` kanal açar ve dairesel dizisi tam N kat kaldırır.
- Üç helix'i `AddNewJoin` ile birleştirip tek rib yapmak **başarısız** (geçişlerde kırık) → kapalı uçlu yayda her parça ayrı rib.
- `AddNewSplit(XY, 0)` düzlemin + tarafını bırakır (taşlanmış yay uçları).
- Sınır kutusu: sarmal gövdede extremum sonucu ölçülemiyor (GeometryName 0) ve gövdenin bütününe `GetMinimumDistance`
  reddediliyor; **extremum → uzak ofset düzlem** mesafesi doğru çalışıyor (yedek yöntem olarak kullanılıyor).
- Dişli delik: `Hole.ThreadingMode = 0`, `ThreadDiameter`, `ThreadPitch`, `ThreadDepth`; delik `Diameter` = kılavuz çapı.
  Sonuna kadar delikte `ThreadDepth` verilmezse varsayılan 10 mm parçadan uzun kalıp update hatası veriyor.
  `AddNewHoleFromSketch(sketch, depth)` derinlik argümanını uygulamıyor (delik 10 mm kalıyor): `BottomLimit.Dimension.Value`
  ayrıca yazılmalı. Kör delikte `ThreadDepth` ≥ delik derinliği ise update hatası; diş boyu delikten kısa olmalı.
- `Hole.GetOrigin` giriş yüzündeki merkez, `Hole.GetDirection` malzemeye doğru birim vektör.
- Rib/Slot varsayılan "keep angle" modu, merkezde olmayan profili helix boyunca **döndürüyor** (helix burulması kadar:
  M8 dış dişte 8,5 turda ~165°; köşe 3,23 yerine 2,94 mm yarıçapa iniyor). `slot.PullingDirElement = <helix ekseni>`
  ile profil eksene göre sabit kalıyor; tur başına hacim Pappus ile 4 haneye kadar eşleşiyor. Dairesel profilde
  (yay, tırtıl) fark etmez.
- Malzeme: `<kurulum>/startup/materials/Catalog.CATMaterial` → `Families` → `Materials` ("Brass" 8216 kg/m³);
  `part.GetItem("CATMatManagerVBExt").ApplyMaterialOnBody(gövde, malzeme, 0)`.

## Görüntü
- `Viewer.CaptureToFile(5, yol)` JPEG; boyut = CATIA görüntü alanı (bu makinede 300×400, bir çalıştırmada 800×379).
- `SpecsAndGeomWindow.Layout = 1` ağacı gizliyor; ara sıra `RPC_E_SERVERFAULT` → tekrar deneniyor.
- `PutSightDirection` / `PutUpDirection` / `Reframe` çalışıyor.

## Doğrulanmayanlar
- Modal diyalog bekçisi (`DialogGuard`) yazıldı ama testlerde CATIA hiç diyalog açmadığı için gerçek bir diyalogla denenmedi.
- Counterbored/countersunk delik, kenar seçiciyle pattern yönü, `create_plane` için yüzey tabanı, `open_document` testte çalıştırılmadı.
- Çakışma lisansı yokken yedek yöntem (bileşenler arası mesafe) gerçek lisanssız ortamda denenmedi.
