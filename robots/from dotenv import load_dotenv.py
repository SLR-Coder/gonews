from dotenv import load_dotenv
load_dotenv()

import os, io, time, textwrap, datetime, traceback, requests
from typing import Tuple, Dict, List, Optional

from PIL import Image, ImageFilter, ImageOps, ImageDraw, ImageFont, ImageEnhance
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from utils.auth import get_gspread_client
from utils.schema import resolve_columns

# =================== .env / Ayarlar ===================
NEWS_TAB              = os.environ.get("NEWS_TAB", "News")
OUT_DIR               = os.environ.get("STYLED_DIR", "styled_images")

# Vision kullanımı
USE_VISION            = os.environ.get("USE_VISION", "1") in ("1", "true", "True")
VISION_LOGO_REMOVE    = os.environ.get("VISION_LOGO_REMOVE", "0") in ("1", "true", "True")
# Vision kapalıysa hafif köşe yumuşatma (fallback)
FALLBACK_LOGO_REMOVE  = os.environ.get("LOGO_REMOVE", "0") in ("1", "true", "True")

# Görsel kalite artırma
SHARPEN_AMOUNT        = float(os.environ.get("SHARPEN_AMOUNT", "1.0"))
CONTRAST_BOOST        = float(os.environ.get("CONTRAST_BOOST", "1.02"))
BRIGHTNESS_BOOST      = float(os.environ.get("BRIGHTNESS_BOOST", "1.00"))

# Batch
BATCH_SIZE            = int(os.environ.get("VISUAL_BATCH", "20"))
BATCH_SLEEP_MS        = int(os.environ.get("VISUAL_SLEEP_MS", "800"))

# Telegram başlık şeridi
TELEGRAM_STYLE_KEEP   = os.environ.get("TELEGRAM_STYLE_KEEP", "1") in ("1", "true", "True")
TELEGRAM_TITLE_LINES  = int(os.environ.get("TELEGRAM_TITLE_LINES", "2"))
TELEGRAM_FONT_PATH    = os.environ.get("TELEGRAM_FONT_PATH", "")

# Formatlar
import os
import io
import time
import gspread
import requests
from PIL import Image, ImageDraw, ImageFont
from google.oauth2.service_account import Credentials

# ==== Sabitler ====
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "service_account.json")
LOGO_PATH = os.path.join(BASE_DIR, "media", "logo.png")
FONT_DIR = os.path.join(BASE_DIR, "media", "font")
SAVE_DIR = os.path.join(BASE_DIR, "styled_images")
os.makedirs(SAVE_DIR, exist_ok=True)

SHEET_ID = "1OZJc3ZapwvzWRfiflA1ElFjAr_0fbYiBw1Lerf4Bbzc"
TAB_NAME = "News"
COL_CATEGORY = 3
COL_TITLE = 8
COL_SUMMARY = 9
COL_IMG = 11
COL_IMG_WEB = 12
COL_IMG_TG = 13
COL_STATUS = 19
COL_NOTE = 20

TG_SIZE = (1080, 1350)
WEB_SIZE = (1280, 720)
MIN_WIDTH = 500
MIN_HEIGHT = 300

SARI = (255, 206, 43)
SARI_BG = (255, 206, 43, 255)
BEYAZ = (255, 255, 255, 255)
SIYAH = (0, 0, 0, 255)
BORDER = 20

