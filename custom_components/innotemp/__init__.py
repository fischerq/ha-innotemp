"""The Innotemp Heating Controller integration."""

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InnotempApiClient
from .coordinator import InnotempDataUpdateCoordinator
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Innotemp Heating Controller from a config entry."""

    hass.data.setdefault(DOMAIN, {})  # type: ignore[no-untyped-call]

    host = entry.data["host"]
    username = entry.data["username"]
    password = entry.data["password"]

    session = async_get_clientsession(hass)
    _LOGGER.debug(
        f"Innotemp: Initializing API client. Session type: {type(session)}, Host: {host}"
    )
    api_client = InnotempApiClient(session, host, username, password)

    # Login and fetch initial configuration
    try:
        await api_client.async_login()
        config_data = await api_client.async_get_config()
        if config_data is None:
            _LOGGER.error(
                "Failed to fetch configuration from Innotemp device (config_data is None). Aborting setup."
            )
            return False
        _LOGGER.debug("Initial configuration fetched: %s", config_data)
    except Exception as ex:
        _LOGGER.error("Failed to connect and fetch initial config: %s", ex)
        return False

    coordinator = InnotempDataUpdateCoordinator(hass, _LOGGER, api_client)

    # Pass the coordinator's async_set_updated_data as the callback for SSE
    coordinator.sse_task = hass.async_create_task(
        api_client.async_sse_connect(coordinator.async_set_updated_data)
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api_client,
        "coordinator": coordinator,
        "config": config_data,  # Store config data for entity creation
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await coordinator.async_config_entry_first_refresh()

    return True
