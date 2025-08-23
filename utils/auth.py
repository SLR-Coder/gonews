# utils/auth.py
# -*- coding: utf-8 -*-

import os
import gspread
import logging
from google.auth import default as google_auth_default
from google.oauth2.service_account import Credentials

# ✅ Cloud Logging entegrasyonu
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GoNews-Auth")


def get_gspread_client():
    """
    Google Sheets'e yetkili gspread istemcisi döndürür.
    - Cloud ortamında (Cloud Run / Functions) Workload Identity ile otomatik yetkilendirme yapılır.
    - Lokal test ortamında ise service_account.json veya .env üzerinden fallback yapılır.
    """
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]

    # === 1) Öncelikle Google Cloud Workload Identity kullanmayı dene ===
    try:
        creds, _ = google_auth_default(scopes=scopes)
        logger.info("✅ Google Cloud Workload Identity ile yetkilendirme başarılı.")
        return gspread.authorize(creds)
    except Exception as e:
        logger.warning(f"⚠️ Workload Identity kullanılamadı, fallback'e geçiliyor → {e}")

    # === 2) Lokal geliştirme için service_account.json fallback ===
    try:
        sa_path = os.path.join(os.path.dirname(__file__), "..", "service_account.json")
        sa_path = os.path.abspath(sa_path)
        if os.path.exists(sa_path):
            creds = Credentials.from_service_account_file(sa_path, scopes=scopes)
            logger.info("✅ Lokal service_account.json ile yetkilendirme başarılı.")
            return gspread.authorize(creds)
    except Exception as e:
        logger.error(f"❌ service_account.json ile yetkilendirme başarısız → {e}")

    # === 3) Hata durumunda durdur ===
    raise RuntimeError("❌ Google Sheets yetkilendirme başarısız! "
                       "Lütfen Workload Identity veya service_account.json'u kontrol edin.")