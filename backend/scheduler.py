"""Auto-dose scheduling — ported from coordinator.py, HA dependencies removed."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from .pk import compute_suggested_regimen, resolve_model_key

if TYPE_CHECKING:
    from .database import EstrannaisDatabase

DEFAULT_UPDATE_INTERVAL = 300  # seconds
MODE_AUTOMATIC = "automatic"
MODE_BOTH = "both"
BACKFILL_DAYS = 90.0


def _parse_dose_time(dose_time: str) -> tuple[int, int]:
    try:
        parts = dose_time.split(":")
        return max(0, min(23, int(parts[0]))), max(
            0, min(59, int(parts[1]) if len(parts) > 1 else 0)
        )
    except (ValueError, IndexError):
        return 8, 0


def _compute_anchor(config: dict, now: float, interval_sec: float) -> float:
    """
    Return the timestamp of the most recent past dose at or before `now`.

    Priority:
    1. start_date (YYYY-MM-DD) + dose_time → exact user-specified first dose,
       then walk forward by interval_sec to find the most recent past occurrence.
    2. Fallback: today's dose time, stepped back so the schedule is aligned
       to today regardless of when it was configured.
    """
    hour, minute = _parse_dose_time(config.get("dose_time", "08:00"))
    start_date_str = config.get("start_date", "").strip()

    if start_date_str:
        try:
            sd = datetime.date.fromisoformat(start_date_str)
            first_dose_dt = datetime.datetime(
                sd.year, sd.month, sd.day, hour, minute, 0
            )
            first_dose_ts = first_dose_dt.timestamp()

            if first_dose_ts > now:
                # Start date is in the future — the anchor is before any dose
                return first_dose_ts - interval_sec

            # Walk forward from first dose to find the most recent dose <= now
            t = first_dose_ts
            last = t
            while t <= now:
                last = t
                t += interval_sec
            return last
        except (ValueError, TypeError):
            pass  # bad date string — fall through to default

    # Default anchor: today's dose time (or yesterday's if today's is in the future)
    today_dt = datetime.datetime.fromtimestamp(now)
    today_dose = today_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    today_dose_ts = today_dose.timestamp()

    if today_dose_ts <= now:
        return today_dose_ts
    else:
        return today_dose_ts - interval_sec


def generate_auto_doses(
    config: dict, now: float, lookahead_days: float = 90.0
) -> list[dict]:
    """Generate future automatic dose records for a single regimen config."""
    if config.get("mode", "manual") not in (MODE_AUTOMATIC, MODE_BOTH):
        return []

    ester = config.get("ester", "")
    method = config.get("method", "")
    dose_mg = config.get("dose_mg", 0.0)
    interval_days = config.get("interval_days", 7.0)
    if interval_days <= 0:
        return []

    future_limit = now + lookahead_days * 86400.0

    if config.get("auto_regimen"):
        suggested = compute_suggested_regimen(
            ester, method, config.get("target_type", "target_range")
        )
        if suggested and "schedules" in suggested:
            doses: list[dict] = []
            for sch in suggested["schedules"]:
                sch_interval_sec = sch["interval_days"] * 86400.0
                if sch_interval_sec <= 0:
                    continue
                anchor = _compute_anchor(config, now, sch_interval_sec)
                t = anchor + sch_interval_sec
                while t <= future_limit:
                    doses.append(
                        {
                            "id": None,
                            "timestamp": t,
                            "model": sch["model_key"],
                            "dose_mg": sch["dose_mg"],
                            "source": "automatic",
                        }
                    )
                    t += sch_interval_sec
            return doses[:1000]
        elif suggested:
            dose_mg = suggested["dose_mg"]
            interval_days = suggested["interval_days"]

    model_key = resolve_model_key(ester, method, interval_days)
    if not model_key:
        return []

    interval_sec = interval_days * 86400.0
    anchor = _compute_anchor(config, now, interval_sec)

    doses = []
    t = anchor + interval_sec
    while t <= future_limit:
        doses.append(
            {
                "id": None,
                "timestamp": t,
                "model": model_key,
                "dose_mg": dose_mg,
                "source": "automatic",
            }
        )
        t += interval_sec
    return doses[:1000]


async def persist_past_auto_doses(
    db: "EstrannaisDatabase", config: dict, now: float
) -> None:
    """
    Write past scheduled doses to the database.

    - Always runs for automatic/both modes.
    - backfill_doses=True: covers 90 days back from now.
    - backfill_doses=False: catches up doses missed since last refresh
      (up to 2 full intervals back, so a restart never loses doses).
    """
    if config.get("mode", "manual") not in (MODE_AUTOMATIC, MODE_BOTH):
        return

    ester = config.get("ester", "")
    method = config.get("method", "")

    # Resolve what schedule(s) to generate
    schedules: list[dict] = []
    if config.get("auto_regimen"):
        suggested = compute_suggested_regimen(
            ester, method, config.get("target_type", "target_range")
        )
        if suggested and "schedules" in suggested:
            schedules = suggested["schedules"]
        elif suggested:
            schedules = [
                {
                    "dose_mg": suggested["dose_mg"],
                    "interval_days": suggested["interval_days"],
                    "model_key": suggested.get("model_key", ""),
                }
            ]

    if not schedules:
        model_key = resolve_model_key(ester, method, config.get("interval_days", 7.0))
        if not model_key:
            return
        schedules = [
            {
                "dose_mg": config.get("dose_mg", 0.0),
                "interval_days": config.get("interval_days", 7.0),
                "model_key": model_key,
            }
        ]

    existing_ts = await db.get_auto_dose_timestamps()
    backfill = config.get("backfill_doses", False)

    for sch in schedules:
        model_key = sch.get("model_key") or resolve_model_key(
            ester, method, sch["interval_days"]
        )
        if not model_key:
            continue
        interval_sec = sch["interval_days"] * 86400.0
        if interval_sec <= 0:
            continue

        # How far back to look
        if backfill:
            lookback_ts = now - BACKFILL_DAYS * 86400.0
        else:
            # Catch up any doses missed since last refresh.
            # Use 2 full intervals so a multi-day restart gap is healed.
            lookback_ts = now - max(DEFAULT_UPDATE_INTERVAL * 2, interval_sec * 2)

        # Find anchor (most recent past dose)
        anchor = _compute_anchor(config, now, interval_sec)

        # Walk backward from anchor until we're past the lookback window
        t = anchor
        while t > lookback_ts:
            t -= interval_sec
        t += interval_sec  # step back into the window

        # Write any missing doses up to (but not past) now
        while t <= now:
            if t >= lookback_ts and not any(abs(t - ets) < 60 for ets in existing_ts):
                await db.add_dose(
                    model=model_key,
                    dose_mg=sch["dose_mg"],
                    timestamp=t,
                    source="automatic",
                )
                existing_ts.add(t)
            t += interval_sec
