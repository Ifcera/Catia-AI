HEDEF
CATIA'da 3D yazıcıyla basılabilir, çalışan bir ortaçağ mancınığı (mangonel tipi) tasarla. Ekteki fotoğraf referanstır:
silüeti ve oranları ona benzesin — sağlam ahşap görünümlü bir çerçeve, iki büyük parmaklıklı (spoklu) teker, ucunda kaşık
biçimli bir kepçe olan uzun bir atış kolu, bir durdurma kirişi ve görünen bir pivot mili. Montajın tamamı 150 × 150 × 150 mm
bir küpe sığmalı.

KOORDİNATLAR
- X = atış yönü (ileri +X), Y = yan, Z = yukarı. Zemin z = 0 (tekerlerin alt noktası).
- Montaj orijini, tabanın zemindeki izdüşümünün ortası. Montaj x ve y'de −75…+75, z'de 0…150 içinde kalmalı.
- Her parçayı kendi doğal orijinine göre kur (miller ve pimler: ekseni Z, alt ucu z = 0), sonra montajda kısıtlarla yerleştir.

PARÇALAR (önerilen başlangıç ölçüleri — gerekirse değiştir ama planda gerekçesini yaz)
1. Yan çerçeve ×2 (aynı parça iki kez): alt kiriş + iki eğik dikme (A biçimi). Dikmelerin birleştiği yerde pivot mili deliği,
   alt kirişte teker mili deliği ve çapraz kirişler için delikler. Kiriş kesiti en az 8 × 10 mm.
2. Çapraz kirişler (en az 2): iki yan çerçeveyi birbirine bağlar; uçlarında yan çerçevedeki deliklere giren pimler.
3. Durdurma kirişi: kolu atış açısında aniden durduran yatay kiriş; kolun çarptığı yüzü düz ve geniş.
4. Atış kolu: pivot deliği, uzun kol ve ucunda kepçe. Kepçe 10–15 mm çaplı taşı tutmalı (iç çap ≈ 20 mm, derinlik ≈ 8 mm).
   Kolun pivot arkasında kalan kısa ucunda lastik bağlantı kancası.
5. Pivot mili: Ø6 mm, kolu iki yan çerçeve arasında taşır; uçlarında kolun ve milin kaymasını önleyen omuz ya da başlık.
6. Tekerler: büyük, parmaklıklı (4–6 parmak), göbek deliği mile 0,3 mm boşluklu (Ø6 mil → Ø6,3 delik). Ayrı gövdeler.
   Fotoğrafta her yanda iki teker varsa toplam 4 teker ve 2 teker mili yap — bunu planda bana sor.
7. Teker mili/milleri: Ø6 mm, tekerin dışında kaymayı önleyen başlık.
8. Lastik bağlantıları: çerçevede (önde, alt kirişlerde) iki çıkıntı/kanca; kolda bir kanca. Lastiğin kayıp çıkmaması için
   kancaların ucunda tırnak, lastiğin değdiği kenarlarda en az 1 mm fillet.
9. Kurma mandalı (yapılabiliyorsa): kolu kurulu konumda tutan, çekilince bırakan bir pim ya da mandal.

İŞLEVSEL KONTROLLER
- Pivot yüksekliğini ve kol boyunu planda hesapla: kolun hem kurulu (arkaya yatık) hem atış (durdurucuya dayalı) konumunda
  kepçe ucu dahil montaj küpün içinde kalmalı (x: −75…+75, z ≤ 150). Başlangıç önerisi: pivot z ≈ 50–55 mm, pivot–kepçe
  merkezi ≈ 70–80 mm, atış açısı yataydan ≈ 70–80°. Planda iki konum için uç koordinatlarını sayıyla ver.
- Hareketli parçalar dönebilmeli: pim/mil ile delik arası 0,2–0,3 mm (çap farkı). Dönen parçalar çevresine en az 1 mm boşluk.

BASKI KISITLARI (FDM, PLA/PETG)
- En ince duvar 1,2 mm. Mümkün olduğunca 45°'den büyük çıkıntı (overhang) yok; destek ihtiyacını en aza indir.
- Her parça ayrı CATPart (birbirine kaynamış gövde yok); her parça 220 × 220 × 250 mm tablaya sığmalı.
- Lastik bağlantı bölgeleri yükü taşıyacak kalınlıkta olsun (kanca kökü ≥ 4 mm).

ÇALIŞMA ŞEKLİ
1. Önce `present_plan` ile planı sun ve dur: parça listesi (adet, yöntem, ana ölçüler, beklenen hacim ve sınır kutusu),
   montaj ilişkileri (hangi parça sabit, hangi eksenler çakışık, hangi yüzeyler temas eder), iki kol konumunun hesabı ve
   küpe sığma hesabı. Yalnızca gerçekten gerekli soruları sor.
2. Onayımdan sonra parçaları tek tek yap. Her parçada: tam kısıtlı sketch'ler, `check_expectation` ile hacim ve sınır kutusu,
   bir `iso` görüntü, kaydet ve `export_stl` ile STL dışa aktar. Önemli ölçüler normal CATIA ölçüleri olsun ki sonradan
   `set_parameter` ile değiştirebileyim (kol boyu, kepçe çapı, teker çapı, taş çapı, bağlantı konumları, boşluklar).
3. Montaj: `new_product`, `add_component`, ilk yan çerçeveyi `fix`le, diğerlerini kısıtlarla yerleştir. Kolu atış konumunda
   (durdurma kirişine dayalı) kur. `check_assembly` ile çakışma (girişim 0 olmalı), beklenen temaslar ve
   `fit_within: [150, 150, 150]` kontrol et. Sonra kolun açı kısıtını kurulu konuma çevirip bir kez daha kontrol et.
   Montajı iso, front ve top görünümlerinde görüntüle; montajı kaydet ve STEP olarak da dışa aktar.
4. Raporu sayılarla ver, izlenimle değil:
   - Montajın sınır kutusu (iki kol konumunda), küpe sığıyor mu.
   - Hareketli parçalar arasında girişim (check_assembly sonucu).
   - Her parçanın hacmi, sınır kutusu, STL üçgen sayısı, update hatası olmadığı (katılar kapalı = su geçirmez).
   - En ince duvar ve en büyük çıkıntı açısı: tasarım ölçülerinden hesapla ve hangi ölçüye dayandığını yaz
     (bunları doğrudan ölçen bir araç yok, bunu açıkça belirt).
5. Özet: parça listesi ve ölçüleri, her parça için önerilen baskı yönü, montaj adımları, lastik bağlantı noktalarının
   koordinatları ve sonradan değiştirilebilecek ana parametrelerin listesi (`list_parameters` adlarıyla).
