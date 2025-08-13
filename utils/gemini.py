# utils/gemini.py
# -*- coding: utf-8 -*-
import os, time, random
from typing import Optional, Any, List

from dotenv import load_dotenv
load_dotenv()

import google.generativeai as genai

# ===== Env =====
GEMINI_API_KEY         = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL           = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
GEMINI_FALLBACK_MODEL  = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-1.5-flash")
GEMINI_EMB_MODEL       = os.getenv("GEMINI_EMB_MODEL", "models/text-embedding-004")

GEMINI_MAX_RETRY       = int(os.getenv("GEMINI_MAX_RETRY", "3"))
GEMINI_MIN_DELAY_S     = float(os.getenv("GEMINI_MIN_DELAY_S", "0.8"))
GEMINI_MAX_DELAY_S     = float(os.getenv("GEMINI_MAX_DELAY_S", "4.0"))

GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "2048"))
INPUT_MAX_CHARS        = int(os.getenv("INPUT_MAX_CHARS", "12000"))

# Güvenlik: 1 ise engel eşiğini kapatır (haber özeti gibi zararsız işler için OK)
GEMINI_SAFETY_OFF      = os.getenv("GEMINI_SAFETY_OFF", "1") in ("1","true","True")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY boş. .env'yi kontrol et.")

genai.configure(api_key=GEMINI_API_KEY)

SAFETY_SETTINGS = None
if GEMINI_SAFETY_OFF:
    # Tüm kategorilerde engellemeyi kapat
    SAFETY_SETTINGS = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUAL", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS", "threshold": "BLOCK_NONE"},
    ]

def _truncate(s: str) -> str:
    if not s:
        return ""
    if len(s) > INPUT_MAX_CHARS:
        return s[:INPUT_MAX_CHARS] + "\n\n[trimmed]"
    return s

def _extract_text(resp: Any) -> str:
    """
    google-generativeai yanıtından güvenli metin çıkarımı.
    resp.text yoksa candidates->parts üzerinden dener; yoksa "" döner.
    """
    if resp is None:
        return ""
    # Çoğu durumda
    try:
        t = getattr(resp, "text", None)
        if t:
            return t.strip()
    except Exception:
        pass
    # Adaylar üzerinden parçalar
    try:
        cands = getattr(resp, "candidates", None) or []
        for c in cands:
            # finish_reason 'SAFETY' ise pas geç, diğerini dene
            fr = getattr(c, "finish_reason", None)
            if isinstance(fr, str) and fr.upper() == "SAFETY":
                continue
            if isinstance(fr, int) and fr == 2:  # 2 ~ SAFETY
                continue
            parts = getattr(c, "content", None)
            parts = getattr(parts, "parts", None) if parts else None
            if parts:
                buf: List[str] = []
                for p in parts:
                    text_part = getattr(p, "text", None)
                    if text_part:
                        buf.append(text_part)
                if buf:
                    return "\n".join(buf).strip()
    except Exception:
        pass
    return ""

def _gen_once(model_name: str, prompt: str, temperature: float = 0.7) -> str:
    model = genai.GenerativeModel(
        model_name,
        safety_settings=SAFETY_SETTINGS,
        generation_config={
            "temperature": temperature,
            "max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS,
        },
    )
    resp = model.generate_content(_truncate(prompt))
    return _extract_text(resp)

def _gen_with_retry(model_name: str, prompt: str, temperature: float) -> str:
    last_err = ""
    for attempt in range(1, GEMINI_MAX_RETRY + 1):
        try:
            text = _gen_once(model_name, prompt, temperature=temperature)
            if text:
                return text
            last_err = "empty response"
        except Exception as e:
            last_err = str(e)
        # bekleme (rate-limit dostu)
        if attempt < GEMINI_MAX_RETRY:
            time.sleep(random.uniform(GEMINI_MIN_DELAY_S, GEMINI_MAX_DELAY_S))
    # Başarısız
    raise RuntimeError(last_err or "gemini empty response")

def generate_text(prompt: str, model: Optional[str] = None, temperature: float = 0.7) -> str:
    """
    Genel üretim: önce ana model, başarısızsa fallback model.
    Boş/engellenmiş cevaplar da tekrar dener ve fallback’e geçer.
    """
    use_model = model or GEMINI_MODEL
    try:
        return _gen_with_retry(use_model, prompt, temperature)
    except Exception:
        if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != use_model:
            return _gen_with_retry(GEMINI_FALLBACK_MODEL, prompt, temperature)
        raise

def generate_embedding(text: str) -> list:
    try:
        resp = genai.embed_content(model=GEMINI_EMB_MODEL, content=_truncate(text))
        # 0.7+ sürümlerde sözlük verir: {"embedding": {"values": [...]}}
        if isinstance(resp, dict):
            return (resp.get("embedding") or {}).get("values") or []
        emb = getattr(resp, "embedding", None)
        return getattr(emb, "values", []) if emb else []
    except Exception:
        return []
