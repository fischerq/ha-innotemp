import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import ClientSession
import json

from custom_components.innotemp.api import (
    InnotempApiClient,
    InnotempAuthError,
    InnotempCommandError,
    InnotempConnectionError,
    InnotempInvalidResponseError,
    format_api_value,
    normalize_host,
    parse_sse_data,
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
    json_payload = {"info": "success"}
    mock_response.json = AsyncMock(return_value=json_payload)
    mock_response.text = AsyncMock(return_value=json.dumps(json_payload))
    mock_response.raise_for_status = MagicMock()

    async_context_manager = AsyncMock()
    async_context_manager.__aenter__.return_value = mock_response
    # send_command issues the request via ``session.request``.
    mock_client_session.request.return_value = async_context_manager

    result = await client.async_send_command(
        room_id=1, param="p1", val_new="10", val_prev="5"
    )

    assert result == {"info": "success"}
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
    json_payload = {"info": "success"}
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

    await client.async_send_command(room_id=1, param="p1", val_new="10", val_prev="5")

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
        room_id=3, param="003_e17par02_gui001out1", val_new=1, val_prev=0
    )

    assert success == {"info": "success"}
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
async def test_send_command_sends_val_prev_once_and_raises_on_rejection(
    mock_client_session,
):
    """A rejected write must raise, without brute-forcing other val_prev values.

    The controller uses val_prev for optimistic locking; retrying with guessed
    previous values (0, 1, '') defeated that check.
    """
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    configure_mock_response(
        mock_client_session.request,
        json_data={"info": "error", "error": "value mismatch"},
    )

    with pytest.raises(InnotempCommandError):
        await client.async_send_command(
            room_id=1, param="p1", val_new="1", val_prev="0"
        )
    assert mock_client_session.request.call_count == 1
    assert mock_client_session.request.call_args.kwargs["data"]["val_prev"] == "0"


