# utils/secrets.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import os
import json
import logging
from typing import Any, Iterable, Optional, Dict

from google.cloud import secretmanager
from google.api_core.exceptions import NotFound, PermissionDenied, GoogleAPICallError

# ---- Logging (Cloud Logging ile uyumlu) ----
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GoNews-Secrets")

# ---- Basit bellek içi cache ----
_cache: Dict[str, Any] = {}

# ---- Proje ID tespiti: Cloud Run için güvenli ----
def _detect_project_id(explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        return explicit
    # Tercih sırası: GCP_PROJECT (senin .env'inde var) -> GOOGLE_CLOUD_PROJECT (Cloud Run default)
    return (
        os.getenv("GCP_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or None
    )

def clear_cache() -> None:
    """Testlerde ya da rotasyon sonrası cache'i temizlemek için."""
    _cache.clear()

def _client() -> secretmanager.SecretManagerServiceClient:
    # Workload Identity ile kimlik otomatik gelir
    return secretmanager.SecretManagerServiceClient()

def get_secret(
    name: str,
    *,
    required: bool = True,
    default: Optional[str] = None,
    project_id: Optional[str] = None,
    version: str = "latest",
    cache: bool = True,
    as_json: bool = False,
    strip: bool = True,
) -> Any:
    """
    Secret Manager'dan bir sır getirir.
    - Secret Manager ÖNCELİKLİDİR.
    - Bulunamazsa .env fallback yapılır.
    - as_json=True ise JSON parse edilmiş obje döner.
    - required=False ve değer bulunamazsa default döner.
    """
    key = f"{name}@{version}"
    if cache and key in _cache:
        return _cache[key]

    proj = _detect_project_id(project_id)
    value: Optional[str] = None

    # 1) Secret Manager
    if proj:
        try:
            sm_client = _client()
            resource = f"projects/{proj}/secrets/{name}/versions/{version}"
            resp = sm_client.access_secret_version(request={"name": resource})
            value = resp.payload.data.decode("utf-8")
            # Güvenlik: Değer loglanmaz, sadece isim loglanır
            logger.info(f"[SecretManager] '{name}' alındı.")
        except NotFound:
            logger.warning(f"[SecretManager] '{name}' bulunamadı (proj={proj}). .env fallback denenecek.")
        except PermissionDenied:
            logger.error(f"[SecretManager] '{name}' erişim izni YOK. IAM rollerini kontrol edin.")
        except GoogleAPICallError as e:
            logger.error(f"[SecretManager] '{name}' alınırken API hatası: {e}. .env fallback denenecek.")
        except Exception as e:
            logger.error(f"[SecretManager] '{name}' beklenmeyen hata: {e}. .env fallback denenecek.")

    # 2) .env fallback (değeri loglama!)
    if value is None:
        env_val = os.getenv(name)
        if env_val is not None:
            value = env_val
            logger.warning(f"[Fallback .env] '{name}' .env üzerinden kullanılıyor.")
        else:
            if required and default is None:
                raise RuntimeError(
                    f"'{name}' sırrı bulunamadı. Secret Manager (proj={proj}) ve .env kontrol edin."
                )
            value = default  # None olabilir; required=False ise normal

    if value is None:
        # required=False + default=None olabilir
        return None

    if strip and isinstance(value, str):
        value = value.strip()

    if as_json:
        try:
            parsed = json.loads(value)
            if cache:
                _cache[key] = parsed
            return parsed
        except json.JSONDecodeError:
            raise RuntimeError(f"'{name}' JSON bekleniyordu ama geçerli JSON değil.")

    if cache:
        _cache[key] = value
    return value


# ---- Tip yardımcıları (şeker fonksiyonlar) ----

def get_secret_int(name: str, *, required: bool = True, default: Optional[int] = None, **kwargs) -> Optional[int]:
    val = get_secret(name, required=required, default=None if required else (default if default is not None else None), **kwargs)
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        raise RuntimeError(f"'{name}' tam sayı (int) olmalı. Mevcut değer geçersiz.")

def get_secret_bool(name: str, *, required: bool = True, default: Optional[bool] = None, **kwargs) -> Optional[bool]:
    val = get_secret(name, required=required, default=None if required else (default if default is not None else None), **kwargs)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    raise RuntimeError(f"'{name}' boolean olmalı (true/false).")

def get_secret_json(name: str, *, required: bool = True, default: Optional[Any] = None, **kwargs) -> Any:
    try:
        return get_secret(name, required=required, default=None, as_json=True, **kwargs)
    except RuntimeError:
        if not required:
            return default
        raise


# ---- Toplu preload (isteğe bağlı performans iyileştirmesi) ----

def preload_secrets(names: Iterable[str], *, project_id: Optional[str] = None, version: str = "latest") -> None:
    """
    Çok sayıda sır aynı çalışmada gerekiyorsa network turunu azaltmak için
    preload edebilirsin. Hata verirse sırayı atlar ve loglar.
    """
    for n in names:
        try:
            get_secret(n, project_id=project_id, version=version, cache=True)
        except Exception as e:
            logger.warning(f"[preload] '{n}' preload edilemedi: {e}")
