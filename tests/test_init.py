"""Tests for integration setup and unload (custom_components/innotemp/__init__.py)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.helpers import entity_registry as er

from custom_components.innotemp.api import (
    InnotempAuthError,
    InnotempConnectionError,
)
from custom_components.innotemp.const import DOMAIN

ROOMCONF = json.loads(
    (Path(__file__).parent / "roomconf_test_data.json").read_text(encoding="utf-8")
)

SAMPLE_CONFIG = {
    "ROOMCONF": [
        {
            "@attributes": {"type": "room1", "var": "room1_var"},
        }
    ]
}


@pytest.fixture(autouse=True)
def mock_setup_entry():
    """Override the conftest autouse fixture: run the real async_setup_entry."""
    yield


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Innotemp Heating Controller",
        data={
            "host": "192.168.1.50",
            "username": "user",
            "password": "pass",
        },
        unique_id="innotemp_test",
    )


def _patch_api(config=SAMPLE_CONFIG):
    """Patch all network-touching API methods."""
    return (
        patch(
            "custom_components.innotemp.InnotempApiClient.async_login",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.innotemp.InnotempApiClient.async_get_config",
            new_callable=AsyncMock,
            return_value=config,
        ),
        patch(
            "custom_components.innotemp.InnotempApiClient.async_sse_connect",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.innotemp.InnotempApiClient.async_sse_disconnect",
            new_callable=AsyncMock,
        ),
    )


@pytest.mark.asyncio
async def test_setup_and_unload_entry(hass: HomeAssistant):
    """The entry must set up, store its data, and clean up fully on unload."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    p_login, p_config, p_connect, p_disconnect = _patch_api()
    with p_login as mock_login, p_config, p_connect, p_disconnect as mock_disconnect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        mock_login.assert_awaited()
        data = hass.data[DOMAIN][entry.entry_id]
        assert data["config"] == SAMPLE_CONFIG

        # The session must keep cookies from IP hosts (PHPSESSID auth): the
        # default "safe" jar silently drops them, breaking everything after
        # login.
        session = data["session"]
        assert session.cookie_jar._unsafe is True

        # Unload must stop the SSE listener, close the session and drop the
        # stored data (previously there was no async_unload_entry at all and
        # the SSE task leaked forever).
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.NOT_LOADED
        assert entry.entry_id not in hass.data[DOMAIN]
        mock_disconnect.assert_awaited()
        assert session.closed


@pytest.mark.parametrize(
    "exc",
    [
        InnotempConnectionError("controller offline"),
        InnotempAuthError("login rejected"),
    ],
)
@pytest.mark.asyncio
async def test_setup_entry_not_ready_when_controller_unavailable(
    hass: HomeAssistant, exc
):
    """An unreachable controller must make HA retry (ConfigEntryNotReady).

    Previously setup returned False, leaving the entry in SETUP_ERROR with no
    retry, so a controller that was offline at boot never came back.
    """
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.innotemp.InnotempApiClient.async_login",
        new_callable=AsyncMock,
        side_effect=exc,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_setup_entry_not_ready_when_config_empty(hass: HomeAssistant):
    entry = _make_entry()
    entry.add_to_hass(hass)

    p_login, p_config, p_connect, p_disconnect = _patch_api(config=None)
    with p_login, p_config, p_connect, p_disconnect:
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_setup_entry_aborts_on_unexpected_login_failure(hass: HomeAssistant):
    """An unexpected (non-API) error is a bug: setup errors without retry."""
    entry = _make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.innotemp.InnotempApiClient.async_login",
        new_callable=AsyncMock,
        side_effect=Exception("boom"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.asyncio
async def test_setup_entry_aborts_on_invalid_host(hass: HomeAssistant):
    """A stored host like 'http' must abort setup with a clear error."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "http", "username": "user", "password": "pass"},
        unique_id="innotemp_bad_host",
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


def _legacy_entry(host="192.168.1.50", unique_id=None) -> MockConfigEntry:
    """An entry as created by the pre-1.2 config flow (no unique id)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Innotemp Heating Controller",
        data={"host": host, "username": "user", "password": "pass"},
        unique_id=unique_id,
        version=1,
        minor_version=1,
    )


@pytest.mark.asyncio
async def test_migration_renames_legacy_none_unique_ids(hass: HomeAssistant):
    """Entities registered as ``None_<param>`` are renamed in place.

    The entity_id (and with it history and customisations) must be kept, and
    setting up the platforms afterwards must not create a duplicate entity.
    """
    entry = _legacy_entry()
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    legacy_sensor = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        "None_room1_display1_inp1",
        config_entry=entry,
        suggested_object_id="legacy_buffer_top",
    )
    legacy_select = ent_reg.async_get_or_create(
        "select",
        DOMAIN,
        "None_room1_param1_entry1",
        config_entry=entry,
        suggested_object_id="legacy_mode",
    )

    p_login, p_config, p_connect, p_disconnect = _patch_api(config=ROOMCONF)
    with p_login, p_config, p_connect, p_disconnect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.unique_id == "192.168.1.50"
    assert (entry.version, entry.minor_version) == (1, 2)

    sensor = ent_reg.async_get(legacy_sensor.entity_id)
    assert sensor.unique_id == "192.168.1.50_room1_display1_inp1"
    assert ent_reg.async_get(legacy_select.entity_id).unique_id == (
        "192.168.1.50_room1_param1_entry1"
    )

    entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    assert not [e for e in entries if e.unique_id.startswith("None_")]
    # No duplicate was created for the migrated sensor.
    same_param = [
        e
        for e in entries
        if e.domain == "sensor" and e.unique_id.endswith("_room1_display1_inp1")
    ]
    assert [e.entity_id for e in same_param] == ["sensor.legacy_buffer_top"]
    # Freshly discovered entities use the same scheme.
    assert len(entries) > 5
    assert all(e.unique_id.startswith("192.168.1.50_") for e in entries)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_migration_falls_back_to_entry_id_when_host_taken(hass: HomeAssistant):
    """If another entry already owns the host unique id, use entry_id."""
    _legacy_entry(unique_id="192.168.1.50").add_to_hass(hass)
    entry = _legacy_entry()
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    legacy = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "None_temp1", config_entry=entry
    )

    p_login, p_config, p_connect, p_disconnect = _patch_api()
    with p_login, p_config, p_connect, p_disconnect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.unique_id is None
    assert entry.minor_version == 2
    assert ent_reg.async_get(legacy.entity_id).unique_id == f"{entry.entry_id}_temp1"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_migration_skips_rename_when_target_exists(hass: HomeAssistant):
    """A legacy entity is left alone if its new unique id is already taken."""
    entry = _legacy_entry()
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    legacy = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "None_temp1", config_entry=entry
    )
    current = ent_reg.async_get_or_create(
        "sensor", DOMAIN, "192.168.1.50_temp1", config_entry=entry
    )

    p_login, p_config, p_connect, p_disconnect = _patch_api()
    with p_login, p_config, p_connect, p_disconnect:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert ent_reg.async_get(legacy.entity_id).unique_id == "None_temp1"
    assert ent_reg.async_get(current.entity_id).unique_id == "192.168.1.50_temp1"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_migration_refuses_future_version(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.1.50", "username": "u", "password": "p"},
        version=2,
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
