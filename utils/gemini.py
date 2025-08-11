# utils/gemini.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, re, json, time, logging
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv
load_dotenv()

import google.generativeai as genai

# -------- ENV --------
API_KEY   = os.environ.get("GEMINI_API_KEY", "")
MODEL     = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")
FALLBACK  = os.environ.get("GEMINI_FALLBACK_MODEL", "")   # ör: "gemini-1.5-flash"
EMB_MODEL = os.environ.get("GEMINI_EMB_MODEL", "models/embedding-001")

MAX_RETRY = int(os.environ.get("GEMINI_MAX_RETRY", "3"))
SLEEP_S   = float(os.environ.get("GEMINI_RETRY_SLEEP_S", "1.0"))
INPUT_MAX = int(os.environ.get("INPUT_MAX_CHARS", "12000"))

genai.configure(api_key=API_KEY)
if not API_KEY:
    logging.warning("GEMINI_API_KEY boş; çağrılar başarısız olabilir.")

GEN_CFG = {"temperature": 0.7, "top_p": 0.95, "top_k": 40, "max_output_tokens": 1024}

# -------- Helpers --------
def _truncate(s: Optional[str], limit: int = INPUT_MAX) -> str:
    s = (s or "").strip()
    if len(s) <= limit: return s
    cut = s[:limit]
    p = cut.rfind(". ")
    return cut[:p+1] if p > 300 else cut

def _retry(fn):
    last = None
    for _ in range(MAX_RETRY):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(SLEEP_S)
    if last: raise last

def _read_text(resp) -> str:
    """resp.text'e asla dokunma; sadece candidates -> parts -> text oku."""
    try:
        cands = getattr(resp, "candidates", []) or []
        for cand in cands:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", []) if content else []
            for p in parts:
                t = getattr(p, "text", None)
                if isinstance(t, str) and t.strip():
                    return t.strip()
    except Exception:
        pass
    return ""

def _json_from(text: str) -> Dict[str, str]:
    text = (text or "").strip()
    # düz json
    try:
        obj = json.loads(text)
        if isinstance(obj, dict): return obj
    except Exception: pass
    # { ... } blob
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if m:
        try: 
            obj = json.loads(m.group(0))
            if isinstance(obj, dict): return obj
        except Exception: pass
    return {"title": "", "summary": "", "long": ""}

def _gen_model(name: str):
    return genai.GenerativeModel(name)

# -------- Public API --------
def get_embedding(text: str) -> List[float]:
    if not text: return []
    def call():
        return genai.embed_content(model=EMB_MODEL, content=_truncate(text, 8000))
    try:
        resp = _retry(call)
        return resp.get("embedding") or resp.get("data", {}).get("embedding") or []
    except Exception as e:
        logging.error(f"[gemini] embedding error: {e}")
        return []

def generate_text(prompt: str) -> str:
    """Basit metin üretimi. Gerekirse fallback modele döner."""
    model = _gen_model(MODEL)

    def call():
        return model.generate_content(_truncate(prompt), generation_config=GEN_CFG)

    try:
        resp = _retry(call)
        out = _read_text(resp)
        if out:
            return out
        # fallback
        if FALLBACK:
            fb = _gen_model(FALLBACK)
            def call_fb():
                return fb.generate_content(_truncate(prompt), generation_config=GEN_CFG)
            resp2 = _retry(call_fb)
            return _read_text(resp2) or ""
        return ""
    except Exception as e:
        logging.error(f"[gemini] generate_text error: {e}")
        return ""

def generate_content(
    *,
    original_title: str,
    original_text: str = "",
    original_lang: str = "en",
    target_lang: str = "tr",
    source_name: str = "",
) -> Dict[str, str]:
    """title/summary/long JSON döner. Gerekirse fallback modele döner."""
    o_title = _truncate(original_title, 800)
    o_text  = _truncate(original_text, 9000)

    prompt = f"""
Orijinal başlık ({original_lang}): {o_title}
Orijinal metin ({original_lang}) (opsiyonel): {o_text}

Görev:
- ÇIKTI DİLİ: {target_lang}
- 1) 55–85 karakter arası, tıklama tuzağı olmayan başlık.
- 2) 2–3 cümlelik tarafsız özet.
- 3) 4–6 paragraf uzun metin; son cümlede "Kaynak: {source_name}" yaz.
- Özel isimleri doğru yaz; tarih/yer/rakamları koru; link/emoji/hashtag kullanma.

Sadece şu JSON'ı ver:
{{"title": "...", "summary": "...", "long": "..."}}
""".strip()

    model = _gen_model(MODEL)

    def call():
        return model.generate_content(_truncate(prompt), generation_config=GEN_CFG)

    try:
        resp = _retry(call)
        text = _read_text(resp)
        if not text and FALLBACK:
            fb = _gen_model(FALLBACK)
            def call_fb():
                return fb.generate_content(_truncate(prompt), generation_config=GEN_CFG)
            resp2 = _retry(call_fb)
            text = _read_text(resp2)

        obj = _json_from(text)

        title   = (obj.get("title") or "").strip()
        summary = (obj.get("summary") or "").strip()
        longtxt = (obj.get("long") or obj.get("article") or "").strip()

        # küçük temizlik
        if title.isupper(): title = title.capitalize()
        if len(title) > 95: title = title[:92].rstrip() + "…"
        if len(summary) > 600: summary = summary[:597].rstrip() + "…"
        if not longtxt: longtxt = summary

        return {"title": title, "summary": summary, "long": longtxt}
    except Exception as e:
        logging.error(f"[gemini] generate_content error: {e}")
        return {"title": "", "summary": "", "long": ""}

# -------- Self test --------
if __name__ == "__main__":
    print("MODEL:", MODEL)
    print("KEY LEN:", len(os.environ.get("GEMINI_API_KEY", "")))

    t = generate_text("Kısaca tek cümlelik nötr bir selamlama üret.")
    print("TEXT:", t if t else "<empty>")

    j = generate_content(
        original_title="OpenAI yeni bir model duyurdu",
        original_text="OpenAI, yeni modelinin muhakeme ve güvenlikte gelişmeler sunduğunu açıkladı.",
        original_lang="tr", target_lang="tr", source_name="Demo Kaynak"
    )
    print("CONTENT:", json.dumps(j, ensure_ascii=False))
