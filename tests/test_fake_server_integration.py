"""Integration-style tests against the fake controller in fake_innotemp.py.

Unlike the unit tests, nothing here is mocked: the real InnotempApiClient
(and, further down, the whole integration inside Home Assistant) talks HTTP
to an aiohttp server that keeps sessions, redirects expired sessions to the
login page, streams SSE and enforces ``val_prev`` on writes.

The fixtures in tests/fixtures/synthetic are SYNTHETIC. If a recording from a
real controller exists in tests/fixtures/recorded (see scripts/smoke_test.py
--record), the read-path test also runs against it.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.innotemp.api import (
    InnotempApiClient,
    InnotempAuthError,
    InnotempCommandError,
    InnotempInvalidResponseError,
)
from custom_components.innotemp.api_parser import extract_initial_states
from custom_components.innotemp.const import DOMAIN

from tests.fake_innotemp import RECORDED_DIR, SYNTHETIC_DIR, FakeInnotemp

# The HA test plugin blocks socket creation; this test module needs a real
# TCP server on 127.0.0.1 (connections elsewhere stay blocked).
pytestmark = pytest.mark.usefixtures("socket_enabled")

HEATING_ROD = "003_e17par02_gui001out1"  # ONOFFAUTO, live signal, room 3
SETPOINT = "001_mixer001_entry1"  # °C setting, NOT a live signal, room 1

FIXTURE_SETS = [pytest.param(SYNTHETIC_DIR, id="synthetic")]
if (RECORDED_DIR / "live_signal.read.json").exists():
    FIXTURE_SETS.append(pytest.param(RECORDED_DIR, id="recorded"))


async def _start(fake: FakeInnotemp) -> TestServer:
    server = TestServer(fake.make_app(), host="127.0.0.1")
    await server.start_server()
    return server


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
async def fake_and_client():
    fake = FakeInnotemp(SYNTHETIC_DIR)
    server = await _start(fake)
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    client = InnotempApiClient(
        session, f"{server.host}:{server.port}", fake.username, fake.password
    )
    client.sse_reconnect_delay = 0.05
    try:
        yield fake, client
    finally:
        await client.async_sse_disconnect()
        await session.close()
        fake.expire_sessions()
        await server.close()


@pytest.mark.parametrize("fixture_dir", FIXTURE_SETS)
@pytest.mark.asyncio
async def test_read_path_against_fixture_set(fixture_dir):
    """Login -> roomconf -> signal names -> SSE values, for each fixture set."""
    fake = FakeInnotemp(fixture_dir)
    server = await _start(fake)
    session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    client = InnotempApiClient(
        session, f"{server.host}:{server.port}", fake.username, fake.password
    )
    try:
        await client.async_login()
        assert fake.logins == 1
        assert "PHPSESSID" in {c.key for c in session.cookie_jar}

        config = await client.async_get_config()
        assert isinstance(config, dict) and config

        names = await client.async_get_signal_names()
        assert names == fake.signal_names

        received: list[dict] = []
        await client.async_sse_connect(received.append)
        await _wait_for(lambda: len(received) >= 2)
        for message in received:
            assert list(message) == names
    finally:
        await client.async_sse_disconnect()
        await session.close()
        fake.expire_sessions()
        await server.close()


@pytest.mark.asyncio
async def test_full_client_flow(fake_and_client):
    """Login, config, SSE, write, stale write, session expiry and re-login."""
    fake, client = fake_and_client

    await client.async_login()
    config = await client.async_get_config()
    initial = extract_initial_states(config)
    assert initial[SETPOINT] == "45"
    assert initial[HEATING_ROD] == "2"

    received: list[dict] = []
    await client.async_sse_connect(received.append)
    await _wait_for(lambda: len(received) >= 1)
    # Comma-separated SSE payload, mapped by position to the signal names.
    assert received[0]["001_d_display002inp1"] == "61.2"
    assert received[0][HEATING_ROD] == "2"
    assert SETPOINT not in received[0]

    # Write with the correct val_prev: accepted and reflected in the stream.
    result = await client.async_send_command(3, HEATING_ROD, 1, val_prev="2")
    assert result["info"] == "success"
    assert fake.writes[-1] == {
        "un": fake.username,
        "pw": fake.password,
        "room_id": "3",
        "param": HEATING_ROD,
        "val_new": "1",
        "val_prev": "2",
    }
    await _wait_for(lambda: received[-1][HEATING_ROD] == "1")

    # A stale val_prev is rejected by the optimistic lock and surfaces as an
    # error (no silent retry with guessed values).
    writes_before = len(fake.writes)
    with pytest.raises(InnotempCommandError):
        await client.async_send_command(3, HEATING_ROD, 0, val_prev="2")
    assert len(fake.writes) == writes_before + 1
    assert fake.values[HEATING_ROD] == "1"

    # Settings that are not live signals are writable too (float -> "21.5").
    await client.async_send_command(1, SETPOINT, 21.5, val_prev="45")
    assert fake.values[SETPOINT] == "21.5"

    # Session expiry: the next request is redirected, the client logs in
    # again and retries; the SSE stream ends and reconnects with a new login.
    logins_before = fake.logins
    connections_before = fake.sse_connections
    fake.expire_sessions()
    await client.async_send_command(3, HEATING_ROD, 2, val_prev="1")
    assert fake.redirects >= 1
    assert fake.logins > logins_before
    await _wait_for(lambda: fake.sse_connections > connections_before)
    count = len(received)
    await _wait_for(lambda: len(received) > count + 1)
    assert received[-1][HEATING_ROD] == "2"


@pytest.mark.asyncio
async def test_wrong_password_is_auth_error(fake_and_client):
    fake, client = fake_and_client
    client._password = "wrong"
    with pytest.raises(InnotempAuthError):
        await client.async_login()
    assert fake.failed_logins == 1


@pytest.mark.asyncio
async def test_non_json_write_response_is_failure(fake_and_client):
    """A 200 with a non-JSON body must not be reported as a successful write."""
    fake, client = fake_and_client
    await client.async_login()
    fake.save_override = ("text/plain", "OK")
    with pytest.raises(InnotempInvalidResponseError):
        await client.async_send_command(3, HEATING_ROD, 1, val_prev="2")


@pytest.mark.asyncio
async def test_html_write_response_triggers_relogin(fake_and_client):
    """An HTML page instead of JSON is treated as an expired session."""
    fake, client = fake_and_client
    await client.async_login()
    fake.save_override = ("text/html", "<html><form>login</form></html>")
    with pytest.raises(InnotempAuthError):
        await client.async_send_command(3, HEATING_ROD, 1, val_prev="2")
    assert fake.logins == 2  # retried once after re-login


# --- The whole integration inside Home Assistant ------------------------------


@pytest.fixture
def mock_setup_entry():
    """Override the conftest autouse fixture: run the real async_setup_entry."""
    yield


@pytest.fixture
async def fake_server():
    fake = FakeInnotemp(SYNTHETIC_DIR)
    server = await _start(fake)
    try:
        yield fake, f"{server.host}:{server.port}"
    finally:
        # End open SSE streams so the server does not wait for them on close.
        fake.expire_sessions()
        await server.close()


def _entity_id(hass, domain, host, param):
    entity_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{host}_{param}"
    )
    assert entity_id, f"no {domain} entity for {param}"
    return entity_id


@pytest.mark.asyncio
async def test_integration_end_to_end(hass: HomeAssistant, fake_server):
    """Set up the real integration against the fake controller."""
    fake, host = fake_server
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": host, "username": fake.username, "password": fake.password},
        unique_id=host,
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator.api_client.sse_reconnect_delay = 0.05
    await _wait_for(
        lambda: fake.sse_connections >= 1
        and coordinator.data.get("001_d_display002inp1") is not None
    )
    await _wait_for(
        lambda: hass.states.get(
            _entity_id(hass, "sensor", host, "001_d_display002inp1")
        ).state
        not in ("unknown", "unavailable")
    )

    # Controller and room devices exist, so entities' via_device resolves.
    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    room = dev_reg.async_get_device(
        identifiers={(DOMAIN, entry.entry_id, "001_room_name")}
    )
    assert room is not None and room.name == "Heizraum"

    # Live sensor value from SSE.
    sensor_id = _entity_id(hass, "sensor", host, "001_d_display002inp1")
    assert float(hass.states.get(sensor_id).state) > 0

    # The roomconf-only setting survives SSE pushes (they used to wipe it).
    number_id = _entity_id(hass, "number", host, SETPOINT)
    assert float(hass.states.get(number_id).state) == 45.0

    # Select write: val_prev is the shown value, sent once.
    select_id = _entity_id(hass, "select", host, HEATING_ROD)
    assert hass.states.get(select_id).state == "Auto"
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": select_id, "option": "On"},
        blocking=True,
    )
    assert fake.writes[-1]["val_new"] == "1"
    assert fake.writes[-1]["val_prev"] == "2"
    assert hass.states.get(select_id).state == "On"

    # Number write.
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": number_id, "value": 30},
        blocking=True,
    )
    assert fake.writes[-1]["val_new"] == "30"
    assert fake.writes[-1]["val_prev"] == "45"
    assert fake.values[SETPOINT] == "30"

    # A write the controller does not confirm surfaces as an error.
    fake.save_override = ("text/plain", "OK")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": select_id, "option": "Off"},
            blocking=True,
        )
    assert hass.states.get(select_id).state == "On"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_integration_retries_when_controller_offline(hass: HomeAssistant):
    """No controller listening -> SETUP_RETRY, not a permanent failure."""
    # Grab a free port and close it again so nothing is listening there.
    server = TestServer(FakeInnotemp().make_app(), host="127.0.0.1")
    await server.start_server()
    host = f"{server.host}:{server.port}"
    await server.close()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": host, "username": "user", "password": "secret"},
        unique_id=host,
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
