# robots/visual_styler.py
# -*- coding: utf-8 -*-

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
WEB_FORMAT            = "WEBP"
SOCIAL_FORMAT         = "JPEG"
JPG_QUALITY           = int(os.environ.get("JPG_QUALITY", "86"))
WEBP_QUALITY          = int(os.environ.get("WEBP_QUALITY", "82"))

# Kalite/kaynak tarama eşikleri
MIN_W                 = int(os.environ.get("MIN_IMAGE_W", "900"))
MIN_H                 = int(os.environ.get("MIN_IMAGE_H", "600"))
WEB_MAX_IMAGES        = int(os.environ.get("WEB_MAX_IMAGES", "3"))  # L sütununa en fazla kaç görsel yazılacak

# =================== Platform Profilleri ===================
# mode: "cover" (kırp), "fit" (pad), "fitw" (genişliği sabitle → 1200, oran koru)
Profile = Dict[str, Dict[str, object]]
PROFILES: Profile = {
    "telegram":      {"size": (1280, 720),  "mode": "cover", "fmt": SOCIAL_FORMAT, "ratio": 1280/720},
    "x":             {"size": (1200, 675),  "mode": "cover", "fmt": SOCIAL_FORMAT, "ratio": 1200/675},
    "bluesky":       {"size": (1200, 675),  "mode": "cover", "fmt": SOCIAL_FORMAT, "ratio": 1200/675},
    "linkedin":      {"size": (1200, 627),  "mode": "cover", "fmt": SOCIAL_FORMAT, "ratio": 1200/627},
    "instagram":     {"size": (1080, 1350), "mode": "cover", "fmt": SOCIAL_FORMAT, "ratio": 1080/1350},
    "ig_video":      {"size": (1080, 1920), "mode": "cover", "fmt": SOCIAL_FORMAT, "ratio": 1080/1920},
    "tiktok_video":  {"size": (1080, 1920), "mode": "cover", "fmt": SOCIAL_FORMAT, "ratio": 1080/1920},
    "yt_shorts":     {"size": (1080, 1920), "mode": "cover", "fmt": SOCIAL_FORMAT, "ratio": 1080/1920},
    "yt_long":       {"size": (1280, 720),  "mode": "cover", "fmt": SOCIAL_FORMAT, "ratio": 1280/720},
    "web":           {"size": (1200, None), "mode": "fitw",  "fmt": WEB_FORMAT,    "ratio": None},
}

# =================== Vision Client (opsiyonel) ===================
_vclient = None
def _get_vision():
    """Google Cloud Vision client; GOOGLE_APPLICATION_CREDENTIALS set olmalı."""
    global _vclient
    if _vclient is not None:
        return _vclient
    if not USE_VISION:
        _vclient = None
        return _vclient
    try:
        from google.cloud import vision
        _vclient = vision.ImageAnnotatorClient()
    except Exception:
        _vclient = None
    return _vclient

# =================== Yardımcılar ===================
def _ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def _http_get_bytes(url: str, timeout=15) -> Optional[bytes]:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent":"GoNewsBot/1.0"})
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None

def _http_get_image(url: str) -> Optional[Image.Image]:
    b = _http_get_bytes(url)
    if not b: return None
    try:
        return Image.open(io.BytesIO(b)).convert("RGB")
    except Exception:
        return None

def _enhance(im: Image.Image) -> Image.Image:
    if BRIGHTNESS_BOOST != 1.0:
        im = ImageEnhance.Brightness(im).enhance(BRIGHTNESS_BOOST)
    if CONTRAST_BOOST != 1.0:
        im = ImageEnhance.Contrast(im).enhance(CONTRAST_BOOST)
    if SHARPEN_AMOUNT > 1.0:
        im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(100*SHARPEN_AMOUNT), threshold=2))
    return im

