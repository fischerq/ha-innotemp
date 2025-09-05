"""Tests for the InnotempDataUpdateCoordinator."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState

from custom_components.innotemp.coordinator import InnotempDataUpdateCoordinator
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
