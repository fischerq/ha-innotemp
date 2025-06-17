import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import ClientSession, ClientResponse
from aiohttp.test_utils import AioHTTPTestCase, TestServer
from aiohttp import web
import asyncio

from custom_components.innotemp.api import InnotempApiClient

# Mock response data
MOCK_LOGIN_SUCCESS = b'{"status":"success"}'
MOCK_CONFIG_SUCCESS = b'{"config_data": "mock_config"}'
MOCK_COMMAND_SUCCESS = b'{"status":"success"}'

# Helper function to create a mock response
async def create_mock_response(status=200, text=None, json_data=None, headers=None, cookie_jar=None):
    """Helper function to create a mock ClientResponse."""
    response = AsyncMock(spec=ClientResponse)
    response.status = status
    response.text = AsyncMock(return_value=text)
    response.json = AsyncMock(return_value=json_data)
    response.headers = headers if headers is not None else {}
    response.cookies = cookie_jar if cookie_jar is not None else {}
    response.release = AsyncMock() # Add a mock release method
    return response

@pytest.fixture
def mock_client_session():
    """Fixture for a mock aiohttp.ClientSession."""
    session = AsyncMock(spec=ClientSession)
    session.post = AsyncMock()
    session.get = AsyncMock()
    session.cookie_jar = MagicMock()
    session.cookie_jar.filter_cookies.return_value = {}
    return session

@pytest.mark.asyncio
async def test_api_client_login(mock_client_session):
    """Test successful login."""
    mock_response = await create_mock_response(text=MOCK_LOGIN_SUCCESS.decode('utf-8'),
                                              headers={'Set-Cookie': 'PHPSESSID=mock_session_id; Path=/'})
    mock_client_session.post.return_value.__aenter__.return_value = mock_response

    client = InnotempApiClient("mock_host", "mock_user", "mock_password", mock_client_session)
    await client.async_login()

    mock_client_session.post.assert_called_once_with(
        'mock_host/inc/groups.read.php', data={'un': 'mock_user', 'pw': 'mock_password'}
    )
    assert client._session_id == 'mock_session_id'

@pytest.mark.asyncio
async def test_api_client_get_config(mock_client_session):
    """Test getting configuration."""
    mock_response = await create_mock_response(text=MOCK_CONFIG_SUCCESS.decode('utf-8'), json_data={"config_data": "mock_config"})
    mock_client_session.post.return_value.__aenter__.return_value = mock_response

    client = InnotempApiClient("mock_host", "mock_user", "mock_password", mock_client_session)
    client._session_id = "mock_session_id" # Simulate a logged-in state

    config = await client.async_get_config()

    mock_client_session.post.assert_called_once_with(
        'mock_host/inc/roomconf.read.php', data={'un': 'mock_user', 'pw': 'mock_password', 'date_string': 0},
        cookies={'PHPSESSID': 'mock_session_id'}
    )
    assert config == {"config_data": "mock_config"}

@pytest.mark.asyncio
async def test_api_client_send_command(mock_client_session):
    """Test sending a command."""
    mock_response = await create_mock_response(text=MOCK_COMMAND_SUCCESS.decode('utf-8'))
    mock_client_session.post.return_value.__aenter__.return_value = mock_response

    client = InnotempApiClient("mock_host", "mock_user", "mock_password", mock_client_session)
    client._session_id = "mock_session_id" # Simulate a logged-in state

    await client.async_send_command("room1", "param1", "new_val", "prev_val")

    mock_client_session.post.assert_called_once_with(
        'mock_host/inc/value.save.php', data={'room_id': 'room1', 'param': 'param1', 'val_new': 'new_val', 'val_prev': 'prev_val'},
        cookies={'PHPSESSID': 'mock_session_id'}
    )

@pytest.mark.asyncio
async def test_api_client_sse_connect_and_disconnect(mock_client_session):
    """Test SSE connection and disconnection."""
    mock_signal_names_response = await create_mock_response(text=b'["signal1", "signal2"]'.decode('utf-8'), json_data=["signal1", "signal2"])
    mock_client_session.post.return_value.__aenter__.return_value = mock_signal_names_response

    # Mock the SSE get request
    mock_sse_response = AsyncMock()
    mock_sse_response.content.readline = AsyncMock(side_effect=[
        b'event: message\ndata: [10, 20]\n\n',
        b'event: message\ndata: [30, 40]\n\n',
        asyncio.TimeoutError, # Simulate connection drop
    ])
    mock_sse_response.headers = {'Content-Type': 'text/event-stream'}
    mock_sse_response.status = 200
    mock_sse_response.release = AsyncMock()

    mock_client_session.get.return_value.__aenter__.return_value = mock_sse_response

    client = InnotempApiClient("mock_host", "mock_user", "mock_password", mock_client_session)
    client._session_id = "mock_session_id"

    mock_callback = AsyncMock()

    # Use patch to mock the async_login call within the sse_connect loop
    with patch.object(client, 'async_login', new_callable=AsyncMock) as mock_login:
        # Run the SSE connection in a task
        sse_task = asyncio.create_task(client.async_sse_connect(mock_callback))

        # Allow the task to run and process a few messages and then the timeout
        await asyncio.sleep(0.1)

        # Disconnect the SSE listener
        await client.async_sse_disconnect()

        # Wait for the task to finish
        await sse_task

        # Verify signal names were fetched
        mock_client_session.post.assert_called_once_with(
             'mock_host/inc/live_signal.read.php', data={'init': 1}, cookies={'PHPSESSID': 'mock_session_id'}
        )

        # Verify the SSE connection was attempted (at least once before re-login)
        mock_client_session.get.assert_called_once_with(
            'mock_host/inc/live_signal.read.SSE.php', cookies={'PHPSESSID': 'mock_session_id'}
        )

        # Verify the callback was called with processed data
        mock_callback.assert_any_call({'signal1': 10, 'signal2': 20})
        mock_callback.assert_any_call({'signal1': 30, 'signal2': 40})

        # Verify re-login was attempted after connection drop
        mock_login.assert_called_once()

@pytest.mark.asyncio
async def test_api_request_retry_on_session_timeout(mock_client_session):
    """Test async_api_request retries on session timeout."""
    # First response simulates a session timeout (e.g., redirects to login or a specific error response)
    # For simplicity, we'll just return an empty 401 response here. A real implementation might check content.
    mock_timeout_response = await create_mock_response(status=401)
    mock_success_response = await create_mock_response(status=200, text='{"status":"success"}')

    mock_client_session.post.side_effect = [
        MagicMock(__aenter__=AsyncMock(return_value=mock_timeout_response)), # First call fails
        MagicMock(__aenter__=AsyncMock(return_value=mock_success_response)),  # Second call succeeds after re-login
    ]

    client = InnotempApiClient("mock_host", "mock_user", "mock_password", mock_client_session)

    with patch.object(client, 'async_login', new_callable=AsyncMock) as mock_login:
        response = await client.async_api_request('POST', '/test_endpoint', data={'key': 'value'})

        # Verify async_login was called
        mock_login.assert_called_once()

        # Verify the request was attempted twice
        assert mock_client_session.post.call_count == 2

        # Verify the final response is the successful one
        assert await response.text() == '{"status":"success"}'