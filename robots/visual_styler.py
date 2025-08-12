# robots/visual_styler.py
# -*- coding: utf-8 -*-

from dotenv import load_dotenv
load_dotenv()

import os, io, uuid, time, re, requests, logging, traceback
from typing import List, Tuple, Dict
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from utils.auth import get_gspread_client
from utils.schema import resolve_columns
from google.cloud import storage, vision

# ====== Ayarlar (.env'den ve sabitler) ======
SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')
BUCKET_NAME = os.environ.get('GOOGLE_STORAGE_BUCKET')
NEWS_TAB = os.environ.get("NEWS_TAB", "News")
USE_VISION = os.environ.get("USE_VISION", "1").lower() not in ("0", "false")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

vision_client = None
if USE_VISION:
    try:
        if 'GCP_PROJECT' in os.environ:
            vision_client = vision.ImageAnnotatorClient()
        elif os.path.exists('service_account.json'):
            vision_client = vision.ImageAnnotatorClient.from_service_account_json('service_account.json')
        else:
            logger.warning("Lokalde Vision API için 'service_account.json' bulunamadı.")
            USE_VISION = False
    except Exception as e:
        logger.error(f"Vision API istemcisi başlatılamadı: {e}")
        vision_client = None
        USE_VISION = False

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO_PATH = os.path.join(BASE_DIR, "media", "logo.png")
FONT_DIR = os.path.join(BASE_DIR, "media", "font")
SAVE_DIR = os.path.join(BASE_DIR, "styled_images")
os.makedirs(SAVE_DIR, exist_ok=True)

WEB_SIZE = (1280, 720)
TG_SIZE  = (1080, 1350)
MIN_WIDTH, MIN_HEIGHT = 500, 300
SARI = (255, 206, 43); BEYAZ = (255, 255, 255); SIYAH = (0, 0, 0)
SARI_BG_RGBA = (255, 206, 43, 255)
BEYAZ_RGBA = (255, 255, 255, 255)
SIYAH_RGBA = (0, 0, 0, 255)
BORDER_PX = 19
FONT_BLACK = os.path.join(FONT_DIR, "Montserrat-Black.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "Montserrat-Bold.ttf")
FONT_LIGHT = os.path.join(FONT_DIR, "Montserrat-Light.ttf")
LOGO_SIZE = (180, 60)
CATEGORY_PILL_SIZE = 50
WEB_TITLE_SIZE = 64
TG_TITLE_SIZE = 72
TG_SUMMARY_SIZE = 34
TITLE_LINE_SPACING = 10
SUMMARY_LINE_SPACING = 8
CM_IN_PX = 38
WEB_GAP_DELTA_PX = -int(0.2 * CM_IN_PX)
TG_TITLE_LINE_SPACING_EXTRA = 4
TG_SUMMARY_LINE_SPACING_EXTRA = 4
PILL_EXTRA_BOTTOM_PAD_PX = 6
PILL_REDUCE_TOP_PAD_PX = 4

TR_STOPWORDS = { 
     "ve","veya","ile","ama","fakat","ancak","çünkü","için","de","da","ki","ya","ya-da","gibi", 
     "göre","kadar","üzere","diye","hem","yine","sonra","önce","ayrıca","aynı","artık","hiç", 
     "hiçbir","şu","an","zaten","böyle","böylece","tüm","her","bazı","bu","şu","o","bir","çok","az","daha" 
}

# ===================== TÜM GÖRSEL İŞLEME YARDIMCILARI (Sizin Kodunuz) =====================

def download_image(url: str, tries=3, timeout=12): 
     last = None 
     for _ in range(tries): 
         try: 
             r = requests.get(url, timeout=timeout, headers={"User-Agent":"Mozilla/5.0"}) 
             r.raise_for_status() 
             img = Image.open(io.BytesIO(r.content)) 
             if img.mode != "RGBA": img = img.convert("RGBA") 
             return img 
         except Exception as e: 
             last = e; time.sleep(1.0) 
     raise last 

def validate_image(img): return img.width >= MIN_WIDTH and img.height >= MIN_HEIGHT 

def _image_to_vision_image(img): 
     buf = io.BytesIO(); img.save(buf, format='PNG') 
     return vision.Image(content=buf.getvalue()) 

def fetch_og_image(page_url: str): 
     try: 
         html = requests.get(page_url, timeout=10, headers={"User-Agent":"Mozilla/5.0"}).text 
         m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I) 
         if m: return m.group(1) 
     except Exception: pass 
     return None 

