"""Tests for the InnotempDataUpdateCoordinator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.innotemp.coordinator import InnotempDataUpdateCoordinator


@pytest.fixture
def mock_api_client():
    """Fixture for a mock InnotempApiClient."""
    client = MagicMock()
    client.async_sse_connect = AsyncMock()
    client.async_sse_disconnect = AsyncMock()
    # Simulate the API client calling the callback when SSE connects
    client.async_sse_connect.side_effect = lambda callback: callback({"some": "data"})
    return client


@pytest.mark.asyncio
async def test_coordinator_receives_sse_data(hass, mock_api_client):
    """Test that the coordinator receives data from the SSE callback."""
    coordinator = InnotempDataUpdateCoordinator(hass, mock_api_client)

    # Start the coordinator and simulate the SSE connection
    await coordinator.async_config_entry_first_refresh()

    # Verify that async_set_updated_data was called with the correct data
    assert coordinator.data == {"some": "data"}

    # Clean up the SSE connection
    await coordinator.async_shutdown()
    mock_api_client.async_sse_disconnect.assert_called_once()


@pytest.mark.asyncio
async def test_coordinator_handles_sse_disconnect(hass, mock_api_client):
    """Test that the coordinator handles SSE disconnect."""
    # Simulate SSE connection dropping after initial data update
    mock_api_client.async_sse_connect.side_effect = None
    mock_api_client.async_sse_connect.return_value = (
        None  # Or raise a specific exception later if needed
    )

    coordinator = InnotempDataUpdateCoordinator(hass, mock_api_client)

    # Start the coordinator and simulate the SSE connection
    await coordinator.async_config_entry_first_refresh()

    # At this point, the initial data is set

    # Simulate a later disconnect (this part would typically be handled within the API client's loop)
    # For this test, we just check if the shutdown calls disconnect
    await coordinator.async_shutdown()
    mock_api_client.async_sse_disconnect.assert_called_once()
