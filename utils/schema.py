# utils/schema.py
# -*- coding: utf-8 -*-
"""
Sheet kolon isimlerini başlıktan dinamik çözer.
Başlıklar yer değişse bile kod kırılmaz.

Kullanım:
from utils.schema import resolve_columns
cols = resolve_columns(ws)  # 1-based index'ler döner, örn. cols.AC == 29
"""

from dataclasses import dataclass
from typing import Dict, List

# TR başlıkları: (tam metin eşleşmesi; boşluk/harf duyarlı!)
HEADERS_TR = [
    "Haber ID",                         # A
    "Tarih/Saat",                       # B
    "Ana Kategori",                     # C
    "Alt Kategori/Kaynak",              # D
    "Orijinal Dil",                     # E
    "Orijinal Başlık",                  # F
    "Orijinal Link",                    # G
    "Yeni Başlık (TR)",                 # H
    "Özet (TR)",                        # I
    "Uzun Metin (TR)",                  # J
    "Görsel Link (Orijinal)",           # K
    "WordPress Web – Görsel",           # L
    "Telegram – Görsel",                # M
    "X.com – Görsel",                   # N
    "Bluesky – Görsel",                 # O
    "LinkedIn – Görsel",                # P
    "Instagram – Görsel",               # Q
    "Instagram – Video İçin Görsel",    # R
    "TikTok – Video İçin Görsel",       # S
    "YouTube Shorts – Video İçin Görsel",# T
    "YouTube Uzun – Video İçin Görsel", # U
    "Kısa Ses",                         # V
    "Uzun Ses",                         # W
    "Podcast Ses",                      # X
    "Instagram – Video",                # Y
    "TikTok – Video",                   # Z
    "YouTube Shorts – Video",           # AA
    "YouTube Uzun – Video",             # AB
    "Durum (üretim aşamasındaki durum)",# AC
    "Notlar",                           # AD
    "Web – paylaşım durum",             # AE
    "Telegram – paylaşım durum",        # AF
    "X.com – paylaşım durum",           # AG
    "Bluesky – paylaşım durum",         # AH
    "LinkedIn – paylaşım durum",        # AI
    "Instagram – paylaşım durum",       # AJ
    "TikTok – paylaşım durum",          # AK
    "YouTube Shorts – paylaşım durum",  # AL
    "YouTube Uzun – paylaşım durum",    # AM
]

# Kolay erişim için sembolik anahtarlar (kodu daha okunaklı yapar)
KEYS = [
    "A","B","C","D","E","F","G","H","I","J",
    "K","L","M","N","O","P","Q","R","S","T",
    "U","V","W","X","Y","Z","AA","AB","AC","AD",
    "AE","AF","AG","AH","AI","AJ","AK","AL","AM"
]

# Bazı başlık varyasyonları/alias (küçük yazım farkları için esneklik)
ALIASES: Dict[str, List[str]] = {
    "Durum (üretim aşamasındaki durum)": ["Durum", "Status", "durum"],
    "Notlar": ["Not", "Açıklama", "Notes"],
}

@dataclass
class Cols:
    # Tüm kolonları 1-based index olarak tutar. Örn: cols.A == 1, cols.AC == 29
    mapping: Dict[str, int]

    def __getattr__(self, item: str) -> int:
        if item in self.mapping:
            return self.mapping[item]
        raise AttributeError(f"Column '{item}' not resolved")

def _normalize(s: str) -> str:
    return (s or "").strip()

def resolve_columns(ws) -> Cols:
    headers = ws.row_values(1)
    name_to_idx: Dict[str, int] = {}
    # Doğrudan eşleşme
    for i, h in enumerate(headers, start=1):
        name_to_idx[_normalize(h)] = i

    # HEADERS_TR sırasını dolaşıp tek tek bul
    mapping: Dict[str, int] = {}
    for idx, h in enumerate(HEADERS_TR, start=1):
        # Önce tam başlığı ara
        if _normalize(h) in name_to_idx:
            mapping[KEYS[idx-1]] = name_to_idx[_normalize(h)]
            continue
        # Alias dene
        for alias in ALIASES.get(h, []):
            if _normalize(alias) in name_to_idx:
                mapping[KEYS[idx-1]] = name_to_idx[_normalize(alias)]
                break
        # Bulunamazsa tahmini pozisyon (son çare; yine de doldur)
        if KEYS[idx-1] not in mapping:
            # Eğer sayfa eksik başlıkla gelmişse, sıraya göre tahminle:
            mapping[KEYS[idx-1]] = idx  # en azından boşa düşmesin
    return Cols(mapping)
