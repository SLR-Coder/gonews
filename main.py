def main(event, context):
    import json
    from robots.news_harvester import run as run_news_harvester  
    from robots.content_crafter import run as run_content_crafter
    from robots.visual_styler import run as run_visual_styler
    from robots.voice_smith import run as run_voice_smith
    from robots.publisher import run as run_publisher

    print("🚀 GoNews otomasyonu tetiklendi")

    try:
        # 1. Haberleri Çek
        run_news_harvester()   # ✅ Artık doğru fonksiyonu çağırıyor

        # 2. İçeriği Üret
        run_content_crafter()

        # 3. Görselleri Hazırla
        run_visual_styler()

        # 4. Sesleri Oluştur
        run_voice_smith()

        # 5. Paylaşım Yap
        run_publisher()

        print("✅ GoNews otomasyonu tamamlandı")
    except Exception as e:
        print(f"❌ Hata oluştu: {e}")
