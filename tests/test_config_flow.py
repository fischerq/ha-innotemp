"""Tests for the Innotemp Heating Controller config flow."""

from unittest.mock import patch
import pytest
from homeassistant import config_entries, data_entry_flow, setup
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from custom_components.innotemp.const import DOMAIN

# InnotempConfigFlow is imported by Home Assistant internally when the flow is triggered
# from custom_components.innotemp.config_flow import InnotempConfigFlow


@pytest.mark.asyncio
async def test_config_flow_success(hass: HomeAssistant) -> None:
    """Test a successful config flow from user initiation to entry creation."""
    await setup.async_setup_component(hass, "persistent_notification", {})

    # Initiate the flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result is not None
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "user"

    # Simulate user input
    user_input = {
        CONF_HOST: "test.host.com",
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
    }

    # Patch the API client's login method to simulate a successful login
    with patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        return_value=True,  # Simulate successful login
    ) as mock_login, patch(  # Also mock get_config if it's called after login
        "custom_components.innotemp.config_flow.InnotempApiClient.async_get_config",
        return_value={"some_key": "some_value"},  # Simulate some config data
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        await hass.async_block_till_done()

    assert result2 is not None
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    # Access the actual ConfigEntry object via result2.result
    assert result2.result is not None
    assert (
        result2.result.title == "Innotemp Heating Controller"
    )  # Title might be just host or a fixed string
    assert result2.result.data == user_input
    assert result2.result.domain == DOMAIN
    mock_login.assert_called_once()


@pytest.mark.asyncio
async def test_config_flow_failure(hass: HomeAssistant) -> None:
    """Test various failure scenarios in the config flow."""
    await setup.async_setup_component(hass, "persistent_notification", {})

    # Scenario 1: API Connection Failure (cannot_connect)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result is not None
    user_input_valid_host = {
        CONF_HOST: "valid.host.com",
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
    }
    with patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        side_effect=Exception("Simulated API connection error"),
    ) as mock_login_failure:
        result_api_fail = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input_valid_host
        )
        await hass.async_block_till_done()

    assert result_api_fail is not None
    assert result_api_fail["type"] == data_entry_flow.FlowResultType.FORM
    assert result_api_fail["step_id"] == "user"
    assert result_api_fail["errors"] == {"base": "cannot_connect"}
    mock_login_failure.assert_called_once()

    # Scenario 2: Invalid Host - Empty Host
    # Re-initialize flow for a clean state if necessary, or continue if flow allows multiple configure calls
    result_empty_host_init = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result_empty_host_init is not None
    user_input_empty_host = {
        CONF_HOST: "",
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
    }
    # No need to patch login here as validation should fail before API call
    result_empty_host = await hass.config_entries.flow.async_configure(
        result_empty_host_init["flow_id"], user_input_empty_host
    )
    await hass.async_block_till_done()

    assert result_empty_host is not None
    assert result_empty_host["type"] == data_entry_flow.FlowResultType.FORM
    assert result_empty_host["errors"] is not None
    assert result_empty_host["errors"].get(CONF_HOST) == "Host cannot be empty."

    # Scenario 3: Invalid Host - Contains "http://"
    result_http_host_init = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result_http_host_init is not None
    user_input_http_host = {
        CONF_HOST: "http://invalidhost",
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
    }
    result_http_host = await hass.config_entries.flow.async_configure(
        result_http_host_init["flow_id"], user_input_http_host
    )
    await hass.async_block_till_done()
    assert result_http_host is not None
    assert result_http_host["type"] == data_entry_flow.FlowResultType.FORM
    assert result_http_host["errors"] is not None
    assert (
        result_http_host["errors"].get(CONF_HOST)
        == "Hostname should not include '://'. Enter just the address."
    )

    # Scenario 4: Invalid Host - Too short (e.g. "h")
    result_short_host_init = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result_short_host_init is not None
    user_input_short_host = {
        CONF_HOST: "h",
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
    }
    result_short_host = await hass.config_entries.flow.async_configure(
        result_short_host_init["flow_id"], user_input_short_host
    )
    await hass.async_block_till_done()
    assert result_short_host is not None
    assert result_short_host["type"] == data_entry_flow.FlowResultType.FORM
    assert result_short_host["errors"] is not None
    assert (
        result_short_host["errors"].get(CONF_HOST)
        == "Hostname is too short or invalid format."
    )

    # Scenario 5: Invalid Host - "http" or "https" as hostname
    result_keyword_host_init = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result_keyword_host_init is not None
    user_input_keyword_host = {
        CONF_HOST: "http",  # or "https"
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
    }
    result_keyword_host = await hass.config_entries.flow.async_configure(
        result_keyword_host_init["flow_id"], user_input_keyword_host
    )
    await hass.async_block_till_done()
    assert result_keyword_host is not None
    assert result_keyword_host["type"] == data_entry_flow.FlowResultType.FORM
    assert result_keyword_host["errors"] is not None
    assert (
        result_keyword_host["errors"].get(CONF_HOST)
        == "Hostname cannot be 'http' or 'https'. Enter a valid IP address or hostname."
    )
