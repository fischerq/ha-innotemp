import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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
    # ``_api_request`` (used by send_command, get_config, ...) goes through
    # ``session.request``; only ``async_login`` uses ``session.post`` directly.
    session.request = MagicMock()
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
    # send_command issues the request via ``session.request``.
    mock_client_session.request.return_value = async_context_manager

    success = await client.async_send_command(
        room_id=1, param="p1", val_new="10", val_prev_options=["5"]
    )

    assert success is True
    mock_client_session.request.assert_called_once()


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
    mock_redirect_response.text = AsyncMock(return_value="")
    mock_redirect_response.headers = {"Location": "/login.php"}
    cm_redirect = AsyncMock()
    cm_redirect.__aenter__.return_value = mock_redirect_response

    # 2. Login attempt -> success
    mock_login_response = MagicMock()
    mock_login_response.status = 200
    mock_login_response.text = AsyncMock(return_value=json.dumps({"info": "success"}))
    mock_login_response.headers = {}
    cm_login = AsyncMock()
    cm_login.__aenter__.return_value = mock_login_response

    # 3. Second command attempt -> success
    mock_command_success_response = MagicMock()
    mock_command_success_response.status = 200
    json_payload = {"info": "success_non_json"}
    mock_command_success_response.json = AsyncMock(return_value=json_payload)
    mock_command_success_response.text = AsyncMock(
        return_value=json.dumps(json_payload)
    )
    cm_command_success = AsyncMock()
    cm_command_success.__aenter__.return_value = mock_command_success_response

    # The command + retry go through ``session.request`` (redirect, then the
    # successful retry); the re-login in between goes through ``session.post``.
    mock_client_session.request.side_effect = [
        cm_redirect,
        cm_command_success,
    ]
    mock_client_session.post.return_value = cm_login

    success = await client.async_send_command(
        room_id=1, param="p1", val_new="10", val_prev_options=["5"]
    )

    assert success is True
    assert mock_client_session.request.call_count == 2
    assert mock_client_session.post.call_count == 1


@pytest.mark.asyncio
async def test_login_redirect_raises_auth_error(mock_client_session):
    """A redirect on login means the host/path is wrong; must not report success."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    configure_mock_response(
        mock_client_session.post,
        status=302,
        text="",
        headers={"Location": "https://mock_host/"},
    )

    with pytest.raises(InnotempAuthError):
        await client.async_login()
    assert client._is_logged_in is False


@pytest.mark.asyncio
async def test_login_non_json_response_raises_auth_error(mock_client_session):
    """An HTML login page (not JSON) must raise instead of passing silently."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    configure_mock_response(
        mock_client_session.post, text="<html><body>Login</body></html>"
    )

    with pytest.raises(InnotempAuthError):
        await client.async_login()
    assert client._is_logged_in is False


@pytest.mark.asyncio
async def test_login_empty_response_raises_auth_error(mock_client_session):
    """An empty body on login must raise instead of passing silently."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    configure_mock_response(mock_client_session.post, text="")

    with pytest.raises(InnotempAuthError):
        await client.async_login()
    assert client._is_logged_in is False


@pytest.mark.asyncio
async def test_login_warns_when_session_cookie_not_stored(mock_client_session, caplog):
    """If the server sets a cookie but the jar drops it, a warning must be logged.

    aiohttp's default CookieJar silently discards cookies from bare-IP hosts,
    which previously made login look fine while everything after it failed.
    """
    client = InnotempApiClient(
        mock_client_session, "192.168.1.50", "mock_user", "mock_password"
    )
    configure_mock_response(
        mock_client_session.post,
        json_data={"info": "success"},
        headers={"Set-Cookie": "PHPSESSID=abc123; path=/"},
    )
    # Simulate an empty cookie jar (cookie was dropped).
    mock_client_session.cookie_jar = iter(())

    await client.async_login()

    assert client._is_logged_in is True
    assert any(
        "cookie jar did not store it" in record.message
        for record in caplog.records
        if record.levelname == "WARNING"
    )


@pytest.mark.asyncio
async def test_send_command_sends_credentials_and_string_values(
    mock_client_session,
):
    """The command payload must contain credentials and stringified values."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    configure_mock_response(mock_client_session.request, json_data={"info": "success"})

    success = await client.async_send_command(
        room_id=3, param="003_e17par02_gui001out1", val_new=1, val_prev_options=[0]
    )

    assert success is True
    call = mock_client_session.request.call_args
    assert call.args[0] == "POST"
    assert call.args[1].endswith("/value.save.php")
    assert call.kwargs["data"] == {
        "un": "mock_user",
        "pw": "mock_password",
        "room_id": "3",
        "param": "003_e17par02_gui001out1",
        "val_new": "1",
        "val_prev": "0",
    }


