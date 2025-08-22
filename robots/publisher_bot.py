# robots/publisher_bot.py
# -*- coding: utf-8 -*-
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import requests
import gspread
import datetime
import re
import json
import time
import tweepy
import io
from atproto import Client, models # YENİ EKLENDİ

from utils.auth import get_gspread_client
from utils.schema import resolve_columns

load_dotenv()

# .env dosyasından gerekli bilgileri al
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID")
NEWS_TAB = os.environ.get("NEWS_TAB", "News")
BATCH_SLEEP_MS = int(os.environ.get("CRAFTER_SLEEP_MS", "800"))

# Platform Değişkenleri
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
X_API_KEY = os.environ.get("X_API_KEY")
X_API_KEY_SECRET = os.environ.get("X_API_KEY_SECRET")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = os.environ.get("X_ACCESS_TOKEN_SECRET")
X_USERNAME = os.environ.get("X_USERNAME")
BLUESKY_HANDLE = os.environ.get("BLUESKY_HANDLE") # YENİ EKLENDİ
BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD") # YENİ EKLENDİ


# Google Sheet durumunu güncellemek için yardımcı fonksiyonlar
def _compose_status_block(current: str, robot_no: int, ok: bool) -> str:
    kept=[]
    for ln in (current or "").splitlines():
        s=ln.strip()
        if not s: continue
        if re.match(rf"^Robot\s+{robot_no}\s+[✅❌]$", s):
            continue
        if re.match(r"^Robot\s+\d+\s+[✅❌]$", s):
            kept.append(s)
    kept.append(f"Robot {robot_no} {'✅' if ok else '❌'}")
    return "\n".join(kept)

def _flush(ws, triples):
    if not triples: return
    payload=[]
    for (r,c,v) in triples:
        a1=gspread.utils.rowcol_to_a1(r,c)
        payload.append({"range": a1, "values": [[v]]})
    if payload:
        ws.batch_update(payload, value_input_option="USER_ENTERED")
        time.sleep(BATCH_SLEEP_MS/1000.0)

# Etiket üretimi için yardımcılar
STOPWORDS_TR = {"ve","ile","de","da","bir","the","of","in","on","and","to","ya","ama","mi","mı","mu","mü", "için", "gibi"}

def generate_hashtags(title: str, category: str, limit: int = 4) -> str:
    clean_category = re.sub(r'[^a-zA-Z0-9\s]', '', category)
    category_hashtag = f"#{''.join(word.capitalize() for word in clean_category.split())}"
    hashtags = {category_hashtag}
    words = [w.strip(",.!?:;()\"'“”") for w in (title or "").split()]
    important_words = [word for word in words if word.lower() not in STOPWORDS_TR and len(word) > 3 and not word.isdigit()]
    for word in important_words:
        if len(hashtags) >= limit: break
        hashtag = f"#{word.capitalize()}"
        hashtags.add(hashtag)
    return " ".join(hashtags)

def send_to_telegram(image_url: str, caption: str, news_url: str):
    # ... (Bu fonksiyon aynı kalıyor)
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("HATA: Telegram .env değişkenleri eksik.")
        return None
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    inline_keyboard = {"inline_keyboard": [[{"text": "Haberi Oku 📰", "url": news_url}]]}
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'photo': image_url, 'caption': caption, 'parse_mode': 'HTML', 'reply_markup': json.dumps(inline_keyboard)}
    try:
        print("Telegram'a gönderim yapılıyor...")
        response = requests.post(api_url, data=payload, timeout=20)
        response_data = response.json()
        if response_data.get("ok"):
            print("✓ Telegram'a başarıyla gönderildi!")
            message_id = response_data['result']['message_id']
            clean_chat_id = TELEGRAM_CHAT_ID.replace('@', '')
            post_url = f"https://t.me/{clean_chat_id}/{message_id}"
            return post_url
        else:
            print(f" HATA: Telegram API -> {response_data.get('description')}")
            return None
    except Exception as e:
        print(f" HATA: Telegram gönderiminde istisna oluştu: {e}")
        return None

def send_to_x_com(text: str, image_url: str):
    # ... (Bu fonksiyon aynı kalıyor)
    if not all([X_API_KEY, X_API_KEY_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET, X_USERNAME]):
        print("HATA: X.com .env değişkenleri eksik (X_USERNAME dahil).")
        return None
    try:
        print("X.com'a gönderim yapılıyor...")
        client = tweepy.Client(consumer_key=X_API_KEY, consumer_secret=X_API_KEY_SECRET, access_token=X_ACCESS_TOKEN, access_token_secret=X_ACCESS_TOKEN_SECRET)
        auth = tweepy.OAuth1UserHandler(consumer_key=X_API_KEY, consumer_secret=X_API_KEY_SECRET, access_token=X_ACCESS_TOKEN, access_token_secret=X_ACCESS_TOKEN_SECRET)
        api = tweepy.API(auth)
        response = requests.get(image_url, timeout=20)
        response.raise_for_status()
        image_file = io.BytesIO(response.content)
        media = api.media_upload(filename="image.jpg", file=image_file)
        media_id = media.media_id
        tweet_response = client.create_tweet(text=text, media_ids=[media_id])
        tweet_id = tweet_response.data['id']
        post_url = f"https://x.com/{X_USERNAME}/status/{tweet_id}"
        print(f"✓ X.com'a başarıyla gönderildi! Link: {post_url}")
        return post_url
    except Exception as e:
        print(f" HATA: X.com gönderiminde istisna oluştu: {e}")
        return None

