# robots/news_harvester.py
# -*- coding: utf-8 -*-

from dotenv import load_dotenv
load_dotenv()

import os
import re
import uuid
import time
import datetime
import feedparser
import requests
from bs4 import BeautifulSoup
from time import mktime
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin

from utils.auth import get_gspread_client
from utils.schema import resolve_columns
from utils.feeds import FEEDS

# ================== AYARLAR (ENV) ==================
NEWS_TAB          = os.environ.get("NEWS_TAB", "News")
LOOKBACK_HOURS    = int(os.environ.get("LOOKBACK_HOURS", "12"))   # son 12 saat
MAX_PER_FEED      = int(os.environ.get("MAX_PER_FEED", "25"))     # feed başına max item
REQUEST_TIMEOUT   = int(os.environ.get("REQUEST_TIMEOUT", "12"))
REQUIRE_IMAGE     = os.environ.get("REQUIRE_IMAGE", "1") in ("1", "true", "True")
APPEND_BATCH_SIZE = int(os.environ.get("APPEND_BATCH_SIZE", "40"))
BATCH_SLEEP_MS    = int(os.environ.get("BATCH_SLEEP_MS", "1200"))
USER_AGENT        = os.environ.get("USER_AGENT", "GoNewsBot/1.0 (+https://example.com)")

HEADERS = {"User-Agent": USER_AGENT}


# ================== YARDIMCI FONKSİYONLAR ==================
def _http_get(url, timeout=REQUEST_TIMEOUT):
    try:
        r = requests.get(url, timeout=timeout, headers=HEADERS)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None


def _best_from_srcset(srcset):
    best = None
    best_w = -1
    for part in (srcset or "").split(","):
        seg = part.strip().split()
        if not seg:
            continue
        url = seg[0]
        w = 0
        if len(seg) > 1 and seg[1].endswith("w"):
            try:
                w = int(seg[1][:-1])
            except Exception:
                w = 0
        if w > best_w:
            best = url
            best_w = w
    return best


def _normalize_lang(code):
    s = (code or "").strip()
    if not s:
        return ""
    s = s.replace("_", "-")
    return s.split("-")[0].lower()  # en-US -> en


def _extract_lang_from_html(soup):
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        return _normalize_lang(html_tag["lang"])

    tag = soup.find("meta", attrs={"property": "og:locale"}) or soup.find("meta", attrs={"name": "og:locale"})
    if tag and tag.get("content"):
        return _normalize_lang(tag["content"])

    tag = soup.find("meta", attrs={"name": "language"})
    if tag and tag.get("content"):
        return _normalize_lang(tag["content"])

    return ""


