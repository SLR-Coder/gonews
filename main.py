from dotenv import load_dotenv
load_dotenv()

from utils.sheets import log_to_sheet
from utils.logger import error_handler

# Robot fonksiyonlarını robots klasöründen import ediyoruz
from robots.news_harvester import run as news_harvester_run
from robots.content_crafter import run as content_crafter_run
from robots.visual_styler import run as visual_styler_run
from robots.voice_smith import run as voice_smith_run
from robots.podcast_duo import run as podcast_duo_run
from robots.video_forge import run as video_forge_run
from robots.publisher_bot import run as publisher_bot_run

def run_sequence():
    robots = [
        ("NewsHarvester", news_harvester_run),
        ("ContentCrafter", content_crafter_run),
        ("VisualStyler", visual_styler_run),
        ("VoiceSmith", voice_smith_run),
        ("PodcastDuo", podcast_duo_run),
        ("VideoForge", video_forge_run),
        ("PublisherBot", publisher_bot_run),
    ]
    for robot_name, robot_func in robots:
        try:
            result = robot_func()
            log_to_sheet(robot_name, "Başarılı", result)
        except Exception as e:
            error_handler(robot_name, e)
            break  # Hata olursa zinciri burada durdurur (isteğe bağlı)



def run_gonews(request):
    try:
        run_sequence()
        return "GoNews robotları başarıyla çalıştı ✅", 200
    except Exception as e:
        print("Hata:", e)
        return f"Hata: {str(e)}", 500
