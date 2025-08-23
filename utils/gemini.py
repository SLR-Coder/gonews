# utils/gemini.py
# -*- coding: utf-8 -*-

import os
import time
import random
from typing import Any, List, Optional
import google.generativeai as genai
from utils.secrets import get_secret
import logging

# ---- Logging ----
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GoNews-Gemini")

# ---- Secret & Env Config ----
GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("❌ GEMINI_API_KEY Secret Manager'da bulunamadı!")

genai.configure(api_key=GEMINI_API_KEY)

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-pro-latest")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-1.5-flash")
GEMINI_EMB_MODEL = os.getenv("GEMINI_EMB_MODEL", "models/text-embedding-004")

GEMINI_SAFETY_OFF = os.getenv("GEMINI_SAFETY_OFF", "1") in ("1", "true", "True")
INPUT_MAX_CHARS = int(os.getenv("INPUT_MAX_CHARS", "12000"))
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "2048"))
GEMINI_MAX_RETRY = int(os.getenv("GEMINI_MAX_RETRY", "3"))
GEMINI_MIN_DELAY_S = float(os.getenv("GEMINI_MIN_DELAY_S", "0.8"))
GEMINI_MAX_DELAY_S = float(os.getenv("GEMINI_MAX_DELAY_S", "4.0"))

# ---- Safety Settings ----
SAFETY_SETTINGS = (
    [
        {"category": c, "threshold": "BLOCK_NONE"}
        for c in [
            "HARM_CATEGORY_HARASSMENT",
            "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUAL",
            "HARM_CATEGORY_DANGEROUS",
        ]
    ]
    if GEMINI_SAFETY_OFF
    else None
)

# ================== Yardımcı Fonksiyonlar ==================
def _truncate(s: str) -> str:
    if not s:
        return ""
    return s[:INPUT_MAX_CHARS] + ("\n\n[trimmed]" if len(s) > INPUT_MAX_CHARS else "")

def _extract_text(resp: Any) -> str:
    """
    google-generativeai yanıtından güvenli metin çıkarımı.
    resp.text yoksa candidates->parts üzerinden dener.
    """
    if resp is None:
        return ""

    try:
        t = getattr(resp, "text", None)
        if t:
            return t.strip()
    except Exception:
        pass

    try:
        cands = getattr(resp, "candidates", None) or []
        for c in cands:
            fr = getattr(c, "finish_reason", None)
            if fr in ("SAFETY", 2):  # 2 = SAFETY
                continue
            parts = getattr(getattr(c, "content", None), "parts", None)
            if parts:
                buf = [p.text for p in parts if getattr(p, "text", None)]
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
            text = _gen_once(model_name, prompt, temperature)
            if text:
                return text
            last_err = "empty response"
        except Exception as e:
            last_err = str(e)
        if attempt < GEMINI_MAX_RETRY:
            delay = random.uniform(GEMINI_MIN_DELAY_S, GEMINI_MAX_DELAY_S)
            logger.warning(f"Gemini retry {attempt}/{GEMINI_MAX_RETRY} (waiting {delay:.1f}s)... Error: {last_err}")
            time.sleep(delay)
    raise RuntimeError(last_err or "Gemini empty response")

# ================== Public API ==================
def generate_text(prompt: str, model: Optional[str] = None, temperature: float = 0.7) -> str:
    """
    Genel üretim: önce ana model, başarısızsa fallback model.
    """
    use_model = model or GEMINI_MODEL
    try:
        return _gen_with_retry(use_model, prompt, temperature)
    except Exception:
        if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != use_model:
            logger.info("Primary model failed, switching to fallback.")
            return _gen_with_retry(GEMINI_FALLBACK_MODEL, prompt, temperature)
        raise

def generate_embedding(text: str) -> list:
    try:
        resp = genai.embed_content(model=GEMINI_EMB_MODEL, content=_truncate(text))
        if isinstance(resp, dict):
            return (resp.get("embedding") or {}).get("values") or []
        emb = getattr(resp, "embedding", None)
        return getattr(emb, "values", []) if emb else []
    except Exception as e:
        logger.error(f"Embedding error: {e}")
        return []
