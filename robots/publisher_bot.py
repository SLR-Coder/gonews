# robots/publisher_bot.py
# -*- coding: utf-8 -*-
import os
import re
import io
import json
import time
import datetime
import requests
import tweepy
from atproto import Client, models
from utils.secrets import get_secret
from utils.auth import get_gspread_client
from utils.schema import resolve_columns

# --- Google Sheets ---
GOOGLE_SHEET_ID = get_secret("GOOGLE_SHEET_ID")
NEWS_TAB = os.environ.get("NEWS_TAB", "News")
BATCH_SLEEP_MS = int(os.environ.get("CRAFTER_SLEEP_MS", "800"))

# --- Platform Secrets ---
TELEGRAM_BOT_TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = get_secret("TELEGRAM_CHAT_ID")
X_API_KEY = get_secret("X_API_KEY")
X_API_KEY_SECRET = get_secret("X_API_KEY_SECRET")
X_ACCESS_TOKEN = get_secret("X_ACCESS_TOKEN")
X_ACCESS_TOKEN_SECRET = get_secret("X_ACCESS_TOKEN_SECRET")
X_USERNAME = get_secret("X_USERNAME")
BLUESKY_HANDLE = get_secret("BLUESKY_HANDLE")
BLUESKY_APP_PASSWORD = get_secret("BLUESKY_APP_PASSWORD")

# === Yardımcılar ===
STOPWORDS_TR = {
    "ve","ile","de","da","bir","the","of","in","on","and","to","ya","ama","mi","mı","mu","mü","için","gibi"
}

def _compose_status_block(current: str, robot_no: int, ok: bool) -> str:
    kept = []
    for ln in (current or "").splitlines():
        s = ln.strip()
        if not s: 
            continue
        if re.match(rf"^Robot\s+{robot_no}\s+[✅❌]$", s):
            continue
        if re.match(r"^Robot\s+\d+\s+[✅❌]$", s):
            kept.append(s)
    kept.append(f"Robot {robot_no} {'✅' if ok else '❌'}")
    return "\n".join(kept)

def _flush(ws, triples):
    if not triples: return
    payload = []
    for (r,c,v) in triples:
        a1 = ws.cell(r, c).address
        payload.append({"range": a1, "values": [[v]]})
    ws.batch_update(payload, value_input_option="USER_ENTERED")
    time.sleep(BATCH_SLEEP_MS / 1000.0)

def generate_hashtags(title: str, category: str, limit: int = 4) -> str:
    clean_category = re.sub(r'[^a-zA-Z0-9\s]', '', category)
    category_hashtag = f"#{''.join(word.capitalize() for word in clean_category.split())}"
    hashtags = {category_hashtag}
    words = [w.strip(",.!?:;()\"'“”") for w in (title or "").split()]
    important_words = [
        word for word in words 
        if word.lower() not in STOPWORDS_TR and len(word) > 3 and not word.isdigit()
    ]
    for word in important_words:
        if len(hashtags) >= limit: break
        hashtags.add(f"#{word.capitalize()}")
    return " ".join(hashtags)

# === Platform Gönderimleri ===
def send_to_telegram(image_url: str, caption: str, news_url: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("HATA: Telegram API bilgileri eksik.")
        return None
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    inline_keyboard = {"inline_keyboard": [[{"text": "Haberi Oku 📰", "url": news_url}]]}
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'photo': image_url,
        'caption': caption,
        'parse_mode': 'HTML',
        'reply_markup': json.dumps(inline_keyboard)
    }
    try:
        print("Telegram'a gönderim yapılıyor...")
        response = requests.post(api_url, data=payload, timeout=20)
        data = response.json()
        if data.get("ok"):
            msg_id = data['result']['message_id']
            chat_id = TELEGRAM_CHAT_ID.replace('@', '')
            return f"https://t.me/{chat_id}/{msg_id}"
        print(f"Telegram HATA: {data.get('description')}")
        return None
    except Exception as e:
        print(f"Telegram gönderiminde hata: {e}")
        return None

