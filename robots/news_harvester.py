import feedparser
import uuid
import datetime
import re
import os # EKLENDİ
from utils.auth import get_gspread_client

SHEET_ID = os.environ.get('GOOGLE_SHEET_ID') # DEĞİŞTİ
NEWS_TAB = 'News'
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
CREDS_FILE = 'service_account.json'

RSS_FEEDS = {
    "spor": [
        ("BBC Sport Genel", "https://feeds.bbci.co.uk/sport/rss.xml?edition=uk"),
        ("BBC Sport Futbol", "https://feeds.bbci.co.uk/sport/football/rss.xml?edition=uk"),
        ("Sky Sports Genel", "https://www.skysports.com/rss/12040"),
        ("Sky Sports Futbol", "https://www.skysports.com/rss/11095"),
    ],
    "ekonomi": [
        ("Financial Times", "https://www.ft.com/?format=rss"),
    ],
    "teknoloji": [
        ("TechCrunch", "https://feeds.feedburner.com/TechCrunch/"),
        ("Engadget", "https://www.engadget.com/rss.xml"),
    ],
    "kultur": [
        ("Smithsonian Magazine", "https://www.smithsonianmag.com/rss/latest_articles/"),
        ("Hyperallergic", "https://hyperallergic.com/feed/"),
        ("Dezeen Magazine", "https://www.dezeen.com/feed/"),
    ],
    "dunya_gundemi": [
        ("BBC World News", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("The Guardian World", "https://www.theguardian.com/world/rss"),
        ("NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ],
    "turkiye_gundemi": [
        ("NTV Gündem", "https://www.ntv.com.tr/gundem.rss"),
        ("NTV Türkiye", "https://www.ntv.com.tr/turkiye.rss"),
    ],
    "sondakika": [
        ("NTV Son Dakika", "https://www.ntv.com.tr/son-dakika.rss"),
    ],
}

def fetch_rss_news(feed_url):
    try:
        feed = feedparser.parse(feed_url)
        print(f"{feed_url} -- entry count: {len(feed.entries)}")  # Debug için
        news_list = []
        for entry in feed.entries:
            # Görsel bulmaya çalış
            image_url = ""
            if "media_content" in entry and entry.media_content:
                image_url = entry.media_content[0].get("url", "")
            elif "media_thumbnail" in entry and entry.media_thumbnail:
                image_url = entry.media_thumbnail[0].get("url", "")
            elif "enclosures" in entry and entry.enclosures:
                image_url = entry.enclosures[0].get("href", "")
            elif "image" in entry:
                image_url = entry.image
            # summary içinde <img src="..."> varsa regex ile bul
            elif "summary" in entry:
                import re
                match = re.search(r'<img\s[^>]*src="([^"]+)"', entry["summary"])
                if match:
                    image_url = match.group(1)

            news_item = {
                "title": entry.title,
                "link": entry.link,
                "summary": entry.summary if "summary" in entry else "",
                "published": entry.published if "published" in entry else "",
                "language": entry.get("language", "en"),
                "image": image_url
            }
            news_list.append(news_item)
        return news_list
    except Exception as e:
        print(f"Hata: {e}")
        return f"Hata: {e}"

def get_existing_links():
    gc = get_gspread_client() # Kimlik doğrulamayı artık bu fonksiyon hallediyor
    worksheet = gc.open_by_key(SHEET_ID).worksheet(NEWS_TAB)
    try:
        existing_links = worksheet.col_values(7)[1:]
    except Exception:
        existing_links = []
    return set(existing_links)


def run():
    errors = []
    all_news_rows = []
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_links = get_existing_links()
    unique_links = set(existing_links)
    new_news_count = 0

    for kategori, sources in RSS_FEEDS.items():
        for source_name, feed_url in sources:
            result = fetch_rss_news(feed_url)
            added = False
            # Eğer kaynaktan hiç haber gelmiyorsa veya parse hatası varsa, sheet'e özel satır bırak
            if isinstance(result, str) and result.startswith("Hata"):
                error_row = [
                    "", now, kategori, source_name, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Kaynak Hatalı", result
                ]
                all_news_rows.append(error_row)
                continue
            if isinstance(result, list) and len(result) == 0:
                error_row = [
                    "", now, kategori, source_name, "", "", "", "", "", "", "", "", "", "", "", "", "", "", "Haber Yok", "Bu kaynaktan hiç haber çekilemiyor"
                ]
                all_news_rows.append(error_row)
                continue
            for item in result:
                if item["link"] in unique_links:
                    continue
                unique_links.add(item["link"])
                news_row = [
                    str(uuid.uuid4()),      # ID (A)
                    now,                    # Tarih/Saat (B)
                    kategori,               # Ana Kategori (C)
                    source_name,            # Alt Kategori / Kaynak (D)
                    item["language"],       # Orijinal Dil (E)
                    item["title"],          # Başlık (F)
                    item["link"],           # Link (G)
                    "", "", "",             # H, I, J boş
                    item["image"],          # K: Görsel Link
                ] + [""] * 7 + ["Robot 1 Başarılı", ""]
                all_news_rows.append(news_row)
                new_news_count += 1
                added = True
            if not added:
                # Haber yoksa sadece bir satır ekle (istersen burayı kaldırabilirsin)
                pass
    # Sheet'e yaz
    credentials = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(credentials)
    worksheet = gc.open_by_key(SHEET_ID).worksheet(NEWS_TAB)
    if all_news_rows:
        worksheet.append_rows(all_news_rows, value_input_option="USER_ENTERED")
    worksheet.append_row([""] * 20, value_input_option="USER_ENTERED")  # Boş satır

    if errors:
        return f"Hata oluştu: {'; '.join(errors)}"
    return f"Robot 1 OK | {new_news_count} yeni haber Sheet'e kaydedildi."