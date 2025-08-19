# robots/visual_styler.py
# -*- coding: utf-8 -*-
from dotenv import load_dotenv
load_dotenv()

import os, io, re, uuid, time, json, hashlib, datetime, unicodedata
from typing import List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter

import gspread
from google.cloud import storage
try:
    from google.cloud import vision
    VISION_AVAILABLE = True
except Exception:
    VISION_AVAILABLE = False

# Proje yardımcıları
from utils.auth import get_gspread_client
from utils.schema import resolve_columns
try:
    from utils.gemini import generate_text
except Exception:
    generate_text = None

# ================== ENV & SABİTLER ==================
NEWS_TAB              = os.environ.get("NEWS_TAB", "News")
GOOGLE_STORAGE_BUCKET = os.environ.get("GOOGLE_STORAGE_BUCKET", "")
REQUEST_TIMEOUT       = int(os.environ.get("REQUEST_TIMEOUT", "10"))
USER_AGENT            = os.environ.get("USER_AGENT", "GoNewsBot/1.0 (+https://example.com)")

CRAFTER_CONCURRENCY   = int(os.environ.get("CRAFTER_CONCURRENCY", "8"))
BATCH_SIZE            = int(os.environ.get("CRAFTER_BATCH", "40"))
BATCH_SLEEP_MS        = int(os.environ.get("CRAFTER_SLEEP_MS", "800"))

USE_VISION            = os.environ.get("USE_VISION", "1") in ("1","true","True")
MAX_WEB_IMAGES        = int(os.environ.get("MAX_WEB_IMAGES", "5"))

MIN_W, MIN_H          = 800, 450

# GCS: imzalı URL süresi (maks 7 gün)
SIGN_URL_TTL_HOURS    = min(max(int(os.environ.get("SIGN_URL_TTL_HOURS", "168")), 1), 168)
STORAGE_PUBLIC        = os.environ.get("STORAGE_PUBLIC", "0") in ("1","true","True")
GCS_BASE_PATH         = os.environ.get("GCS_BASE_PATH", "news")

# Boyutlar
SIZE_TG   = (1080, 1350)     # Telegram 4:5
SIZE_SOC  = (1200, 675)      # X/BS/LI 16:9

# Fontlar
FONT_BOLD_PATH  = "media/font/Montserrat-Bold.ttf"
FONT_BLACK_PATH = "media/font/Montserrat-Black.ttf"
FONT_REG_PATH   = "media/font/Montserrat-Regular.ttf"

LOGO_PATH       = os.environ.get("LOGO_PATH", "media/logo.png")
LOGO_MAX_W      = 156
LOGO_PAD        = 24

# 0.5 cm ≈ 19px
FRAME_PX_TG     = int(os.environ.get("FRAME_PX_TG", "20"))
FRAME_PX_SOC    = int(os.environ.get("FRAME_PX_SOC", "20"))

HIGHLIGHT_AI    = os.environ.get("HIGHLIGHT_AI", "1") in ("1","true","True")

HEADERS = {"User-Agent": USER_AGENT}
SESS = requests.Session(); SESS.headers.update(HEADERS)

# ✓ Kategori bandı köşe yarıçapı (px) — istersen .env ile PILL_CORNER_RADIUS ayarlayabilirsin
PILL_CORNER_RADIUS = int(os.environ.get("PILL_CORNER_RADIUS", "20"))

# ================== YARDIMCI GENELLER ==================
def _compose_status_block(current: Optional[str], robot_no: int, ok: bool) -> str:
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

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode("utf-8","ignore")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "news"

def _open_image(b: bytes) -> Optional[Image.Image]:
    try: return Image.open(io.BytesIO(b)).convert("RGB")
    except Exception: return None

def _fetch(url: str) -> Optional[bytes]:
    try:
        r=SESS.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code==200 and r.content: return r.content
    except Exception: pass
    return None

def _draw_white_frame(img: Image.Image, thickness_px: int):
    d=ImageDraw.Draw(img); w,h=img.size
    d.rectangle([0,0,w-1,h-1], outline=(255,255,255), width=thickness_px)

def _paste_logo(img: Image.Image):
    try:
        if not os.path.exists(LOGO_PATH): return
        logo=Image.open(LOGO_PATH).convert("RGBA")
        r=min(1.0, LOGO_MAX_W/max(1,logo.width))
        if r<1.0: logo=logo.resize((int(logo.width*r), int(logo.height*r)), Image.LANCZOS)
        img.paste(logo,(LOGO_PAD,LOGO_PAD),logo)
    except Exception: pass

