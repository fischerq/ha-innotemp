"""Tests for the InnotempDataUpdateCoordinator."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState

from custom_components.innotemp.const import DOMAIN
from custom_components.innotemp.coordinator import (
    InnotempCoordinatorEntity,
    InnotempDataUpdateCoordinator,
    entity_unique_id,
)
from custom_components.innotemp.api import (
    InnotempApiClient,
)


@pytest.fixture
def mock_api_client_success():
    """Fixture for a mock InnotempApiClient that successfully connects and provides data."""
    client = MagicMock(spec=InnotempApiClient)
    client.async_sse_connect = AsyncMock()
    client.async_sse_disconnect = AsyncMock()

    async def mock_connect(callback):
        await asyncio.sleep(0.01)  # Simulate async operation
        callback({"sensor1": "value1", "status": "connected"})

    client.async_sse_connect.side_effect = mock_connect
    return client


@pytest.fixture
def mock_api_client_failure():
    """Fixture for a mock InnotempApiClient that simulates a connection failure or error."""
    client = MagicMock(spec=InnotempApiClient)
    client.async_sse_connect = AsyncMock(side_effect=Exception("SSE Connection Failed"))
    client.async_sse_disconnect = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_coordinator_success(hass, mock_api_client_success):
    """Test successful data retrieval and update via SSE."""
    logger = logging.getLogger(__name__)
    config_entry = MagicMock(spec=config_entries.ConfigEntry)
    config_entry.state = config_entries.ConfigEntryState.SETUP_IN_PROGRESS
    coordinator = InnotempDataUpdateCoordinator(hass, logger, mock_api_client_success)
    coordinator.config_entry = config_entry

    await coordinator.async_config_entry_first_refresh()

    await asyncio.sleep(0.05)

    assert coordinator.data == {"sensor1": "value1", "status": "connected"}
    mock_api_client_success.async_sse_connect.assert_called_once()

    await coordinator.async_shutdown()


def _bare_coordinator(hass):
    client = MagicMock(spec=InnotempApiClient)
    client.async_sse_connect = AsyncMock()
    return InnotempDataUpdateCoordinator(hass, logging.getLogger(__name__), client)


@pytest.mark.asyncio
async def test_sse_update_merges_instead_of_replacing(hass):
    """An SSE push must not wipe settings that only came from roomconf."""
    coordinator = _bare_coordinator(hass)
    coordinator.async_set_updated_data({"setpoint": "21", "temp": "20.0"})

    coordinator.async_handle_sse_update({"temp": "20.5", "soc": "88"})

    assert coordinator.data == {"setpoint": "21", "temp": "20.5", "soc": "88"}
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_sse_update_with_no_previous_data(hass):
    coordinator = _bare_coordinator(hass)
    coordinator.async_handle_sse_update({"temp": "20.5"})
    assert coordinator.data == {"temp": "20.5"}
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_sse_listener_receives_merge_callback(hass):
    coordinator = _bare_coordinator(hass)
    await hass.async_block_till_done()
    coordinator.api_client.async_sse_connect.assert_awaited_once_with(
        coordinator.async_handle_sse_update
    )
    await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("unique_id", "expected"),
    [("192.168.1.5", "192.168.1.5_p1"), (None, "entry123_p1")],
)
def test_entity_unique_id(unique_id, expected):
    entry = MagicMock(unique_id=unique_id, entry_id="entry123")
    assert entity_unique_id(entry, "p1") == expected


def _entity(hass, room=None, comp=None):
    coordinator = _bare_coordinator(hass)
    entry = MagicMock(unique_id="host", entry_id="e1")
    entity = InnotempCoordinatorEntity(
        coordinator, entry, {"param": "p1", "label": "Label"}
    )
    if room is not None:
        entity._room_attributes = room
    if comp is not None:
        entity._component_attributes = comp
    return entity, coordinator


@pytest.mark.asyncio
async def test_device_info_component_device(hass):
    entity, coordinator = _entity(
        hass,
        room={"var": "room1", "label": "Keller", "type": "room001"},
        comp={"var": "mix1", "type": "mixer001", "label": "Mischer"},
    )
    info = entity.device_info
    assert info["identifiers"] == {(DOMAIN, "e1", "room1", "mix1")}
    assert info["name"] == "Keller > Mischer"
    assert info["model"] == "mixer001"
    assert info["via_device"] == (DOMAIN, "e1", "room1")
    assert entity.unique_id == "host_p1"
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_device_info_component_from_label_slug(hass):
    entity, coordinator = _entity(
        hass,
        room={"var": "room1", "label": "Keller"},
        comp={"label": "Heiz Stab #2"},
    )
    info = entity.device_info
    assert info["identifiers"] == {(DOMAIN, "e1", "room1", "heiz-stab-2")}
    assert info["model"] == "Component"
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_device_info_room_device(hass):
    entity, coordinator = _entity(
        hass, room={"var": "room1", "label": "Keller", "type": "room001"}, comp={}
    )
    info = entity.device_info
    assert info["identifiers"] == {(DOMAIN, "e1", "room1")}
    assert info["name"] == "Keller"
    assert info["via_device"] == (DOMAIN, "e1")
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_device_info_controller_device(hass):
    entity, coordinator = _entity(hass)
    info = entity.device_info
    assert info["identifiers"] == {(DOMAIN, "e1")}
    assert info["name"] == "Innotemp Heating Controller"
    await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_get_api_value_branches(hass):
    entity, coordinator = _entity(hass)
    entity.entity_id = "sensor.x"
    assert entity._get_api_value() is None  # no _param_id
    entity._param_id = "p1"
    assert entity._get_api_value() is None  # data None
    coordinator.async_set_updated_data({"other": "1"})
    assert entity._get_api_value() is None  # missing param
    coordinator.async_set_updated_data({"p1": "3"})
    assert entity._get_api_value() == "3"
    await coordinator.async_shutdown()