def _extract_image_from_html(page_url, html):
    soup = BeautifulSoup(html, "html.parser")

    # 1) og:image / twitter:image
    for prop in ("og:image", "twitter:image", "twitter:image:src"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            return urljoin(page_url, tag["content"])

    # 2) link rel="image_src"
    link_tag = soup.find("link", attrs={"rel": "image_src"})
    if link_tag and link_tag.get("href"):
        return urljoin(page_url, link_tag["href"])

    # 3) <img srcset> en büyük
    best = None
    best_w = -1
    for im in soup.find_all("img"):
        if im.get("srcset"):
            cand = _best_from_srcset(im["srcset"])
            if cand:
                cand_w = 9999  # srcset'te en büyük seçildi varsay
                if cand_w > best_w:
                    best = cand
                    best_w = cand_w
    if best:
        return urljoin(page_url, best)

    # 4) <img src> (fallback)
    for im in soup.find_all("img"):
        if im.get("src"):
            return urljoin(page_url, im["src"])

    return None


def _canonicalize(url):
    """UTM/fbclid/gclid vb. izleme parametrelerini temizleyip, path'i normalize et."""
    try:
        u = urlparse(url)
        q = [
            (k, v)
            for k, v in parse_qsl(u.query, keep_blank_values=True)
            if not k.lower().startswith(("utm_", "fbclid", "gclid"))
        ]
        path = u.path.rstrip("/") or "/"
        return urlunparse((u.scheme, u.netloc, path, "", urlencode(q, doseq=True), ""))
    except Exception:
        return url


def _parse_entry_basic(e):
    """Feed entry'den başlık/link/lang/img ve published_ts döndür."""
    title = (e.get("title") or "").strip()
    link = (e.get("link") or "").strip()
    lang = (e.get("language") or e.get("dc_language") or "").strip()

    # published -> updated fallback
    published_ts = None
    if getattr(e, "published_parsed", None):
        try:
            published_ts = int(mktime(e.published_parsed))
        except Exception:
            published_ts = None
    if not published_ts and getattr(e, "updated_parsed", None):
        try:
            published_ts = int(mktime(e.updated_parsed))
        except Exception:
            published_ts = None

    img = ""
    media = e.get("media_content") or e.get("media_thumbnail")
    if media and isinstance(media, list) and media and isinstance(media[0], dict):
        img = media[0].get("url") or ""
    if not img:
        summary = (e.get("summary") or "")
        m = re.search(r'<img[^>]+src="([^"]+)"', summary or "")
        if m:
            img = m.group(1)

    return title, link, lang, img, published_ts


# ================== STATÜ YARDIMCILARI ==================
def status_text(robot_no, ok):
    return f"Robot {robot_no} {'✅' if ok else '❌'}"


def append_status_to_cell(ws, row_idx, col_idx, text):
    """
    Aynı hücreye alt satıra ekler. Boşsa direkt yazar.
    ws: gspread Worksheet
    row_idx: 1-based
    col_idx: 1-based
    """
    cell = ws.cell(row_idx, col_idx)
    if cell.value and cell.value.strip():
        new_val = f"{cell.value}\n{text}"
    else:
        new_val = text
    ws.update_cell(row_idx, col_idx, new_val)


# ================== ANA ÇEKİM ==================
def run():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID boş.")

    gc = get_gspread_client()
    ws = gc.open_by_key(sheet_id).worksheet(NEWS_TAB)
    cols = resolve_columns(ws)

    # Performans: sadece link sütunu (G) çek
    try:
        existing_links = set(x.strip() for x in ws.col_values(cols.G)[2:] if x.strip())
    except Exception:
        # fallback: tüm tabloyu al
        values = ws.get_all_values()
        existing_links = set()
        for i, row in enumerate(values, start=1):
            if i == 1:
                continue
            if len(row) >= cols.G and row[cols.G - 1]:
                existing_links.add(row[cols.G - 1].strip())

    # kanonikleştir
    existing_links = set(_canonicalize(u) for u in existing_links)

    # tablo uzunluğu (bitişte AD yazımı için)
    used_rows_before = len(ws.get_all_values())

    now_ts = int(time.time())
    lookback_cut = now_ts - LOOKBACK_HOURS * 3600
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not FEEDS or sum(len(v) for v in FEEDS.values()) == 0:
        print("FEEDS boş: utils/feeds.py dosyasını doldur.")
        return

    def append_rows(rows):
        """Batch append + kısa uyku (quota dostu)."""
        if not rows:
            return
        ws.append_rows(rows, value_input_option="RAW")
        time.sleep(BATCH_SLEEP_MS / 1000.0)

    to_add_rows = []
    row_len = max(getattr(cols, "AC", 29), getattr(cols, "K", 11), 11)  # güvenli üst limit

    try:
        # === Kaynakları tara ===
        for category, sources in FEEDS.items():
            for source_name, feed_url in sources:
                try:
                    feed = feedparser.parse(feed_url)
                    entries = list(getattr(feed, "entries", []))[:MAX_PER_FEED]

                    parsed = skipped_old = skipped_dup = skipped_noimg = added = 0

                    for e in entries:
                        title, link, lang, img, published_ts = _parse_entry_basic(e)
                        if not title or not link:
                            continue
                        parsed += 1

                        # kanonik link
                        link = _canonicalize(link)

                        # Lookback
                        if published_ts and published_ts < lookback_cut:
                            skipped_old += 1
                            continue

                        # Dedupe
                        if link in existing_links:
                            skipped_dup += 1
                            continue

                        # Gerekirse sayfadan dil/görsel çıkar
                        page_lang = ""
                        page_img = None
                        need_lang = not lang
                        need_img = (not img)

                        if need_lang or need_img:
                            html = _http_get(link)
                            if html:
                                soup = BeautifulSoup(html, "html.parser")
                                if need_lang:
                                    page_lang = _extract_lang_from_html(soup)
                                if need_img:
                                    page_img = _extract_image_from_html(link, html)

                        # Dil & görsel
                        lang_norm = _normalize_lang(lang) or page_lang or ""
                        image_url = img or page_img or ""

                        # Görsel zorunlu ise ve yoksa atla
                        if REQUIRE_IMAGE and not image_url:
                            skipped_noimg += 1
                            continue

                        # Satır hazırla
                        r = [""] * row_len
                        r[cols.A  - 1] = str(uuid.uuid4())
                        r[cols.B  - 1] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        r[cols.C  - 1] = category
                        r[cols.D  - 1] = source_name
                        r[cols.E  - 1] = lang_norm
                        r[cols.F  - 1] = title
                        r[cols.G  - 1] = link
                        r[cols.K  - 1] = image_url
                        r[cols.AC - 1] = status_text(1, True)  # Durum: Robot 1 ✅

                        to_add_rows.append(r)
                        existing_links.add(link)
                        added += 1

                    print(f"[{category} / {source_name}] parsed={parsed}, added={added}, dup={skipped_dup}, old={skipped_old}, noimg={skipped_noimg}")

                except Exception as ex:
                    # Tek feed hatası akışı durdurmasın
                    print("Feed hata:", category, source_name, ex)

        # === Yazımlar ===
        if not to_add_rows:
            # Haber yoksa bilgi satırı yaz ve çık
            no_news = [""] * row_len
            no_news[cols.A  - 1] = f"ℹ️ Yeni haber bulunmadı: {now_str}"
            no_news[cols.AC - 1] = f"{status_text(1, True)} — No-News"
            append_rows([no_news])
            print("Yeni haber yok.")
            return

        # Ayırıcı
        sep = [""] * row_len
        sep[cols.A  - 1] = f"🆕 Yeni haber çekimi: {now_str}"
        sep[cols.AC - 1] = "Separator"
        append_rows([sep])

        # Haberler (batch)
        for i in range(0, len(to_add_rows), APPEND_BATCH_SIZE):
            append_rows(to_add_rows[i:i + APPEND_BATCH_SIZE])

        # Not (AD) — sep dahil satır hesabı
        last_row_index = used_rows_before + 1 + len(to_add_rows)  # +1: sep
        ws.update_cell(last_row_index, cols.AD, f"{len(to_add_rows)} haber eklendi")

        print(f"✓ {len(to_add_rows)} yeni haber eklendi.")

    except Exception as e:
        # Genel hata: tabloya ❌ düş
        err = [""] * row_len
        err[cols.A  - 1] = f"❗ Harvester hata: {now_str}"
        err[cols.AC - 1] = f"{status_text(1, False)} — {str(e)[:180]}"
        ws.append_rows([err], value_input_option="RAW")
        raise


if __name__ == "__main__":
    run()