def find_better_image_with_vision(img): 
     if not vision_client: return None 
     try: 
         resp = vision_client.web_detection(image=_image_to_vision_image(img)) 
         wd = resp.web_detection 
         urls = [] 
         if wd.full_matching_images:    urls += [x.url for x in wd.full_matching_images] 
         if wd.partial_matching_images: urls += [x.url for x in wd.partial_matching_images] 
         seen, cand = set(), [] 
         for u in urls: 
             if u and u not in seen: 
                 cand.append(u); seen.add(u) 
             if len(cand) >= 5: break 
         for u in cand: 
             try: 
                 alt = download_image(u, tries=2) 
                 if validate_image(alt): return alt 
             except: pass 
     except Exception: pass 
     return None 

def smart_get_image(img_url: str|None, page_url: str|None=None): 
     img = None 
     if img_url: 
         try: img = download_image(img_url) 
         except Exception: img = None 
     if img and validate_image(img): return img, img_url, "" 
     if page_url: 
         og = fetch_og_image(page_url) 
         if og: 
             try: 
                 alt = download_image(og) 
                 if validate_image(alt): return alt, og, "og:image" 
             except Exception: pass 
     if img and USE_VISION: 
         alt2 = find_better_image_with_vision(img) 
         if alt2 and validate_image(alt2): return alt2, img_url, "vision-web-detection" 
     return (img if img else None), (img_url or ""), "fallback" 

def get_text_size(draw, text, font): 
     x1,y1,x2,y2 = draw.textbbox((0,0), text, font=font) 
     return (x2-x1, y2-y1) 

def draw_white_border(img, border=BORDER_PX): 
     w,h = img.size 
     canvas = Image.new("RGBA", (w+2*border, h+2*border), BEYAZ_RGBA) 
     canvas.paste(img, (border,border), img if img.mode=="RGBA" else None) 
     return canvas 

def draw_logo(img, pos=(20,20), size=(180,60)): 
     try: 
         if os.path.exists(LOGO_PATH): 
             lg = Image.open(LOGO_PATH).convert("RGBA").resize(size, Image.Resampling.LANCZOS) 
             img.paste(lg, pos, lg) 
     except: pass 
     return img 

def save_jpg(img, path): 
     if img.mode == "RGBA": 
         bg = Image.new("RGB", img.size, (0,0,0)) 
         bg.paste(img, mask=img.split()[-1]); img = bg 
     img.save(path, "JPEG", quality=90, optimize=True, progressive=True)

def blur_box(img, box, radius=16): 
     x1,y1,x2,y2 = map(int, box) 
     x1,y1 = max(0,x1), max(0,y1) 
     x2,y2 = min(img.width,x2), min(img.height,y2) 
     if x2<=x1 or y2<=y1: return img 
     crop = img.crop((x1,y1,x2,y2)).filter(ImageFilter.GaussianBlur(radius)) 
     img.paste(crop, (x1,y1)); return img 

def detect_and_blur_logos(img): 
     if not vision_client: return img 
     try: 
         resp = vision_client.logo_detection(image=_image_to_vision_image(img)) 
         if not resp or not resp.logo_annotations: return img 
         for ann in resp.logo_annotations: 
             v = ann.bounding_poly.vertices 
             xs, ys = [p.x for p in v], [p.y for p in v] 
             img = blur_box(img, (min(xs),min(ys),max(xs),max(ys)), radius=18) 
     except Exception: pass 
     return img 