# Font dosyaları
FONT_BLACK = os.path.join(FONT_DIR, "Montserrat-Black.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "Montserrat-Bold.ttf")
FONT_SEMIBOLD = os.path.join(FONT_DIR, "Montserrat-SemiBold.ttf")
FONT_REGULAR = os.path.join(FONT_DIR, "Montserrat-Regular.ttf")

def download_image(url):
    """URL'den görsel indir"""
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGBA")
    return img

def add_logo(img, logo_path, pos, size):
    """Görsele logo ekle"""
    if os.path.exists(logo_path):
        logo = Image.open(logo_path).convert("RGBA")
        logo = logo.resize(size, Image.Resampling.LANCZOS)
        img.paste(logo, pos, logo)
    return img

def validate_image(img):
    """Görsel validasyonu"""
    if img.width < MIN_WIDTH or img.height < MIN_HEIGHT:
        return False, f"{img.width}x{img.height}, {img.mode} | Küçük görsel"
    if img.mode not in ["RGB", "RGBA"]:
        return False, f"{img.width}x{img.height}, {img.mode} | Görsel mode'u hatalı"
    return True, ""

def smart_get_image(img_url):
    """Akıllı görsel indirme ve validasyon"""
    try:
        img = download_image(img_url)
        valid, note = validate_image(img)
        if valid:
            return img, img_url, ""
        return img, img_url, f"Düşük kalite veya format | {note}"
    except Exception as e:
        return None, img_url, f"Görsel indirilemedi: {str(e)}"

def get_text_size(draw, text, font):
    """Text boyutunu hesapla"""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]

def ai_find_keywords(text, max_keywords=2):
    """Metinden anahtar kelimeleri bul"""
    words = text.split()
    candidates = [w for w in words if w.isupper() or len(w) >= 5]
    unique = []
    for w in candidates:
        if w.lower() not in [x.lower() for x in unique]:
            unique.append(w)
        if len(unique) >= max_keywords:
            break
    return unique if unique else []

def draw_ai_yellow_text(draw, text, font, pos, color, keywords, bold_font, center_width=None):
    """Anahtar kelimeleri sarı ve kalın yaz"""
    words = text.split(" ")
    x, y = pos
    
    total_w = 0
    temp_x = 0
    for word in words:
        draw_word = word + " "
        is_keyword = word.lower().strip(",.!") in [k.lower() for k in keywords]
        fnt = bold_font if is_keyword else font
        w, _ = get_text_size(draw, draw_word, fnt)
        total_w += w
        
    if center_width:
        x = (center_width - total_w) // 2
        
    for word in words:
        draw_word = word + " "
        is_keyword = word.lower().strip(",.!") in [k.lower() for k in keywords]
        fnt = bold_font if is_keyword else font
        clr = SARI if is_keyword else color
        draw.text((x, y), draw_word, font=fnt, fill=clr)
        w, _ = get_text_size(draw, draw_word, fnt)
        x += w
    return x

def wrap_text(draw, text, font, max_width):
    """Metni satırlara böl"""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = current + (" " if current else "") + word
        if get_text_size(draw, test, font)[0] > max_width:
            if current: lines.append(current)
            current = word
        else:
            current = test
    if current: lines.append(current)
    return lines

def add_white_border(img, border_px=BORDER):
    """Beyaz kenarlık ekle"""
    w, h = img.size
    bordered = Image.new("RGBA", (w + border_px * 2, h + border_px * 2), BEYAZ)
    bordered.paste(img, (border_px, border_px))
    return bordered

# DÜZELTME: Sadece sol üst ve sağ alt köşeleri yuvarlak yapan orijinal fonksiyonunuz geri getirildi.
def draw_rounded_rectangle(draw, xy, radius, fill):
    """Yuvarlak köşeli dikdörtgen çiz - sadece sol üst ve sağ alt köşeler yuvarlak"""
    x1, y1, x2, y2 = xy
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
    draw.pieslice([x1, y1, x1 + 2*radius, y1 + 2*radius], 180, 270, fill=fill)
    draw.pieslice([x2 - 2*radius, y2 - 2*radius, x2, y2], 0, 90, fill=fill)
    draw.rectangle([x2 - radius, y1, x2, y1 + radius], fill=fill)
    draw.rectangle([x1, y2 - radius, x1 + radius, y2], fill=fill)

def calculate_adaptive_font_size(draw, text, base_font_path, max_width, max_size, min_size=20):
    """Metne göre uyarlanabilir font boyutu hesapla"""
    font_size = max_size
    while font_size >= min_size:
        font = ImageFont.truetype(base_font_path, font_size)
        lines = wrap_text(draw, text, font, max_width)
        if len(lines) <= 2:  # 1 veya 2 satır olması kabul edilebilir
            w, h = get_text_size(draw, text, font)
            if w <= max_width or len(lines) > 1: # Tek satırda sığıyorsa veya zaten birden çok satırsa
                 return font, font_size
        font_size -= 2
    return ImageFont.truetype(base_font_path, min_size), min_size