def _pillow_corner_logo_soften(im: Image.Image) -> Image.Image:
    if not FALLBACK_LOGO_REMOVE:
        return im
    w,h = im.size
    pad = max(int(min(w,h)*0.12), 80)
    mask = Image.new("L",(w,h),0)
    d = ImageDraw.Draw(mask)
    for poly in [ [(0,0),(pad,0),(0,pad)],
                  [(w,0),(w-pad,0),(w,pad)],
                  [(0,h),(pad,h),(0,h-pad)],
                  [(w,h),(w-pad,h),(w,h-pad)] ]:
        d.polygon(poly, fill=110)
    mask = mask.filter(ImageFilter.GaussianBlur(8))
    blur = im.filter(ImageFilter.GaussianBlur(3))
    return Image.composite(blur, im, ImageOps.invert(mask))

def _save(im: Image.Image, path: str, fmt: str) -> str:
    _ensure_dir(os.path.dirname(path))
    params = {}
    fu = fmt.upper()
    if fu in ("JPG","JPEG"):
        fmt="JPEG"; params.update(quality=JPG_QUALITY, optimize=True)
    elif fu == "WEBP":
        fmt="WEBP"; params.update(quality=WEBP_QUALITY, method=5)
    im.save(path, fmt, **params)
    return path

def _wrap_title(title: str, max_chars=42, max_lines=2) -> List[str]:
    return textwrap.wrap((title or "").strip(), width=max_chars)[:max_lines]

def _draw_telegram_banner(im: Image.Image, title: str) -> Image.Image:
    if not TELEGRAM_STYLE_KEEP: return im
    w,h = im.size; bh = max(int(h*0.18), 120)
    overlay = Image.new("RGBA",(w,h)); draw = ImageDraw.Draw(overlay)
    draw.rectangle([0,h-bh,w,h], fill=(0,0,0,180))
    if TELEGRAM_FONT_PATH and os.path.exists(TELEGRAM_FONT_PATH):
        try:
            font = ImageFont.truetype(TELEGRAM_FONT_PATH, size=max(36, int(bh*0.28)))
        except Exception:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()
    lines = _wrap_title(title, 42, TELEGRAM_TITLE_LINES)
    total_h = sum(font.getbbox(t)[3]-font.getbbox(t)[1] + 6 for t in lines)
    y = h-bh + (bh-total_h)//2
    for t in lines:
        tw = font.getbbox(t)[2]-font.getbbox(t)[0]
        x = (w-tw)//2
        draw.text((x+2,y+2), t, font=font, fill=(0,0,0,200))
        draw.text((x,y), t, font=font, fill=(255,255,255,230))
        y += (font.getbbox(t)[3]-font.getbbox(t)[1]) + 6
    return Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")

def _platform_filename(haber_id: str, key: str, fmt: str) -> str:
    ext = "jpg" if fmt.upper() in ("JPG","JPEG") else "webp"
    return os.path.join(OUT_DIR, haber_id, f"{haber_id}_{key}.{ext}")

# ============ HTML'den görsel çıkarımı ============
def _best_from_srcset(srcset: str) -> Optional[str]:
    best = None; best_w = -1
    for part in (srcset or "").split(","):
        seg = part.strip().split()
        if not seg: continue
        u = seg[0]; w = 0
        if len(seg) > 1 and seg[1].endswith("w"):
            try: w = int(seg[1][:-1])
            except: w = 0
        if w > best_w:
            best = u; best_w = w
    return best

def _extract_images_from_html(page_url: str, html: bytes) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: List[str] = []

    for prop in ("og:image", "twitter:image", "twitter:image:src"):
        t = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if t and t.get("content"):
            out.append(urljoin(page_url, t["content"]))

    link_tag = soup.find("link", attrs={"rel": "image_src"})
    if link_tag and link_tag.get("href"):
        out.append(urljoin(page_url, link_tag["href"]))

    for im in soup.find_all("img"):
        if im.get("srcset"):
            cand = _best_from_srcset(im["srcset"])
            if cand:
                out.append(urljoin(page_url, cand))

    for im in soup.find_all("img"):
        if im.get("src"):
            out.append(urljoin(page_url, im["src"]))

    # uniq
    seen = set(); uniq = []
    for u in out:
        if u not in seen:
            uniq.append(u); seen.add(u)
    return uniq

