"""The Innotemp Heating Controller integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InnotempApiClient
from .coordinator import InnotempDataUpdateCoordinator
from .api_parser import extract_initial_states
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER]

_LOGGER = logging.getLogger(__name__)
# _LOGGER.setLevel(logging.WARNING) # Keep default level, can be changed in HA config


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Innotemp Heating Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data["host"]

    # Validate host format
    invalid_host_messages = {
        (not host): "Stored Innotemp configuration has an empty host.",
        (host.lower() in ["http", "https"]): f"Stored Innotemp configuration has an invalid host: '{host}'. This should be an IP address or hostname.",
        ("://" in host): f"Stored Innotemp configuration host '{host}' contains '://'. Please use just the IP address or hostname.",
        (len(host) < 3): f"Stored Innotemp configuration host '{host}' is too short or invalid.",
    }

    for condition, message in invalid_host_messages.items():
        if condition:
            _LOGGER.error(f"{message} Please remove and re-add the integration.")
            return False

    username = entry.data["username"]
    password = entry.data["password"]

    session = async_get_clientsession(hass)
    api_client = InnotempApiClient(session, host, username, password)

    try:
        _LOGGER.debug("Attempting to login and fetch initial configuration from Innotemp API.")
        await api_client.async_login()
        config_data = await api_client.async_get_config()
        if config_data is None:
            _LOGGER.error("Failed to fetch configuration from Innotemp device. Aborting setup.")
            return False
        _LOGGER.debug("Initial configuration fetched successfully.")
    except Exception as ex:
        _LOGGER.error(f"Failed to connect and fetch initial config: {ex}")
        return False

    coordinator = InnotempDataUpdateCoordinator(hass, _LOGGER, api_client)

    if config_data:
        initial_states = extract_initial_states(config_data)
        if initial_states:
            coordinator.async_set_updated_data(initial_states)
            _LOGGER.debug(f"Set {len(initial_states)} initial states on coordinator.")
        else:
            _LOGGER.debug("No initial states extracted from config_data.")
    else: # Should not happen if previous check passed, but defensive
        _LOGGER.warning("config_data is None after fetch, cannot set initial states.")


    hass.data[DOMAIN][entry.entry_id] = {
        "api": api_client,
        "coordinator": coordinator,
        "config": config_data, # Store original config_data for entity discovery
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_config_entry_first_refresh() # Standard practice

    return True