# ================== TELEGRAM GÖRSELİ ==================
def process_image_telegram(img_url, title, summary, category):
    """Telegram için görsel işle"""
    img, used_url, note = smart_get_image(img_url)
    if img is None:
        raise Exception("Görsel indirilemedi")
    
    full_w, full_h = TG_SIZE[0] - 2*BORDER, TG_SIZE[1] - 2*BORDER
    bar_h = 675
    img_h = full_h - bar_h
    
    aspect = img.width / img.height
    target_h = img_h
    target_w = int(target_h * aspect)
    if target_w < full_w:
        target_w = full_w
        target_h = int(target_w / aspect)
    
    img_crop = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    left = (target_w - full_w) // 2
    top = (target_h - img_h) // 2
    crop_img = img_crop.crop((left, top, left+full_w, top+img_h))
    
    out_img = Image.new("RGBA", (full_w, full_h), SIYAH)
    out_img.paste(crop_img, (0,0))
    add_logo(out_img, LOGO_PATH, pos=(20, 20), size=(112, 70))
    draw = ImageDraw.Draw(out_img)

    # ==== SARI KUTU ====
    kutu_font_size = 54
    font_cat = ImageFont.truetype(FONT_BLACK, kutu_font_size)
    cat_text = category.upper()
    
    w_cat, h_cat = get_text_size(draw, cat_text, font=font_cat)
    
    padding = 19
    kutu_w = w_cat + padding * 2
    kutu_h = h_cat + padding * 2
    
    kutu_x = (full_w - kutu_w) // 2
    kutu_y = img_h - 38
    
    radius = 20
    # DÜZELTME: Sadece iki köşeyi yuvarlak yapan fonksiyon kullanılıyor.
    draw_rounded_rectangle(draw, [kutu_x, kutu_y, kutu_x + kutu_w, kutu_y + kutu_h], radius, SARI_BG)
    
    # DÜZELTME: Metin, kutu içinde tam olarak ortalanıyor.
    text_x = kutu_x + (kutu_w - w_cat) // 2
    text_y = kutu_y + (kutu_h - h_cat) // 2
    draw.text((text_x, text_y), cat_text, font=font_cat, fill=SIYAH)

    # ==== BAŞLIK ====
    keywords = ai_find_keywords(title)
    y_title = kutu_y + kutu_h + 38
    max_title_w = full_w - 60
    
    base_title_size = 72
    font_title, title_size = calculate_adaptive_font_size(draw, title, FONT_BOLD, max_title_w, base_title_size, 40)
    font_title_bold = ImageFont.truetype(FONT_BLACK, title_size)
    
    title_lines = wrap_text(draw, title, font_title, max_title_w)
    
    for i, line in enumerate(title_lines[:3]):
        w, h = get_text_size(draw, line, font_title)
        draw_ai_yellow_text(draw, line, font_title, (0, y_title), BEYAZ, keywords, 
                          bold_font=font_title_bold, center_width=full_w)
        y_title += h + 15

    # ==== ÖZET ====
    font_sum = ImageFont.truetype(FONT_REGULAR, 34)
    y_sum = y_title + 25
    max_sum_w = full_w - 70
    summary_lines = wrap_text(draw, summary, font_sum, max_sum_w)
    
    display_lines = summary_lines[:5]
    if len(summary_lines) > 5:
        last_line = display_lines[-1]
        words = last_line.split()
        while len(words) > 1:
            test_line = " ".join(words[:-1]) + "..."
            if get_text_size(draw, test_line, font_sum)[0] <= max_sum_w:
                display_lines[-1] = test_line
                break
            words = words[:-1]
    
    for line in display_lines:
        w, h = get_text_size(draw, line, font_sum)
        x = (full_w - w) // 2
        draw.text((x, y_sum), line, font=font_sum, fill=BEYAZ)
        y_sum += h + 14

    out_img = add_white_border(out_img, border_px=BORDER)
    
    filename = f"tg_{int(time.time()*1000)}.jpg"
    filepath = os.path.join(SAVE_DIR, filename)
    out_img.convert("RGB").save(filepath, quality=95, optimize=True)
    
    return filepath, used_url, note

