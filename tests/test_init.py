"""Tests for integration setup and unload (custom_components/innotemp/__init__.py)."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.innotemp.const import DOMAIN

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


def _patch_api():
    """Patch all network-touching API methods."""
    return (
        patch(
            "custom_components.innotemp.InnotempApiClient.async_login",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.innotemp.InnotempApiClient.async_get_config",
            new_callable=AsyncMock,
            return_value=SAMPLE_CONFIG,
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


@pytest.mark.asyncio
async def test_setup_entry_aborts_on_login_failure(hass: HomeAssistant):
    """A failing login must abort setup instead of half-initialising."""
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
