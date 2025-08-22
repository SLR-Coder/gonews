import os
import time
import logging
from robots import news_harvester, content_crafter, visual_styler, voice_smith, publisher_bot
from utils.google_sheet_logger import log_to_sheet

# Logger ayarları
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GoNews-Master")

def run_all_robots():
    try:
        logger.info("▶️ Robot 1: NewsHarvester başlatılıyor...")
        news_harvester.run()
        logger.info("✅ NewsHarvester tamamlandı.")

        logger.info("▶️ Robot 2: ContentCrafter başlatılıyor...")
        content_crafter.run()
        logger.info("✅ ContentCrafter tamamlandı.")

        logger.info("▶️ Robot 3: VisualStyler başlatılıyor...")
        visual_styler.run()
        logger.info("✅ VisualStyler tamamlandı.")

        logger.info("▶️ Robot 4: VoiceSmith başlatılıyor...")
        voice_smith.run()
        logger.info("✅ VoiceSmith tamamlandı.")

        logger.info("▶️ Robot 5: PublisherBot başlatılıyor...")
        publisher_bot.run()
        logger.info("✅ PublisherBot tamamlandı.")

        # Opsiyonel: Google Sheets'e log yaz
        log_to_sheet(status="Success", message="Tüm robotlar başarıyla tamamlandı.")

    except Exception as e:
        error_msg = f"❌ Hata oluştu: {str(e)}"
        logger.error(error_msg)
        log_to_sheet(status="Error", message=error_msg)

if __name__ == "__main__":
    logger.info("🚀 GoNews Automation başlıyor...")
    run_all_robots()
    logger.info("🏁 Otomasyon tamamlandı.")
