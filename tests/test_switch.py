"""Tests for the Innotemp switch platform."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.core import HomeAssistant

from custom_components.innotemp.const import DOMAIN
from custom_components.innotemp.coordinator import InnotempDataUpdateCoordinator
from custom_components.innotemp.switch import InnotempSwitch


@pytest.fixture
def mock_coordinator():
    """Fixture for a mock InnotempDataUpdateCoordinator."""
    coordinator = MagicMock(spec=InnotempDataUpdateCoordinator)
    coordinator.data = {}
    coordinator.api_client = AsyncMock()
    return coordinator


@pytest.mark.asyncio
async def test_switch_attributes(mock_coordinator: InnotempDataUpdateCoordinator):
    """Test the switch attributes."""
    entity_config = {
        "room_id": "1",
        "param": "test_switch_param",
        "label": "Test Switch",
        "type": "ONOFFAUTO",
    }
    mock_coordinator.data = {"test_switch_param": 0}  # Initial state OFF

    switch = InnotempSwitch(mock_coordinator, entity_config)

    assert switch.unique_id == "innotemp_1_test_switch_param"
    assert switch.name == "Test Switch"
    assert switch.is_on is False

    mock_coordinator.data = {"test_switch_param": 1}  # State ON
    switch.coordinator.async_set_updated_data({"test_switch_param": 1})
    assert switch.is_on is True


@pytest.mark.asyncio
async def test_switch_turn_on(mock_coordinator: InnotempDataUpdateCoordinator):
    """Test the turn_on method."""
    entity_config = {
        "room_id": "1",
        "param": "test_switch_param",
        "label": "Test Switch",
        "type": "ONOFFAUTO",
    }
    mock_coordinator.data = {"test_switch_param": 0}  # Initial state OFF

    switch = InnotempSwitch(mock_coordinator, entity_config)

    await switch.async_turn_on()

    mock_coordinator.api_client.async_send_command.assert_called_once_with(
        room_id="1", param="test_switch_param", val_new=1, val_prev=0
    )


@pytest.mark.asyncio
async def test_switch_turn_off(mock_coordinator: InnotempDataUpdateCoordinator):
    """Test the turn_off method."""
    entity_config = {
        "room_id": "1",
        "param": "test_switch_param",
        "label": "Test Switch",
        "type": "ONOFFAUTO",
    }
    mock_coordinator.data = {"test_switch_param": 1}  # Initial state ON

    switch = InnotempSwitch(mock_coordinator, entity_config)

    await switch.async_turn_off()

    mock_coordinator.api_client.async_send_command.assert_called_once_with(
        room_id="1", param="test_switch_param", val_new=0, val_prev=1
    )