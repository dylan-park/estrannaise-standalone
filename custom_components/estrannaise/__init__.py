"""Estrannaise HRT Monitor integration for Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, PLATFORMS, PK_PARAMETERS, resolve_model_key
from .coordinator import EstrannaisCoordinator
from .database import EstrannaisDatabase

_LOGGER = logging.getLogger(__name__)

# Valid model keys for the log_dose service (internal PK keys + "patch")
_VALID_SERVICE_MODELS = [
    k for k in PK_PARAMETERS
] + ["patch"]

SERVICE_LOG_DOSE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): str,
        vol.Required("model"): vol.In(_VALID_SERVICE_MODELS),
        vol.Required("dose_mg"): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
        vol.Optional("timestamp"): vol.All(
            vol.Coerce(float), vol.Range(min=1577836800, max=4102444800)
        ),
    }
)

SERVICE_LOG_BLOOD_TEST_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): str,
        vol.Required("level_pg_ml"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Optional("timestamp"): vol.All(
            vol.Coerce(float), vol.Range(min=1577836800, max=4102444800)
        ),
        vol.Optional("notes"): str,
        vol.Optional("on_schedule"): bool,
    }
)

SERVICE_DELETE_DOSE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): str,
        vol.Required("dose_id"): vol.Coerce(int),
    }
)

SERVICE_DELETE_BLOOD_TEST_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): str,
        vol.Required("test_id"): vol.Coerce(int),
    }
)

SERVICE_CLEAR_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): str,
    }
)


def _get_coordinator(
    hass: HomeAssistant, entity_id: str
) -> EstrannaisCoordinator:
    """Resolve coordinator from entity_id."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        raise ValueError(f"Entity not found: {entity_id}")
    config_entry_id = entry.config_entry_id
    if config_entry_id is None:
        raise ValueError(f"No config entry for entity: {entity_id}")
    coordinator = hass.data[DOMAIN].get(config_entry_id)
    if coordinator is None or not isinstance(coordinator, EstrannaisCoordinator):
        raise ValueError(f"No coordinator for config entry: {config_entry_id}")
    return coordinator


async def _refresh_all_coordinators(hass: HomeAssistant) -> None:
    """Refresh all estrannaise coordinators after a data change."""
    for key, val in list(hass.data.get(DOMAIN, {}).items()):
        if isinstance(val, EstrannaisCoordinator):
            try:
                await val.async_request_refresh()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to refresh coordinator %s", key)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Estrannaise from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Register static paths for JS cards (once)
    if "frontend_loaded" not in hass.data[DOMAIN]:
        www_dir = Path(__file__).parent / "www"

        def _scan_js_files() -> list[StaticPathConfig]:
            return [
                StaticPathConfig(
                    f"/estrannaise/{f.name}", str(f), False
                )
                for f in www_dir.iterdir()
                if f.is_file() and f.suffix == ".js"
            ]

        static_configs = await hass.async_add_executor_job(_scan_js_files)
        if static_configs:
            await hass.http.async_register_static_paths(static_configs)
        hass.data[DOMAIN]["frontend_loaded"] = True

    # Serialize entry setup to prevent SQLite "database is locked" errors
    # when multiple entries are created simultaneously (e.g. cycle-fit import)
    if "_setup_lock" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["_setup_lock"] = asyncio.Lock()
    setup_lock: asyncio.Lock = hass.data[DOMAIN]["_setup_lock"]

    async with setup_lock:
        # Open / reuse database
        if "database" not in hass.data[DOMAIN]:
            db_path = Path(hass.config.config_dir) / "estrannaise.db"
            database = EstrannaisDatabase(db_path)
            await database.async_setup()
            hass.data[DOMAIN]["database"] = database
        else:
            database = hass.data[DOMAIN]["database"]

        # Create coordinator (store before first refresh so _get_all_entry_configs works)
        coordinator = EstrannaisCoordinator(hass, entry, database)
        hass.data[DOMAIN][entry.entry_id] = coordinator
        await coordinator.async_config_entry_first_refresh()

    # Forward to entity platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options changes
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Register services (once)
    if "services_registered" not in hass.data[DOMAIN]:
        _register_services(hass)
        hass.data[DOMAIN]["services_registered"] = True

    # Refresh ALL coordinators so existing entries immediately pick up
    # the new entry in their all_configs (otherwise they wait 5 min)
    await _refresh_all_coordinators(hass)

    return True


def _register_services(hass: HomeAssistant) -> None:
    """Register estrannaise services."""

    async def handle_log_dose(call: ServiceCall) -> None:
        coord = _get_coordinator(hass, call.data["entity_id"])
        entry_id = coord.config_entry.entry_id
        model = call.data["model"]
        ts = call.data.get("timestamp", time.time())

        # Resolve "patch" to internal model key
        if model == "patch":
            cfg = coord._get_config()
            resolved = resolve_model_key("E", "patch", cfg["interval_days"])
            model = resolved or "patch tw"

        await coord.database.add_dose(
            config_entry_id=entry_id,
            model=model,
            dose_mg=call.data["dose_mg"],
            timestamp=ts,
            source="manual",
        )
        await _refresh_all_coordinators(hass)

    async def handle_log_blood_test(call: ServiceCall) -> None:
        coord = _get_coordinator(hass, call.data["entity_id"])
        entry_id = coord.config_entry.entry_id
        ts = call.data.get("timestamp", time.time())
        await coord.database.add_blood_test(
            config_entry_id=entry_id,
            level_pg_ml=call.data["level_pg_ml"],
            timestamp=ts,
            notes=call.data.get("notes"),
            on_schedule=call.data.get("on_schedule"),
        )
        await _refresh_all_coordinators(hass)

    async def handle_delete_dose(call: ServiceCall) -> None:
        coord = _get_coordinator(hass, call.data["entity_id"])
        entry_id = coord.config_entry.entry_id
        await coord.database.delete_dose(entry_id, call.data["dose_id"])
        await _refresh_all_coordinators(hass)

    async def handle_delete_blood_test(call: ServiceCall) -> None:
        coord = _get_coordinator(hass, call.data["entity_id"])
        entry_id = coord.config_entry.entry_id
        await coord.database.delete_blood_test(entry_id, call.data["test_id"])
        await _refresh_all_coordinators(hass)

    async def handle_clear_data(call: ServiceCall) -> None:
        coord = _get_coordinator(hass, call.data["entity_id"])
        await coord.database.clear_all_data()
        await _refresh_all_coordinators(hass)

    hass.services.async_register(
        DOMAIN, "log_dose", handle_log_dose, schema=SERVICE_LOG_DOSE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "log_blood_test",
        handle_log_blood_test,
        schema=SERVICE_LOG_BLOOD_TEST_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "delete_dose",
        handle_delete_dose,
        schema=SERVICE_DELETE_DOSE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "delete_blood_test",
        handle_delete_blood_test,
        schema=SERVICE_DELETE_BLOOD_TEST_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "clear_data",
        handle_clear_data,
        schema=SERVICE_CLEAR_DATA_SCHEMA,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # Close database if no more coordinator entries remain
    remaining = [
        eid
        for eid, val in hass.data[DOMAIN].items()
        if isinstance(val, EstrannaisCoordinator)
    ]
    if not remaining and "database" in hass.data[DOMAIN]:
        db: EstrannaisDatabase = hass.data[DOMAIN].pop("database")
        await db.async_close()
    elif remaining:
        # Refresh remaining coordinators so they drop the removed entry
        await _refresh_all_coordinators(hass)

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
