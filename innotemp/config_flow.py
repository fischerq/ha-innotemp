import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

class InnotempConfigFlow(config_entries.ConfigFlow, domain="innotemp"):
    """Innotemp config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle a flow initiated by the user."""
        errors = {}

        if user_input is not None:
            # Here you would typically validate the input,
            # for example by trying to connect to the device.
            # For this example, we'll assume it's valid.
            return self.async_create_entry(
                title="Innotemp Heating Controller", data=user_input
            )

        data_schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
        })

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )