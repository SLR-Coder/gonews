# -*- coding: utf-8 -*-
import os, json, time, datetime, traceback
from typing import List, Dict
from dotenv import load_dotenv
load_dotenv()

# ---- Robots: modülleri içe aktar ----
# Bu importlar bir şey eksikse hatayı yakalayıp temiz log yazar.
def _safe_import():
    mods = {}
    errors = {}
    try:
        from robots import visual_styler as _vs
        mods["styler"] = _vs
    except Exception as e:
        errors["styler"] = str(e)

    try:
        from robots import publisher_bot as _pub
        mods["publisher"] = _pub
    except Exception as e:
        errors["publisher"] = str(e)

    try:
        from robots import content_crafter as _crafter
        mods["crafter"] = _crafter
    except Exception as e:
        errors["crafter"] = str(e)

    try:
        from robots import cleaner_bot as _clean
        mods["cleaner"] = _clean
    except Exception as e:
        errors["cleaner"] = str(e)

    try:
        from robots import news_crawler as _crawler
        mods["crawler"] = _crawler
    except Exception as e:
        errors["crawler"] = str(e)

    return mods, errors

MODULES, IMPORT_ERRORS = _safe_import()

# ---- GCS lock: aynı anda 2 çalışmayı önle ----
from google.cloud import storage
LOCK_BKT  = os.environ.get("GOOGLE_STORAGE_BUCKET", "")
LOCK_KEY  = os.environ.get("CRON_LOCK_KEY", "locks/gonews-cron.lock")
LOCK_TTL  = int(os.environ.get("CRON_LOCK_TTL_SEC", "900"))  # 15 dk

def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def acquire_lock() -> (bool, str):
    """GCS üzerinde optimistic create ile lock alır."""
    if not LOCK_BKT:
        return True, "no-bucket"
    cli = storage.Client()
    bkt = cli.bucket(LOCK_BKT)
    blob = bkt.blob(LOCK_KEY)
    # varsa ve taze ise bırak
    if blob.exists():
        age = (_now_utc() - blob.time_created).total_seconds()
        if age < LOCK_TTL:
            return False, f"busy ({int(age)}s)"
        # bayat; temizleyip almayı dene
        try:
            blob.delete()
        except Exception:
            pass
    try:
        blob.upload_from_string(str(time.time()), if_generation_match=0)
        return True, "acquired"
    except Exception:
        return False, "busy"

def release_lock():
    if not LOCK_BKT: 
        return
    try:
        storage.Client().bucket(LOCK_BKT).blob(LOCK_KEY).delete()
    except Exception:
        pass

# ---- Yardımcılar ----
def _log(msg: str, **kw):
    print(json.dumps({"t": _now_utc().isoformat(), "msg": msg, **kw}, ensure_ascii=False))

def _run_step(name: str) -> Dict:
    """Belirli robotu çalıştır."""
    mod = MODULES.get(name)
    if not mod:
        raise RuntimeError(f"Modül yüklenemedi: {name} (import error: {IMPORT_ERRORS.get(name)})")
    if not hasattr(mod, "run"):
        raise RuntimeError(f"Modülde run() yok: {name}")
    t0 = time.time()
    _log(f"→ step start: {name}")
    mod.run()         # ROBOT ÇAĞRISI
    dur = round(time.time() - t0, 2)
    _log(f"✓ step done: {name}", secs=dur)
    return {"step": name, "seconds": dur, "ok": True}

def _parse_workflow(raw: str) -> List[str]:
    """
    Örnekler:
      "styler,publisher"
      "crawler,cleaner,crafter,styler,publisher"
    """
    raw = (raw or "").strip()
    if not raw:
        return ["styler", "publisher"]  # varsayılan
    return [s.strip().lower() for s in raw.split(",") if s.strip()]

def _check_secret(request) -> None:
    """İsteğin geldiği yer Cloud Scheduler ise:
       - ya OIDC ile kimlikli gelir (önerilen)
       - extra olarak X-Cron-Token veya ?key= ile shared secret kontrolü yapıyoruz (opsiyonel).
    """
    want = os.environ.get("CRON_SECRET", "")
    if not want:
        return  # secret tanımlı değilse kontrol etme

    got = request.headers.get("X-Cron-Token") or request.args.get("key")
    if got != want:
        raise PermissionError("invalid-cron-secret")

# ===================== ENTRY POINT (Cloud Functions Gen2) =====================
def run_gonews(request):
    """
    HTTP tetikleyici. GET/POST kabul eder.
    Body JSON örneği:
      { "workflow": "styler,publisher" }
    """
    try:
        _check_secret(request)

        # Aynı anda 2 kez koşmasın
        ok, reason = acquire_lock()
        if not ok:
            return (json.dumps({"ok": False, "reason": reason}), 429, {"Content-Type": "application/json"})

        try:
            # workflow seçimi: body->query->ENV (WORKFLOW)
            data = {}
            if request.data:
                try:
                    data = request.get_json(force=True, silent=True) or {}
                except Exception:
                    data = {}
            wf_raw = (data.get("workflow")
                      or request.args.get("workflow")
                      or os.environ.get("WORKFLOW", "styler,publisher"))
            steps = _parse_workflow(wf_raw)

            _log("workflow", steps=steps)

            results = []
            for step in steps:
                try:
                    results.append(_run_step(step))
                except Exception as e:
                    _log("step failed", step=step, err=str(e), tb=traceback.format_exc())
                    # başarısız olan adı ve hata mesajını da dön
                    results.append({"step": step, "ok": False, "error": str(e)})
                    # zinciri burada keselim:
                    break

            ok_all = all(r.get("ok") for r in results)
            return (json.dumps({"ok": ok_all, "results": results}, ensure_ascii=False),
                    200 if ok_all else 500,
                    {"Content-Type": "application/json"})

        finally:
            release_lock()

    except PermissionError:
        return (json.dumps({"ok": False, "error": "forbidden"}), 403, {"Content-Type": "application/json"})
    except Exception as e:
        _log("fatal", err=str(e), tb=traceback.format_exc())
        return (json.dumps({"ok": False, "error": str(e)}), 500, {"Content-Type": "application/json"})
