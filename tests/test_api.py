import pytest
from unittest.mock import AsyncMock, MagicMock
from aiohttp import ClientSession
import json

from custom_components.innotemp.api import (
    InnotempApiClient,
    InnotempAuthError,
)


@pytest.fixture
def mock_client_session():
    """Fixture for a mock aiohttp.ClientSession that can be configured in tests."""
    session = MagicMock(spec=ClientSession)
    session.post = MagicMock()
    session.get = MagicMock()
    return session


def configure_mock_response(
    mock_method, status=200, json_data=None, text=None, headers=None
):
    """Helper to configure a mock for aiohttp session methods."""
    mock_response = MagicMock()
    mock_response.status = status
    if json_data is not None and text is None:
        text = json.dumps(json_data)
    mock_response.json = AsyncMock(return_value=json_data)
    mock_response.text = AsyncMock(return_value=text)
    mock_response.headers = headers if headers is not None else {}
    mock_response.raise_for_status = MagicMock()
    if status >= 400:
        mock_response.raise_for_status.side_effect = Exception(f"HTTP Error {status}")

    async_context_manager = AsyncMock()
    async_context_manager.__aenter__.return_value = mock_response
    mock_method.return_value = async_context_manager


@pytest.mark.asyncio
async def test_login_success(mock_client_session):
    """Test successful login."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    configure_mock_response(mock_client_session.post, json_data={"info": "success"})

    await client.async_login()

    mock_client_session.post.assert_called_once()
    assert client._is_logged_in is True


@pytest.mark.asyncio
async def test_login_failure(mock_client_session):
    """Test failed login."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    configure_mock_response(
        mock_client_session.post,
        json_data={"info": "error", "error": "Access denied."},
    )

    with pytest.raises(InnotempAuthError):
        await client.async_login()
    assert client._is_logged_in is False


@pytest.mark.asyncio
async def test_send_command_success(mock_client_session):
    """Test sending a command successfully."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True  # Pretend we are logged in

    mock_response = MagicMock()
    mock_response.status = 200
    json_payload = {"info": "success_non_json"}
    mock_response.json = AsyncMock(return_value=json_payload)
    mock_response.text = AsyncMock(return_value=json.dumps(json_payload))
    mock_response.raise_for_status = MagicMock()

    async_context_manager = AsyncMock()
    async_context_manager.__aenter__.return_value = mock_response
    mock_client_session.post.return_value = async_context_manager


    success = await client.async_send_command(
        room_id=1, param="p1", val_new="10", val_prev_options=["5"]
    )

    assert success is True
    mock_client_session.post.assert_called_once()


@pytest.mark.asyncio
async def test_retry_on_auth_error(mock_client_session):
    """Test that a command is retried after a re-login on auth error."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True

    # Mock responses for the sequence of calls
    # 1. First command attempt -> redirect, indicating auth error
    mock_redirect_response = MagicMock()
    mock_redirect_response.status = 302
    cm_redirect = AsyncMock()
    cm_redirect.__aenter__.return_value = mock_redirect_response

    # 2. Login attempt -> success
    mock_login_response = MagicMock()
    mock_login_response.status = 200
    mock_login_response.json = AsyncMock(return_value={"info": "success"})
    cm_login = AsyncMock()
    cm_login.__aenter__.return_value = mock_login_response

    # 3. Second command attempt -> success
    mock_command_success_response = MagicMock()
    mock_command_success_response.status = 200
    json_payload = {"info": "success_non_json"}
    mock_command_success_response.json = AsyncMock(
        return_value=json_payload
    )
    mock_command_success_response.text = AsyncMock(
        return_value=json.dumps(json_payload)
    )
    cm_command_success = AsyncMock()
    cm_command_success.__aenter__.return_value = mock_command_success_response

    mock_client_session.post.side_effect = [
        cm_redirect,
        cm_login,
        cm_command_success,
    ]

    success = await client.async_send_command(
        room_id=1, param="p1", val_new="10", val_prev_options=["5"]
    )

    assert success is True
    assert mock_client_session.post.call_count == 3
