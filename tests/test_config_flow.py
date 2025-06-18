"""Tests for the Innotemp Heating Controller config flow."""

from unittest.mock import patch

from homeassistant import config_entries, setup
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.innotemp.const import DOMAIN
from custom_components.innotemp.config_flow import InnotempConfigFlow


async def test_config_flow_success(hass: HomeAssistant):
    """Test a successful config flow."""
    await setup.async_setup_component(hass, "persistent_notification", {})

    with patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        return_value=True,
    ) as mock_login, patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_get_config",
        return_value={},
    ) as mock_get_config:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        user_input = {
            "host": "test_host",
            "username": "test_user",
            "password": "test_password",
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["title"] == "Innotemp Heating Controller"
        assert result["data"] == user_input
        mock_login.assert_called_once()
        mock_get_config.assert_called_once()


async def test_config_flow_failure(hass: HomeAssistant):
    """Test config flow with connection failure."""
    await setup.async_setup_component(hass, "persistent_notification", {})

    with patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        side_effect=Exception("Connection Failed"),
    ) as mock_login:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        user_input = {
            "host": "test_host",
            "username": "test_user",
            "password": "test_password",
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}
        mock_login.assert_called_once()
