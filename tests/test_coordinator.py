"""Tests for the InnotempDataUpdateCoordinator."""

from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import pytest

from custom_components.innotemp.coordinator import InnotempDataUpdateCoordinator
from custom_components.innotemp.api import (
    InnotempApiClient,
)  # For type hinting if needed


@pytest.fixture
def mock_api_client_success():
    """Fixture for a mock InnotempApiClient that successfully connects and provides data."""
    client = MagicMock(spec=InnotempApiClient)
    client.async_sse_connect = AsyncMock()
    client.async_sse_disconnect = AsyncMock()

    # Simulate the API client calling the callback with data when SSE connects
    async def mock_connect(callback):
        await asyncio.sleep(0.01)  # Simulate async operation
        callback({"sensor1": "value1", "status": "connected"})
        # Keep the "connection" alive by not returning, or by simulating a long-running task
        # For this test, we'll let it complete after one callback.
        # If the coordinator's _async_listen_sse is a loop, this mock might need adjustment
        # or the test needs to manage the task lifecycle.

    client.async_sse_connect.side_effect = mock_connect
    return client


@pytest.fixture
def mock_api_client_failure():
    """Fixture for a mock InnotempApiClient that simulates a connection failure or error."""
    client = MagicMock(spec=InnotempApiClient)
    # Simulate failure to connect or an error during connection
    client.async_sse_connect = AsyncMock(side_effect=Exception("SSE Connection Failed"))
    client.async_sse_disconnect = AsyncMock()  # Should still be callable for cleanup
    return client


@pytest.mark.asyncio
async def test_coordinator_success(hass, mock_api_client_success):
    """Test successful data retrieval and update via SSE."""
    logger = logging.getLogger(__name__)  # Get a logger instance
    coordinator = InnotempDataUpdateCoordinator(hass, logger, mock_api_client_success)

    # Start the coordinator and trigger the first refresh which starts SSE
    # In a real scenario, _async_listen_sse would run in a loop.
    # For this test, we assume async_config_entry_first_refresh starts the listening process.
    # The mock_api_client_success's async_sse_connect will call the callback.
    await coordinator.async_config_entry_first_refresh()

    # Allow some time for the callback to be processed if it's truly async
    await asyncio.sleep(0.05)

    # Verify that data was received and set in the coordinator
    assert coordinator.data == {"sensor1": "value1", "status": "connected"}
    mock_api_client_success.async_sse_connect.assert_called_once()

    # Test shutdown
    await coordinator.async_shutdown()
    mock_api_client_success.async_sse_disconnect.assert_called_once()


import logging  # Ensure logging is imported


@pytest.mark.asyncio
async def test_coordinator_failure(hass, mock_api_client_failure, caplog):
    """Test coordinator behavior on SSE connection failure and graceful shutdown."""
    logger = logging.getLogger(__name__)  # Get a logger instance
    coordinator = InnotempDataUpdateCoordinator(hass, logger, mock_api_client_failure)

    # Attempt to start the coordinator and connect to SSE
    # This should trigger the mock_api_client_failure's side_effect
    # The coordinator's _async_listen_sse is expected to handle this exception.
    # We are checking if it logs the error and handles it gracefully.
    # async_config_entry_first_refresh usually calls _async_update_data which calls _async_listen_sse

    # To test the exception handling within _async_listen_sse, we might need to call it more directly
    # or ensure first_refresh properly invokes it and handles its exceptions.
    # For now, let's assume first_refresh will trigger the connect attempt.

    # Patch logger to check for error messages
    with patch.object(coordinator.logger, "error") as mock_logger_error:
        await coordinator.async_config_entry_first_refresh()
        await asyncio.sleep(0.05)  # Allow time for async operations

        # Check if connect was attempted
        mock_api_client_failure.async_sse_connect.assert_called_once()

        # Verify that an error was logged due to SSE connection failure
        # The exact log message depends on the implementation in _async_listen_sse
        # For this example, we assume it logs the exception.
        # This assertion might need adjustment based on actual logging.
        # Example: mock_logger_error.assert_any_call(f"Error during SSE connection: SSE Connection Failed", exc_info=True)
        # A more robust way is to check caplog if the logger is standard.
        assert any(
            "SSE Connection Failed" in record.message
            for record in caplog.records
            if record.levelname == "ERROR"
        )

    # Ensure data is None or some default if connection failed
    assert coordinator.data is None  # Or whatever the initial/default state is

    # Test shutdown even after failure
    await coordinator.async_shutdown()
    mock_api_client_failure.async_sse_disconnect.assert_called_once()
