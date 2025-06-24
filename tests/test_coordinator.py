"""Tests for the InnotempDataUpdateCoordinator."""

from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import pytest

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
    coordinator = InnotempDataUpdateCoordinator(hass, logger, mock_api_client_success)

    await coordinator.async_config_entry_first_refresh()

    await asyncio.sleep(0.05)

    assert coordinator.data == {"sensor1": "value1", "status": "connected"}
    mock_api_client_success.async_sse_connect.assert_called_once()

    await coordinator.async_shutdown()
    with patch.object(coordinator.logger, "error") as mock_logger_error:
        await coordinator.async_config_entry_first_refresh()
        await asyncio.sleep(0.05)  # Allow time for async operations

        # Check if connect was attempted
        mock_api_client_failure.async_sse_connect.assert_called_once()

        # Verify that an error was logged due to SSE connection failure
        # The exact log message depends on the implementation in _async_listen_sse
        # For this example, we assume it logs the exception.
        assert any(
            "SSE Connection Failed" in record.message
            for record in caplog.records
            if record.levelname == "ERROR"
        )

    assert coordinator.data is None  # Or whatever the initial/default state is

    await coordinator.async_shutdown()
    mock_api_client_failure.async_sse_disconnect.assert_called_once()