# ========== VISION: yazı/logo kontrolü ==========
def _check_text_on_image(im: Image.Image) -> bool:
    if not (USE_VISION and VISION_AVAILABLE):
        return False
    try:
        client = vision.ImageAnnotatorClient()
        buf = io.BytesIO(); im.save(buf, format="JPEG", quality=92); buf.seek(0)
        vimg = vision.Image(content=buf.read())
        t = client.text_detection(image=vimg)
        logos = getattr(client.logo_detection(image=vimg), "logo_annotations", []) or []
        texts = getattr(t, "text_annotations", []) or []
        return len(texts) > 1 or len(logos) > 0
    except Exception:
        return False

def _blur_boxes(im: Image.Image, boxes, radius=14) -> Image.Image:
    if not boxes: return im
    base=im.copy()
    for (x1,y1,x2,y2) in boxes:
        x1=max(0,x1); y1=max(0,y1); x2=min(base.width,x2); y2=min(base.height,y2)
        if x2-x1<=0 or y2-y1<=0: continue
        region=base.crop((x1,y1,x2,y2)).filter(ImageFilter.GaussianBlur(radius))
        base.paste(region,(x1,y1))
    return base

def _vision_clean(im: Image.Image) -> Image.Image:
    if not (USE_VISION and VISION_AVAILABLE): return im
    try:
        client=vision.ImageAnnotatorClient()
        buf=io.BytesIO(); im.save(buf, format="JPEG", quality=92); buf.seek(0)
        vimg=vision.Image(content=buf.read())

        boxes=[]
        for l in getattr(client.logo_detection(image=vimg),"logo_annotations",[]) or []:
            for poly in getattr(l,"bounding_polys",[]):
                xs=[v.x for v in poly.vertices]; ys=[v.y for v in poly.vertices]
                boxes.append((min(xs),min(ys),max(xs),max(ys)))
        t=client.text_detection(image=vimg)
        for a in getattr(t,"text_annotations",[])[1:]:
            vs=a.bounding_poly.vertices; xs=[v.x for v in vs]; ys=[v.y for v in vs]
            boxes.append((min(xs),min(ys),max(xs),max(ys)))

        boxes=[b for b in boxes if (b[2]-b[0])*(b[3]-b[1])>=900]
        return _blur_boxes(im, boxes, radius=14)
    except Exception:
        return im

# ========== SAYFA KAZIMA + ADAY ==========
def _best_from_srcset(srcset: str) -> Optional[str]:
    best=None; best_w=-1
    for part in (srcset or "").split(","):
        seg=part.strip().split()
        if not seg: continue
        url=seg[0]; w=0
        if len(seg)>1 and seg[1].endswith("w"):
            try: w=int(seg[1][:-1])
            except: w=0
        if w>best_w: best=url; best_w=w
    return best

BAD_HINT = re.compile(r"(sprite|logo|placeholder|icon|ads|tracking)", re.I)

def _score_candidate(url, meta, title):
    score=0
    src = meta.get("src","")
    if src=="og": score+=100
    if src=="twitter": score+=95
    if src=="primary": score+=90
    if src=="article": score+=60
    w,h = meta.get("w",0), meta.get("h",0)
    if w and h:
        score += (w*h)/1_000_000
        ar = w/max(1,h)
        if ar<0.55 or ar>2.2: score -= 40
    kws=[k for k in re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşüİ0-9]+", title or "") if len(k)>=4][:3]
    u=url.lower()
    if any(k.lower() in u for k in kws): score+=25
    if BAD_HINT.search(url): score-=100
    return score

def _scrape_candidates(page_url: str, title: str) -> List[dict]:
    out=[]
    html=_fetch(page_url) if page_url else None
    if not html: return out
    soup=BeautifulSoup(html,"html.parser")

    def add(u, src, w=0, h=0):
        if not u: return
        u=requests.compat.urljoin(page_url,u)
        out.append({"url":u,"src":src,"w":w,"h":h})

    for prop, src in [("og:image","og"), ("twitter:image","twitter"), ("twitter:image:src","twitter")]:
        t=soup.find("meta",attrs={"property":prop}) or soup.find("meta",attrs={"name":prop})
        if t and t.get("content"): add(t["content"], src)

    for sc in soup.find_all("script",attrs={"type":"application/ld+json"}):
        try: data=json.loads(sc.string or "{}")
        except Exception: continue
        items=data if isinstance(data,list) else [data]
        for it in items:
            if not isinstance(it,dict): continue
            t=it.get("@type")
            if t in ("NewsArticle","Article","ImageObject"):
                im=it.get("primaryImageOfPage") or it.get("image") or it.get("thumbnailUrl")
                if isinstance(im,str): add(im,"primary")
                elif isinstance(im,list) and im: add(im[0],"primary")
            if t=="ImageGallery":
                for e in it.get("itemListElement") or []:
                    if isinstance(e,dict) and str(e.get("position"))=="1":
                        im=e.get("image") or e.get("url")
                        if im: add(im,"primary")

    for fig in soup.select("article img, .article img, .content img, figure img"):
        u=fig.get("src") or fig.get("data-src") or fig.get("data-original") or fig.get("data-lazy-src")
        if not u: continue
        try: w=int(fig.get("width") or 0); h=int(fig.get("height") or 0)
        except: w=h=0
        add(u,"article",w,h)

    for s in soup.find_all("source"):
        ss=s.get("srcset") or s.get("data-srcset")
        if ss:
            best=_best_from_srcset(ss)
            if best: add(best,"article")

    return out

