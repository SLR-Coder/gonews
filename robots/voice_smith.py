import sys
import os
import requests
import base64
import json
import wave
import gspread
from google.oauth2.service_account import Credentials
import time

# ========== SABİT AYARLAR ========== #
# Lütfen kendi Gemini API anahtarınızı buraya girin.
GEMINI_API_KEY = "AIzaSyCAtEahY-5u7j0CfUcIIkz6urDV_S6dCFE"
SHEET_ID = "1OZJc3ZapwvzWRfiflA1ElFjAr_0fbYiBw1Lerf4Bbzc"
TAB_NAME = "News"
# 'service_account.json' dosyasının bu script'in bir üst klasöründe olduğundan emin olun.
CREDS_FILE = os.path.join(os.path.dirname(__file__), '..', 'service_account.json')

# ========== Gemini API URL ========== #
GEMINI_TTS_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key={GEMINI_API_KEY}"

# ========== Google Sheets GÜNCELLEME FONKSİYONU ========== #
def update_news_row(sheet_id, tab_name, row_index, update_dict):
    """Belirtilen satırdaki hücreleri günceller."""
    try:
        SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(sheet_id).worksheet(tab_name)
        col_map = {
            'N': 14,  # Kısa Ses (Link)
            'O': 15,  # Uzun Ses (Link)
            'S': 19,  # Durum
            'T': 20,  # Not
        }
        for key, value in update_dict.items():
            if key in col_map:
                print(f"Sheet Güncelleniyor: Satır {row_index}, Sütun {key} -> '{str(value)[:60]}...'")
                ws.update_cell(row_index, col_map[key], str(value))
                # API limitlerine takılmamak için küçük bir bekleme ekleniyor.
                time.sleep(1.1)
    except Exception as e:
        print(f"[HATA] Sheet güncellenemedi: {e}")

def log_to_sheet(source, status, message):
    """İşlem loglarını konsola basar."""
    print(f"LOG: [{source}] - {status} - {message}")

def save_pcm_as_wav(filename, pcm_data, sample_rate, channels=1, sampwidth=2):
    """Gelen PCM verisini WAV dosyası olarak kaydeder."""
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    print(f"→ WAV kaydedildi: {filename}")

def generate_gemini_tts(text, filename, voice_name):
    """Verilen metni Gemini TTS API'si ile sese dönüştürür."""
    print(f"Gemini TTS: '{os.path.basename(filename)}' '{voice_name}' sesiyle üretiliyor...")
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
        raise Exception(f"Gemini API hatası: {response.status_code} {response.text}")
    try:
        part = response.json()['candidates'][0]['content']['parts'][0]
        audio_data_base64 = part['inlineData']['data']
        mime_type = part['inlineData']['mimeType']
        sample_rate = int(mime_type.split('rate=')[-1])
        audio_bytes = base64.b64decode(audio_data_base64)
        save_pcm_as_wav(filename, audio_bytes, sample_rate)
        return filename
    except (KeyError, IndexError, TypeError) as e:
        raise Exception(f"Gemini yanıtı beklenmedik formatta: {e} -- Yanıt: {response.text}")

def get_articles_for_voice(sheet_id, tab_name):
    """Seslendirilecek uygun haberleri Google Sheet'ten çeker."""
    print("Google Sheet'e bağlanılıyor ve haberler okunuyor...")
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    ws = gc.open_by_key(sheet_id).worksheet(tab_name)
    rows = ws.get_all_records()
    result = []
    for i, row in enumerate(rows, start=2):
        status_str = str(row.get('Durum') or row.get('S') or '').strip()
        short_audio = row.get('Kısa Ses (MP3 Link)') or row.get('N')
        long_audio = row.get('Uzun Ses (MP3 Link)') or row.get('O')
        # Sadece durumu uygun olan ve daha önce seslendirilmemiş haberleri seçer.
        if status_str == "Robot 1 Başarılı / Robot 2 Başarılı / Robot 3 Başarılı" and not short_audio and not long_audio:
            article_data = {
                'row_index': i,
                'haber_id': row.get('Haber ID') or row.get('A'),
                'title': row.get('Yeni Başlık (TR)') or row.get('H'),
                'summary': row.get('Özet (TR)') or row.get('I'),
                'long_text': row.get('Uzun Metin (TR)') or row.get('J')
            }
            if all(article_data.values()):
                result.append(article_data)
    return result