def smart_crop_with_ai(img, target_w, target_h): 
     if vision_client: 
         try: 
             resp = vision_client.crop_hints( 
                 image=_image_to_vision_image(img), 
                 image_context={"crop_hints_params": {"aspect_ratios": [target_w/target_h]}} 
             ) 
             hints = resp.crop_hints_annotation.crop_hints 
             if hints: 
                 v = hints[0].bounding_poly.vertices 
                 xs, ys = [p.x for p in v], [p.y for p in v] 
                 crop = img.crop((min(xs),min(ys),max(xs),max(ys))) 
                 return crop.resize((target_w,target_h), Image.Resampling.LANCZOS) 
         except Exception: pass 
     aspect = img.width / img.height 
     t = target_w / target_h 
     if aspect > t: 
         new_w = int(img.height * t) 
         left = (img.width - new_w)//2 
         crop = img.crop((left,0,left+new_w,img.height)) 
     else: 
         new_h = int(img.width / t) 
         top = (img.height - new_h)//2 
         crop = img.crop((0,top,img.width,top+new_h)) 
     return crop.resize((target_w,target_h), Image.Resampling.LANCZOS) 

def pick_emphasis_tokens(text, max_yellow=2, max_bold=3): 
     tokens = re.findall(r"[0-9]+(?:\.[0-9]+)?%?|[A-Za-zÇĞİÖŞÜçğıöşü]+", text) 
     words = [t for t in tokens if t and t.lower() not in TR_STOPWORDS] 
     scored = [] 
     for w in words: 
         s = 0 
         if re.search(r"[0-9]", w): s += 2 
         if w.endswith("%"): s += 1 
         if w[0].isupper(): s += 1 
         s += min(2, max(0, len(w)-4)/3) 
         scored.append((s, w)) 
     scored.sort(reverse=True) 
     return {w for _,w in scored[:max_yellow]}, {w for _,w in scored[max_yellow:max_yellow+max_bold]} 

def draw_pill_two_round_corners(img, text, center_x, center_y, base_font_size=CATEGORY_PILL_SIZE): 
     font = ImageFont.truetype(FONT_BLACK, int(base_font_size * 1.5)) 
     draw = ImageDraw.Draw(img) 
     text_w, text_h = get_text_size(draw, text, font) 
     pad_y_top = max(0, int(text_h * 0.45) - PILL_REDUCE_TOP_PAD_PX) 
     pad_y_bot = int(text_h * 0.45) + PILL_EXTRA_BOTTOM_PAD_PX 
     pad_x = int(text_h * 0.45) 
     w, h = text_w + 2*pad_x, text_h + pad_y_top + pad_y_bot 
     left, top = center_x - w//2, center_y - h//2 
     right, bottom = left + w, top + h 
     mask = Image.new("L", (w, h), 0) 
     mdraw = ImageDraw.Draw(mask) 
     r = int(h * 0.45) 
     mdraw.rounded_rectangle((0,0,w,h), radius=r, fill=255) 
     mdraw.rectangle((w-r, 0, w, r), fill=255) 
     mdraw.rectangle((0, h-r, r, h), fill=255) 
     pill = Image.new("RGBA", (w,h), SARI + (255,)) 
     shadow = Image.new('RGBA', (w+8, h+8), (0,0,0,0)) 
     sd = ImageDraw.Draw(shadow) 
     sd.rounded_rectangle((4,4,w+4,h+4), radius=r, fill=(0,0,0,120)) 
     img.alpha_composite(shadow, (left-4, top-4)) 
     img.paste(pill, (left, top), mask) 
     draw.text((left + pad_x, top + pad_y_top - 1), text, font=font, fill=SIYAH + (255,)) 
     return img, (left, top, right, bottom) 