def _download_and_check(urls: List[str], need_ratio: Optional[float]=None) -> List[Image.Image]:
    """Uygun boyutta (>=MIN_W/MIN_H) görselleri indir; oran yakınlığına göre puanla."""
    candidates: List[Tuple[float, Image.Image]] = []
    for u in urls:
        im = _http_get_image(u)
        if im is None: continue
        w,h = im.size
        if w < MIN_W or h < MIN_H:
            continue
        score = float(w*h)
        if need_ratio:
            ratio = w/h
            score *= max(0.6, 1.0 - abs(ratio - need_ratio)*0.15)
        candidates.append((score, im))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [im for _, im in candidates]

# ============ Vision tabanlı smart crop / logo ============
def _bytes_of(im: Image.Image) -> bytes:
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=92); return buf.getvalue()

def _vision_smart_crop(im: Image.Image, aspect_ratio: float) -> Optional[Tuple[int,int,int,int]]:
    client = _get_vision()
    if not client or aspect_ratio is None:
        return None
    try:
        from google.cloud import vision
        img = vision.Image(content=_bytes_of(im))
        ctx = vision.ImageContext(crop_hints_params=vision.CropHintsParams(aspect_ratios=[aspect_ratio]))
        resp = client.crop_hints(image=img, image_context=ctx)
        hints = resp.crop_hints_annotation.crop_hints
        if not hints: return None
        poly = sorted(hints, key=lambda h: getattr(h, "confidence", 0), reverse=True)[0].bounding_poly
        xs = [v.x for v in poly.vertices]; ys = [v.y for v in poly.vertices]
        left, right = max(0,min(xs)), max(xs); top, bottom = max(0,min(ys)), max(ys)
        if (right-left) < 100 or (bottom-top) < 100: return None
        return (left, top, right, bottom)
    except Exception:
        return None

def _vision_logo_mask(im: Image.Image) -> Optional[Image.Image]:
    if not VISION_LOGO_REMOVE: return None
    client = _get_vision()
    if not client: return None
    try:
        from google.cloud import vision
        img = vision.Image(content=_bytes_of(im))
        resp = client.logo_detection(image=img)
        ann = resp.logo_annotations
        if not ann: return None
        w,h = im.size
        mask = Image.new("L",(w,h),0); d = ImageDraw.Draw(mask)
        for logo in ann:
            poly = logo.bounding_poly.vertices
            if not poly: continue
            pts = [(max(0,min(v.x,w-1)), max(0,min(v.y,h-1))) for v in poly]
            d.polygon(pts, fill=120)
        return mask.filter(ImageFilter.GaussianBlur(6))
    except Exception:
        return None

def _apply_crop(im: Image.Image, box: Tuple[int,int,int,int]) -> Image.Image:
    L,T,R,B = box
    L = max(0,min(L, im.width-2)); T=max(0,min(T, im.height-2))
    R = max(L+1,min(R, im.width));  B=max(T+1,min(B, im.height))
    return im.crop((L,T,R,B))

