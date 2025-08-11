import os
import requests
import io
from PIL import Image, ImageDraw, ImageFont

print("--- Görsel İşleme Detaylı Testi Başlatılıyor ---")

try:
    # --- Gerekli Dosya ve Klasör Yollarını Tanımlama ---
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    FONT_DIR = os.path.join(BASE_DIR, "media", "font")
    SAVE_DIR = os.path.join(BASE_DIR, "styled_images")
    FONT_BOLD = os.path.join(FONT_DIR, "Montserrat-Bold.ttf")

    # Test için kullanılacak basit bir görsel URL'si
    test_url = "https://storage.googleapis.com/cloud-samples-data/vision/label/wakeupcat.jpg"

    # 1. Klasör kontrolü
    print(f"[TEST] Kayıt dizini: {SAVE_DIR}")
    if not os.path.exists(SAVE_DIR):
        print("  -> Kayıt dizini yok, oluşturuluyor...")
        os.makedirs(SAVE_DIR, exist_ok=True)
    print("✅ Adım 1: Kayıt dizini hazır.")

    # 2. Font kontrolü
    print(f"[TEST] Font dosyası aranıyor: {FONT_BOLD}")
    if not os.path.exists(FONT_BOLD):
        raise FileNotFoundError(f"Font dosyası bulunamadı: {FONT_BOLD}")
    print("✅ Adım 2: Font dosyası bulundu.")

    # 3. Görsel indirme
    print(f"[TEST] Görsel indiriliyor: {test_url}")
    response = requests.get(test_url, timeout=20)
    response.raise_for_status()
    print("✅ Adım 3: Görsel başarıyla indirildi.")

    # 4. Pillow ile açma
    print("[TEST] Görsel Pillow ile açılıyor...")
    original_image = Image.open(io.BytesIO(response.content))
    print(f"✅ Adım 4: Görsel açıldı. Boyut: {original_image.size}, Mod: {original_image.mode}")

    # 5. Tuval oluşturma
    print("[TEST] Yeni tuval oluşturuluyor...")
    canvas = Image.new("RGB", (1280, 720), (0,0,0))
    print("✅ Adım 5: Tuval oluşturuldu.")

    # 6. Font yükleme
    print("[TEST] Font yükleniyor...")
    font = ImageFont.truetype(FONT_BOLD, 50)
    print("✅ Adım 6: Font başarıyla yüklendi.")

    # 7. Diske kaydetme
    filepath = os.path.join(SAVE_DIR, "debug_test_image.jpg")
    print(f"[TEST] Görsel diske kaydediliyor: {filepath}")
    canvas.save(filepath, "JPEG")
    print("✅ Adım 7: Görsel başarıyla kaydedildi!")

    # 8. Kaydedilen dosyayı KONTROL ETME
    print("[TEST] Kaydedilen dosyanın varlığı kontrol ediliyor...")
    if os.path.exists(filepath):
        print("🎉🎉🎉 ZAFER! Dosya diskte bulundu!")
        os.remove(filepath) # Test sonrası temizlik
    else:
        print("❌❌❌ İNANILMAZ HATA! Save() komutu hata vermedi ama dosya oluşmadı!")

except Exception as e:
    import traceback
    print(f"\n❌ Test sırasında bir hata oluştu: {type(e).__name__}")
    print(f"Detaylar: {e}")
    traceback.print_exc()