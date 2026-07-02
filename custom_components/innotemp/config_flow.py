"""Config flow for Innotemp Heating Controller."""

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import DOMAIN
from .api import InnotempApiClient

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
            host = user_input["host"]
            username = user_input["username"]
            _LOGGER.info(
                "[innotemp] Config flow: attempting login to host=%s, username=%s",
                host,
                username,
            )
            # Use an unsafe cookie jar so the controller's PHPSESSID cookie is
            # kept even when the host is a bare IP address (aiohttp's default
            # jar drops cookies from IP hosts). Matches async_setup_entry.
            session = async_create_clientsession(
                self.hass, cookie_jar=aiohttp.CookieJar(unsafe=True)
            )
            api_client = InnotempApiClient(
                session,
                host,
                username,
                user_input["password"],
            )
            try:
                await api_client.async_login()
                _LOGGER.info("[innotemp] Config flow: login successful")
                return self.async_create_entry(
                    title="Innotemp Heating Controller", data=user_input
                )
            except Exception as ex:
                _LOGGER.error(
                    "[innotemp] Config flow: login failed: %s (type=%s)",
                    ex,
                    type(ex).__name__,
                )
                errors["base"] = "cannot_connect"
                return self.async_show_form(
                    step_id="user",
                    data_schema=data_schema,
                    errors=errors,
                )
            finally:
                await session.close()

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )
