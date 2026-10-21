"""Config flow for Innotemp Heating Controller."""

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import DOMAIN
from .api import (
    InnotempApiClient,
    InnotempAuthError,
    InnotempConnectionError,
    InnotempApiError,
    normalize_host,
)

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


class InnotempConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Innotemp Heating Controller."""

    VERSION = 1
    # 1.2: entries carry a host-based unique id; see async_migrate_entry.
    MINOR_VERSION = 2

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Host validation lives here rather than in the schema: a custom
            # validator function in the schema cannot be serialised for the
            # frontend, and a per-field error is clearer for the user.
            try:
                host = normalize_host(user_input[CONF_HOST])
            except ValueError as err:
                _LOGGER.debug("Rejected host %r: %s", user_input[CONF_HOST], err)
                errors[CONF_HOST] = "invalid_host"
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()

                errors = await self._async_try_login(
                    host, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
                if not errors:
                    return self.async_create_entry(
                        title="Innotemp Heating Controller",
                        data={**user_input, CONF_HOST: host},
                    )

        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input), errors=errors
        )

    async def _async_try_login(
        self, host: str, username: str, password: str
    ) -> dict[str, str]:
        """Try to log in; return a form errors dict (empty on success)."""
        # Unsafe cookie jar: the controller's PHPSESSID cookie must be kept
        # even for bare-IP hosts. Matches async_setup_entry.
        session = async_create_clientsession(
            self.hass, cookie_jar=aiohttp.CookieJar(unsafe=True)
        )
        api_client = InnotempApiClient(session, host, username, password)
        try:
            await api_client.async_login()
        except InnotempAuthError as err:
            _LOGGER.info("Innotemp login to %s rejected: %s", host, err)
            return {"base": "invalid_auth"}
        except (InnotempConnectionError, InnotempApiError) as err:
            _LOGGER.info("Cannot connect to Innotemp controller %s: %s", host, err)
            return {"base": "cannot_connect"}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error logging in to %s", host)
            return {"base": "unknown"}
        finally:
            await session.close()
        return {}
