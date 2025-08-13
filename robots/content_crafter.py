# robots/content_crafter.py
# -*- coding: utf-8 -*-

from dotenv import load_dotenv
load_dotenv()

import os
import re
import time
import datetime
import unicodedata
from typing import List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import gspread
import requests
from bs4 import BeautifulSoup

from utils.auth import get_gspread_client
from utils.schema import resolve_columns
from utils.gemini import generate_text  # retry + fallback .env'e göre

# ================== ENV / AYARLAR ==================
NEWS_TAB               = os.environ.get("NEWS_TAB", "News")

CRAFTER_MAX_ROWS       = int(os.environ.get("CRAFTER_MAX_ROWS", "100"))
CRAFTER_CONCURRENCY    = int(os.environ.get("CRAFTER_CONCURRENCY", "8"))   # paralel iş parçası
CRAFTER_BATCH          = int(os.environ.get("CRAFTER_BATCH", "40"))        # tek batch'te kaç hücre yazalım
CRAFTER_SLEEP_MS       = int(os.environ.get("CRAFTER_SLEEP_MS", "800"))    # batch arası bekleme (ms)

INPUT_MAX_CHARS        = int(os.environ.get("INPUT_MAX_CHARS", "12000"))
SUMMARY_MAX_WORDS      = int(os.environ.get("SUMMARY_MAX_WORDS", "70"))
TITLE_MIN_CHARS        = int(os.environ.get("TITLE_MIN_CHARS", "55"))
TITLE_MAX_CHARS        = int(os.environ.get("TITLE_MAX_CHARS", "85"))
# başlık hedef aralığı: fazla uzamasın diye min ile min+20
TITLE_TARGET_MIN       = TITLE_MIN_CHARS
TITLE_TARGET_MAX       = min(TITLE_MAX_CHARS, TITLE_MIN_CHARS + 20)

ARTICLE_MIN_WORDS      = int(os.environ.get("ARTICLE_MIN_WORDS", "450"))
ARTICLE_MAX_WORDS      = int(os.environ.get("ARTICLE_MAX_WORDS", "700"))

REQUEST_TIMEOUT        = int(os.environ.get("REQUEST_TIMEOUT", "10"))
USER_AGENT             = os.environ.get("USER_AGENT", "GoNewsBot/1.0 (+https://example.com)")
HEADERS                = {"User-Agent": USER_AGENT}

# ================== STATÜ YARDIMCILARI ==================
def status_text(robot_no: int, ok: bool) -> str:
    return f"Robot {robot_no} {'✅' if ok else '❌'}"

