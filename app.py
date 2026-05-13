"""
KronViva – spelstatistik onsdagar (handicap + scratch).
Uppdateras automatiskt varje fredag kl. 06:00 (tidzon Europa/Stockholm på servern).
"""

import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from scraper import fetch_all_results, load_birthdates, load_results, save_results

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_data_dir = Path("/data") if Path("/data").exists() else Path("data")
DATA_PATH = str(_data_dir / "results.json")
REFRESH_SECRET = os.environ.get("REFRESH_SECRET", "bridge2026")

_cache: dict | None = None
_initial_fetch_lock = threading.Lock()
_initial_fetch_started = False


def empty_payload() -> dict:
    """Tom cache tills första hämtningen från SVB är klar (behövs för t.ex. Render-start)."""
    return {
        "players": [],
        "meta": {
            "last_updated": None,
            "total_competitions": 0,
            "wednesday_competitions": 0,
            "period_start": "2026-01-01",
            "period_end": date.today().isoformat(),
            "club": "KronViva",
        },
        "competitions": [],
    }


def ensure_background_fetch_once() -> None:
    global _initial_fetch_started
    with _initial_fetch_lock:
        if _initial_fetch_started:
            return
        _initial_fetch_started = True
        threading.Thread(target=refresh_data, daemon=True).start()


def refresh_data() -> None:
    global _cache
    logger.info("Startar datauppdatering (KronViva)…")
    try:
        birthdates = load_birthdates()
        results = fetch_all_results(birthdates)
        save_results(results, DATA_PATH)
        _cache = results
        logger.info(
            "Datauppdatering klar – %s tävlingar, %s spelare.",
            results["meta"]["total_competitions"],
            len(results["players"]),
        )
    except Exception as e:
        logger.error("Fel vid datauppdatering: %s", e)


def get_data() -> dict:
    """
    Vid tom disk kör första hämtning i bakgrunden (undviker att Render health check
    timeout:ar medan många tävlingar scrapas sekventellt).
    """
    global _cache
    if _cache is None:
        _cache = load_results(DATA_PATH)
    if _cache is None:
        logger.info("Ingen lokal data – hämtar från Svenska Bridgeförbundet i bakgrunden…")
        _cache = empty_payload()
        ensure_background_fetch_once()
    return _cache or {}


scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_data()
    tz = os.environ.get("REFRESH_CRON_TIMEZONE", "Europe/Stockholm")
    h = int(os.environ.get("REFRESH_CRON_HOUR", "6"))
    m = int(os.environ.get("REFRESH_CRON_MINUTE", "0"))
    scheduler.add_job(
        refresh_data,
        trigger=CronTrigger(
            day_of_week="fri",
            hour=h,
            minute=m,
            timezone=tz,
        ),
        id="weekly_refresh",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Schemaläggare startad – automatisk hämtning varje fredag kl. %s:%02d (%s)",
        h,
        m,
        tz,
    )
    yield
    scheduler.shutdown()


app = FastAPI(
    title="KronViva – Spelstatistik",
    lifespan=lifespan,
)


@app.get("/api/results")
def api_results():
    data = get_data()
    return JSONResponse(data.get("players", []))


@app.get("/api/meta")
def api_meta():
    data = get_data()
    return JSONResponse(data.get("meta", {}))


@app.get("/api/competitions")
def api_competitions():
    data = get_data()
    return JSONResponse(data.get("competitions", []))


@app.post("/api/refresh")
def api_refresh(secret: str = ""):
    if secret != REFRESH_SECRET:
        raise HTTPException(status_code=403, detail="Felaktig nyckel")
    threading.Thread(target=refresh_data, daemon=True).start()
    return {"status": "Uppdatering startad i bakgrunden"}


@app.post("/api/reload")
def api_reload(secret: str = ""):
    """Laddar om cache från lokal results.json utan att skrapa om SVB."""
    global _cache
    if secret != REFRESH_SECRET:
        raise HTTPException(status_code=403, detail="Felaktig nyckel")
    data = load_results(DATA_PATH)
    if data is None:
        raise HTTPException(status_code=404, detail="Ingen lokal data att ladda")
    _cache = data
    return {
        "status": "Cache omladdad från fil",
        "competitions": data["meta"].get("total_competitions", 0),
        "players": len(data.get("players", [])),
    }


@app.get("/api/names")
def api_names():
    data = get_data()
    names = sorted(p["name"] for p in data.get("players", []))
    return JSONResponse(names)


@app.get("/api/health")
def api_health():
    data = get_data()
    meta = data.get("meta", {})
    return {
        "status": "ok",
        "last_updated": meta.get("last_updated"),
        "players": len(data.get("players", [])),
        "competitions": meta.get("total_competitions", 0),
    }


static_dir = Path("static")
if static_dir.exists():
    app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