def create_voice_files(article, speaker_profile, audio_dir_name="audio_files_wav"):
    """Bir haber için kısa ve uzun ses dosyalarını oluşturur."""
    audio_dir = os.path.join(os.path.dirname(__file__), '..', audio_dir_name)
    os.makedirs(audio_dir, exist_ok=True)
    haber_id = article['haber_id']
    voice_name = speaker_profile['voice_name']
    
    short_text = f"{article['title']}. {article['summary']}"
    long_text = f"{article['title']}. {article['long_text']}"
    
    short_path = os.path.join(audio_dir, f"{haber_id}_short.wav")
    long_path = os.path.join(audio_dir, f"{haber_id}_long.wav")
    
    generate_gemini_tts(short_text, short_path, voice_name=voice_name)
    generate_gemini_tts(long_text, long_path, voice_name=voice_name)
    
    return {
        "short": os.path.join(audio_dir_name, f"{haber_id}_short.wav"),
        "long": os.path.join(audio_dir_name, f"{haber_id}_long.wav")
    }

def update_sheet_success(sheet_id, tab_name, row_index, short_path, long_path):
    """Başarılı işlem sonrası Sheet'i günceller."""
    update_news_row(sheet_id, tab_name, row_index, {
        'N': short_path, 'O': long_path,
        'S': 'Robot 1 Başarılı / Robot 2 Başarılı / Robot 3 Başarılı / Robot 4 Başarılı'
    })

def update_sheet_error(sheet_id, tab_name, row_index, error_msg):
    """Hatalı işlem sonrası Sheet'i günceller."""
    update_news_row(sheet_id, tab_name, row_index, {'T': f"VoiceSmith Hatası: {error_msg}"})

def main_process(erkek_profil, kadin_profil):
    """Haberleri alır ve spikerleri sırayla değiştirerek seslendirir."""
    print("==== GoNews VoiceSmith (Sıralı Seslendirme) Başladı ====")
    try:
        articles = get_articles_for_voice(SHEET_ID, TAB_NAME)
        if not articles:
            print("Ses üretilecek uygun haber bulunamadı.")
            return
            
        print(f"{len(articles)} haber işlenecek.")
        
        for idx, article in enumerate(articles):
            # Sıra numarasına göre spiker seçimi yapılıyor.
            if idx % 2 == 0:
                speaker_profile = erkek_profil
            else:
                speaker_profile = kadin_profil
            
            # --- HATA AYIKLAMA SATIRI ---
            # Her döngüde hangi sesin seçildiğini terminale yazdırarak kontrol sağlıyoruz.
            print(f"--> DEBUG: Haber index {idx}. Seçilen ses profili: {speaker_profile['voice_name']}")
            
            try:
                print(f"\n[Haber] ID: {article['haber_id']} - Spiker: {speaker_profile['name']} - Başlık: {article['title'][:50]}...")
                audio_paths = create_voice_files(article, speaker_profile)
                update_sheet_success(SHEET_ID, TAB_NAME, article['row_index'], audio_paths['short'], audio_paths['long'])
                log_to_sheet("VoiceSmith", "Başarılı", f"Haber ID: {article['haber_id']} sesleri ({speaker_profile['name']}) ile üretildi.")
            except Exception as e:
                print(f"[HATA] Haber ID: {article.get('haber_id')} - {e}")
                update_sheet_error(SHEET_ID, TAB_NAME, article['row_index'], str(e))
                log_to_sheet("VoiceSmith", "Hata", f"Haber ID: {article.get('haber_id')} | {e}")
                
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"[KRİTİK HATA] Google Sheet bulunamadı. SHEET_ID '{SHEET_ID}' doğru mu?")
    except gspread.exceptions.WorksheetNotFound:
        print(f"[KRİTİK HATA] Sekme bulunamadı. TAB_NAME '{TAB_NAME}' doğru mu?")
    except Exception as e:
        print(f"[KRİTİK HATA] Ana süreçte beklenmedik bir hata oluştu: {e}")

    print("==== GoNews VoiceSmith Tamamlandı ====")

if __name__ == "__main__":
    # Spiker profilleri tanımlanıyor.
    ERKEK_SPIKER = {
        "name": "Erkek Haber Spikeri",
        # --- DEĞİŞİKLİK BURADA ---
        # "Kore" sesi yerine daha tok bir ses olan "Algenib" seçildi.
        # Diğer alternatifler: "Orus", "Fenrir", "Rasalgethi", "Zubenelgenubi"
        "voice_name": "Algenib"
    }
    KADIN_SPIKER = {
        "name": "Kadın Haber Spikeri",
        "voice_name": "Callirrhoe"  # Kadın ses için bir model
    }
    
    # Gerekli kontroller yapılıyor.
    if not GEMINI_API_KEY or "YOUR_GEMINI_API_KEY_HERE" in GEMINI_API_KEY:
        print("HATA: GEMINI_API_KEY eksik veya geçersiz. Lütfen API anahtarınızı ekleyin.")
    elif not os.path.exists(CREDS_FILE):
        print(f"HATA: Google Sheets kimlik doğrulama dosyası bulunamadı: {CREDS_FILE}")
    else:
        # Ana işlem fonksiyonu her iki spiker profiliyle birlikte çağrılıyor.
        main_process(ERKEK_SPIKER, KADIN_SPIKER)
