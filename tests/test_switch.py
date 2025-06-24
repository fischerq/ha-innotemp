"""Tests for the Innotemp switch platform."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.entity_platform import AddEntitiesCallback


from custom_components.innotemp.switch import async_setup_entry, InnotempSwitch
from custom_components.innotemp.const import DOMAIN
from custom_components.innotemp.api import InnotempApiClient


@pytest.fixture
def mock_coordinator_switch_success(hass: HomeAssistant):
    coordinator = MagicMock(spec=DataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.api_client = AsyncMock(spec=InnotempApiClient)
    coordinator.data = {
        "switches": [
            {
                "room_id": "101",
                "param": "heating_mode",
                "label": "Main Heating",
                "type": "ONOFFAUTO",
            },
            {
                "room_id": "102",
                "param": "fan_power",
                "label": "Ventilation Fan",
                "type": "ONOFF",
            },
        ],
        "heating_mode": 0,
        "fan_power": 1,  # Initial state: ON
    }
    config_entry_mock = MagicMock(
        spec=config_entries.ConfigEntry, entry_id="test_switch_entry"
    )
    type(coordinator).config_entry = PropertyMock(return_value=config_entry_mock)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][config_entry_mock.entry_id] = {
        "coordinator": coordinator,
        "config": coordinator.data,
    }
    return coordinator


@pytest.fixture
def mock_coordinator_switch_failure(hass: HomeAssistant):
    coordinator = MagicMock(spec=DataUpdateCoordinator)
    # Simulate API command failure
    coordinator.api_client.async_send_command = AsyncMock(
        side_effect=Exception("API Command Failed")
    )
    coordinator.data = {
        "switches": [
            {
                "room_id": "201",
                "param": "faulty_switch",
                "label": "Faulty Switch",
                "type": "ONOFF",
            },
            {
                "room_id": "202",
                "param": "missing_data_switch",
                "label": "Switch Missing Data",
                "type": "ONOFF",
            },
        ],
        "faulty_switch": 0,  # Initial state for faulty_switch
        # "missing_data_switch" key is intentionally missing
    }
    config_entry_mock = MagicMock(
        spec=config_entries.ConfigEntry, entry_id="test_switch_entry_failure"
    )
    type(coordinator).config_entry = PropertyMock(return_value=config_entry_mock)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][config_entry_mock.entry_id] = {
        "coordinator": coordinator,
        "config": coordinator.data,
    }
    return coordinator


@pytest.mark.asyncio
async def test_switch_operation_success(
    hass: HomeAssistant, mock_coordinator_switch_success
):
    """Test successful switch setup, attribute checks, and turn_on/turn_off operations."""
    add_entities_callback = MagicMock(spec=AddEntitiesCallback)

    await async_setup_entry(
        hass, mock_coordinator_switch_success.config_entry, add_entities_callback
    )
    add_entities_callback.assert_called_once()
    entities = add_entities_callback.call_args[0][0]
    assert len(entities) == 2

    heating_switch_config = mock_coordinator_switch_success.data["switches"][0]

    assert isinstance(heating_switch, InnotempSwitch)
    assert (
        heating_switch.unique_id
        == f"{mock_coordinator_switch_success.config_entry.entry_id}-{heating_switch_config['param']}"
    )
    assert heating_switch.name == "Main Heating"
    assert heating_switch.is_on is False

    mock_coordinator_switch_success.api_client.async_send_command.reset_mock()
    mock_coordinator_switch_success.api_client.async_send_command.assert_called_once_with(
        room_id="101", param="heating_mode", val_new=1, val_prev=0
    )
    # Simulate state update after command
    mock_coordinator_switch_success.data["heating_mode"] = 1
    heating_switch.async_write_ha_state()  # Manually trigger update for test if coordinator doesn't auto-push
    assert heating_switch.is_on is True
    mock_coordinator_switch_success.api_client.async_send_command.reset_mock()
    mock_coordinator_switch_success.api_client.async_send_command.assert_called_once_with(
        room_id="101", param="heating_mode", val_new=0, val_prev=1
    )
    mock_coordinator_switch_success.data["heating_mode"] = 0
    heating_switch.async_write_ha_state()
    assert heating_switch.is_on is False

    fan_switch_config = mock_coordinator_switch_success.data["switches"][1]
    fan_switch = next(
        e for e in entities if e.entity_description.key == fan_switch_config["param"]
    )
    assert fan_switch.is_on is True  # Initial state from "fan_power": 1
    mock_coordinator_switch_success.api_client.async_send_command.reset_mock()
    await fan_switch.async_turn_off()
    mock_coordinator_switch_success.api_client.async_send_command.assert_called_once_with(
        room_id="102", param="fan_power", val_new=0, val_prev=1
    )
    mock_coordinator_switch_success.data["fan_power"] = 0
    fan_switch.async_write_ha_state()
    assert fan_switch.is_on is False


@pytest.mark.asyncio
async def test_switch_operation_failure(
    hass: HomeAssistant, mock_coordinator_switch_failure, caplog
):
    """Test switch behavior with API errors and missing data."""
    add_entities_callback = MagicMock(spec=AddEntitiesCallback)

    await async_setup_entry(
        hass, mock_coordinator_switch_failure.config_entry, add_entities_callback
    )
    add_entities_callback.assert_called_once()
    entities = add_entities_callback.call_args[0][0]
    assert len(entities) == 2

    faulty_switch_config = mock_coordinator_switch_failure.data["switches"][0]
    faulty_switch = next(
        e for e in entities if e.entity_description.key == faulty_switch_config["param"]
    )

    mock_coordinator_switch_failure.api_client.async_send_command.reset_mock()
    await faulty_switch.async_turn_on()
    mock_coordinator_switch_failure.api_client.async_send_command.assert_called_once_with(
        room_id="201", param="faulty_switch", val_new=1, val_prev=0
    )
    # Check logs for error message (actual message depends on implementation)
    assert any(
        "Error sending command to Innotemp API" in record.message
        for record in caplog.records
        if record.levelname == "ERROR"
    )
    # State should ideally not change if the command failed
    assert faulty_switch.is_on is False

    # Switch 2: Switch with missing data key in coordinator
    missing_data_switch_config = mock_coordinator_switch_failure.data["switches"][1]
    missing_data_switch = next(
        e
        for e in entities
        if e.entity_description.key == missing_data_switch_config["param"]
    )

    assert missing_data_switch.name == "Switch Missing Data"
    # The is_on property should handle cases where the data key is missing
    # In the current implementation, it defaults to False if key not found or value is not 1
    assert missing_data_switch.is_on is False

    # Attempting to turn it on should still try to send a command,
    # but its 'prev_val' might be based on the default False state.
    mock_coordinator_switch_failure.api_client.async_send_command.reset_mock()
    caplog.clear()
    await missing_data_switch.async_turn_on()
    # val_prev will be 0 because its state was False (due to missing data)
    mock_coordinator_switch_failure.api_client.async_send_command.assert_called_once_with(
        room_id="202", param="missing_data_switch", val_new=1, val_prev=0
    )
    assert any(
        "Error sending command to Innotemp API" in record.message
        for record in caplog.records
        if record.levelname == "ERROR"
    )
    assert (
        missing_data_switch.is_on is False
    )  # State should remain unchanged due to API error & missing data
