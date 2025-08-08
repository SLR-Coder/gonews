import sys
import os
import requests
import base64
import json
import wave
import gspread
from google.oauth2.service_account import Credentials
import time
import shutil

# ========== SABİT AYARLAR ========== #
GEMINI_API_KEY = "AIzaSyCAtEahY-5u7j0CfUcIIkz6urDV_S6dCFE"
SHEET_ID = "1OZJc3ZapwvzWRfiflA1ElFjAr_0fbYiBw1Lerf4Bbzc"
TAB_NAME = "News"
# service_account.json ana dizinde olmalı:
CREDS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'service_account.json')
PODCAST_AUDIO_DIR = os.path.join(os.path.dirname(__file__), "podcast_audio_files_wav")

# ========== Gemini API URL'leri ========== #
GEMINI_TEXT_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro-latest:generateContent?key={GEMINI_API_KEY}"
GEMINI_TTS_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"

# ========== Google Sheets Bağlantı ========== #
def authorize_gspread():
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive.file']
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc

def update_news_row(gc, sheet_id, tab_name, row_index, update_dict):
    ws = gc.open_by_key(sheet_id).worksheet(tab_name)
    col_map = {
        'P': 16,  # Podcast Ses (Link)
        'S': 19,  # Durum
        'T': 20,  # Not
    }
    for key, value in update_dict.items():
        if key in col_map:
            print(f"Sheet Güncelleniyor: Satır {row_index}, Sütun {key} -> '{str(value)[:60]}...'")
            ws.update_cell(row_index, col_map[key], str(value))
            time.sleep(1.2)

# ========== Podcast Senaryosu ========== #
def generate_podcast_script(title, long_text):
    print(f"'{title}' başlıklı haber için podcast senaryosu üretiliyor...")
    prompt = f"""
    Giriş: Sen profesyonel bir podcast senaryo yazarısın. Görevin, aşağıda verilen haber başlığı ve metnini, 'Sunucu 1' ve 'Sunucu 2' adlı iki sunucu arasında geçen, yaklaşık 5 ila 10 dakika sürecek akıcı ve ilgi çekici bir diyalog formatına dönüştürmek.
    Kurallar:
    1. Diyalog doğal ve sohbet havasında olmalı.
    2. Her konuşma satırı mutlaka "Sunucu 1:" veya "Sunucu 2:" ile başlamalıdır.
    3. Haberin ana fikrini ve önemli detaylarını koru.
    4. Sadece ve sadece diyaloğu yaz, başka hiçbir ek açıklama veya başlık ekleme.
    Haber Başlığı: "{title}"
    Haber Metni: "{long_text}"
    """
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    response = requests.post(GEMINI_TEXT_URL, headers=headers, data=json.dumps(payload))
    if response.status_code != 200:
        raise Exception(f"Gemini Metin API hatası: {response.status_code} {response.text}")
    try:
        script = response.json()['candidates'][0]['content']['parts'][0]['text']
        print("→ Senaryo başarıyla üretildi.")
        return script.strip()
    except (KeyError, IndexError, TypeError) as e:
        raise Exception(f"Gemini metin yanıtı beklenmedik formatta: {e} -- Yanıt: {response.text}")

# ========== Ses Üretimi ve WAV Kaydı ========== #
def save_pcm_as_wav(filename, pcm_data, sample_rate, channels=1, sampwidth=2):
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    print(f"→ Ses parçası kaydedildi: {os.path.basename(filename)}")

def generate_tts_segment(text, filename, voice_name):
    # Gemini 2.5 TTS endpointini kullan!
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}}
        },
        "model": "gemini-2.5-flash-preview-tts"
    }
    response = requests.post(GEMINI_TTS_URL, headers=headers, data=json.dumps(payload))
    if response.status_code != 200:
        raise Exception(f"Gemini TTS API hatası: {response.status_code} {response.text}")
    try:
        part = response.json()['candidates'][0]['content']['parts'][0]
        audio_data_base64 = part['inlineData']['data']
        mime_type = part['inlineData']['mimeType']
        sample_rate = int(mime_type.split('rate=')[-1])
        audio_bytes = base64.b64decode(audio_data_base64)
        save_pcm_as_wav(filename, audio_bytes, sample_rate)
        return filename
    except (KeyError, IndexError, TypeError) as e:
        raise Exception(f"Gemini TTS yanıtı beklenmedik formatta: {e} -- Yanıt: {response.text}")

def combine_wav_files(segment_paths, output_path):
    print("Ses parçaları birleştiriliyor...")
    if not segment_paths:
        print("[UYARI] Birleştirilecek ses segmenti yok.")
        return
    output_wav = wave.open(output_path, 'wb')
    with wave.open(segment_paths[0], 'rb') as first_wav:
        output_wav.setparams(first_wav.getparams())
    for path in segment_paths:
        with wave.open(path, 'rb') as segment_wav:
            output_wav.writeframes(segment_wav.readframes(segment_wav.getnframes()))
    output_wav.close()
    print(f"→ Podcast tamamlandı: {output_path}")

