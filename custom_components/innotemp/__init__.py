"""The Innotemp Heating Controller integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InnotempApiClient
from .coordinator import InnotempDataUpdateCoordinator
from .const import DOMAIN
from .api_parser import extract_initial_states

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER]

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.WARNING)  # Changed logger level to warning


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Innotemp Heating Controller from a config entry."""

    hass.data.setdefault(DOMAIN, {})  # type: ignore[no-untyped-call]

    host = entry.data["host"]

    # Specific check for known invalid host formats from stored config
    invalid_host_detected = False
    if not host:
        _LOGGER.error(
            "Stored Innotemp configuration has an empty host. "
            "Please remove and re-add the integration with a valid host."
        )
        invalid_host_detected = True
    elif host.lower() in ["http", "https"]:
        _LOGGER.error(
            f"Stored Innotemp configuration has an invalid host: '{host}'. "
            "This should be an IP address or hostname, not 'http' or 'https'. "
            "Please remove and re-add the integration."
        )
        invalid_host_detected = True
    elif "://" in host:
        _LOGGER.error(
            f"Stored Innotemp configuration host '{host}' contains '://'. "
            "Please use just the IP address or hostname. "
            "Please remove and re-add the integration."
        )
        invalid_host_detected = True
    elif len(host) < 3:  # Basic sanity check
        _LOGGER.error(
            f"Stored Innotemp configuration host '{host}' is too short or invalid. "
            "Please remove and re-add the integration with a valid host."
        )
        invalid_host_detected = True

    if invalid_host_detected:
        return False  # Abort setup early

    username = entry.data["username"]
    password = entry.data["password"]

    session = async_get_clientsession(hass)
    _LOGGER.debug(
        f"Innotemp: Initializing API client. Session type: {type(session)}, Host: {host}"
    )
    api_client = InnotempApiClient(session, host, username, password)

    # Login and fetch initial configuration
    try:
        _LOGGER.debug(
            "Attempting to login to Innotemp API with host: %s, username: %s, password_provided: %s",
            host,
            username,
            bool(password),
        )
        await api_client.async_login()
        _LOGGER.debug("Login successful.")
        _LOGGER.debug("Attempting to fetch initial configuration.")
        config_data = await api_client.async_get_config()
        _LOGGER.debug("Configuration fetching complete.")
        if config_data is None:
            _LOGGER.error(
                "Failed to fetch configuration from Innotemp device (config_data is None). Aborting setup."
            )
            return False
        _LOGGER.debug(f"Fetched initial config data: {config_data}")
        _LOGGER.debug("Initial configuration fetched: %s", config_data)
    except Exception as ex:
        _LOGGER.error("Failed to connect and fetch initial config: %s", ex)
        return False

    coordinator = InnotempDataUpdateCoordinator(hass, _LOGGER, api_client)

    # Extract initial states from the detailed config_data and set it on the coordinator
    if config_data:
        _LOGGER.debug(
            "Processing config_data to extract initial states for coordinator."
        )
        initial_states = extract_initial_states(config_data)
        if initial_states:
            _LOGGER.debug(
                f"Setting {len(initial_states)} initial states on coordinator."
            )
            coordinator.async_set_updated_data(initial_states)
        else:
            _LOGGER.debug("No initial states extracted from config_data.")
    else:
        _LOGGER.warning(
            "config_data is None, cannot set initial states on coordinator."
        )

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api_client,
        "coordinator": coordinator,
        "config": config_data,  # Still store original config_data for entity discovery if needed
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # This call might now be partially redundant if initial_states were set,
    # but it's standard practice and handles coordinator lifecycle.
    # If _async_update_data is a no-op (as it is for SSE), this won't fetch again.
    await coordinator.async_config_entry_first_refresh()

    return True