# YENİ EKLENDİ: Bluesky'a gönderim fonksiyonu
def send_to_bluesky(text: str, image_url: str):
    if not (BLUESKY_HANDLE and BLUESKY_APP_PASSWORD):
        print("HATA: Bluesky .env değişkenleri eksik.")
        return None
    try:
        print("Bluesky'a gönderim yapılıyor...")
        client = Client()
        client.login(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)
        
        # Görseli URL'den indir
        response = requests.get(image_url, timeout=20)
        response.raise_for_status()
        image_bytes = response.content
        
        # Görseli Bluesky'a yükle
        upload = client.upload_blob(image_bytes)
        
        # Gönderiyi görselle birlikte oluştur
        embed = models.AppBskyEmbedImages.Main(images=[models.AppBskyEmbedImages.Image(alt='', image=upload.blob)])
        
        post_response = client.send_post(text=text, embed=embed)
        
        # Post linkini oluştur: URI'daki son parça post ID'sidir (rkey)
        post_id = post_response.uri.split('/')[-1]
        post_url = f"https://bsky.app/profile/{BLUESKY_HANDLE}/post/{post_id}"
        print(f"✓ Bluesky'a başarıyla gönderildi! Link: {post_url}")
        return post_url

    except Exception as e:
        print(f" HATA: Bluesky gönderiminde istisna oluştu: {e}")
        return None

def run():
    print("Robot 4 (Publisher) başlatıldı...")
    try:
        gc = get_gspread_client()
        ws = gc.open_by_key(GOOGLE_SHEET_ID).worksheet(NEWS_TAB)
        cols = resolve_columns(ws)
        data = ws.get_all_values()[1:]
        
        for i, row in enumerate(data, start=2):
            status = row[cols.AC - 1] if len(row) >= cols.AC else ""
            if "robot 3 ✅" in status.lower() and "robot 4" not in status.lower():
                target_row_index = i
                print(f"\n--- Haber Bulundu: Satır {target_row_index} ---")

                # Gerekli verileri satırdan çek
                headline = row[cols.H - 1]
                summary = row[cols.I - 1]
                category = row[cols.C - 1]
                news_source_url = row[cols.G - 1]
                tg_image_url = row[cols.M - 1]
                social_image_url = row[cols.N - 1]
                
                updates = []
                
                # --- Telegram Gönderimi ---
                tg_hashtags = generate_hashtags(title=headline, category=category)
                tg_caption_parts = [f"📰 <b>{headline}</b>", f"📄 {summary}", f"🗓️ Tarih: {datetime.datetime.now().strftime('%d.%m.%Y')}", f"#️⃣ {tg_hashtags}"]
                tg_caption = "\n\n".join(tg_caption_parts)
                tg_post_url = send_to_telegram(image_url=tg_image_url, caption=tg_caption, news_url=news_source_url)
                if tg_post_url:
                    updates.append((target_row_index, cols.AF, tg_post_url))

                # --- X.com Gönderimi ---
                x_hashtags = generate_hashtags(title=headline, category=category, limit=3)
                ideal_text = f"📰 {headline}\n\n📄 {summary}\n\n{x_hashtags}"
                x_text = ideal_text
                if len(ideal_text) > 280:
                    non_summary_len = len(f"📰 {headline}\n\n📄 \n\n{x_hashtags}")
                    max_summary_len = 280 - non_summary_len - 3
                    if max_summary_len > 0:
                        truncated_summary = summary[:max_summary_len].rsplit(' ', 1)[0] + "..."
                        x_text = f"📰 {headline}\n\n📄 {truncated_summary}\n\n{x_hashtags}"
                    else:
                        x_text = f"📰 {headline[:250]}...\n\n{x_hashtags}"
                x_post_url = send_to_x_com(text=x_text, image_url=social_image_url)
                if x_post_url:
                    updates.append((target_row_index, cols.AG, x_post_url))

                # --- Bluesky Gönderimi ---
                # Bluesky karakter limiti 300'dür. X.com metni genellikle uyar.
                bsky_text = x_text # Şimdilik X.com ile aynı metni kullanalım
                bsky_post_url = send_to_bluesky(text=bsky_text, image_url=social_image_url)
                if bsky_post_url:
                    updates.append((target_row_index, cols.AH, bsky_post_url))

                # --- Google Sheet'i Güncelle ---
                if updates:
                    current_status = row[cols.AC - 1]
                    new_status = _compose_status_block(current_status, 4, True)
                    updates.append((target_row_index, cols.AC, new_status))
                    _flush(ws, updates)
                    print("✓ Google Sheet başarıyla güncellendi.")
                
                print("\nBir sonraki haber için 5 saniye bekleniyor...")
                time.sleep(5)
            
        print("Yayınlanacak başka yeni haber bulunamadı.")

    except Exception as e:
        import traceback
        print(f"Ana işlem sırasında bir hata oluştu: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    run()