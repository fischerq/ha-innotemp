"""Tests for the Innotemp Heating Controller config flow."""

from unittest.mock import patch
import pytest  # Added
from homeassistant import (
    config_entries,
    data_entry_flow,
    setup,
)  # data_entry_flow added
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD  # Added
from custom_components.innotemp.const import DOMAIN

# from custom_components.innotemp.api import InnotempApiClient # Not strictly needed for this test due to patching config_flow.InnotempApiClient
from custom_components.innotemp.config_flow import InnotempConfigFlow  # Already present


async def test_config_flow_success(hass: HomeAssistant):
    """Test a successful config flow."""
    await setup.async_setup_component(hass, "persistent_notification", {})

    with patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        return_value=True,
    ) as mock_login, patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_get_config",  # This was in existing test, might be needed if login success implies config fetch
        return_value={},
    ) as mock_get_config:  # Ensure this mock is also handled if it's called
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert (
            result["type"] == data_entry_flow.FlowResultType.FORM
        )  # Changed to use imported data_entry_flow
        assert result["step_id"] == "user"

        user_input = {
            "host": "test_host",  # Kept existing simple host for this test
            "username": "test_user",
            "password": "test_password",
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )

        assert result["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY  # Changed
        assert result["title"] == "Innotemp Heating Controller"
        assert result["data"] == user_input
        mock_login.assert_called_once()
        # mock_get_config.assert_called_once() # This depends on whether config_flow actually calls get_config after login


async def test_config_flow_failure(hass: HomeAssistant):
    """Test config flow with connection failure."""
    # This test already covers the "cannot_connect" base error for API login failures.
    await setup.async_setup_component(hass, "persistent_notification", {})

    with patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        side_effect=Exception("Connection Failed"),
    ) as mock_login:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == data_entry_flow.FlowResultType.FORM  # Changed
        assert result["step_id"] == "user"

        user_input = {
            # Using a host that would pass validation to ensure we test the login failure itself
            "host": "valid.host.com",
            "username": "test_user",
            "password": "test_password",
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )

        assert result["type"] == data_entry_flow.FlowResultType.FORM  # Changed
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}  # Verifies base error
        mock_login.assert_called_once()


# New test based on the subtask description
async def test_successful_flow_from_subtask(
    hass: HomeAssistant,
) -> None:  # mock_setup_entry is auto-used from conftest.py
    """Test a successful config flow as per subtask description."""
    # mock_setup_entry is a fixture that prevents attempts to actually set up the integration

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result["step_id"] == "user"

    # Patch InnotempApiClient and its async_login method
    # The path to patch depends on where it's imported in config_flow.py
    # config_flow.py does: from .api import InnotempApiClient
    # So, the path should be custom_components.innotemp.config_flow.InnotempApiClient
    with patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        return_value=True,  # Simulate successful login
    ) as mock_login:
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "test.host.com",
                CONF_USERNAME: "testuser",
                CONF_PASSWORD: "testpassword",
            },
        )
        await hass.async_block_till_done()  # Ensure all tasks are processed

    assert result2["type"] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    assert result2["title"] == "Innotemp Heating Controller"
    assert result2["data"] == {
        CONF_HOST: "test.host.com",
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
    }
    assert result2["domain"] == DOMAIN
    assert len(mock_login.mock_calls) == 1  # Ensure async_login was called


# Host validation tests
async def test_flow_invalid_host_empty(hass: HomeAssistant) -> None:
    """Test config flow with an empty host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.RESULT_TYPE_FORM

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "",  # Empty host
            CONF_USERNAME: "testuser",
            CONF_PASSWORD: "testpassword",
        },
    )
    await hass.async_block_till_done()

    assert result2["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result2["errors"] is not None
    assert result2["errors"].get(CONF_HOST) == "Host cannot be empty."


async def test_flow_invalid_host_is_http(hass: HomeAssistant) -> None:
    """Test config flow with 'http' as host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "http", CONF_USERNAME: "u", CONF_PASSWORD: "p"}
    )
    await hass.async_block_till_done()
    assert result2["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result2["errors"] is not None
    assert (
        result2["errors"].get(CONF_HOST)
        == "Hostname cannot be 'http' or 'https'. Enter a valid IP address or hostname."
    )


async def test_flow_invalid_host_is_https(hass: HomeAssistant) -> None:
    """Test config flow with 'https' as host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "https", CONF_USERNAME: "u", CONF_PASSWORD: "p"}
    )
    await hass.async_block_till_done()
    assert result2["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result2["errors"] is not None
    assert (
        result2["errors"].get(CONF_HOST)
        == "Hostname cannot be 'http' or 'https'. Enter a valid IP address or hostname."
    )


async def test_flow_invalid_host_contains_protocol(hass: HomeAssistant) -> None:
    """Test config flow with '://' in host."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "http://validhost", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    )
    await hass.async_block_till_done()
    assert result2["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result2["errors"] is not None
    assert (
        result2["errors"].get(CONF_HOST)
        == "Hostname should not include '://'. Enter just the address."
    )


async def test_flow_invalid_host_too_short(hass: HomeAssistant) -> None:
    """Test config flow with a host that is too short."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: "h", CONF_USERNAME: "u", CONF_PASSWORD: "p"},  # Too short
    )
    await hass.async_block_till_done()
    assert result2["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result2["errors"] is not None
    assert (
        result2["errors"].get(CONF_HOST) == "Hostname is too short or invalid format."
    )


# Test for API connection error specifically (as requested, though similar to test_config_flow_failure)
async def test_flow_api_connection_error(hass: HomeAssistant) -> None:
    """Test config flow with an API connection error during login."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result["step_id"] == "user"

    # Patch InnotempApiClient.async_login to raise an exception
    with patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        side_effect=Exception(
            "Simulated API connection error"
        ),  # Simulate any exception during login
    ) as mock_login:
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "valid.host.com",  # Host format is valid
                CONF_USERNAME: "testuser",
                CONF_PASSWORD: "testpassword",
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] == data_entry_flow.RESULT_TYPE_FORM
    assert result2["errors"] is not None
    assert result2["errors"].get("base") == "cannot_connect"  # Check for the base error
    assert len(mock_login.mock_calls) == 1  # Ensure async_login was attempted
