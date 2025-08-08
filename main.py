# Gerekli importları yapıyoruz
from robots.news_harvester import run as news_harvester_run
from utils.logger import error_handler # Hata durumunda loglama için
from dotenv import load_dotenv

# .env dosyasını yükle (Cloud Function ortamında da değişkenleri okuyabilmesi için)
load_dotenv()

# BU FONKSİYON SADECE NEWS_HARVESTER'I TEST ETMEK İÇİNDİR
def run_gonews(request):
    """
    Sadece news_harvester robotunu çalıştırır ve sonucunu döndürür.
    Bu fonksiyon, bizim Cloud Function'ımızın giriş noktasıdır.
    """
    print("🤖 Sadece News Harvester testi çalıştırılıyor...")
    try:
        # Sadece haber çekme robotunu çalıştırıyoruz
        result = news_harvester_run()

        print(f"✅ News Harvester başarıyla tamamlandı. Sonuç: {result}")
        return f"News Harvester testi başarılı: {result}", 200
    except Exception as e:
        # Hata olursa, hem ana loglara yazdır hem de hata olarak döndür
        error_handler("NewsHarvester_Test", e) 
        print(f"❌ News Harvester testi sırasında hata: {str(e)}")
        return f"Hata: {str(e)}", 500
