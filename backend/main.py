"""
Estrannaise Standalone - FastAPI backend
Serves the REST API used by the frontend in place of Home Assistant.
"""

from __future__ import annotations

import math
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import AppConfig, load_config, save_config
from .database import EstrannaisDatabase
from .pk import (
    ESTERS,
    MENSTRUAL_CYCLE_DATA,
    METHODS,
    PATCH_WEAR_DAYS,
    PK_PARAMETERS,
    compute_e2_at_time,
    compute_suggested_regimen,
)
from .scheduler import persist_past_auto_doses

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = Path("/data")
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "estrannaise.db"
CONFIG_PATH = DATA_DIR / "config.json"

# ── App lifecycle ─────────────────────────────────────────────────────────────

db: EstrannaisDatabase | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    db = EstrannaisDatabase(DB_PATH)
    await db.async_setup()
    yield
    if db:
        await db.close()


app = FastAPI(title="Estrannaise Standalone", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pydantic models ───────────────────────────────────────────────────────────


class DoseLog(BaseModel):
    model: str
    dose_mg: float
    timestamp: float | None = None


class BloodTestLog(BaseModel):
    level_pg_ml: float
    timestamp: float | None = None
    notes: str | None = None
    on_schedule: bool | None = None


class ConfigUpdate(BaseModel):
    regimens: list[dict[str, Any]]
    units: str = "pg/mL"


# ── State builder ─────────────────────────────────────────────────────────────


async def _build_state(config: AppConfig) -> dict[str, Any]:
    """Compute the full state dict that mirrors HA sensor attributes."""
    now = time.time()
    all_configs = config.regimens

    # Persist any past automatic doses (backfill + catch-up)
    for reg in all_configs:
        await persist_past_auto_doses(db, reg, now)

    all_doses = await db.get_all_doses()
    all_blood_tests = await db.get_all_blood_tests()

    # Scaling factor from blood tests
    scaling_factor, scaling_variance = await db.compute_scaling_factor(
        all_doses, all_configs
    )

    # Unit conversion factor
    cf = 3.6713 if config.units == "pmol/L" else 1.0
    current_e2 = compute_e2_at_time(now, all_doses, scaling_factor) * cf

    # Suggested / cycle-fit regimen (first auto_regimen entry wins)
    suggested_regimen = None
    cycle_fit_regimen = None
    for reg in all_configs:
        if reg.get("auto_regimen"):
            suggested_regimen = compute_suggested_regimen(
                reg["ester"], reg["method"], reg.get("target_type", "target_range")
            )
            if suggested_regimen and "schedules" in suggested_regimen:
                cycle_fit_regimen = suggested_regimen
                suggested_regimen = None
            break

    # Baseline E2 zero-state handling (mirrors coordinator.py logic exactly)
    baseline_e2 = 0.0
    baseline_test_ts = 0.0
    baseline_candidates = [bt for bt in all_blood_tests if not bt.get("on_schedule")]
    if baseline_candidates:
        all_negligible = all(
            compute_e2_at_time(bt["timestamp"], all_doses) < 1.0
            for bt in baseline_candidates
        )
        if all_negligible:
            latest = max(baseline_candidates, key=lambda t: t["timestamp"])
            baseline_e2 = latest["level_pg_ml"]
            baseline_test_ts = latest["timestamp"]
            age_days = (now - baseline_test_ts) / 86400.0
            baseline_decayed = baseline_e2 * math.exp(-0.02 * max(0, age_days))
            scaling_factor = 1.0
            scaling_variance = 0.0
            current_e2 += baseline_decayed * cf

    return {
        "current_e2": round(current_e2, 1),
        "units": config.units,
        "doses": all_doses,
        "blood_tests": all_blood_tests,
        "scaling_factor": scaling_factor,
        "scaling_variance": scaling_variance,
        "pk_parameters": PK_PARAMETERS,
        "patch_wear_days": PATCH_WEAR_DAYS,
        "menstrual_cycle_data": MENSTRUAL_CYCLE_DATA,
        "esters": ESTERS,
        "methods": METHODS,
        "all_configs": all_configs,
        "target_range": {"lower": 100, "upper": 200},
        "suggested_regimen": suggested_regimen,
        "cycle_fit_regimen": cycle_fit_regimen,
        "baseline_e2": round(baseline_e2, 2),
        "baseline_test_ts": baseline_test_ts,
    }


# ── API routes ────────────────────────────────────────────────────────────────


@app.get("/api/state")
async def get_state():
    """Full computed state (mirrors HA sensor attributes)."""
    config = load_config(CONFIG_PATH)
    return await _build_state(config)


@app.get("/api/config")
async def get_config():
    """Current app configuration."""
    return load_config(CONFIG_PATH).model_dump()


@app.post("/api/config")
async def update_config(body: ConfigUpdate):
    """Update app configuration. Immediately triggers backfill for any regimens that need it."""
    config = load_config(CONFIG_PATH)
    config.regimens = body.regimens
    config.units = body.units
    save_config(CONFIG_PATH, config)
    # Run persist immediately so backfill takes effect right after saving
    now = time.time()
    for reg in config.regimens:
        await persist_past_auto_doses(db, reg, now)
    return {"ok": True}


@app.post("/api/doses")
async def log_dose(body: DoseLog):
    """Log a dose."""
    ts = body.timestamp or time.time()
    if body.model not in PK_PARAMETERS:
        raise HTTPException(status_code=400, detail=f"Unknown model key: {body.model}")
    if body.dose_mg <= 0:
        raise HTTPException(status_code=400, detail="dose_mg must be > 0")
    dose_id = await db.add_dose(
        model=body.model,
        dose_mg=body.dose_mg,
        timestamp=ts,
        source="manual",
    )
    return {"ok": True, "id": dose_id}


@app.delete("/api/doses/{dose_id}")
async def delete_dose(dose_id: int):
    """Delete a dose by ID."""
    deleted = await db.delete_dose(dose_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Dose not found")
    return {"ok": True}


@app.post("/api/blood-tests")
async def log_blood_test(body: BloodTestLog):
    """Log a blood test result."""
    ts = body.timestamp or time.time()
    if body.level_pg_ml < 0:
        raise HTTPException(status_code=400, detail="level_pg_ml must be >= 0")
    test_id = await db.add_blood_test(
        level_pg_ml=body.level_pg_ml,
        timestamp=ts,
        notes=body.notes,
        on_schedule=body.on_schedule,
    )
    return {"ok": True, "id": test_id}


@app.delete("/api/blood-tests/{test_id}")
async def delete_blood_test(test_id: int):
    """Delete a blood test by ID."""
    deleted = await db.delete_blood_test(test_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Blood test not found")
    return {"ok": True}


@app.delete("/api/data")
async def clear_all_data():
    """Delete all doses and blood tests (irreversible)."""
    await db.clear_all()
    return {"ok": True}


# ── Static frontend ───────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


@app.get("/")
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str = ""):
    """Serve the SPA for all non-API routes."""
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"error": "Frontend not found"}
