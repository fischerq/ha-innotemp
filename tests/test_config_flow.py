"""Tests for the Innotemp Heating Controller config flow."""

from unittest.mock import patch
import pytest
from homeassistant import config_entries, data_entry_flow, setup
from homeassistant.core import HomeAssistant
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.innotemp.api import (
    InnotempAuthError,
    InnotempConnectionError,
)
from custom_components.innotemp.const import DOMAIN


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

    user_input = {
        CONF_HOST: "test.host.com",
        CONF_USERNAME: "testuser",
        CONF_PASSWORD: "testpassword",
    }

    with patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        return_value=True,
    ) as mock_login, patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_get_config",
        return_value={"some_key": "some_value"},
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input
        )
        await hass.async_block_till_done()

    assert result2 is not None
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["result"] is not None
    assert result2["result"].title == "Innotemp Heating Controller"
    assert result2["result"].data == user_input
    assert result2["result"].domain == DOMAIN
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
        side_effect=InnotempConnectionError("Simulated API connection error"),
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


async def _start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


def _patch_login(**kwargs):
    return patch(
        "custom_components.innotemp.config_flow.InnotempApiClient.async_login",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_config_flow_sets_normalised_host_unique_id(hass: HomeAssistant):
    """The entry must get a host-based unique id (it used to be None)."""
    result = await _start(hass)
    with _patch_login(return_value=None):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "  192.168.25.204/ ",
                CONF_USERNAME: "u",
                CONF_PASSWORD: "p",
            },
        )
    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    entry = result2["result"]
    assert entry.unique_id == "192.168.25.204"
    assert entry.data[CONF_HOST] == "192.168.25.204"
    assert entry.minor_version == 2


@pytest.mark.asyncio
async def test_config_flow_aborts_if_host_already_configured(hass: HomeAssistant):
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="192.168.25.204",
        data={CONF_HOST: "192.168.25.204", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    ).add_to_hass(hass)

    result = await _start(hass)
    with _patch_login(return_value=None) as mock_login:
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.25.204", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
        )
    assert result2["type"] == data_entry_flow.FlowResultType.ABORT
    assert result2["reason"] == "already_configured"
    mock_login.assert_not_called()


@pytest.mark.parametrize(
    "host",
    [
        "",
        "   ",
        "http",
        "https",
        "http://192.168.1.5",
        "192.168.1.5/inc",
        "a b",
        "h:99999",
    ],
)
@pytest.mark.asyncio
async def test_config_flow_rejects_invalid_host(hass: HomeAssistant, host):
    result = await _start(hass)
    with _patch_login(return_value=None) as mock_login:
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: host, CONF_USERNAME: "u", CONF_PASSWORD: "p"},
        )
    assert result2["type"] == data_entry_flow.FlowResultType.FORM
    assert result2["errors"] == {CONF_HOST: "invalid_host"}
    mock_login.assert_not_called()


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (InnotempAuthError("bad pw"), "invalid_auth"),
        (InnotempConnectionError("offline"), "cannot_connect"),
        (RuntimeError("bug"), "unknown"),
    ],
)
@pytest.mark.asyncio
async def test_config_flow_maps_login_errors(hass: HomeAssistant, exc, error):
    result = await _start(hass)
    with _patch_login(side_effect=exc):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "10.0.0.2", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
        )
    assert result2["type"] == data_entry_flow.FlowResultType.FORM
    assert result2["errors"] == {"base": error}


def test_translations_cover_all_flow_errors():
    """strings.json and every translation must define the keys the flow uses."""
    import json
    from pathlib import Path

    base = Path(__file__).parent.parent / "custom_components" / "innotemp"
    files = [base / "strings.json", *sorted((base / "translations").glob("*.json"))]
    assert len(files) >= 2
    for path in files:
        config = json.loads(path.read_text(encoding="utf-8"))["config"]
        assert set(config["error"]) >= {
            "cannot_connect",
            "invalid_auth",
            "invalid_host",
            "unknown",
        }, path
        assert "already_configured" in config["abort"], path
        assert set(config["step"]["user"]["data"]) == {
            "host",
            "username",
            "password",
        }, path
