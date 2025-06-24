import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import ClientSession, ClientResponse
import asyncio
import json
import os

from custom_components.innotemp.api import InnotempApiClient

# Mock response data
MOCK_LOGIN_SUCCESS_TEXT = '{"status":"success"}'
MOCK_COMMAND_SUCCESS_TEXT = '{"status":"success"}'


# Helper function to create a mock response
async def create_mock_response(
    status=200, text=None, json_data=None, headers=None, cookie_jar=None
):
    """Helper function to create a mock ClientResponse."""
    response = AsyncMock(spec=ClientResponse)
    response.status = status
    response.text = AsyncMock(return_value=text)
    response.json = AsyncMock(return_value=json_data)
    response.headers = headers if headers is not None else {}
    response.cookies = cookie_jar if cookie_jar is not None else {}
    response.release = AsyncMock()
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
async def test_api_success(mock_client_session):
    """Test successful API operations: login, get_config, send_command, and SSE."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )

    # 1. Test successful login
    mock_login_response = await create_mock_response(
        text=MOCK_LOGIN_SUCCESS_TEXT,
        headers={"Set-Cookie": "PHPSESSID=mock_session_id; Path=/"},
    )
    # Setup for async with context manager
    mock_client_session.post.return_value.__aenter__.return_value = mock_login_response
    await client.async_login()
    mock_client_session.post.assert_called_with(
        "http://mock_host/inc/groups.read.php",
        data={"un": "mock_user", "pw": "mock_password"},
    )
    assert client._session_id == "mock_session_id"
    assert client._session_id == "mock_session_id"
    mock_client_session.post.reset_mock()  # Reset the main post mock for subsequent configurations

    # Prepare responses for the sequence of POST calls
    # 2. Getting configuration
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "roomconf_test_data.json")
    with open(file_path, "r") as f:
        roomconf_data = json.load(f)
    mock_config_response = await create_mock_response(json_data=roomconf_data)

    # 3. Sending a command
    mock_command_response = await create_mock_response(text=MOCK_COMMAND_SUCCESS_TEXT)

    # 4. SSE signal names
    mock_signal_names_response = await create_mock_response(
        json_data=["signal1", "signal2"]
    )

    # Configure side_effect for mock_client_session.post to handle multiple calls with different responses
    # Each item in side_effect should be the object that the `async with` statement's target (response) becomes.
    # So, each item should be an AsyncMock that has __aenter__ returning the actual mock response.

    # Create context manager mocks for each post call
    post_context_login = AsyncMock()
    post_context_login.__aenter__.return_value = (
        mock_login_response  # This was used for the first login call
    )
    # which is already done. We need to set up for next calls.

    post_context_get_config = AsyncMock()
    post_context_get_config.__aenter__.return_value = mock_config_response

    post_context_send_command = AsyncMock()
    post_context_send_command.__aenter__.return_value = mock_command_response

    post_context_get_signals = AsyncMock()
    post_context_get_signals.__aenter__.return_value = mock_signal_names_response

    # The first call to post (login) is already asserted.
    # Now, set up side_effect for subsequent calls.
    mock_client_session.post.side_effect = [
        post_context_get_config,  # For async_get_config
        post_context_send_command,  # For async_send_command
        post_context_get_signals,  # For _get_signal_names (SSE setup)
    ]

    # Call 2: async_get_config
    config_data = await client.async_get_config()
    assert config_data == roomconf_data
    # Assertion for this call will be based on its position in call_args_list if using side_effect
    # Or we can inspect mock_client_session.post.call_args_list[0] (since login was call 0 before reset, this is now 0 again)
    # For simplicity, let's assume specific mock objects for each call or use call_args_list
    # With side_effect, the call count accumulates. Login was call 0.
    # After reset, this is the first call again to the .post mock object.
    assert mock_client_session.post.call_args_list[0][0] == (
        "http://mock_host/inc/roomconf.read.php",
    )
    assert mock_client_session.post.call_args_list[0][1] == {
        "data": {"un": "mock_user", "pw": "mock_password", "date_string": 0}
        # Cookies are implicitly added by async_api_request if session_id is set
    }

    # Call 3: async_send_command
    await client.async_send_command("room1", "param1", "new_val", "prev_val")
    assert mock_client_session.post.call_args_list[1][0] == (
        "http://mock_host/inc/value.save.php",
    )
    assert mock_client_session.post.call_args_list[1][1] == {
        "data": {
            "room_id": "room1",
            "param": "param1",
            "val_new": "new_val",
            "val_prev": "prev_val",
        },
        "cookies": {"PHPSESSID": "mock_session_id"},
    }

    # Call 4: _get_signal_names (for SSE)
    # This is called internally by async_sse_connect
    # The mock_sse_response for the GET request:
    mock_sse_response = AsyncMock()
    mock_sse_response.content.readline = AsyncMock(
        side_effect=[
            b"event: message\ndata: [10, 20]\n\n",
            b"event: message\ndata: [30, 40]\n\n",
            asyncio.TimeoutError,  # Simulate connection drop
        ]
    )
    mock_sse_response.headers = {"Content-Type": "text/event-stream"}
    mock_sse_response.status = 200
    mock_sse_response.release = AsyncMock()

    sse_get_context_manager = AsyncMock()
    sse_get_context_manager.__aenter__.return_value = mock_sse_response
    mock_client_session.get.return_value = (
        sse_get_context_manager  # Setup for the GET request
    )

    mock_callback = AsyncMock()
    with patch.object(
        client, "async_login", new_callable=AsyncMock
    ) as mock_sse_relogin:
        sse_task = asyncio.create_task(client.async_sse_connect(mock_callback))
        await asyncio.sleep(0.1)  # Allow task to run
        await client.async_sse_disconnect()
        await sse_task

        # Check the POST call for _get_signal_names
        assert mock_client_session.post.call_args_list[2][0] == (
            "http://mock_host/inc/live_signal.read.php",
        )
        assert mock_client_session.post.call_args_list[2][1] == {
            "data": {"init": 1},
            "cookies": {"PHPSESSID": "mock_session_id"},
        }

        # Check the GET call for SSE stream
        mock_client_session.get.assert_called_once_with(
            "http://mock_host/inc/live_signal.read.SSE.php",  # Corrected URL
            cookies={"PHPSESSID": "mock_session_id"},
        )
        mock_callback.assert_any_call({"signal1": 10, "signal2": 20})
        mock_callback.assert_any_call({"signal1": 30, "signal2": 40})
        mock_sse_relogin.assert_called_once()  # SSE re-login attempt


@pytest.mark.asyncio
async def test_api_failure(mock_client_session, caplog):
    """Test API failure scenarios: invalid JSON, empty JSON, and session timeout/retry."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    # Set a session ID for calls that require it post-login
    client._session_id = "mock_session_id"

    # 1. Test async_get_config with invalid JSON response
    invalid_json_text = "This is not JSON"
    mock_invalid_json_response = await create_mock_response(
        status=200,
        text=invalid_json_text,
        headers={"Content-Type": "application/json"},
    )
    mock_invalid_json_response.json = AsyncMock(
        side_effect=json.JSONDecodeError("Error", "doc", 0)
    )

    # Setup mock for this specific call
    post_context_invalid_json = AsyncMock()
    post_context_invalid_json.__aenter__.return_value = mock_invalid_json_response
    mock_client_session.post.return_value = (
        post_context_invalid_json  # Set the context manager mock
    )
    # or if post is already a context manager mock:
    # mock_client_session.post.__aenter__.return_value = mock_invalid_json_response -> This is wrong for .post
    # Correct: mock_client_session.post.return_value (the CM) . __aenter__.return_value (the response)
    # For a fresh mock_client_session.post:
    mock_client_session.post = AsyncMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_invalid_json_response)
        )
    )

    config = await client.async_get_config()
    assert config is None
    mock_client_session.post.assert_called_once_with(
        "http://mock_host/inc/roomconf.read.php",  # Corrected URL
        data={"un": "mock_user", "pw": "mock_password", "date_string": 0},
        # Cookies implicitly added by async_api_request
    )
    assert any(
        "Failed to decode JSON response" in record.message
        and f"Response text: '{invalid_json_text}'" in record.message
        for record in caplog.records
        if record.levelname == "ERROR"
    ), "Expected error log for invalid JSON not found."
    mock_client_session.post.reset_mock()
    caplog.clear()

    # 2. Test async_get_config with empty JSON response
    empty_json_text = ""
    mock_empty_json_response = await create_mock_response(
        status=200,
        text=empty_json_text,
        headers={"Content-Type": "application/json"},
    )
    mock_empty_json_response.json = AsyncMock(
        side_effect=json.JSONDecodeError("Error", "doc", 0)
    )

    mock_client_session.post = AsyncMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_empty_json_response)
        )
    )

    config = await client.async_get_config()
    assert config is None
    mock_client_session.post.assert_called_once_with(
        "http://mock_host/inc/roomconf.read.php",  # Corrected URL
        data={"un": "mock_user", "pw": "mock_password", "date_string": 0},
        # Cookies implicitly added
    )
    assert any(
        "Failed to decode JSON response" in record.message
        and f"Response text: '{empty_json_text}'" in record.message
        for record in caplog.records
        if record.levelname == "ERROR"
    ), "Expected error log for empty JSON not found."
    mock_client_session.post.reset_mock()
    caplog.clear()

    # 3. Test async_api_request retries on session timeout (e.g., 401 response)
    mock_timeout_response = await create_mock_response(status=401)
    mock_success_after_timeout_response = await create_mock_response(
        status=200, text='{"status":"success_after_relogin"}'
    )

    # Configure side_effect for multiple calls to post
    # First call simulates timeout, second call (after mocked re-login) succeeds.
    mock_client_session.post.side_effect = [
        MagicMock(__aenter__=AsyncMock(return_value=mock_timeout_response)),
        MagicMock(
            __aenter__=AsyncMock(return_value=mock_success_after_timeout_response)
        ),
    ]

    # Patch the client's own async_login method for this specific test
    with patch.object(client, "async_login", new_callable=AsyncMock) as mock_relogin:
        # Simulate that login sets a new session_id, though the test doesn't strictly rely on checking it here.
        async def side_effect_login():
            client._session_id = "new_mock_session_id"
            return True  # Or whatever async_login returns

        mock_relogin.side_effect = side_effect_login

        response = await client.async_api_request(
            "POST", "/test_endpoint", data={"key": "value"}
        )

        mock_relogin.assert_called_once()
        assert mock_client_session.post.call_count == 2
        # First call (timeout)
        mock_client_session.post.assert_any_call(
            "mock_host/test_endpoint",
            data={"key": "value"},
            cookies={"PHPSESSID": "mock_session_id"},  # Original session_id
        )
        # Second call (after re-login)
        mock_client_session.post.assert_any_call(
            "mock_host/test_endpoint",
            data={"key": "value"},
            cookies={
                "PHPSESSID": "new_mock_session_id"
            },  # New session_id after re-login
        )
        assert await response.text() == '{"status":"success_after_relogin"}'
    mock_client_session.post.reset_mock()
    mock_client_session.post.side_effect = None  # Clear side_effect