def _resize_for_profile(src: Image.Image, profile: Dict[str, object]) -> Image.Image:
    size: Tuple[Optional[int],Optional[int]] = profile["size"]  # type: ignore
    mode: str = profile["mode"]  # type: ignore
    ratio = profile.get("ratio", None)

    im = src
    if mode == "cover" and USE_VISION and isinstance(ratio, (int,float)):
        box = _vision_smart_crop(src, float(ratio))
        if box:
            try:
                im = _apply_crop(src, box)
            except Exception:
                im = src

    tw, th = size
    ow, oh = im.size

    if mode == "fitw":
        if tw is None: return im
        scale = tw/float(ow); new_h = int(oh*scale)
        return im.resize((tw, new_h), Image.LANCZOS)

    if mode == "cover":
        src_ratio = ow/oh; dst_ratio = tw/th
        if src_ratio > dst_ratio:
            new_h = th; new_w = int(th*src_ratio)
        else:
            new_w = tw; new_h = int(tw/src_ratio)
        resized = im.resize((new_w,new_h), Image.LANCZOS)
        left = (new_w - tw)//2; top = (new_h - th)//2
        return resized.crop((left, top, left+tw, top+th))

    # fit
    src_ratio = ow/oh; dst_ratio = tw/th
    if src_ratio > dst_ratio:
        new_w = tw; new_h = int(tw/src_ratio)
    else:
        new_h = th; new_w = int(th*src_ratio)
    resized = im.resize((new_w,new_h), Image.LANCZOS)
    canvas = Image.new("RGB",(tw,th),(10,10,10))
    canvas.paste(resized, ((tw-new_w)//2, (th-new_h)//2))
    return canvas

def _apply_logo_processing(im: Image.Image) -> Image.Image:
    # Vision logo maskesi
    mask = _vision_logo_mask(im)
    if mask:
        blur = im.filter(ImageFilter.GaussianBlur(3))
        return Image.composite(blur, im, ImageOps.invert(mask))
    # Fallback köşe yumuşatma
    return _pillow_corner_logo_soften(im)

# =================== ANA AKIŞ ===================
def run():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id: raise RuntimeError("GOOGLE_SHEET_ID boş.")

    gc = get_gspread_client()
    ws = gc.open_by_key(sheet_id).worksheet(NEWS_TAB)
    cols = resolve_columns(ws)

    values = ws.get_all_values()
    data = values[1:]  # header hariç

    candidates: List[Tuple[int,str,str,str]] = []
    for i, row in enumerate(data, start=2):
        img_url = (row[cols.K-1].strip() if len(row)>=cols.K else "")
        if not img_url:
            continue

        # Yalnızca Robot 1 & Robot 2 başarılı olanlar
        ac_val = (row[cols.AC - 1].strip().lower() if len(row) >= cols.AC else "")
        if "robot 1 başarılı / robot 2 başarılı" not in ac_val:
            continue

        # Platform sütunlarından herhangi biri boşsa (henüz üretilmemiş) işlenecek
        targets = [cols.L, cols.M, cols.N, cols.O, cols.P, cols.Q, cols.R, cols.S, cols.T, cols.U]
        done = True
        for c in targets:
            if len(row) < c or not row[c-1].strip():
                done = False; break
        if done:
            continue

        haber_id = (row[cols.A-1].strip() if len(row)>=cols.A else "")
        title_tr = (row[cols.H-1].strip() if len(row)>=cols.H else "") or (row[cols.F-1].strip() if len(row)>=cols.F else "")
        candidates.append((i, haber_id, img_url, title_tr))

    print(f"Üretilecek satır: {len(candidates)} (Vision={'ON' if _get_vision() else 'OFF'})")

    for s in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[s:s+BATCH_SIZE]

        for (row_idx, haber_id, img_url, title_tr) in batch:
            try:
                base = _http_get_image(img_url)

                # Kalitesiz ise sayfadan daha iyi görselleri ara
                use_base_list: List[Image.Image] = []
                if base is not None:
                    bw, bh = base.size
                    if bw >= MIN_W and bh >= MIN_H:
                        use_base_list = [base]
                    else:
                        # Orijinal sayfa
                        row_vals = ws.row_values(row_idx)
                        page_url = row_vals[cols.G - 1] if len(row_vals) >= cols.G else ""
                        if page_url:
                            html = _http_get_bytes(page_url)
                            if html:
                                ratio_hint = PROFILES["web"]["ratio"]
                                urls = _extract_images_from_html(page_url, html)
                                better = _download_and_check(urls, need_ratio=ratio_hint if isinstance(ratio_hint, (int,float)) else None)
                                if better:
                                    use_base_list = better
                if not use_base_list and base is not None:
                    use_base_list = [base]

                if not use_base_list:
                    ws.update_cell(row_idx, cols.AC, "Robot 1 Başarılı / Robot 2 Başarılı / Robot 3 Hata")
                    ws.update_cell(row_idx, cols.AD, "Görsel bulunamadı/indirilemedi")
                    continue

                # ========== 1) WEB — çoklu (max WEB_MAX_IMAGES), 1200w WebP ==========
                web_paths: List[str] = []
                p_web = PROFILES["web"]
                for idx_img, im_src in enumerate(use_base_list[:WEB_MAX_IMAGES], start=1):
                    im2 = _apply_logo_processing(im_src)
                    im2 = _enhance(im2)
                    imw = _resize_for_profile(im2, p_web)
                    web_path = _platform_filename(haber_id, f"web_{idx_img}", p_web["fmt"])
                    _save(imw, web_path, p_web["fmt"])
                    web_paths.append(web_path)
                if web_paths:
                    ws.update_cell(row_idx, cols.L, ",".join(web_paths))

                # ---------- diğer platformlar: ilk iyi görselle ----------
                src_im = use_base_list[0]
                out_paths = {}

                # 2) Telegram
                p = PROFILES["telegram"]; im = _resize_for_profile(_enhance(_apply_logo_processing(src_im)), p)
                im = _draw_telegram_banner(im, title_tr)
                tg_path = _platform_filename(haber_id, "telegram", p["fmt"]); _save(im, tg_path, p["fmt"]); out_paths["M"]=tg_path

                # 3) X.com
                p = PROFILES["x"]; im = _resize_for_profile(_enhance(_apply_logo_processing(src_im)), p)
                x_path = _platform_filename(haber_id, "x", p["fmt"]); _save(im, x_path, p["fmt"]); out_paths["N"]=x_path

                # 4) Bluesky
                p = PROFILES["bluesky"]; im = _resize_for_profile(_enhance(_apply_logo_processing(src_im)), p)
                b_path = _platform_filename(haber_id, "bluesky", p["fmt"]); _save(im, b_path, p["fmt"]); out_paths["O"]=b_path

                # 5) LinkedIn
                p = PROFILES["linkedin"]; im = _resize_for_profile(_enhance(_apply_logo_processing(src_im)), p)
                ln_path = _platform_filename(haber_id, "linkedin", p["fmt"]); _save(im, ln_path, p["fmt"]); out_paths["P"]=ln_path

                # 6) Instagram görsel
                p = PROFILES["instagram"]; im = _resize_for_profile(_enhance(_apply_logo_processing(src_im)), p)
                ig_img_path = _platform_filename(haber_id, "instagram", p["fmt"]); _save(im, ig_img_path, p["fmt"]); out_paths["Q"]=ig_img_path

                # 7) IG video kapak
                p = PROFILES["ig_video"]; im = _resize_for_profile(_enhance(_apply_logo_processing(src_im)), p)
                igv_path = _platform_filename(haber_id, "ig_video", p["fmt"]); _save(im, igv_path, p["fmt"]); out_paths["R"]=igv_path

                # 8) TikTok kapak
                p = PROFILES["tiktok_video"]; im = _resize_for_profile(_enhance(_apply_logo_processing(src_im)), p)
                tk_path = _platform_filename(haber_id, "tiktok", p["fmt"]); _save(im, tk_path, p["fmt"]); out_paths["S"]=tk_path

                # 9) YouTube Shorts
                p = PROFILES["yt_shorts"]; im = _resize_for_profile(_enhance(_apply_logo_processing(src_im)), p)
                ys_path = _platform_filename(haber_id, "yt_shorts", p["fmt"]); _save(im, ys_path, p["fmt"]); out_paths["T"]=ys_path

                # 10) YouTube Uzun
                p = PROFILES["yt_long"]; im = _resize_for_profile(_enhance(_apply_logo_processing(src_im)), p)
                yl_path = _platform_filename(haber_id, "yt_long", p["fmt"]); _save(im, yl_path, p["fmt"]); out_paths["U"]=yl_path

                # Sheet update (platformlar)
                for key, col in [("M",cols.M),("N",cols.N),("O",cols.O),("P",cols.P),
                                 ("Q",cols.Q),("R",cols.R),("S",cols.S),("T",cols.T),("U",cols.U)]:
                    if key in out_paths:
                        ws.update_cell(row_idx, col, out_paths[key])

                # Durum
                ws.update_cell(row_idx, cols.AC, "Robot 1 Başarılı / Robot 2 Başarılı / Robot 3 Başarılı")
                ws.update_cell(row_idx, cols.AD, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

            except Exception as e:
                ws.update_cell(row_idx, cols.AC, "Robot 1 Başarılı / Robot 2 Başarılı / Robot 3 Hata")
                ws.update_cell(row_idx, cols.AD, f"{type(e).__name__}: {e}")
                traceback.print_exc()

        time.sleep(BATCH_SLEEP_MS/1000.0)

    print("✓ Robot 3 bitti (Vision {}).".format("ON" if _get_vision() else "OFF"))

if __name__ == "__main__":
    run()