def draw_words_emphasized_center(img, area, text, font_regular_path, font_bold_path, size, yellow_words, bold_words, line_spacing): 
     left, top, right, bottom = area 
     draw = ImageDraw.Draw(img) 
     f_r = ImageFont.truetype(font_regular_path, size) 
     f_b = ImageFont.truetype(font_bold_path, size) 
     words = text.split() 
     lines, cur = [], "" 
     for w in words: 
         test = (cur + " " + w).strip() 
         if get_text_size(draw, test, f_r)[0] <= (right-left): cur = test 
         else: 
             if cur: lines.append(cur); cur = w 
     if cur: lines.append(cur) 
     line_h = get_text_size(draw, "Ay", f_r)[1] 
     total_h = len(lines)*line_h + (len(lines)-1)*line_spacing 
     y = top + max(0, ((bottom-top)-total_h)//2) 
     space_w,_ = get_text_size(draw," ", f_r) 
     for line in lines: 
         tokens = line.split() 
         w_sum = 0 
         for t in tokens: 
             fw = t.strip(",.!?;:") 
             font = f_b if fw in bold_words else f_r 
             w_sum += get_text_size(draw, t, font)[0] + space_w 
         w_sum -= space_w 
         x = left + max(0, ((right-left)-w_sum)//2) 
         for t in tokens: 
             fw = t.strip(",.!?;:") 
             font = f_b if fw in bold_words else f_r 
             color = SARI + (255,) if fw in yellow_words else BEYAZ_RGBA 
             tw,_ = get_text_size(draw, t, font) 
             if x + tw > right: break 
             draw.text((x,y), t, font=font, fill=color) 
             x += tw + space_w 
         y += line_h + line_spacing 

def draw_multiline_center_with_trunc(img, area, text, font_path, size, fill=BEYAZ_RGBA, max_lines=None, line_spacing=6): 
     left, top, right, bottom = area 
     draw = ImageDraw.Draw(img) 
     font = ImageFont.truetype(font_path, size) 
     words = text.split() 
     lines, cur = [], "" 
     for w in words: 
         test = (cur + " " + w).strip() 
         if get_text_size(draw, test, font)[0] <= (right-left): cur = test 
         else: 
             if cur: lines.append(cur); cur = w 
     if cur: lines.append(cur) 
     if max_lines and len(lines) > max_lines: 
         lines = lines[:max_lines] 
         lines[-1] = (lines[-1] + " . . .").strip() 
     lh = get_text_size(draw, "Ay", font)[1] 
     total_h = len(lines)*lh + (len(lines)-1)*line_spacing 
     y = top + max(0, ((bottom-top)-total_h)//2) 
     for line in lines: 
         tw,_ = get_text_size(draw, line, font) 
         x = left + ((right-left)-tw)//2 
         draw.text((x,y), line, font=font, fill=fill) 
         y += lh + line_spacing 

def add_bottom_to_pill_fade(img, pill_box, strength=340): 
     w,h = img.size 
     _, pill_top, _, _ = pill_box 
     pill_top = max(0, min(h-1, pill_top)) 
     fade = Image.new('L', (w, h), 0) 
     dr = ImageDraw.Draw(fade) 
     denom = max(1, (h - 1 - pill_top)) 
     for y in range(pill_top, h): 
         alpha = int(strength * ((y - pill_top) / denom)) 
         if alpha > 255: alpha = 255 
         dr.line((0, y, w, y), fill=alpha) 
     black = Image.new('RGBA', (w, h), (0,0,0,255)) 
     black.putalpha(fade) 
     return Image.alpha_composite(img, black) 

def soft_midline_blend(base, mid_y, band=110, opacity_boost=1.45): 
     w,h = base.size 
     grad = Image.new('L', (w, band), 0) 
     dr = ImageDraw.Draw(grad) 
     for y in range(band): 
         a = int(255 * (y / (band-1)) * opacity_boost) 
         if a > 255: a = 255 
         dr.line((0,y,w,y), fill=a) 
     blk = Image.new('RGBA', (w, band), (0,0,0,255)); blk.putalpha(grad) 
     base.paste(blk, (0, mid_y - band//2), blk) 
     return base 

def render_web(img_url, page_url, category, title): 
     W,H = WEB_SIZE 
     img, _, _ = smart_get_image(img_url, page_url) 
     canvas = smart_crop_with_ai(img, W, H) if img else Image.new("RGBA", (W,H), (12,12,12,255)) 
     canvas = detect_and_blur_logos(canvas) 
     draw_logo(canvas, pos=(20,20), size=LOGO_SIZE) 
     canvas, pill_box = draw_pill_two_round_corners( 
         canvas, (category or "HABER").upper(), center_x=W//2, center_y=int(H*0.54) 
     ) 
     canvas = add_bottom_to_pill_fade(canvas, pill_box, strength=340) 
     title_font = ImageFont.truetype(FONT_BLACK, WEB_TITLE_SIZE) 
     line_h = title_font.getbbox("Ay")[3] 
     default_gap = 3 * max(12, line_h // 3) 
     gap = max(0, default_gap + WEB_GAP_DELTA_PX) 
     title_area = (60, pill_box[3] + 23, W-60, H-60) 
     yellow, bold = pick_emphasis_tokens(title, max_yellow=2, max_bold=3) 
     draw_words_emphasized_center( 
         canvas, title_area, title, 
         font_regular_path=FONT_BLACK, font_bold_path=FONT_BOLD, 
         size=WEB_TITLE_SIZE, yellow_words=yellow, bold_words=bold, 
         line_spacing=TITLE_LINE_SPACING 
     ) 
     return draw_white_border(canvas, BORDER_PX) 

def render_telegram(img_url, page_url, category, title, summary): 
     W,H = TG_SIZE 
     base = Image.new("RGBA", (W,H), (0,0,0,255)) 
     mid = H//2 
     img, _, _ = smart_get_image(img_url, page_url) 
     if img: 
         top_img = smart_crop_with_ai(img, W, mid) 
         top_img = detect_and_blur_logos(top_img) 
         base.paste(top_img, (0,0)) 
     base = soft_midline_blend(base, mid, band=110, opacity_boost=1.45) 
     base, pill_box = draw_pill_two_round_corners( 
         base, (category or "HABER").upper(), center_x=W//2, center_y=mid 
     ) 
     title_font = ImageFont.truetype(FONT_BLACK, TG_TITLE_SIZE) 
     line_h = title_font.getbbox("Ay")[3] 
     default_gap = 2 * max(12, line_h // 3) 
     gap = max(0, default_gap - int(0.1 * CM_IN_PX)) 
     pad_x = BORDER_PX + 40 
     title_area = (pad_x, pill_box[3] + gap, W - pad_x, mid + (H-mid)//2 - 12) 
     summary_area = (pad_x, mid + (H-mid)//2 + 12, W - pad_x, H - BORDER_PX - 24) 
     yellow, bold = pick_emphasis_tokens(title, max_yellow=2, max_bold=3) 
     draw_words_emphasized_center( 
         base, title_area, title, 
         font_regular_path=FONT_BLACK, font_bold_path=FONT_BOLD, 
         size=TG_TITLE_SIZE, yellow_words=yellow, bold_words=bold, 
         line_spacing=TITLE_LINE_SPACING + TG_TITLE_LINE_SPACING_EXTRA 
     ) 
     draw_multiline_center_with_trunc( 
         base, summary_area, summary or "", font_path=FONT_LIGHT, size=TG_SUMMARY_SIZE, 
         fill=BEYAZ_RGBA, max_lines=5, line_spacing=SUMMARY_LINE_SPACING + TG_SUMMARY_LINE_SPACING_EXTRA
     ) 
     draw_logo(base, pos=(20,20), size=LOGO_SIZE) 
     return draw_white_border(base, BORDER_PX)

def process_image_web(img_url, title, summary, category, page_url=None): 
     out = render_web(img_url, page_url, category or "Haber", title) 
     path = os.path.join(SAVE_DIR, f"web_{uuid.uuid4()}.jpg"); save_jpg(out, path) 
     return path, img_url, "web" 

def process_image_telegram(img_url, title, summary, category, page_url=None): 
     out = render_telegram(img_url, page_url, category or "Haber", title, summary or "") 
     path = os.path.join(SAVE_DIR, f"tg_{uuid.uuid4()}.jpg"); save_jpg(out, path) 
     return path, img_url, "tg" 

# ===================== Çalıştırıcı (YENİ YAPIYA UYGUN) ===================== 

def run(): 
    logger.info("🤖 AI Destekli Visual Styler robotu başlatılıyor...") 
    
    try:
        gc = get_gspread_client()
        ws = gc.open_by_key(SHEET_ID).worksheet(TAB_NAME)
        cols = resolve_columns(ws)
        all_rows = ws.get_all_values()
    except Exception as e:
        logger.error(f"❌ Google Sheet'e bağlanırken veya okurken kritik hata: {e}")
        traceback.print_exc()
        return "Sheet bağlantı hatası."

    # İşlenecekleri topla
    rows_to_process = []
    for i, row in enumerate(all_rows[1:], start=2):
        try:
            if len(row) <= cols.AC -1 : continue
            status = row[cols.AC - 1].strip()
            title = row[cols.H - 1].strip()
            img_url = row[cols.K - 1].strip()
            img_web = row[cols.L - 1].strip() if len(row) > cols.L -1 else ""
            img_tg = row[cols.M - 1].strip() if len(row) > cols.M -1 else ""

            if "robot 2 başarılı" in status.lower() and title and img_url and (not img_web or not img_tg):
                rows_to_process.append({
                    "row_index": i, "title": title,
                    "summary": row[cols.I - 1].strip(),
                    "category": row[cols.C - 1].strip(),
                    "image_url": img_url,
                    "page_url": row[cols.G - 1].strip()
                })
        except IndexError:
            logger.warning(f"Satır {i} beklenenden daha az sütuna sahip, atlanıyor.")
            continue

    if not rows_to_process:
        logger.info("İşlenecek yeni görsel bulunamadı.")
        return "İşlenecek görsel yok."

    logger.info(f"{len(rows_to_process)} adet haber için görsel işlenecek...")
    
    for row in rows_to_process:
        idx = row["row_index"]
        logger.info(f"--- Satır {idx}: {row['title'][:50]}... işleniyor ---")
        try:
            # Web görseli oluştur, yerel diske kaydet
            path_web, _, _ = process_image_web(row["image_url"], row["title"], row["summary"], row["category"], page_url=row["page_url"])
            
            # Cloud Storage'a yükle ve linki al
            web_gcs_url = upload_to_gcs(path_web, "web-images")
            ws.update_cell(idx, cols.L, web_gcs_url)
            logger.info(f"  ✓ Web görseli yüklendi: {web_gcs_url}")

            # Telegram görseli oluştur, yerel diske kaydet
            path_tg, _, _ = process_image_telegram(row["image_url"], row["title"], row["summary"], row["category"], page_url=row["page_url"])
            
            # Cloud Storage'a yükle ve linki al
            tg_gcs_url = upload_to_gcs(path_tg, "telegram-images")
            ws.update_cell(idx, cols.M, tg_gcs_url)
            logger.info(f"  ✓ Telegram görseli yüklendi: {tg_gcs_url}")

            # Durumu güncelle
            current_status = ws.cell(idx, cols.AC).value
            ws.update_cell(idx, cols.AC, f"{current_status} / Robot 3 Başarılı (AI)")

        except Exception as e:
            logger.error(f"  ✗ Hata (Satır {idx}): {e}")
            traceback.print_exc()
            current_status = ws.cell(idx, cols.AC).value
            ws.update_cell(idx, cols.AC, f"{current_status} / Robot 3 Hata")
            ws.update_cell(idx, cols.AD, str(e))
            continue
    
    logger.info(f"\n✅ AI Destekli VisualStyler işlemi tamamlandı!")
    return f"İşlem tamamlandı. {len(rows_to_process)} görsel işlendi."

if __name__ == "__main__":
    run()
