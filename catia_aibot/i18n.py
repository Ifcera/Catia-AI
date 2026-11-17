"""Arayüz dili (Türkçe / English).

Metinler kodda Türkçe yazılır ve `t("...")` ile sarılır; İngilizce seçiliyse karşılığı EN sözlüğünden gelir
(karşılığı yoksa Türkçe metin gösterilir). Biçimli metinlerde yer tutucular `{ad}` olarak bırakılır ve
`t(...).format(ad=...)` ile doldurulur. Dil uygulama başlarken bir kez seçilir; değiştirmek yeniden başlatma ister.
"""
from __future__ import annotations

LANGUAGES = {"tr": "Türkçe", "en": "English"}
_lang = "tr"


def set_lang(code: str) -> None:
    global _lang
    _lang = code if code in LANGUAGES else "tr"


def lang() -> str:
    return _lang


def t(text: str) -> str:
    if _lang == "en":
        return EN.get(text, text)
    return text


EN: dict[str, str] = {
    # --- pencere / window
    "CATIA Asistanı": "CATIA Assistant",
    "Asistan": "Assistant",
    "Agent bağlı değil": "Agent not connected",
    "Her zaman üstte": "Always on top",
    "CATIA'nın yanına yasla": "Dock beside CATIA",
    "Bağlantı ayarları": "Connection settings",
    "Anahtar…": "Key…",
    "Seç…": "Browse…",
    "Bağlan": "Connect",
    "Sağlayıcı": "Provider",
    "Model": "Model",
    "Klasör": "Folder",
    "Özen": "Effort",
    "Dil": "Language",
    "Dil değişikliği uygulamayı yeniden başlatınca geçerli olur.":
        "The language change takes effect after restarting the app.",
    "Bağlanmak için ayarları seçip 'Bağlan'a basın.": "Choose your settings and press 'Connect'.",
    "CATIA'da ne yapılmasını istediğinizi yazın (ör. \"50×30×10 mm blok, ortasına Ø8 delik\"). "
    "Değişiklikler normal CATIA feature'ları olarak oluşur; her mesaj tek adımda geri alınabilir.":
        "Describe what you want done in CATIA (e.g. \"50×30×10 mm block with a Ø8 hole in the middle\"). "
        "Changes are created as normal CATIA features; each message can be undone in one step.",
    "Ne yapılsın?  (Enter: gönder · Shift+Enter: yeni satır · fotoğraf: + / sürükle / Ctrl+V)":
        "What should I do?  (Enter: send · Shift+Enter: new line · photo: + / drag / Ctrl+V)",
    "Fotoğraf ekle (en fazla {n}). Pencereye sürükleyip bırakabilir ya da panodan Ctrl+V ile yapıştırabilirsiniz.":
        "Add photos (up to {n}). You can also drag them onto the window or paste with Ctrl+V.",
    "Görünümü ekle": "Attach view",
    "CATIA'nın o anki görüntüsünü mesaja iliştir": "Attach the current CATIA view to the message",
    "Seçimi ekle": "Attach selection",
    "CATIA'da seçili öğeleri bağlam olarak gönder": "Send the items selected in CATIA as context",
    "Son mesajın CATIA'daki tüm değişikliklerini geri al": "Undo all CATIA changes of the last message",
    "Gönder": "Send",
    "Durdur": "Stop",
    "Yeni konuşma": "New conversation",
    "Bilgisayarınızdaki Claude Code oturumu kullanılır; API anahtarı gerekmez, kullanım claude.ai aboneliğinizden düşer.":
        "Uses the Claude Code session on this computer; no API key needed, usage counts against your claude.ai subscription.",
    "{p} kullanımı sağlayıcı hesabınızdan ücretlendirilir.": "{p} usage is billed to your provider account.",
    "(varsayılan)": "(default)",
    "Çalışma klasörü": "Working folder",
    "Bağlanıyor…": "Connecting…",
    "Bağlı": "Connected",
    "Hazır · {d}": "Ready · {d}",
    "Bağlandı: {d} · klasör: {w}": "Connected: {d} · folder: {w}",
    "Bağlı değil": "Not connected",
    "Bağlanamadı.": "Could not connect.",
    "CATIA kapalı": "CATIA closed",
    "Durduruluyor…": "Stopping…",
    "Ekteki görüntüye bak.": "Look at the attached image.",
    "Önce 'Bağlan' ile bir sağlayıcıya bağlanın.": "Connect to a provider with 'Connect' first.",
    "Bir mesaja en fazla {n} fotoğraf eklenebilir.": "At most {n} photos can be attached to a message.",
    "Fotoğraf ekle": "Add photo",
    "Görüntüler ({p})": "Images ({p})",
    "Çalışıyor…": "Working…",
    "{s} sn": "{s} s",
    "{m} dk {s} sn": "{m} min {s} s",
    "Plan onayınızı bekliyor.": "Waiting for your plan approval.",
    "Durduruldu.": "Stopped.",
    "Hata ile bitti.": "Finished with an error.",
    "Adım sınırına ulaşıldı.": "Step limit reached.",
    "Hazır.": "Ready.",
    "Model yanıtı bekleniyor": "Waiting for the model",
    "Model düşünüyor": "Model is thinking",
    "Yanıt yazılıyor": "Writing the answer",
    "{x} çalışıyor": "{x} running",
    "Plan onaylandı, başla.": "Plan approved, start.",
    "Hata": "Error",
    "Durduruldu. Yarım kalan değişiklikler '↶' ile geri alınabilir.": "Stopped. Partial changes can be undone with '↶'.",
    "Parça: {p} · ": "Part: {p} · ",
    "Planda şunları değiştir: ": "Change the following in the plan: ",
    "CATIA penceresi bulunamadı.": "CATIA window not found.",
    "CATIA'nın yanına yaslandı.": "Docked beside CATIA.",
    # --- widgets
    "{n} — açmak için tıklayın": "{n} — click to open",
    "Seçim: {c}": "Selection: {c}",
    "Montaj kontrolü": "Assembly check",
    "Kontrol": "Check",
    "geçti": "passed",
    "KALDI": "FAILED",
    "<b>Plan</b> — onayınızı bekliyor": "<b>Plan</b> — waiting for your approval",
    "Montaj:": "Assembly:",
    "Onayla ve başla": "Approve and start",
    "Değiştir…": "Change…",
    "Kaldır": "Remove",
    # --- controller
    "Claude Code (abonelik)": "Claude Code (subscription)",
    "Claude API (anahtar)": "Claude API (key)",
    "ChatGPT (anahtar)": "ChatGPT (key)",
    "Gemini (anahtar)": "Gemini (key)",
    "Model listesi alınamadı: {e}": "Could not get the model list: {e}",
    "CATIA açık değil. CATIA V5'i açıp tekrar bağlanın.": "CATIA is not running. Start CATIA V5 and connect again.",
    "Claude Code kurulu değil (claude komutu bulunamadı).": "Claude Code is not installed (claude command not found).",
    "Claude Code'da oturum açık değil. Terminalde 'claude' çalıştırıp giriş yapın.":
        "Claude Code is not signed in. Run 'claude' in a terminal and sign in.",
    "abonelik": "subscription",
    "varsayılan": "default",
    "{p} için kayıtlı API anahtarı yok. 'Anahtar…' ile girin.": "No saved API key for {p}. Add one with 'Key…'.",
    "Görünüm alınamadı: {e}": "Could not capture the view: {e}",
    "CATIA'da seçili öğe yok; seçim eklenmedi.": "Nothing is selected in CATIA; no selection attached.",
    "Geri alınacak bir değişiklik yok.": "There is nothing to undo.",
    "'{g}' kısmen geri alındı; hatalar: {e}": "'{g}' partly undone; errors: {e}",
    "'{g}' geri alındı ({n} işlem).": "'{g}' undone ({n} operations).",
    "Yeni konuşma başladı (CATIA'daki model olduğu gibi kaldı).":
        "New conversation started (the model in CATIA was left as is).",
    # --- key dialog
    "{p} — API anahtarı": "{p} — API key",
    "Kayıtlı anahtar: {k}": "Saved key: {k}",
    "Anahtarı yapıştırın": "Paste the key",
    "Anahtar yalnızca Windows Kimlik Bilgileri Yöneticisi'ne kaydedilir; dosyaya ya da koda yazılmaz.":
        "The key is stored only in Windows Credential Manager; never in a file or in code.",
    "Sil": "Delete",
    "Doğrula ve kaydet": "Verify and save",
    "Kapat": "Close",
    "Anahtar boş.": "The key is empty.",
    "Doğrulanıyor…": "Verifying…",
    "Doğrulanamadı, kaydedilmedi: {e}": "Could not verify, not saved: {e}",
    "Kaydedildi ✓ — {n} model erişilebilir.": "Saved ✓ — {n} models available.",
    "Anahtar silindi.": "Key deleted.",
    # --- attachments
    "Dosya bulunamadı: {n}": "File not found: {n}",
    "{n} çok büyük (en fazla {m} MB).": "{n} is too large (max {m} MB).",
    "Görüntü açılamadı ({e}).": "Could not open the image ({e}).",
    # --- labels: araç adları / tool names
    "Yeni parça": "New part", "Yeni montaj": "New assembly", "Belgeler": "Documents", "Belge etkin": "Document activated",
    "Belge açıldı": "Document opened", "Kaydedildi": "Saved", "STEP dışa aktarıldı": "STEP exported",
    "Belge kapatıldı": "Document closed", "STL dışa aktarıldı": "STL exported", "Sketch": "Sketch",
    "Helix": "Helix", "Süpürme": "Sweep", "Yay": "Spring", "Tırtıl": "Knurl", "Gerçek diş": "Modelled thread", "Malzeme": "Material", "Malzemeler": "Materials",
    "Sketch'e ekleme": "Add to sketch", "Sketch bilgisi": "Sketch info", "Delik": "Hole", "Pah": "Chamfer",
    "Doğrusal dizi": "Rectangular pattern", "Dairesel dizi": "Circular pattern", "Ayna": "Mirror",
    "Yeni gövde": "New body", "Referans düzlem": "Reference plane", "Parametreler": "Parameters",
    "Parametre değişti": "Parameter changed", "Silindi": "Deleted", "Etkinlik": "Activation",
    "Ağaç okundu": "Tree read", "Güncelleme": "Update", "Ölçüm": "Measure", "Mesafe": "Distance",
    "Topoloji": "Topology", "Görüntü": "Capture", "Seçim": "Selection", "Plan": "Plan",
    "Bileşen eklendi": "Component added", "Bileşenler": "Components", "Konum": "Position",
    "Montaj kısıtı": "Assembly constraint", "Montaj güncellendi": "Assembly updated", "Kısıt silindi": "Constraint removed",
    # --- labels: özetler / summaries
    "Hacim": "Volume", "Boyut": "Size", "Min köşe": "Min corner", "Maks köşe": "Max corner",
    "sonuna kadar": "through all", "yüzey": "face", "hata": "error", "yön düzeltildi": "direction corrected",
    "tam kısıtlı": "fully constrained", "serbestlik≈{n}": "DOF≈{n}", "GEÇTİ": "PASSED",
    "{n} yüz": "{n} faces", "{n} kenar": "{n} edges", "{n} feature": "{n} features",
    " · update hatası var": " · update error", " · taşındı: {m}": " · moved: {m}",
    "hatasız": "no errors", "beklenen": "expected", "Kısıtlar: {n} adet": "Constraints: {n}",
    " · sorunlu: ": " · problems: ", "Çakışma yok": "No clash", "Çakışma: ": "Clash: ",
    "Temas: {a} ↔ {b}": "Contact: {a} ↔ {b}", " (beklenen)": " (expected)",
    "Beklenen temas yok: {a} ↔ {b}": "Expected contact missing: {a} ↔ {b}",
    "Abonelik": "Subscription", " · 5 saatlik kullanım %{p}": " · 5-hour usage {p}%",
    "{i} giriş / {o} çıkış token": "{i} input / {o} output tokens",
}

# Model yanıt dili: İngilizce seçiliyse sistem prompt'una eklenir (prompt Türkçe kalır, yanıt İngilizce olur).
REPLY_LANGUAGE_NOTE = {
    "en": "\n\nLANGUAGE: The user interface is in English. Always reply to the user in English "
          "(plans, questions, summaries), whatever language the instructions above are written in.",
}