def send_to_x_com(text: str, image_url: str):
    if not all([X_API_KEY, X_API_KEY_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET, X_USERNAME]):
        print("HATA: X.com API bilgileri eksik.")
        return None
    try:
        print("X.com'a gönderim yapılıyor...")
        client = tweepy.Client(
            consumer_key=X_API_KEY,
            consumer_secret=X_API_KEY_SECRET,
            access_token=X_ACCESS_TOKEN,
            access_token_secret=X_ACCESS_TOKEN_SECRET
        )
        auth = tweepy.OAuth1UserHandler(
            consumer_key=X_API_KEY,
            consumer_secret=X_API_KEY_SECRET,
            access_token=X_ACCESS_TOKEN,
            access_token_secret=X_ACCESS_TOKEN_SECRET
        )
        api = tweepy.API(auth)
        img = io.BytesIO(requests.get(image_url, timeout=20).content)
        media = api.media_upload(filename="image.jpg", file=img)
        tweet = client.create_tweet(text=text, media_ids=[media.media_id])
        return f"https://x.com/{X_USERNAME}/status/{tweet.data['id']}"
    except Exception as e:
        print(f"X.com gönderiminde hata: {e}")
        return None

def send_to_bluesky(text: str, image_url: str):
    if not (BLUESKY_HANDLE and BLUESKY_APP_PASSWORD):
        print("HATA: Bluesky API bilgileri eksik.")
        return None
    try:
        print("Bluesky'a gönderim yapılıyor...")
        client = Client()
        client.login(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)
        img = requests.get(image_url, timeout=20).content
        upload = client.upload_blob(img)
        embed = models.AppBskyEmbedImages.Main(
            images=[models.AppBskyEmbedImages.Image(alt='', image=upload.blob)]
        )
        resp = client.send_post(text=text, embed=embed)
        post_id = resp.uri.split('/')[-1]
        return f"https://bsky.app/profile/{BLUESKY_HANDLE}/post/{post_id}"
    except Exception as e:
        print(f"Bluesky gönderiminde hata: {e}")
        return None

# === Ana Çalışma Fonksiyonu ===
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
                idx = i
                print(f"\n--- Haber Bulundu: Satır {idx} ---")

                headline = row[cols.H - 1]
                summary = row[cols.I - 1]
                category = row[cols.C - 1]
                news_url = row[cols.G - 1]
                tg_img = row[cols.M - 1]
                social_img = row[cols.N - 1]

                updates = []
                
                # Telegram
                tg_hashtags = generate_hashtags(headline, category)
                tg_caption = "\n\n".join([
                    f"📰 <b>{headline}</b>",
                    f"📄 {summary}",
                    f"🗓️ Tarih: {datetime.datetime.now().strftime('%d.%m.%Y')}",
                    f"#️⃣ {tg_hashtags}"
                ])
                tg_url = send_to_telegram(tg_img, tg_caption, news_url)
                if tg_url: updates.append((idx, cols.AF, tg_url))

                # X.com
                x_hashtags = generate_hashtags(headline, category, limit=3)
                ideal = f"📰 {headline}\n\n📄 {summary}\n\n{x_hashtags}"
                x_text = ideal if len(ideal) <= 280 else ideal[:277] + "..."
                x_url = send_to_x_com(x_text, social_img)
                if x_url: updates.append((idx, cols.AG, x_url))

                # Bluesky
                bsky_text = x_text
                bsky_url = send_to_bluesky(bsky_text, social_img)
                if bsky_url: updates.append((idx, cols.AH, bsky_url))

                # Google Sheet Güncelleme
                if updates:
                    new_status = _compose_status_block(status, 4, True)
                    updates.append((idx, cols.AC, new_status))
                    _flush(ws, updates)
                    print("✓ Google Sheet başarıyla güncellendi.")
                
                print("\nBir sonraki haber için 5 saniye bekleniyor...")
                time.sleep(5)
        
        print("Yayınlanacak başka yeni haber bulunamadı.")
    except Exception as e:
        import traceback
        print(f"Ana işlem sırasında hata: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    run()
