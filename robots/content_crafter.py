# robots/content_crafter.py
# -*- coding: utf-8 -*-

from dotenv import load_dotenv
load_dotenv()

import os
import time
import datetime
import traceback
from typing import List, Tuple, Dict

from utils.auth import get_gspread_client
from utils.schema import resolve_columns
from utils.gemini import generate_content, get_embedding

# ====== Ayarlar (.env) ======
NEWS_TAB           = os.environ.get("NEWS_TAB", "News")

# Duplicate eşiği (0.0–1.0 arası anlamlı). >=1.5 ise KAPALI sayılır (performans için).
DUP_THRESHOLD      = float(os.environ.get("DUP_THRESHOLD", "2.0"))

# Kaç satırı bir seferde yazalım / aralarda bekleme
CRAFTER_BATCH      = int(os.environ.get("CRAFTER_BATCH", "30"))
CRAFTER_SLEEP_MS   = int(os.environ.get("CRAFTER_SLEEP_MS", "800"))

# Yumuşak limitler
TITLE_MIN_CHARS    = int(os.environ.get("TITLE_MIN_CHARS", "55"))
TITLE_MAX_CHARS    = int(os.environ.get("TITLE_MAX_CHARS", "85"))
SUMMARY_MAX_WORDS  = int(os.environ.get("SUMMARY_MAX_WORDS", "70"))

# İsteğe bağlı üst sınır (0 = sınırsız)
CRAFTER_MAX_ROWS   = int(os.environ.get("CRAFTER_MAX_ROWS", "0"))

# ====== Yardımcılar ======
def _trim_words(text: str, max_words: int) -> str:
    w = (text or "").split()
    return " ".join(w[:max_words]) + ("…" if len(w) > max_words else "")

def _clip_title(title: str) -> str:
    t = (title or "").strip()
    if len(t) > TITLE_MAX_CHARS:
        return t[:TITLE_MAX_CHARS - 1].rstrip() + "…"
    return t

def _cos_sim(a: List[float], b: List[float]) -> float:
    # küçük ve hızlı: normalize etmeden kosinüs
    if not a or not b: return 0.0
    dot = sum(x*y for x, y in zip(a, b))
    na  = (sum(x*x for x in a)) ** 0.5
    nb  = (sum(y*y for y in b)) ** 0.5
    if na == 0 or nb == 0: return 0.0
    return dot / (na * nb)

def _duplicate_in_batch(cat: str, title: str, cache: Dict[str, List[Tuple[str, List[float]]]]) -> bool:
    """Aynı kategori içinde, aynı batch’te üretilenler arasında benzerlik kontrolü."""
    if DUP_THRESHOLD >= 1.5:
        return False
    cur_emb = get_embedding(title) or []
    if not cur_emb:
        return False
    for prev_title, prev_emb in cache.get(cat, []):
        if prev_emb:
            if _cos_sim(cur_emb, prev_emb) >= DUP_THRESHOLD:
                return True
    # cache’e ekle
    cache.setdefault(cat, []).append((title, cur_emb))
    return False

# ====== Ana Çalışma ======
def run():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID boş.")

    gc   = get_gspread_client()
    ws   = gc.open_by_key(sheet_id).worksheet(NEWS_TAB)
    cols = resolve_columns(ws)

    values = ws.get_all_values()
    data   = values[1:]  # header hariç

    # İşlenecekleri topla: sadece AC = Robot 1 Başarılı ve H/I/J boş
    todo: List[Tuple[int, str, str, str, str, str]] = []
    for i, row in enumerate(data, start=2):
        ac   = (row[cols.AC - 1].strip() if len(row) >= cols.AC else "")
        h    = (row[cols.H  - 1].strip() if len(row) >= cols.H  else "")
        i_tx = (row[cols.I  - 1].strip() if len(row) >= cols.I  else "")
        j    = (row[cols.J  - 1].strip() if len(row) >= cols.J  else "")
        title= (row[cols.F  - 1].strip() if len(row) >= cols.F  else "")
        link = (row[cols.G  - 1].strip() if len(row) >= cols.G  else "")
        cat  = (row[cols.C  - 1].strip() if len(row) >= cols.C  else "")
        src  = (row[cols.D  - 1].strip() if len(row) >= cols.D  else "")
        lang = (row[cols.E  - 1].strip() if len(row) >= cols.E  else "")

        if "robot 1 başarılı" not in ac.lower():
            continue
        if not title or not link or not cat:
            continue
        if h or i_tx or j:
            continue

        todo.append((i, title, link, cat, src, lang))

    if CRAFTER_MAX_ROWS and len(todo) > CRAFTER_MAX_ROWS:
        todo = todo[:CRAFTER_MAX_ROWS]

    print(f"İşlenecek satır: {len(todo)} (yalnızca AC='Robot 1 Başarılı')")

    processed = 0
    # batch içi duplicate kontrolü için embedding cache
    batch_dup_cache: Dict[str, List[Tuple[str, List[float]]]] = {}

    for start in range(0, len(todo), CRAFTER_BATCH):
        batch = todo[start:start + CRAFTER_BATCH]

        for (row_idx, title, link, cat, src, lang) in batch:
            try:
                # — Opsiyonel duplicate (sadece aynı batch içinde, hızlı)
                if _duplicate_in_batch(cat, title, batch_dup_cache):
                    ws.update_cell(row_idx, cols.AC, "Tekrarlanan Haber - Atlandı")
                    ws.update_cell(row_idx, cols.AD, f"Batch dup ≥ {DUP_THRESHOLD}")
                    continue

                # — İçerik üretimi
                out = generate_content(
                    original_title=title,
                    original_text="",
                    original_lang=lang or "en",
                    target_lang="tr",
                    source_name=src
                )

                new_h = (out.get("title") or "").strip()
                new_i = (out.get("summary") or "").strip()
                new_j = (out.get("long") or "").strip()

                # --- Yumuşak düzeltmeler ---
                # Başlık: boşsa orijinal başlık; sonra max’a göre kısalt
                if not new_h:
                    new_h = title.strip()
                new_h = _clip_title(new_h)

                # Özet: boşsa uzun metinden ilk cümle; o da yoksa başlık
                if not new_i:
                    first = ""
                    txt = (new_j or "").strip()
                    if txt:
                        for sep in [".", "!", "?"]:
                            p = txt.find(sep)
                            if p > 20:
                                first = txt[:p+1].strip()
                                break
                    if not first:
                        first = txt or new_h
                    new_i = first
                # Özet kelime limiti
                new_i = _trim_words(new_i, SUMMARY_MAX_WORDS)

                # Ufak kalite: bağıran başlıkları sakinleştir
                if new_h.isupper():
                    new_h = new_h.capitalize()

                # — Sheet’e yaz
                ws.update_cell(row_idx, cols.H,  new_h)
                ws.update_cell(row_idx, cols.I,  new_i)
                ws.update_cell(row_idx, cols.J,  new_j)
                ws.update_cell(row_idx, cols.AC, "Robot 1 Başarılı / Robot 2 Başarılı")
                ws.update_cell(row_idx, cols.AD, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                processed += 1

            except Exception as e:
                ws.update_cell(row_idx, cols.AC, "Robot 1 Başarılı / Robot 2 Hata")
                ws.update_cell(row_idx, cols.AD, f"{type(e).__name__}: {e}")
                traceback.print_exc()

        # quota-dostu bekleme
        time.sleep(CRAFTER_SLEEP_MS / 1000.0)

    print(f"✓ Robot 2 bitti — işlendi: {processed}")

if __name__ == "__main__":
    run()