def create_podcast_from_script(haber_id, script, spiker1, spiker2):
    temp_dir = os.path.join(os.path.dirname(__file__), 'temp_audio_segments')
    os.makedirs(temp_dir, exist_ok=True)
    output_dir = PODCAST_AUDIO_DIR
    os.makedirs(output_dir, exist_ok=True)
    segment_paths = []
    lines = [line.strip() for line in script.split('\n') if line.strip()]
    for i, line in enumerate(lines):
        segment_filename = os.path.join(temp_dir, f"segment_{i}.wav")
        text_to_speak = ""
        voice_name = ""
        if line.startswith("Sunucu 1:"):
            text_to_speak = line.replace("Sunucu 1:", "").strip()
            voice_name = spiker1['voice_name']
            print(f"[{i+1}/{len(lines)}] Sunucu 1 ({voice_name}) konuşuyor...")
        elif line.startswith("Sunucu 2:"):
            text_to_speak = line.replace("Sunucu 2:", "").strip()
            voice_name = spiker2['voice_name']
            print(f"[{i+1}/{len(lines)}] Sunucu 2 ({voice_name}) konuşuyor...")
        if text_to_speak and voice_name:
            generate_tts_segment(text_to_speak, segment_filename, voice_name)
            segment_paths.append(segment_filename)
            time.sleep(0.5)
    final_podcast_path = os.path.join(output_dir, f"{haber_id}_podcast.wav")
    combine_wav_files(segment_paths, final_podcast_path)
    shutil.rmtree(temp_dir)
    print("Geçici dosyalar temizlendi.")
    return os.path.join("podcast_audio_files_wav", f"{haber_id}_podcast.wav")  # Sheet'e göreceli yol yaz

# ========== Haberleri Sheet'ten Al ========== #
def get_articles_for_podcast(gc, sheet_id, tab_name):
    print("Google Sheet'e bağlanılıyor ve haberler okunuyor...")
    ws = gc.open_by_key(sheet_id).worksheet(tab_name)
    rows = ws.get_all_records()
    result = []
    for i, row in enumerate(rows, start=2):
        status_str = str(row.get('Durum') or row.get('S') or '').strip()
        podcast_link = row.get('Padcast Ses') or row.get('P')
        if status_str == "Robot 1 Başarılı / Robot 2 Başarılı / Robot 3 Başarılı / Robot 4 Başarılı" and not podcast_link:
            article_data = {
                'row_index': i,
                'haber_id': row.get('Haber ID') or row.get('A'),
                'title': row.get('Yeni Başlık (TR)') or row.get('H'),
                'long_text': row.get('Uzun Metin (TR)') or row.get('J')
            }
            if all(article_data.values()):
                result.append(article_data)
    return result

# ========== Ana Akış ========== #
def main_process(spiker1_profil, spiker2_profil):
    print("==== GoNews Podcast Üretim Robotu Başladı ====")
    try:
        gspread_client = authorize_gspread()
        articles = get_articles_for_podcast(gspread_client, SHEET_ID, TAB_NAME)
        if not articles:
            print("Podcast üretilecek uygun haber bulunamadı.")
            return
        print(f"{len(articles)} haber işlenecek.")
        for article in articles:
            try:
                print(f"\n[Haber] ID: {article['haber_id']} - Başlık: {article['title'][:50]}...")
                podcast_script = generate_podcast_script(article['title'], article['long_text'])
                podcast_file_path = create_podcast_from_script(article['haber_id'], podcast_script, spiker1_profil, spiker2_profil)
                update_news_row(gspread_client, SHEET_ID, TAB_NAME, article['row_index'], {
                    'P': podcast_file_path,
                    'S': 'Robot 1 Başarılı / Robot 2 Başarılı / Robot 3 Başarılı / Robot 4 Başarılı / Robot 5 Başarılı'
                })
                print(f"BAŞARILI: Haber ID {article['haber_id']} için podcast üretildi ve Sheet güncellendi.")
            except Exception as e:
                error_msg = f"Podcast Üretim Hatası: {e}"
                print(f"[HATA] Haber ID: {article.get('haber_id')} - {error_msg}")
                update_news_row(gspread_client, SHEET_ID, TAB_NAME, article['row_index'], {'T': error_msg})
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"[KRİTİK HATA] Google Sheet bulunamadı. SHEET_ID '{SHEET_ID}' doğru mu?")
    except gspread.exceptions.WorksheetNotFound:
        print(f"[KRİTİK HATA] Sekme bulunamadı. TAB_NAME '{TAB_NAME}' doğru mu?")
    except Exception as e:
        print(f"[KRİTİK HATA] Ana süreç durduruldu: {e}")
    print("==== GoNews Podcast Üretim Robotu Tamamlandı ====")

if __name__ == "__main__":
    # Spiker profilleri
    SUNUCU_1 = {"name": "Erkek Sunucu", "voice_name": "Algenib"}
    SUNUCU_2 = {"name": "Kadın Sunucu", "voice_name": "Callirrhoe"}
    # --- Kontroller ve Başlatma ---
    if not GEMINI_API_KEY or "YOUR_GEMINI_API_KEY" in GEMINI_API_KEY:
        print("HATA: GEMINI_API_KEY eksik veya geçersiz. Lütfen API anahtarınızı ekleyin.")
    elif not os.path.exists(CREDS_FILE):
        print(f"HATA: Google Sheets kimlik doğrulama dosyası bulunamadı: {CREDS_FILE}")
    else:
        main_process(SUNUCU_1, SUNUCU_2)
