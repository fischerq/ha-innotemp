"""Config flow for Innotemp Heating Controller."""

import asyncio
import logging
import voluptuous as vol

from homeassistant import config_entries, core
from homeassistant.const import Platform
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .api import InnotempApiClient
from .coordinator import InnotempDataUpdateCoordinator
from . import PLATFORMS  # Import PLATFORMS from __init__.py

_LOGGER = logging.getLogger(__name__)


class InnotempConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Innotemp Heating Controller."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        def _validate_host_input(host_input: str) -> str:
            if not host_input:
                raise vol.Invalid("Host cannot be empty.")
            if host_input.lower() in ["http", "https"]:
                raise vol.Invalid(
                    "Hostname cannot be 'http' or 'https'. Enter a valid IP address or hostname."
                )
            if "://" in host_input:
                raise vol.Invalid(
                    "Hostname should not include '://'. Enter just the address."
                )
            if (
                len(host_input) < 3
            ):  # Basic length check, e.g., "a.b" is too short for a valid TLD host
                raise vol.Invalid("Hostname is too short or invalid format.")
            return host_input

        data_schema = vol.Schema(
            {
                vol.Required("host"): str,  # _validate_host_input,
                vol.Required("username"): str,
                vol.Required("password"): str,
            }
        )

        if user_input is not None:
            # Basic validation: Try to connect
            session = async_get_clientsession(self.hass)
            api_client = InnotempApiClient(
                session,
                user_input["host"],
                user_input["username"],
                user_input["password"],
            )
            try:
                await api_client.async_login()
                # If login is successful, create the entry
                return self.async_create_entry(
                    title="Innotemp Heating Controller", data=user_input
                )
            except Exception as ex:
                _LOGGER.error("Failed to connect to Innotemp: %s", ex)
                errors["base"] = "cannot_connect"
                # Show an error form if connection fails
                return self.async_show_form(
                    step_id="user",
                    data_schema=data_schema,
                    errors=errors,
                )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )


async def async_setup_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Set up Innotemp Heating Controller from a config entry."""

    hass.data.setdefault(DOMAIN, {})

    host = entry.data["host"]
    username = entry.data["username"]
    password = entry.data["password"]

    session = async_get_clientsession(hass)
    api_client = InnotempApiClient(session, host, username, password)

    # Login and fetch initial configuration
    try:
        await api_client.async_login()
        config_data = await api_client.async_get_config()
        _LOGGER.debug("Initial configuration fetched: %s", config_data)
    except Exception as ex:
        _LOGGER.error("Failed to connect and fetch initial config: %s", ex)
        return False

    # Correct instantiation of the coordinator, similar to __init__.py
    # Use the module-level _LOGGER for consistency
    coordinator = InnotempDataUpdateCoordinator(hass, _LOGGER, api_client)

    # Pass the coordinator's async_set_updated_data as the callback for SSE
    # It's possible this whole async_setup_entry in config_flow.py is problematic
    # if it races with or duplicates the one in __init__.py.
    _LOGGER.debug(
        "Config_flow.py: Setting up SSE connection via its async_setup_entry."
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api_client,
        "coordinator": coordinator,
        "config": config_data,  # Store config data for entity creation
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(
    hass: core.HomeAssistant, entry: config_entries.ConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        api_client = hass.data[DOMAIN][entry.entry_id]["api"]
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

        # Disconnect SSE before removing the entry data
        if coordinator.sse_task:
            await api_client.async_sse_disconnect()
            coordinator.sse_task.cancel()
            try:
                await coordinator.sse_task
            except asyncio.CancelledError:
                pass

        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
