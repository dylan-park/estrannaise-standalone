"""Calendar platform for Estrannaise HRT Monitor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_ENABLE_CALENDAR,
    DEFAULT_ENABLE_CALENDAR,
    DOMAIN,
    ESTERS,
    METHODS,
    MODE_AUTOMATIC,
    MODE_BOTH,
    compute_suggested_regimen,
    get_dose_units,
)
from .coordinator import EstrannaisCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Estrannaise calendar entity."""
    # Subsidiary entries don't create calendar entities
    if entry.data.get("subsidiary", False):
        return
    enabled = entry.data.get(
        CONF_ENABLE_CALENDAR,
        entry.options.get(CONF_ENABLE_CALENDAR, DEFAULT_ENABLE_CALENDAR),
    )
    if not enabled:
        return

    coordinator: EstrannaisCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EstrannaisCalendar(coordinator, entry)])


class EstrannaisCalendar(
    CoordinatorEntity[EstrannaisCoordinator], CalendarEntity
):
    """Calendar entity showing dose schedule and past doses."""

    _attr_has_entity_name = True
    _attr_name = "Dose Schedule"

    def __init__(
        self,
        coordinator: EstrannaisCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the calendar entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_dose_calendar"
        self._entry = entry

    @property
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming dose event."""
        events = self._build_events()
        if not events:
            return None

        now = datetime.now(timezone.utc)
        future = [e for e in events if e.end >= now]
        if future:
            future.sort(key=lambda e: e.start)
            return future[0]

        events.sort(key=lambda e: e.start, reverse=True)
        return events[0]

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return events in the given date range."""
        events = self._build_events()
        return [
            e
            for e in events
            if e.end > start_date and e.start < end_date
        ]

    def _build_events(self) -> list[CalendarEvent]:
        """Build calendar events from dose data and schedule."""
        if not self.coordinator.data:
            return []

        # Collect raw dose records: (datetime, ester_key, dose_mg, label, desc)
        raw_doses: list[dict] = []
        data = self.coordinator.data
        all_configs = data.get("all_configs", [])

        # Past manual doses from database (all entries)
        for dose in data.get("doses", []):
            ts = dose.get("timestamp")
            if not ts:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            model = dose.get("model", "")
            mg = dose.get("dose_mg", 0)
            # Extract ester key from model (e.g. "EEn im" → "EEn")
            ester_key = model.split(" ")[0] if model else ""
            ester_name = ESTERS.get(ester_key, model)
            raw_doses.append({
                "dt": dt, "ester": ester_key, "dose_mg": mg,
                "label": f"{mg}mg {ester_name}",
                "desc": f"Logged dose: {mg}mg {model}\nSource: {dose.get('source', 'manual')}",
            })

        # Future scheduled doses from ALL entries
        for cfg in all_configs:
            mode = cfg.get("mode", "manual")
            if mode not in (MODE_AUTOMATIC, MODE_BOTH):
                continue

            ester = cfg.get("ester", "")
            method = cfg.get("method", "im")
            ester_name = ESTERS.get(ester, ester)
            method_name = METHODS.get(method, method)
            dose_unit = get_dose_units(method)

            dose_time_str = cfg.get("dose_time", "08:00")
            try:
                parts = dose_time_str.split(":")
                hour = int(parts[0])
                minute = int(parts[1]) if len(parts) > 1 else 0
            except (ValueError, IndexError):
                hour, minute = 8, 0
            hour = max(0, min(23, hour))
            minute = max(0, min(59, minute))

            now = datetime.now(timezone.utc)
            local_tz = dt_util.DEFAULT_TIME_ZONE
            now_local = now.astimezone(local_tz)

            # Compute today's dose time in local timezone (→ UTC timestamp)
            today_dose_local = now_local.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )

            schedules = None
            if cfg.get("auto_regimen", False):
                target_type = cfg.get("target_type", "target_range")
                suggested = compute_suggested_regimen(ester, method, target_type)
                if suggested and "schedules" in suggested:
                    schedules = suggested["schedules"]
                elif suggested:
                    schedules = [{
                        "dose_mg": suggested["dose_mg"],
                        "interval_days": suggested["interval_days"],
                        "phase_days": 0,
                    }]

            if not schedules:
                schedules = [{
                    "dose_mg": cfg.get("dose_mg", 0),
                    "interval_days": cfg.get("interval_days", 7),
                    "phase_days": cfg.get("phase_days", 0),
                }]

            for sch in schedules:
                dose_mg = sch["dose_mg"]
                interval_days_val = sch["interval_days"]
                interval = timedelta(days=interval_days_val)
                if interval.total_seconds() <= 0:
                    continue
                phase_days_val = sch.get("phase_days", 0)
                if phase_days_val and phase_days_val > 0:
                    # Phase-based anchoring (matches coordinator logic)
                    epoch_day_local = int(now_local.replace(
                        hour=0, minute=0, second=0, microsecond=0
                    ).timestamp() // 86400)
                    cycle_day_now = epoch_day_local % 28
                    days_back = (cycle_day_now - int(phase_days_val)) % 28
                    anchor_ts = today_dose_local.timestamp() - days_back * 86400.0
                    t = anchor_ts
                    interval_sec = interval_days_val * 86400.0
                    now_ts = now.timestamp()
                    while t <= now_ts:
                        t += interval_sec
                    next_dose = datetime.fromtimestamp(t, tz=timezone.utc)
                else:
                    today_dose_utc = today_dose_local.astimezone(timezone.utc)
                    next_dose = today_dose_utc if today_dose_utc > now else today_dose_utc + interval
                while next_dose < now + timedelta(days=30):
                    raw_doses.append({
                        "dt": next_dose, "ester": ester, "dose_mg": dose_mg,
                        "label": f"{dose_mg}{dose_unit} {ester_name} ({method_name})",
                        "desc": f"Scheduled dose: {dose_mg}{dose_unit} {ester_name} ({method_name})",
                    })
                    next_dose += interval

        # Merge coincident doses of the same ester (within 1 hour)
        raw_doses.sort(key=lambda d: (d["dt"], d["ester"]))
        merged_doses: list[dict] = []
        for rd in raw_doses:
            prev = merged_doses[-1] if merged_doses else None
            if (prev and prev["ester"] == rd["ester"]
                    and abs((rd["dt"] - prev["dt"]).total_seconds()) < 3600):
                prev["dose_mg"] += rd["dose_mg"]
                ester_name = ESTERS.get(prev["ester"], prev["ester"])
                prev["label"] = f"{prev['dose_mg']}mg {ester_name}"
                prev["desc"] = f"{prev['desc']}\n+ {rd['desc']}"
            else:
                merged_doses.append({**rd})

        # Convert to CalendarEvents
        events: list[CalendarEvent] = []
        for md in merged_doses:
            events.append(CalendarEvent(
                summary=f"E2 Dose: {md['label']}",
                start=md["dt"],
                end=md["dt"] + timedelta(minutes=15),
                description=md["desc"],
            ))

        return events
