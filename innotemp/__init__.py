"""The Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Home Assistant integration from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    # Forward the setup to your platforms (e.g., light, sensor, switch)
    # await hass.config_entries.async_forward_entry_setups(entry, ["light", "sensor", "switch"])

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["light", "sensor", "switch"])
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok