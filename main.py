# main.py (Flask Entegrasyonlu Yeni Versiyon)
# -*- coding: utf-8 -*-

import os, json, time, datetime, traceback
from typing import List, Dict
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, jsonify

# ---- Sizin yazdığınız harika kodlar burada başlıyor ----

# ---- Robots: modülleri içe aktar ----
def _safe_import():
    mods = {}
    errors = {}
    robot_names = ["news_crawler", "content_crafter", "visual_styler", "publisher_bot", "cleaner_bot"]
    for name in robot_names:
        try:
            # Örnek: from robots import news_crawler as _crawler
            module = __import__(f"robots.{name}", fromlist=[None])
            # Sizin kodunuzdaki anahtar isimlendirmesine uyum sağlıyoruz
            if name == "news_crawler": key = "crawler"
            elif name == "content_crafter": key = "crafter"
            elif name == "visual_styler": key = "styler"
            elif name == "publisher_bot": key = "publisher"
            elif name == "cleaner_bot": key = "cleaner"
            else: key = name
            mods[key] = module
        except Exception as e:
            errors[name] = str(e)
    return mods, errors

MODULES, IMPORT_ERRORS = _safe_import()

# ---- GCS lock: aynı anda 2 çalışmayı önle ----
from google.cloud import storage
LOCK_BKT  = os.environ.get("GOOGLE_STORAGE_BUCKET", "")
LOCK_KEY  = os.environ.get("CRON_LOCK_KEY", "locks/gonews-cron.lock")
LOCK_TTL  = int(os.environ.get("CRON_LOCK_TTL_SEC", "900"))

def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def acquire_lock() -> (bool, str):
    if not LOCK_BKT: return True, "no-bucket"
    cli = storage.Client()
    bkt = cli.bucket(LOCK_BKT)
    blob = bkt.blob(LOCK_KEY)
    
    # Blob'un varlığını kontrol etmek yerine doğrudan `time_created`'a erişmeye çalışalım
    try:
        blob.reload() # En güncel metadata'yı al
        age = (_now_utc() - blob.time_created).total_seconds()
        if age < LOCK_TTL:
            return False, f"busy ({int(age)}s)"
        blob.delete()
    except Exception: # Örneğin 404 Not Found hatası
        pass # Kilit yoksa veya eski ise devam et

    try:
        blob.upload_from_string(str(time.time()), if_generation_match=0)
        return True, "acquired"
    except Exception:
        return False, "busy"

def release_lock():
    if not LOCK_BKT: return
    try: storage.Client().bucket(LOCK_BKT).blob(LOCK_KEY).delete()
    except Exception: pass

def _log(msg: str, **kw):
    print(json.dumps({"t": _now_utc().isoformat(), "msg": msg, **kw}, ensure_ascii=False))

def _run_step(name: str) -> Dict:
    mod = MODULES.get(name)
    if not mod:
        raise RuntimeError(f"Modül yüklenemedi: {name} (import error: {IMPORT_ERRORS.get(name)})")
    if not hasattr(mod, "run"):
        raise RuntimeError(f"Modülde run() yok: {name}")
    t0 = time.time()
    _log(f"→ step start: {name}")
    mod.run()
    dur = round(time.time() - t0, 2)
    _log(f"✓ step done: {name}", secs=dur)
    return {"step": name, "seconds": dur, "ok": True}

def _parse_workflow(raw: str) -> List[str]:
    raw = (raw or "").strip()
    if not raw:
        # Varsayılan tam iş akışı
        return ["crawler", "crafter", "styler", "publisher"]
    return [s.strip().lower() for s in raw.split(",") if s.strip()]

def _check_secret(request) -> None:
    want = os.environ.get("CRON_SECRET", "")
    if not want: return
    got = request.headers.get("X-Cron-Token") or request.args.get("key")
    if got != want:
        raise PermissionError("invalid-cron-secret")

# ---- YENİ BÖLÜM: Flask Uygulaması ----
app = Flask(__name__)

@app.route('/')
def run_gonews_endpoint():
    """
    Bu, Cloud Scheduler tarafından çağrılacak olan web endpoint'idir.
    Sizin yazdığınız tüm harika mantığı çalıştırır.
    """
    try:
        _check_secret(request)

        ok, reason = acquire_lock()
        if not ok:
            return jsonify({"ok": False, "reason": reason}), 429

        try:
            data = request.get_json(force=True, silent=True) or {}
            wf_raw = (data.get("workflow")
                      or request.args.get("workflow")
                      or os.environ.get("WORKFLOW", ""))
            steps = _parse_workflow(wf_raw)

            _log("workflow", steps=steps)

            results = []
            for step in steps:
                try:
                    results.append(_run_step(step))
                except Exception as e:
                    _log("step failed", step=step, err=str(e), tb=traceback.format_exc())
                    results.append({"step": step, "ok": False, "error": str(e)})
                    break

            ok_all = all(r.get("ok") for r in results)
            return jsonify({"ok": ok_all, "results": results}), 200 if ok_all else 500

        finally:
            release_lock()

    except PermissionError:
        return jsonify({"ok": False, "error": "forbidden"}), 403
    except Exception as e:
        _log("fatal", err=str(e), tb=traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    # Bu bölüm, Google Cloud'un uygulamayı başlatmak için kullanacağı yerdir.
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))