@pytest.mark.asyncio
async def test_send_command_tries_fallback_prev_values(mock_client_session):
    """A rejected val_prev must be retried with the next fallback value."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True

    def make_cm(payload):
        response = MagicMock()
        response.status = 200
        response.text = AsyncMock(return_value=json.dumps(payload))
        response.headers = {}
        response.raise_for_status = MagicMock()
        cm = AsyncMock()
        cm.__aenter__.return_value = response
        return cm

    mock_client_session.request.side_effect = [
        make_cm({"info": "error", "error": "value mismatch"}),
        make_cm({"info": "success"}),
    ]

    success = await client.async_send_command(
        room_id=1, param="p1", val_new="1", val_prev_options=["0", None]
    )

    assert success is True
    assert mock_client_session.request.call_count == 2
    first, second = mock_client_session.request.call_args_list
    assert first.kwargs["data"]["val_prev"] == "0"
    # None fallback is sent as an empty string.
    assert second.kwargs["data"]["val_prev"] == ""


@pytest.mark.asyncio
async def test_send_command_returns_false_when_all_prev_rejected(
    mock_client_session,
):
    """If every val_prev attempt is rejected the command must report failure."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    configure_mock_response(
        mock_client_session.request,
        json_data={"info": "error", "error": "value mismatch"},
    )

    success = await client.async_send_command(
        room_id=1, param="p1", val_new="1", val_prev_options=["0", "1", None]
    )

    assert success is False
    assert mock_client_session.request.call_count == 3


@pytest.mark.asyncio
async def test_send_command_handles_non_string_info(mock_client_session):
    """A non-string 'info' field must not crash the success check."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    configure_mock_response(mock_client_session.request, json_data={"info": None})

    success = await client.async_send_command(
        room_id=1, param="p1", val_new="1", val_prev_options=["0"]
    )

    assert success is False


@pytest.mark.asyncio
async def test_api_request_access_denied_payload_triggers_relogin(
    mock_client_session,
):
    """An HTTP-200 'Access denied.' payload must trigger re-login and retry."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True

    def make_cm(payload):
        response = MagicMock()
        response.status = 200
        response.text = AsyncMock(return_value=json.dumps(payload))
        response.headers = {}
        response.raise_for_status = MagicMock()
        cm = AsyncMock()
        cm.__aenter__.return_value = response
        return cm

    mock_client_session.request.side_effect = [
        make_cm({"info": "error", "error": "Access denied."}),
        make_cm({"info": "success"}),
    ]
    mock_client_session.post.return_value = make_cm({"info": "success"})

    success = await client.async_send_command(
        room_id=1, param="p1", val_new="1", val_prev_options=["0"]
    )

    assert success is True
    assert mock_client_session.request.call_count == 2
    assert mock_client_session.post.call_count == 1


async def _run_one_sse_iteration(client):
    """Run one iteration of the SSE listener (the retry sleep is cancelled)."""
    with patch(
        "custom_components.innotemp.api.asyncio.sleep",
        side_effect=asyncio.CancelledError,
    ):
        await client.async_sse_connect(MagicMock())
        with pytest.raises(asyncio.CancelledError):
            await client._sse_task
    client._sse_task = None


@pytest.mark.asyncio
async def test_sse_redirect_marks_session_expired(mock_client_session):
    """A redirected SSE request means the session is dead: force a re-login.

    Previously the redirect was followed to the login page, the stream ended
    with zero messages and the listener reconnected forever without ever
    re-authenticating.
    """
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    client._signal_names_cache = ["sig1"]

    mock_response = MagicMock()
    mock_response.status = 302
    mock_response.headers = {"Location": "/index.php"}
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_response
    mock_client_session.get.return_value = cm

    await _run_one_sse_iteration(client)

    assert client._is_logged_in is False


@pytest.mark.asyncio
async def test_sse_stream_maps_values_to_signal_names(mock_client_session):
    """SSE data lines must be zipped with the signal names and forwarded."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    client._signal_names_cache = ["temp_top", "battery_soc"]

    async def line_iter():
        yield b": keep-alive\n"
        yield b'data: [21.5, "88.0"]\n'

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.headers = {}
    mock_response.raise_for_status = MagicMock()
    mock_response.content = line_iter()
    cm = AsyncMock()
    cm.__aenter__.return_value = mock_response
    mock_client_session.get.return_value = cm

    received = []
    with patch(
        "custom_components.innotemp.api.asyncio.sleep",
        side_effect=asyncio.CancelledError,
    ):
        await client.async_sse_connect(received.append)
        with pytest.raises(asyncio.CancelledError):
            await client._sse_task
    client._sse_task = None

    assert received == [{"temp_top": 21.5, "battery_soc": "88.0"}]
    # After a stream ends the client must re-authenticate before reconnecting.
    assert client._is_logged_in is False