def pick_images(row, cols) -> Tuple[List[Image.Image], List[str]]:
    page_url = row[cols.G-1].strip() if len(row) >= cols.G else ""
    title    = row[cols.H-1] if len(row)>=cols.H else ""
    cand = []
    notes=[]

    if len(row)>=cols.K and row[cols.K-1].strip():
        cand.append({"url": row[cols.K-1].strip(), "src":"manual"})

    cand += _scrape_candidates(page_url, title)

    seen_url=set(); seen_hash=set(); pool=[]
    for c in cand:
        url=c["url"]
        if url in seen_url: continue
        seen_url.add(url)
        b=_fetch(url)
        if not b: continue
        h=hashlib.sha1(b).hexdigest()
        if h in seen_hash: continue
        im=_open_image(b)
        if not im: continue
        w,hpx=im.size
        if w<MIN_W or hpx<MIN_H: continue
        if _check_text_on_image(im):
            notes.append(f"Görsel üzerinde yazı tespit edildi: {url}")
        seen_hash.add(h)
        c["w"],c["h"],c["im"]=w,hpx,im
        c["score"]=_score_candidate(url,c,title)
        pool.append(c)

    pool.sort(key=lambda x:x["score"], reverse=True)
    return [c["im"] for c in pool[:MAX_WEB_IMAGES]], notes

# ========== KIRPMA & ODAK ==========
def _detect_face_box(im: Image.Image) -> Optional[Tuple[int,int,int,int]]:
    if not (USE_VISION and VISION_AVAILABLE): return None
    try:
        client=vision.ImageAnnotatorClient()
        buf=io.BytesIO(); im.save(buf, format="JPEG", quality=90); buf.seek(0)
        vimg=vision.Image(content=buf.read())
        faces=(client.face_detection(image=vimg).face_annotations or [])
        if not faces: return None
        def bbox(f):
            vs=f.bounding_poly.vertices
            xs=[v.x for v in vs]; ys=[v.y for v in vs]
            return (min(xs),min(ys),max(xs),max(ys))
        boxes=[bbox(f) for f in faces]
        boxes.sort(key=lambda b:(b[2]-b[0])*(b[3]-b[1]), reverse=True)
        return boxes[0]
    except Exception:
        return None