# ================== WEB GÖRSELİ ==================
def process_image_web(img_url, title, summary, category):
    """Web için görsel işle"""
    img, used_url, note = smart_get_image(img_url)
    if img is None:
        raise Exception("Görsel indirilemedi")

    full_w, full_h = WEB_SIZE[0] - 2*BORDER, WEB_SIZE[1] - 2*BORDER
    
    temp_img = Image.new("RGBA", (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)

    # DÜZELTME: Önce içeriklerin ne kadar yer kaplayacağını hesaplayıp sonra bar'ı çiziyoruz.
    # 1. Kategori kutusu yüksekliğini hesapla
    kutu_font_size = 46
    font_cat = ImageFont.truetype(FONT_BLACK, kutu_font_size)
    cat_text = category.upper()
    w_cat, h_cat = get_text_size(temp_draw, cat_text, font_cat)
    padding_kutu = 19
    kutu_h = h_cat + padding_kutu * 2

    # 2. Başlık yüksekliğini hesapla
    max_title_w = full_w - 140
    font_title, title_size = calculate_adaptive_font_size(temp_draw, title, FONT_BOLD, max_title_w, 56, 30)
    title_lines = wrap_text(temp_draw, title, font_title, max_title_w)[:2]
    
    title_line_height = get_text_size(temp_draw, "Test", font_title)[1]
    total_title_height = len(title_lines) * title_line_height + (10 * (len(title_lines) - 1))

    # 3. Bar yüksekliğini dinamik olarak hesapla (orijinal estetiğe sadık kalarak)
    # Başlık kutunun 38px altında başlıyor, ve sonda da ~30px boşluk olsun.
    content_height_in_bar = kutu_h + 38 + total_title_height + 30 
    bar_h = content_height_in_bar
    
    # 4. Görsel yüksekliğini ve crop işlemini yap
    img_h = full_h - bar_h
    
    aspect = img.width / img.height
    target_h = img_h
    target_w = int(target_h * aspect)
    if target_w < full_w:
        target_w = full_w
        target_h = int(target_w / aspect)
    
    img_crop = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    left = (target_w - full_w) // 2
    top = (target_h - img_h) // 2
    crop_img = img_crop.crop((left, top, left+full_w, top+img_h))
    
    out_img = Image.new("RGBA", (full_w, full_h), SIYAH)
    out_img.paste(crop_img, (0,0))
    add_logo(out_img, LOGO_PATH, pos=(20, 20), size=(90, 55))
    draw = ImageDraw.Draw(out_img)

    # 5. Öğeleri doğru konumlara yerleştir
    # SARI KUTU
    kutu_x = 60
    # DÜZELTME: Kutu, görselin alt kenarından 38px yukarıda başlayarak üzerine taşıyor.
    kutu_y = img_h - 38
    w_cat, h_cat = get_text_size(draw, cat_text, font_cat)
    kutu_w = w_cat + padding_kutu * 2
    
    radius = 18
    # DÜZELTME: Sadece iki köşeyi yuvarlak yapan fonksiyon kullanılıyor.
    draw_rounded_rectangle(draw, [kutu_x, kutu_y, kutu_x + kutu_w, kutu_y + kutu_h], radius, SARI_BG)
    
    # DÜZELTME: Metin, kutu içinde tam olarak ortalanıyor.
    text_cat_x = kutu_x + (kutu_w - w_cat) // 2
    text_cat_y = kutu_y + (kutu_h - h_cat) // 2
    draw.text((text_cat_x, text_cat_y), cat_text, font=font_cat, fill=SIYAH)

    # BAŞLIK
    font_title_bold = ImageFont.truetype(FONT_BLACK, title_size)
    keywords = ai_find_keywords(title)
    
    # Başlık, kutunun bittiği yerden 38px sonra başlıyor.
    start_y = kutu_y + kutu_h + 38
    
    for i, line in enumerate(title_lines):
        _, h = get_text_size(draw, line, font_title)
        x_title = kutu_x + 4
        y_title = start_y + (i * (h + 10))
        
        draw_ai_yellow_text(draw, line, font_title, (x_title, y_title), BEYAZ, keywords, 
                          bold_font=font_title_bold)

    out_img = add_white_border(out_img, border_px=BORDER)
    
    filename = f"web_{int(time.time()*1000)}.jpg"
    filepath = os.path.join(SAVE_DIR, filename)
    out_img.convert("RGB").save(filepath, quality=95, optimize=True)
    
    return filepath, used_url, note

# Main ve Sheet fonksiyonları aynı kalabilir...
def update_sheet_with_retry(worksheet, cell, value, max_retries=3):
    """Sheet güncelleme with retry logic"""
    for attempt in range(max_retries):
        try:
            worksheet.update(cell, value)
            break
        except Exception as e:
            time.sleep(2*(attempt+1))
            if attempt == max_retries-1:
                print(f"Sheet update failed after {max_retries} attempts for {cell}: {str(e)}")

def main():
    """Ana fonksiyon"""
    try:
        credentials = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ])
        gc = gspread.authorize(credentials)
        ws = gc.open_by_key(SHEET_ID).worksheet(TAB_NAME)
        
        rows = ws.get_all_values()
        
        print(f"Toplam {len(rows)-1} satır bulundu. İşleme başlanıyor...")
        
        for idx, row in enumerate(rows[1:], start=2):
            status = row[COL_STATUS-1].strip().lower() if len(row) >= COL_STATUS else ""
            title = row[COL_TITLE-1].strip() if len(row) >= COL_TITLE else ""
            summary = row[COL_SUMMARY-1].strip() if len(row) >= COL_SUMMARY else ""
            img_url = row[COL_IMG-1].strip() if len(row) >= COL_IMG else ""
            category = row[COL_CATEGORY-1].strip() if len(row) >= COL_CATEGORY else "Haber"
            img_processed_web = row[COL_IMG_WEB-1].strip() if len(row) >= COL_IMG_WEB else ""
            img_processed_tg = row[COL_IMG_TG-1].strip() if len(row) >= COL_IMG_TG else ""
            
            status_ok = status in ["robot 1 başarılı / robot 2 başarılı", "robot 1 başarılı/robot 2 başarılı"]
            needs_processing = status_ok and title and img_url and (not img_processed_web or not img_processed_tg)
            
            if needs_processing:
                try:
                    print(f"Satır {idx}: {title[:50]}... işleniyor")
                    
                    out_path_web, used_url_web, note_web = process_image_web(img_url, title, summary, category)
                    update_sheet_with_retry(ws, f"L{idx}", [[out_path_web]])
                    print(f"  ✓ Web görseli oluşturuldu")
                    
                    out_path_tg, used_url_tg, note_tg = process_image_telegram(img_url, title, summary, category)
                    update_sheet_with_retry(ws, f"M{idx}", [[out_path_tg]])
                    print(f"  ✓ Telegram görseli oluşturuldu")
                    
                    if used_url_web != img_url:
                        update_sheet_with_retry(ws, f"K{idx}", [[used_url_web]])
                    
                    notes = [n for n in [note_web, note_tg] if n]
                    if notes:
                        update_sheet_with_retry(ws, f"T{idx}", [["; ".join(notes)]])
                    
                    update_sheet_with_retry(ws, f"S{idx}", [["Robot 1 Başarılı / Robot 2 Başarılı / Robot 3 Başarılı"]])
                    
                    time.sleep(1)
                    
                except Exception as e:
                    error_msg = str(e)
                    print(f"  ✗ Hata: {error_msg}")
                    update_sheet_with_retry(ws, f"T{idx}", [[error_msg]])
                    update_sheet_with_retry(ws, f"S{idx}", [[f"Robot 1 Başarılı / Robot 2 Başarılı / Robot 3 Hata: {error_msg}"]])
                    continue
        
        print("\n✅ VisualStyler işlemi tamamlandı!")
        
    except Exception as e:
        print(f"\n❌ Kritik hata: {str(e)}")
        raise

if __name__ == "__main__":
    main()