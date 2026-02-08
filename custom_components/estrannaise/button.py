"""Button platform for Estrannaise HRT Monitor."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import EstrannaisCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Estrannaise button entities."""
    # Subsidiary entries don't create button entities
    if entry.data.get("subsidiary", False):
        return
    coordinator: EstrannaisCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EstrannaisResetButton(coordinator, entry)])


class EstrannaisResetButton(ButtonEntity):
    """Button to reset all estrannaise dose and blood test data."""

    _attr_has_entity_name = True
    _attr_name = "Reset All Data"
    _attr_icon = "mdi:delete-sweep"

    def __init__(
        self,
        coordinator: EstrannaisCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the button."""
        self._coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_reset_data"

    @property
    def device_info(self):
        """Return device info (none — standalone entity)."""
        return None

    async def async_press(self) -> None:
        """Handle the button press — clear all data."""
        database = self.hass.data.get(DOMAIN, {}).get("database")
        if database:
            await database.clear_all_data()
            _LOGGER.info("Estrannaise: All dose and blood test data cleared")
            # Refresh all coordinators
            for val in self.hass.data.get(DOMAIN, {}).values():
                if isinstance(val, EstrannaisCoordinator):
                    await val.async_request_refresh()