@pytest.mark.parametrize(
    ("val_prev", "sent"),
    [(None, ""), ("1.0", "1"), (18.0, "18"), (21.5, "21.5"), ("2", "2")],
)
@pytest.mark.asyncio
async def test_send_command_formats_val_prev(mock_client_session, val_prev, sent):
    """val_prev is normalised the way the web UI sends values."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    configure_mock_response(mock_client_session.request, json_data={"info": "success"})

    await client.async_send_command(room_id=1, param="p1", val_new=1, val_prev=val_prev)

    assert mock_client_session.request.call_args.kwargs["data"]["val_prev"] == sent


@pytest.mark.parametrize(
    "body",
    [
        "OK",
        "1",
        "Gespeichert",
        '"success"',
        "[]",
        '{"info": "success_x"}',
        '{"info": null}',
    ],
)
@pytest.mark.asyncio
async def test_send_command_non_json_or_unconfirmed_is_failure(
    mock_client_session, body
):
    """A 200 that is not ``{"info": "success"}`` must not count as success.

    Previously any non-JSON 200 was turned into ``{"info": "success_non_json"}``
    and reported as a successful write.
    """
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    configure_mock_response(mock_client_session.request, text=body)

    with pytest.raises((InnotempCommandError, InnotempInvalidResponseError)):
        await client.async_send_command(
            room_id=1, param="p1", val_new="1", val_prev="0"
        )


@pytest.mark.asyncio
async def test_api_request_non_json_raises_invalid_response(mock_client_session):
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    configure_mock_response(mock_client_session.request, text="not json at all")

    with pytest.raises(InnotempInvalidResponseError):
        await client._api_request("POST", "roomconf.read.php", {})


@pytest.mark.asyncio
async def test_api_request_html_page_is_treated_as_expired_session(
    mock_client_session,
):
    """An HTML login page instead of JSON means the session expired."""
    client = InnotempApiClient(
        mock_client_session, "mock_host", "mock_user", "mock_password"
    )
    client._is_logged_in = True
    configure_mock_response(
        mock_client_session.request,
        text="<!DOCTYPE html><html><form action='login'></form></html>",
    )

    with pytest.raises(InnotempAuthError):
        await client._api_request("POST", "value.save.php", {})


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

    result = await client.async_send_command(
        room_id=1, param="p1", val_new="1", val_prev="0"
    )

    assert result == {"info": "success"}
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


# --- SSE payload parsing ----------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # Comma-separated, as documented in docs/API_SPEC.md
        ("21.5,88.0,1", ["21.5", "88.0", "1"]),
        (b"data: 21.5, 88.0 ,1\n", ["21.5", "88.0", "1"]),
        ("data:21.5,88.0\r\n", ["21.5", "88.0"]),
        ('"21.5","off"', ["21.5", "off"]),
        ("42", ["42"]),
        ("1,,3", ["1", "", "3"]),
        # JSON array, as the previous implementation assumed
        ('[21.5, "88.0"]', [21.5, "88.0"]),
        (b"data: [1, 2, 3]\n", [1, 2, 3]),
        ("[1,2", ["1", "2"]),  # truncated JSON falls back to CSV
        # Nothing usable
        ("", None),
        ("data:", None),
        ('{"a": 1}', None),
        (None, None),
    ],
)
def test_parse_sse_data_accepts_csv_and_json(payload, expected):
    assert parse_sse_data(payload) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (True, "1"),
        (0, "0"),
        (21.0, "21"),
        (21.5, "21.5"),
        ("1.0", "1"),
        (" 18 ", "18"),
        ("18.25", "18.25"),
        ("abc", "abc"),
        ("", ""),
    ],
)
def test_format_api_value(value, expected):
    assert format_api_value(value) == expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("192.168.25.204", "192.168.25.204"),
        (" 192.168.25.204/ ", "192.168.25.204"),
        ("Innotemp.LOCAL", "innotemp.local"),
        ("innotemp:8080", "innotemp:8080"),
        ("[fe80::1]:80", "[fe80::1]:80"),
    ],
)
def test_normalize_host_valid(host, expected):
    assert normalize_host(host) == expected


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        " ",
        "http",
        "HTTPS",
        "http://x",
        "x/inc",
        "a b",
        "-x",
        "x:0",
        "x:70000",
    ],
)
def test_normalize_host_invalid(host):
    with pytest.raises(ValueError):
        normalize_host(host)


# --- SSE listener branches --------------------------------------------------


def _sse_cm(status=200, lines=(), headers=None, raise_exc=None):
    async def line_iter():
        for line in lines:
            yield line

    response = MagicMock()
    response.status = status
    response.headers = headers or {}
    response.raise_for_status = MagicMock()
    if raise_exc is not None:
        response.raise_for_status.side_effect = raise_exc
    response.content = line_iter()
    cm = AsyncMock()
    cm.__aenter__.return_value = response
    return cm


async def _run_sse_until(client, received, count, timeout=2):
    client.sse_reconnect_delay = 0
    await client.async_sse_connect(received.append)
    try:
        async with asyncio.timeout(timeout):
            while len(received) < count:
                await asyncio.sleep(0.001)
    finally:
        await client.async_sse_disconnect()


@pytest.mark.asyncio
async def test_sse_stream_parses_comma_separated_values(mock_client_session):
    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    client._is_logged_in = True
    client._signal_names_cache = ["temp_top", "battery_soc", "pump"]
    mock_client_session.get.return_value = _sse_cm(
        lines=[b"retry: 1000\n", b"data: 21.5,88.0,1\n", b"\n"]
    )
    received = []
    await _run_sse_until(client, received, 1)
    assert received[0] == {"temp_top": "21.5", "battery_soc": "88.0", "pump": "1"}


@pytest.mark.asyncio
async def test_sse_reconnects_after_connection_error(mock_client_session):
    """A dropped connection is retried with a fresh login."""
    import aiohttp

    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    client._signal_names_cache = ["a"]
    configure_mock_response(mock_client_session.post, json_data={"info": "success"})
    mock_client_session.get.side_effect = [
        aiohttp.ClientConnectionError("reset"),
        _sse_cm(lines=[b"data: 7\n"]),
    ] + [_sse_cm() for _ in range(50)]

    received = []
    await _run_sse_until(client, received, 1)

    assert received[0] == {"a": "7"}
    assert mock_client_session.post.call_count >= 2  # re-login before reconnect


@pytest.mark.asyncio
async def test_sse_redirect_then_relogin_and_resume(mock_client_session):
    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    client._is_logged_in = True
    client._signal_names_cache = ["a"]
    configure_mock_response(mock_client_session.post, json_data={"info": "success"})
    mock_client_session.get.side_effect = [
        _sse_cm(status=302, headers={"Location": "/index.php"}),
        _sse_cm(lines=[b"data: 5\n"]),
    ] + [_sse_cm() for _ in range(50)]

    received = []
    await _run_sse_until(client, received, 1)

    assert received[0] == {"a": "5"}
    assert mock_client_session.post.call_count >= 1


@pytest.mark.asyncio
async def test_sse_length_mismatch_refetches_signal_names(mock_client_session):
    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    client._is_logged_in = True
    client._signal_names_cache = ["a"]  # stale: stream has two values
    configure_mock_response(mock_client_session.post, json_data={"info": "success"})
    configure_mock_response(mock_client_session.request, json_data=["a", "b"])
    mock_client_session.get.side_effect = [
        _sse_cm(lines=[b"data: 1,2\n"]),
        _sse_cm(lines=[b"data:\n", b"data: {}\n", b"data: 3,4\n"]),
    ] + [_sse_cm() for _ in range(50)]

    received = []
    await _run_sse_until(client, received, 1)

    assert received[0] == {"a": "3", "b": "4"}
    assert client._signal_names_cache == ["a", "b"]


@pytest.mark.asyncio
async def test_sse_survives_api_and_unexpected_errors(mock_client_session):
    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    client._is_logged_in = True
    configure_mock_response(mock_client_session.post, json_data={"info": "success"})
    # 1st: signal names not a list (API error); 2nd: unexpected exception
    # from the HTTP layer; 3rd: works.
    mock_client_session.request.side_effect = [
        _sse_cm_text('{"info": "nope"}'),
        _sse_cm_text('["a"]'),
        _sse_cm_text('["a"]'),
    ]
    mock_client_session.get.side_effect = [
        RuntimeError("boom"),
        _sse_cm(lines=[b"data: 9\n"]),
    ] + [_sse_cm() for _ in range(50)]

    received = []
    await _run_sse_until(client, received, 1)
    assert received[0] == {"a": "9"}


@pytest.mark.asyncio
async def test_sse_http_error_is_retried(mock_client_session):
    import aiohttp

    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    client._is_logged_in = True
    client._signal_names_cache = ["a"]
    configure_mock_response(mock_client_session.post, json_data={"info": "success"})
    err = aiohttp.ClientResponseError(MagicMock(), (), status=500, message="err")
    mock_client_session.get.side_effect = [
        _sse_cm(status=500, raise_exc=err),
        _sse_cm(lines=[b"data: 1\n"]),
    ] + [_sse_cm() for _ in range(50)]

    received = []
    await _run_sse_until(client, received, 1)
    assert received[0] == {"a": "1"}


@pytest.mark.asyncio
async def test_sse_connect_twice_does_not_start_second_listener(mock_client_session):
    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    blocker = asyncio.Event()

    async def never(*_a, **_k):
        await blocker.wait()

    client.async_login = never
    await client.async_sse_connect(lambda d: None)
    first = client._sse_task
    await client.async_sse_connect(lambda d: None)
    assert client._sse_task is first
    await client.async_sse_disconnect()
    assert client._sse_task is None


def _sse_cm_text(text):
    response = MagicMock()
    response.status = 200
    response.headers = {}
    response.text = AsyncMock(return_value=text)
    response.raise_for_status = MagicMock()
    cm = AsyncMock()
    cm.__aenter__.return_value = response
    return cm


# --- login / request error mapping ---------------------------------------------


@pytest.mark.asyncio
async def test_login_connection_error_is_not_an_auth_error(mock_client_session):
    """An offline controller must be distinguishable from bad credentials."""
    import aiohttp

    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    mock_client_session.post.side_effect = aiohttp.ClientConnectorError(
        MagicMock(), OSError("unreachable")
    )
    with pytest.raises(InnotempConnectionError):
        await client.async_login()


@pytest.mark.asyncio
async def test_login_timeout_is_connection_error(mock_client_session):
    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    mock_client_session.post.side_effect = asyncio.TimeoutError()
    with pytest.raises(InnotempConnectionError):
        await client.async_login()


@pytest.mark.asyncio
async def test_login_http_500_is_connection_error(mock_client_session):
    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    configure_mock_response(mock_client_session.post, status=500, text="oops")
    with pytest.raises(InnotempConnectionError):
        await client.async_login()


@pytest.mark.asyncio
async def test_api_request_connection_error(mock_client_session):
    import aiohttp

    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    client._is_logged_in = True
    mock_client_session.request.side_effect = aiohttp.ServerDisconnectedError()
    with pytest.raises(InnotempConnectionError):
        await client.async_get_config()


@pytest.mark.parametrize(("status", "exc"), [(403, InnotempAuthError), (500, None)])
@pytest.mark.asyncio
async def test_api_request_http_errors(mock_client_session, status, exc):
    import aiohttp

    from custom_components.innotemp.api import InnotempApiError

    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    response = MagicMock()
    response.status = status
    response.headers = {}
    response.text = AsyncMock(return_value="")
    response.raise_for_status = MagicMock(
        side_effect=aiohttp.ClientResponseError(
            MagicMock(), (), status=status, message="x"
        )
    )
    cm = AsyncMock()
    cm.__aenter__.return_value = response
    mock_client_session.request.return_value = cm
    with pytest.raises(exc or InnotempApiError):
        await client._api_request("POST", "roomconf.read.php", {})


@pytest.mark.asyncio
async def test_get_config_non_dict_returns_none(mock_client_session):
    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    client._is_logged_in = True
    configure_mock_response(mock_client_session.request, text="")
    assert await client.async_get_config() is None


@pytest.mark.asyncio
async def test_signal_names_cached_and_validated(mock_client_session):
    client = InnotempApiClient(mock_client_session, "h", "u", "p")
    client._is_logged_in = True
    configure_mock_response(mock_client_session.request, json_data=["a", "b"])
    assert await client.async_get_signal_names() == ["a", "b"]
    assert await client.async_get_signal_names() == ["a", "b"]
    assert mock_client_session.request.call_count == 1

    client._signal_names_cache = None
    configure_mock_response(mock_client_session.request, json_data=[])
    with pytest.raises(InnotempInvalidResponseError):
        await client.async_get_signal_names()