def _compose_status_block(current: Optional[str], robot_no: int, ok: bool) -> str:
    """
    AC hücresi için:
      - Mevcut bloktaki "Robot n ✅/❌" satırını siler
      - "Robot n ✅|❌" satırını en alta ekler
      - Açıklama parçalarını (— …) temizler
    """
    lines: List[str] = []
    for ln in (current or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        m = re.match(r"^(Robot\s+\d+\s+[✅❌])", s)
        if m:
            s = m.group(1)
        if re.match(rf"^Robot\s+{robot_no}\s+[✅❌]$", s):
            continue
        lines.append(s)
    lines.append(f"Robot {robot_no} {'✅' if ok else '❌'}")
    return "\n".join(lines)

# ================== UZUN METİN SÜTUNU BULUCU ==================
CANDIDATE_LONGTEXT_HEADERS = [
    "content","article","text","body","metin","haber metni","içerik","icerik",
    "fulltext","longtext","article text","content body","story","story body"
]

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode("utf-8","ignore")
    s = s.lower()
    return re.sub(r"[^a-z0-9]+","", s)

def find_longtext_col(ws: gspread.Worksheet, cols) -> Optional[int]:
    # 1) schema öncelik: J varsa onu kullan
    j = getattr(cols, "J", None)
    if isinstance(j, int) and j > 0:
        return j
    # 2) başlıklarda esnek arama
    headers = ws.row_values(1)
    normed = [_norm(h) for h in headers]
    keys = [_norm(k) for k in CANDIDATE_LONGTEXT_HEADERS]
    for idx, h in enumerate(normed, start=1):  # 1-based
        if not h: continue
        for k in keys:
            if k in h:
                return idx
    return None

# ================== SAYFA İNDİRME & GÖVDE ÇIKARMA ==================
SESS = requests.Session()
SESS.headers.update(HEADERS)

def fetch_html(url: str) -> Optional[bytes]:
    try:
        r = SESS.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        pass
    return None

def extract_article_text(html: bytes) -> str:
    """
    Güçlü çıkarıcı:
      - <article> ve 'article|content|post|story|entry|main|read|detail|text|body' içeren div/section/main adayları
      - aday içinden <h1..h4>, <p>, <li> metinleri birleştirir
      - aday yoksa tüm dokümanda aynı birleşimi dener
      - minimum uzunluk garantisi için en uzun bloğu seçer ve normalize eder
    """
    soup = BeautifulSoup(html, "html.parser")

    # gürültü temizliği
    for t in soup(["script","style","noscript","header","footer","form","nav","aside","iframe"]):
        t.decompose()

    def _collect_text(node):
        parts: List[str] = []
        for h in node.find_all(["h1","h2","h3","h4"]):
            txt = h.get_text(" ", strip=True)
            if txt: parts.append(txt)
        for p in node.find_all("p"):
            txt = p.get_text(" ", strip=True)
            if txt: parts.append(txt)
        for li in node.find_all("li"):
            txt = li.get_text(" ", strip=True)
            if txt: parts.append(txt)
        return "\n".join(parts)

    # 1) <article> öncelik
    candidates: List[Tuple[int,str]] = []
    for art in soup.find_all("article"):
        txt = _collect_text(art)
        if len(txt.split()) > 80:
            candidates.append((len(txt.split()), txt))

    # 2) class/id eşleşmeli div/section/main
    if not candidates:
        patt = re.compile(r"(article|content|post|story|entry|main|read|detail|text|body)", re.I)
        for tag in soup.find_all(["div","section","main"]):
            blob = " ".join(tag.get("class") or []) + " " + (tag.get("id") or "")
            if patt.search(blob):
                txt = _collect_text(tag)
                if len(txt.split()) > 80:
                    candidates.append((len(txt.split()), txt))

    # 3) fallback: tüm doküman
    if not candidates:
        whole = _collect_text(soup)
        if whole:
            candidates.append((len(whole.split()), whole))

    if not candidates:
        return ""

    # en uzun bloğu seç
    candidates.sort(reverse=True, key=lambda x: x[0])
    text = candidates[0][1]

    # normalize
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = "\n\n".join(lines)
    return text

# ================== PROMPTLAR ==================
def build_title_prompt(source_title: str, category: str) -> str:
    return (
        "Aşağıdaki haber başlığını Türkçe'de yeniden yaz.\n"
        f"- Dikkat çekici ama abartısız olsun.\n"
        f"- Hedef uzunluk: {TITLE_TARGET_MIN}-{TITLE_TARGET_MAX} karakter.\n"
        f"- Asla {TITLE_MAX_CHARS} karakteri geçme.\n"
        f"- Kategori: {category}\n\n"
        f"Orijinal başlık:\n{source_title}\n\n"
        "ÇIKTI YALNIZCA YENİ BAŞLIK OLSUN."
    )

def build_summary_prompt(source_title: str, body_text: str, link: str) -> str:
    body_short = " ".join(body_text.split())[:INPUT_MAX_CHARS]
    return (
        "Aşağıdaki haber gövdesine dayanarak Türkçe, tarafsız ve bilgi verici BİR paragraf özet yaz.\n"
        f"- En fazla {SUMMARY_MAX_WORDS} kelime.\n"
        "- Sadece doğrulanabilir bilgi; duygu/yorum yok.\n"
        "- Gereksiz sıfat/slogan olmasın.\n"
        f"- Kaynak: {link}\n\n"
        f"Başlık: {source_title}\n\n"
        f"Gövde:\n{body_short}\n\n"
        "ÇIKTI YALNIZCA ÖZET PARAGRAFI OLSUN."
    )

def build_article_prompt(source_title: str, body_text: str, link: str, category: str) -> str:
    body_short = " ".join(body_text.split())[:INPUT_MAX_CHARS]
    return (
        "Aşağıdaki haber gövdesine dayanarak Türkçe, tarafsız ve TAM METİN yaz.\n"
        f"- EN AZ {ARTICLE_MIN_WORDS} kelime olsun; tercihen {ARTICLE_MAX_WORDS} kelime civarı (3–7 paragraf).\n"
        "- Net, bilgilendirici, abartısız bir dil kullan.\n"
        "- Doğrulanabilir bilgiye odaklan; spekülasyon/yorum yok.\n"
        "- Gereksiz tekrar yapma; verilerle, tarihlerle bağlam ver.\n"
        f"- Kategori: {category}\n"
        "- Son satıra kaynak bağlantısını parantez içinde ekle: (Kaynak: <link>)\n\n"
        f"Başlık: {source_title}\n\n"
        f"Gövde:\n{body_short}\n\n"
        "ÇIKTI YALNIZCA DÜZ METİN OLSUN (markdown/HTML kullanma)."
    )

# ================== ANA AKIŞ ==================
def run():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID boş.")

    gc = get_gspread_client()
    ws = gc.open_by_key(sheet_id).worksheet(NEWS_TAB)
    cols = resolve_columns(ws)
    longtext_col = find_longtext_col(ws, cols)  # J varsa öncelik

    values = ws.get_all_values()
    if not values:
        print("Sheet boş.")
        return

    data = values[1:]
    base_row_index = 2  # ilk veri satırı

    def _is_done_for_robot2(val: str) -> bool:
        if not val: return False
        s = val.lower()
        return ("robot 2 ✅" in s) or ("robot 2 ❌" in s)

    def _has_robot1_ok(val: str) -> bool:
        return bool(val) and ("robot 1 ✅" in val.lower())

    # işlenecek satırlar: Robot 1 ✅ var, Robot 2 yok, H/I veya longtext eksik
    todo: List[Tuple[int, List[str]]] = []
    for i, row in enumerate(data, start=base_row_index):
        try:
            ac_val = row[cols.AC - 1] if len(row) >= cols.AC else ""
            if not _has_robot1_ok(ac_val) or _is_done_for_robot2(ac_val):
                continue

            has_title   = len(row) >= cols.H and row[cols.H - 1].strip() != ""
            has_summary = len(row) >= cols.I and row[cols.I - 1].strip() != ""
            has_long    = False
            if longtext_col is not None and len(row) >= longtext_col:
                has_long = row[longtext_col - 1].strip() != ""

            if has_title and has_summary and (has_long or longtext_col is None):
                continue

            todo.append((i, row))
            if len(todo) >= CRAFTER_MAX_ROWS:
                break
        except Exception:
            continue

    if not todo:
        print("ContentCrafter: işlenecek satır yok.")
        return

    print(f"ContentCrafter: hedef {len(todo)} satır.")

    updates_lock = threading.Lock()
    updates: List[Tuple[int, int, str]] = []  # (row_idx, col_idx, value)

    def stage(r, c, v):
        with updates_lock:
            updates.append((r, c, v))

    def process_row(row_idx: int, row: List[str]) -> None:
        try:
            source_title = row[cols.F - 1] if len(row) >= cols.F else ""
            link         = row[cols.G - 1] if len(row) >= cols.G else ""
            category     = row[cols.C - 1] if len(row) >= cols.C else ""

            if not source_title or not link:
                prev_ac = row[cols.AC - 1] if len(row) >= cols.AC else ""
                stage(row_idx, cols.AC, _compose_status_block(prev_ac, 2, False))
                return

            need_title   = not (len(row) >= cols.H and row[cols.H - 1].strip())
            need_summary = not (len(row) >= cols.I and row[cols.I - 1].strip())
            need_long    = (longtext_col is not None) and not (len(row) >= longtext_col and row[longtext_col - 1].strip())

            body_text = ""
            if need_summary or need_long:
                html = fetch_html(link)
                if html:
                    body_text = extract_article_text(html)

            # Başlık — 55–85 hedef; asla TITLE_MAX_CHARS'ı geçme
            if need_title:
                t_prompt = build_title_prompt(source_title.strip()[:INPUT_MAX_CHARS], category)
                new_title = generate_text(t_prompt).strip()
                if len(new_title) > TITLE_MAX_CHARS:
                    new_title = new_title[:TITLE_MAX_CHARS].rstrip()
                if len(new_title) < max(10, TITLE_MIN_CHARS // 2):
                    # ikinci deneme: biraz daha uzun iste
                    t_prompt2 = t_prompt + "\n\nDaha kapsamlı ama yine de yalın bir başlık yaz."
                    new_title2 = generate_text(t_prompt2).strip()
                    if len(new_title2) > TITLE_MAX_CHARS:
                        new_title2 = new_title2[:TITLE_MAX_CHARS].rstrip()
                    if len(new_title2) >= max(10, TITLE_MIN_CHARS // 2):
                        new_title = new_title2
                    else:
                        # yine kısa ise mevcut başlığı kırparak kullan
                        nt = source_title.strip()
                        if len(nt) > TITLE_MAX_CHARS:
                            nt = nt[:TITLE_MAX_CHARS].rstrip()
                        new_title = nt
                stage(row_idx, cols.H, new_title)

            # Özet — gövdeye dayalı, tek paragraf
            if need_summary:
                s_prompt = build_summary_prompt(source_title, body_text or source_title, link)
                new_summary = generate_text(s_prompt).strip()
                new_summary = " ".join(new_summary.split())
                stage(row_idx, cols.I, new_summary)

            # Uzun metin — J sütunu (veya bulunan sütun)
            if need_long:
                a_prompt = build_article_prompt(source_title, body_text or source_title, link, category)
                article = generate_text(a_prompt).strip()
                words = article.split()

                # çok kısa geldiyse daha kapsamlı yazmasını iste
                if len(words) < max(ARTICLE_MIN_WORDS - 50, 200):
                    retry_prompt = a_prompt + "\n\nLütfen daha kapsamlı ve ayrıntılı yaz. Veri, tarih ve bağlam ekle."
                    article2 = generate_text(retry_prompt).strip()
                    if len(article2.split()) > len(words):
                        article = article2
                        words = article.split()

                # aşırı uzun ise nazikçe kısalt (1.6x esnek)
                soft_cap = int(ARTICLE_MAX_WORDS * 1.6)
                if len(words) > soft_cap:
                    article = " ".join(words[:soft_cap])

                stage(row_idx, longtext_col, article)

            # Statü ✅
            prev_ac = row[cols.AC - 1] if len(row) >= cols.AC else ""
            ac_val = _compose_status_block(prev_ac, 2, True)
            stage(row_idx, cols.AC, ac_val)

        except Exception:
            prev_ac = row[cols.AC - 1] if len(row) >= cols.AC else ""
            ac_val = _compose_status_block(prev_ac, 2, False)
            stage(row_idx, cols.AC, ac_val)

    # Paralel işleme
    with ThreadPoolExecutor(max_workers=CRAFTER_CONCURRENCY) as ex:
        futures = [ex.submit(process_row, r, row) for (r, row) in todo]
        for _ in as_completed(futures):
            pass

    # Toplu yazım (büyük batch'ler halinde)
    _flush_updates(ws, updates)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    note = f"ContentCrafter bitti: toplam={len(todo)} — {now_str}"
    try:
        ws.update_cell(len(ws.get_all_values()), cols.AD, note)
    except Exception:
        pass
    print(note)

def _flush_updates(ws: gspread.Worksheet, updates: List[Tuple[int, int, str]]):
    if not updates:
        return
    for i in range(0, len(updates), CRAFTER_BATCH):
        chunk = updates[i:i+CRAFTER_BATCH]
        data = [{"range": gspread.utils.rowcol_to_a1(r, c), "values": [[v]]} for (r, c, v) in chunk]
        body = {"valueInputOption": "RAW", "data": data}
        ws.spreadsheet.values_batch_update(body)
        time.sleep(CRAFTER_SLEEP_MS / 1000.0)

if __name__ == "__main__":
    run()