def _cover_focus(im: Image.Image, target_wh: Tuple[int,int], face_box=None, bias_up=0.0):
    tw, th = target_wh
    w, h = im.size
    scale = max(tw / w, th / h)

    nw, nh = int(w * scale), int(h * scale)
    im2 = im.resize((nw, nh), Image.LANCZOS)

    # odak merkezi
    cx, cy = nw // 2, nh // 2
    if face_box:
        x1, y1, x2, y2 = face_box
        cx = int((x1 + x2) / 2 * scale)
        cy = int((y1 + y2) / 2 * scale)

    cy = int(cy - bias_up * th)

    left = max(0, min(nw - tw, cx - tw // 2))
    top  = max(0, min(nh - th, cy - th // 2))

    # ✅ Hedef boyut: (tw, th)
    return im2.crop((left, top, left + tw, top + th))


# ========== METİN/RENK ==========
def _font(path, size):
    # Hata gizleme kaldırıldı. Font bulunamazsa program hata verip duracak.
    # Bu, font yolunun yanlış olduğunu anlamanızı sağlar.
    # Lütfen "media/font/" klasörlerinin ve .ttf dosyalarının doğru yerde olduğundan emin olun.
    return ImageFont.truetype(path, size)

def tr_upper(s: str) -> str:
    s = s or ""
    s = s.replace('ı', 'I').replace('i', 'İ')
    return s.upper()

def _fix_category_text(raw: str) -> str:
    # Fazladan boşluk yok; padding gerçek değerlerden gelsin
    s = (raw or "").replace("_", " ").replace("-", " ").strip()

    # ASCII’ye indir ve map’le (aksan düzeltme)
    repl = str.maketrans({
        "ç":"c","Ç":"c","ğ":"g","Ğ":"g","ı":"i","İ":"i",
        "ö":"o","Ö":"o","ş":"s","Ş":"s","ü":"u","Ü":"u",
    })
    key = re.sub(r"\s+", " ", s.translate(repl)).strip().upper()

    CANON = {
        "TURKIYE GUNDEMI": "TÜRKİYE GÜNDEMİ",
        "GUNDEM":          "GÜNDEM",
        "EKONOMI":         "EKONOMİ",
        "TEKNOLOJI":       "TEKNOLOJİ",
        "KULTUR SANAT":    "KÜLTÜR & SANAT",
        "KULTUR & SANAT":  "KÜLTÜR & SANAT",
        "DUNYA":           "DÜNYA",
        "SAGLIK":          "SAĞLIK",
        "SPOR":            "SPOR",
        "BILIM":           "BİLİM",
    }
    if key in CANON:
        s = CANON[key]

    # TR upper
    return s.replace("i", "İ").replace("ı", "I").upper()

# ↓↓↓ BUNU EKLE (heuristic’ten ÖNCE olsun)
STOPWORDS_TR = {
    "ve","ile","de","da","bir","the","of","in","on","and","to",
    "ya","ama","mi","mı","mu","mü"
}


def _choose_highlights_heuristic(text: str):
    words=[w.strip(",.!?:;()\"'“”") for w in (text or "").split()]
    scored=[]
    for w in words:
        score=(any(c.isdigit() for c in w))*3 + min(len(w),12)/10 + (w[:1].isupper())*0.6
        if w.lower() in STOPWORDS_TR: score-=1.5
        scored.append((score,w))
    scored.sort(reverse=True)
    yellow=[]; bold=[]
    for _,w in scored:
        if len(yellow)<2 and w not in yellow: yellow.append(w); continue
        if len(bold)<3 and (w not in yellow) and (w not in bold): bold.append(w)
        if len(yellow)>=2 and len(bold)>=3: break
    return yellow,bold

def _choose_highlights(title: str):
    if not HIGHLIGHT_AI or generate_text is None:
        return _choose_highlights_heuristic(title)
    try:
        prompt=('Başlıktaki en önemli kelimeleri seç. Çıktı JSON: {"yellow":[],"bold":[]} \n'+(title or ""))
        js=json.loads(generate_text(prompt).strip())
        y=[str(x) for x in (js.get("yellow") or [])][:2]
        b=[str(x) for x in (js.get("bold") or [])][:3]
        if y or b: return y,b
    except Exception:
        pass
    return _choose_highlights_heuristic(title)

def _choose_summary_highlights(summary: str):
    if not HIGHLIGHT_AI or generate_text is None:
        words=[w.strip(",.!?:;()\"'“”") for w in (summary or "").split()]
        scored=[]
        for w in words:
            score=(any(c.isdigit() for c in w))*2 + min(len(w),12)/10 + (w[:1].isupper())*0.5
            if w.lower() in STOPWORDS_TR: score -= 2
            scored.append((score,w))
        scored.sort(reverse=True)
        bold=[]
        for _,w in scored:
            if len(bold)>=5: break
            if w not in bold: bold.append(w)
        return bold
    try:
        js=json.loads(generate_text('Özet önemli 5 kelime: {"bold":[]} \n'+(summary or "")).strip())
        b=[str(x) for x in (js.get("bold") or [])][:5]
        if b: return b
    except Exception:
        pass
    return []

def _wrap_by_width(text: str, font, max_w: int) -> List[str]:
    w=[]; cur=""
    d=ImageDraw.Draw(Image.new("RGB",(10,10)))
    for t in (text or "").split():
        cand=(cur+" "+t).strip()
        if d.textbbox((0,0), cand, font=font)[2] <= max_w or not cur:
            cur=cand
        else:
            w.append(cur); cur=t
    if cur: w.append(cur)
    return w

# ========== FADELER ==========
def _bottom_fade_to_black(base: Image.Image, start_y: int, strength: int = 255, fade_h: Optional[int] = None):
    w,h=base.size
    start_y=max(0,min(h-1,start_y))
    fh = (h-start_y) if fade_h is None else fade_h
    if fh<=0: return
    
    grad=Image.new("L",(w,fh),0); d=ImageDraw.Draw(grad)
    for y in range(fh):
        t=y/max(1,fh-1)
        alpha=int(strength*(t**1.2))
        d.line([(0,y),(w,y)], fill=alpha)
    overlay=Image.new("RGB",(w,fh),(0,0,0))
    base.paste(overlay,(0,start_y),grad)

# ========== PILL (NİHAİ KÖŞE DÜZELTMESİ) ==========
def _make_mask_selective(w: int, h: int, radius: int, rounded=("tl","br")) -> Image.Image:
    """
    Önce tüm köşeleri yuvarlak çizer; ardından rounded içinde OLMAYAN köşeleri
    kare ile doldurup keskinleştirir. Sağ-alt köşe pürüzlerini engeller.
    """
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    # Taban: tüm köşeler yuvarlak
    d.rounded_rectangle((0, 0, w-1, h-1), radius=radius, fill=255)
    rset = set(s.lower() for s in rounded)
    if "tl" not in rset: d.rectangle((0, 0, radius, radius), fill=255)
    if "tr" not in rset: d.rectangle((w - radius, 0, w, radius), fill=255)
    if "bl" not in rset: d.rectangle((0, h - radius, radius, h), fill=255)
    if "br" not in rset: d.rectangle((w - radius, h - radius, w, h), fill=255)
    return mask

def _make_pill(text: str, font, pad_x: int, pad_y: int) -> Image.Image:
    """
    Kategori bandı: sol-üst ve sağ-alt yuvarlak; diğer iki köşe keskin.
    Padding: pad_x=10, pad_y=13 (çağıran taraflarda bu değerlerle kullanılıyor).
    """
    txt=text
    tmp=Image.new("RGBA",(10,10),(0,0,0,0))
    tbb=ImageDraw.Draw(tmp).textbbox((0,0), txt, font=font)
    pill_w=(tbb[2]-tbb[0]) + 2*pad_x
    pill_h=(tbb[3]-tbb[1]) + 2*pad_y

    # Köşe yarıçapı: istenen küçük yuvarlak
    radius = min(PILL_CORNER_RADIUS, max(1, min(pill_w, pill_h)//2 - 1))

    # Maske: yalnızca ("tl","br") yuvarlak
    mask = _make_mask_selective(pill_w, pill_h, radius, rounded=("tl","br"))

    # Gradyan
    grad=Image.new("RGBA",(pill_w,pill_h))
    gd=ImageDraw.Draw(grad)
    for x in range(pill_w):
        t = x/max(1,pill_w-1)
        # soldan sağa #FFCC00 → #F7B500
        r = int(255 + (247-255)*t)
        g = int(204 + (181-204)*t)
        b = int(  0 + (  0-  0)*t)
        gd.line([(x,0),(x,pill_h)], fill=(r,g,b,255))
        
    pill=Image.new("RGBA",(pill_w,pill_h),(0,0,0,0))
    pill.paste(grad,(0,0),mask)

    # Metin
    pd=ImageDraw.Draw(pill)
    text_x = pad_x
    text_y = (pill_h - (tbb[3] - tbb[1])) / 2 - tbb[1]
    pd.text((text_x, text_y), txt, font=font, fill=(0,0,0))
    return pill

# ========== SOSYAL (X/Bluesky/LinkedIn) ==========
def _overlay_social(im: Image.Image, title: str, category: str) -> Image.Image:
    face=_detect_face_box(im)
    base=_cover_focus(im, SIZE_SOC, face, bias_up=0.05)
    W,H=base.size

    max_w=int(W*0.88)
    title_lines=None; title_fs=None; title_h=0
    for fs in (88,82,76,70,64,58,52,46):
        f=_font(FONT_BLACK_PATH, fs)
        lines=_wrap_by_width(title or "", f, max_w)
        if len(lines)<=2:
            title_lines=lines; title_fs=fs
            title_h=len(lines)*int(fs*1.2)
            break
    if title_lines is None:
        title_fs=46
        f=_font(FONT_BLACK_PATH, title_fs)
        title_lines=_wrap_by_width(title or "", f, max_w)[:2]
        title_h=len(title_lines)*int(title_fs*1.2)

    f_cat=_font(FONT_BLACK_PATH, 46)
    pill=_make_pill(_fix_category_text(category or "GÜNDEM"),
                    f_cat, pad_x=10, pad_y=13)  # ← padding güncellendi

    pad_bottom = 50
    gap_pill_title = 20
    
    title_y = H - pad_bottom - title_h
    pill_y  = title_y - gap_pill_title - pill.size[1]
    pill_x  = (W - pill.size[0])//2
    
    # Transparanlık güçlendirildi: Başlangıç noktası yukarı çekildi
    _bottom_fade_to_black(base, pill_y - 60, strength=450)
    
    base.paste(pill,(pill_x,pill_y), pill)

    yellow,bold=_choose_highlights(title or "")
    f_reg =_font(FONT_REG_PATH, title_fs)
    f_black=_font(FONT_BLACK_PATH, title_fs)

    overlay=Image.new("RGBA",(W,title_h),(0,0,0,0))
    dd=ImageDraw.Draw(overlay); y=0
    for ln in title_lines:
        segs=[]; toks=ln.split()
        for i,tok_raw in enumerate(toks):
            tok = tok_raw.strip(",.!?:;'\"“”")
            ff, col = f_reg, (255,255,255,255)
            
            if any(yw.strip(",.!?:;'\"“”") == tok for yw in yellow):
                ff, col = f_black, (249,200,38,255)
            elif any(bw.strip(",.!?:;'\"“”") == tok for bw in bold):
                ff, col = f_black, (255,255,255,255)

            txt = (" " if i>0 else "") + tok_raw
            word_width = dd.textbbox((0,0), txt, font=ff)[2]
            segs.append((txt, ff, col, word_width))
        
        lw=sum(s[3] for s in segs)
        x=W//2 - lw//2
        
        for t,ff,cc,wpx in segs:
            dd.text((x,y),t,font=ff,fill=cc)
            x += wpx
            
        y += int(title_fs*1.2)

    base.paste(overlay,(0,title_y), overlay)
    _paste_logo(base)
    _draw_white_frame(base, FRAME_PX_SOC)
    return base

# ========== TELEGRAM ==========
def _overlay_tg(im: Image.Image, title: str, summary: str, category: str) -> Image.Image:
    W,H=SIZE_TG
    usable_h = H - 2*FRAME_PX_TG
    half = usable_h // 2
    img_paste_y = FRAME_PX_TG
    black_area_start_y = img_paste_y + half
    
    face=_detect_face_box(im)
    
    base=Image.new("RGB",(W,H),(0,0,0))
    
    top_img=_cover_focus(im,(W,half), face, bias_up=0.08)
    
    base.paste(top_img,(FRAME_PX_TG, img_paste_y))
    
    _bottom_fade_to_black(base, start_y=black_area_start_y-120, strength=255, fade_h=120)

    f_cat=_font(FONT_BLACK_PATH, 44)
    pill=_make_pill(_fix_category_text(category or "GÜNDEM"),
                    f_cat, pad_x=10, pad_y=13)  # ← padding güncellendi
    pill_x=(W - pill.size[0])//2
    pill_y = black_area_start_y - pill.size[1]//2
    base.paste(pill,(pill_x,pill_y), pill)
    
    gap_pill_title = 40
    title_area_top = pill_y + pill.size[1] + gap_pill_title
    
    summary_area_top = int(title_area_top + (H - title_area_top) * 0.45)
    title_area_bottom = summary_area_top - 20
    
    allowed_h = max(60, title_area_bottom - title_area_top)
    max_w = int(W*0.90)

    yellow,bold=_choose_highlights(title or "")
    overlay=None; used_h=None;
    for fs in (86,80,74,68,62,56,50,46):
        f_reg = _font(FONT_REG_PATH, fs)
        f_black = _font(FONT_BLACK_PATH, fs)
        lines=_wrap_by_width((title or "").strip(), f_black, max_w)
        if len(lines)>3: continue
        
        line_h=int(fs*1.15)
        current_used_h = line_h*len(lines)
        if current_used_h > allowed_h: continue

        used_h = current_used_h
        ov=Image.new("RGBA",(W,used_h),(0,0,0,0))
        dd=ImageDraw.Draw(ov); y_cursor=0
        for ln in lines:
            segs=[]; toks=ln.split()
            for i,tok_raw in enumerate(toks):
                tok = tok_raw.strip(",.!?:;'\"“”")
                ff, col = f_reg, (255,255,255,255)

                if any(yw.strip(",.!?:;'\"“”") == tok for yw in yellow):
                    ff, col = f_black, (249,200,38,255)
                elif any(bw.strip(",.!?:;'\"“”") == tok for bw in bold):
                    ff, col = f_black, (255,255,255,255)

                txt = (" " if i>0 else "")+tok_raw
                word_width = dd.textbbox((0,0),txt,font=ff)[2]
                segs.append((txt,ff,col,word_width))

            lw=sum(s[3] for s in segs); x=W//2 - lw//2
            for t,ff,cc,wpx in segs:
                dd.text((x,y_cursor),t,font=ff,fill=cc); x+=wpx
            y_cursor+=line_h
        overlay=ov; break
        
    if overlay:
        paste_y = title_area_top + (allowed_h - used_h) // 2
        base.paste(overlay,(0, paste_y), overlay)

    f_sum   = _font(FONT_REG_PATH, 33)
    f_sbold = _font(FONT_BOLD_PATH, 33)
    bold_sum= _choose_summary_highlights(summary or "")
    max_w_s = int(W*0.92)
    lh      = int(33*1.35)

    meas = ImageDraw.Draw(Image.new("RGB",(10,10)))
    lines=[]; cur=""
    for w_ in (summary or "").split():
        cand=(cur+" "+w_).strip()
        if meas.textbbox((0,0), cand, font=f_sum)[2] <= max_w_s or not cur:
            cur=cand
        else:
            lines.append(cur); cur=w_
    if cur: lines.append(cur)
    trunc = len(lines) > 5
    lines = lines[:5]

    dd = ImageDraw.Draw(base)
    summary_total_h = len(lines) * lh
    summary_allowed_h = (H - FRAME_PX_TG) - summary_area_top
    y_start = summary_area_top + (summary_allowed_h - summary_total_h) // 2
    y = y_start
    
    for i, ln in enumerate(lines):
        if i == len(lines)-1 and trunc:
            ell = " …"
            while meas.textbbox((0,0), ln+ell, font=f_sum)[2] > max_w_s and len(ln) > 3:
                ln = ln[:-2]
            ln = ln + ell

        segs=[]; toks=ln.split()
        for j,tok_raw in enumerate(toks):
            tok = tok_raw.strip(",.!?:;")
            ff = f_sbold if any(b.strip(",.!?:;") == tok for b in bold_sum) else f_sum
            txt=(" " if j>0 else "")+tok_raw
            segs.append((txt,ff,meas.textbbox((0,0),txt,font=ff)[2]))
            
        lw=sum(s[2] for s in segs); x=W//2 - lw//2
        for t,ff,wpx in segs:
            dd.text((x,y), t, font=ff, fill=(230,230,230))
            x+=wpx
        y += lh

    _paste_logo(base)
    _draw_white_frame(base, FRAME_PX_TG)
    return base

# ========== GCS ==========
def _img_bytes(im: Image.Image, q=92) -> bytes:
    buf=io.BytesIO(); im.save(buf, format="JPEG", quality=q); return buf.getvalue()

def _upload_gcs(b: bytes, object_path: str) -> str:
    if not GOOGLE_STORAGE_BUCKET:
        raise RuntimeError("GOOGLE_STORAGE_BUCKET boş.")
    client=storage.Client(); bucket=client.bucket(GOOGLE_STORAGE_BUCKET)
    blob=bucket.blob(object_path)
    blob.cache_control="public, max-age=31536000"
    blob.upload_from_string(b, content_type="image/jpeg")
    if STORAGE_PUBLIC:
        try:
            blob.make_public()
            return f"https://storage.googleapis.com/{GOOGLE_STORAGE_BUCKET}/{blob.name}"
        except Exception:
            pass
    return blob.generate_signed_url(
        expiration=datetime.timedelta(hours=SIGN_URL_TTL_HOURS),
        method="GET", version="v4"
    )

LINK_STYLE = os.environ.get("LINK_STYLE", "raw").lower()
# raw  → düz URL; formula → =HYPERLINK("url","etiket") (görsel ve tıklanabilir)

def _mk_link_cell(urls, label_prefix="web"):
    if not urls:
        return ""
    if LINK_STYLE == "formula":
        # Etiketli ve kısa görünen linkler
        items = [f'=HYPERLINK("{u}","{label_prefix}_{i:03d}")' for i, u in enumerate(urls, 1)]
    else:
        # Düz URL
        items = urls
    # Virgül değil, satır sonu kullan → otomatik linkleme hatası yaşamazsın
    return "\n".join(items)

# ========== SHEET YAZMA ==========
def _flush(ws, triples):
    if not triples: return
    payload_raw, payload_user = [], []
    for (r, c, v) in triples:
        a1 = gspread.utils.rowcol_to_a1(r, c)
        if isinstance(v, str) and v.startswith("="):
            payload_user.append({"range": a1, "values": [[v]]})
        else:
            payload_raw.append({"range": a1, "values": [[v]]})

    for i in range(0, len(payload_raw), 400):
        ws.batch_update(payload_raw[i:i+400], value_input_option="RAW")
        time.sleep(BATCH_SLEEP_MS/1000.0)
    for i in range(0, len(payload_user), 400):
        ws.batch_update(payload_user[i:i+400], value_input_option="USER_ENTERED")
        time.sleep(BATCH_SLEEP_MS/1000.0)


# ========== ANA AKIŞ ==========
def run():
    sheet_id=os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id: raise RuntimeError("GOOGLE_SHEET_ID boş.")
    gc=get_gspread_client()
    ws=gc.open_by_key(sheet_id).worksheet(NEWS_TAB)
    cols=resolve_columns(ws)

    values=ws.get_all_values()
    if not values:
        print("Sheet boş."); return
    data=values[1:]; base_idx=2

    def r12ok(ac:str)->bool:
        s=(ac or "").lower()
        return ("robot 1 ✅" in s) and ("robot 2 ✅" in s)

    targets=[]
    for i,row in enumerate(data, start=base_idx):
        ac=row[cols.AC-1] if len(row)>=cols.AC else ""
        if not r12ok(ac): continue
        if "robot 3 ✅" in (ac or "").lower(): continue
        targets.append((i,row))
    if not targets:
        print("VisualStyler: işlenecek satır yok."); return
    print(f"VisualStyler: hedef {len(targets)} satır.")

    updates_lock=threading.Lock()
    updates=[]; notes=[]
    def stage(r,c,v):
        with updates_lock: updates.append((r,c,v))
    def note(r,msg):
        with updates_lock: notes.append((r, cols.AD, msg))

    def process_row(ridx:int, row: List[str]):
        try:
            title   = row[cols.H-1] if len(row)>=cols.H else ""
            summary = row[cols.I-1] if len(row)>=cols.I else ""
            cat     = row[cols.C-1] if len(row)>=cols.C else "GENEL"
            row_id  = row[cols.A-1] if len(row)>=cols.A and row[cols.A-1].strip() else uuid.uuid4().hex
            today   = datetime.datetime.now(datetime.timezone.utc).strftime("%Y/%m/%d")
            root    = f"{GCS_BASE_PATH}/{today}/{row_id}"

            imgs, img_notes = pick_images(row, cols)
            if not imgs:
                prev=row[cols.AC-1] if len(row)>=cols.AC else ""
                stage(ridx, cols.AC, _compose_status_block(prev, 3, False))
                note(ridx, "Robot 3: uygun ana görsel bulunamadı")
                return

            web_urls=[]
            for i,im in enumerate(imgs, start=1):
                clean = im if _check_text_on_image(im) else _vision_clean(im)
                url=_upload_gcs(_img_bytes(clean, q=92), f"{root}/web/web_{i:03d}.jpg")
                web_urls.append(url)
            main_web=web_urls[0]

            tg=_overlay_tg(imgs[0], title, summary, cat)
            tg_url=_upload_gcs(_img_bytes(tg, q=92), f"{root}/telegram/tg_1080x1350.jpg")

            soc=_overlay_social(imgs[0], title, cat)
            soc_url=_upload_gcs(_img_bytes(soc, q=92), f"{root}/social/social_1200x675.jpg")

            stage(ridx, cols.L, _mk_link_cell(web_urls, "web"))
            stage(ridx, cols.U, _mk_link_cell(web_urls, "web"))
            stage(ridx, cols.M, tg_url)
            stage(ridx, cols.Q, tg_url)
            stage(ridx, cols.N, soc_url)
            stage(ridx, cols.O, soc_url)
            stage(ridx, cols.P, soc_url)
            stage(ridx, cols.R, main_web)
            stage(ridx, cols.S, main_web)
            stage(ridx, cols.T, main_web)

            if img_notes:
                note(ridx, "Robot 3 not: " + " | ".join(img_notes))

            prev=row[cols.AC-1] if len(row)>=cols.AC else ""
            stage(ridx, cols.AC, _compose_status_block(prev, 3, True))

        except Exception as e:
            import traceback
            print(traceback.format_exc())
            prev=row[cols.AC-1] if len(row)>=cols.AC else ""
            stage(ridx, cols.AC, _compose_status_block(prev, 3, False))
            note(ridx, f"Robot 3 hata: {str(e)[:200]}")

    with ThreadPoolExecutor(max_workers=CRAFTER_CONCURRENCY) as ex:
        futs=[ex.submit(process_row, r, row) for (r,row) in targets]
        for _ in as_completed(futs): pass

    _flush(ws, updates)
    _flush(ws, notes)
    print(f"VisualStyler bitti: {len(targets)} satır işlendi.")

if __name__=="__main__":
    run()
