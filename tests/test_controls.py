"""Regression tests for the writable control platforms (switch/select/number).

These cover the command (write) path, which was broken in several ways:

* ``val_prev`` was read from the wrong variable (the mapped state var instead of
  the control's own value), so the controller's optimistic-locking check failed
  and the change was silently dropped.
* ``number`` sent the room's string ``var`` as ``room_id`` instead of the
  numeric room id required by ``value.save.php``.
* No optimistic state update meant the UI snapped back to the old value after a
  successful command.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant import config_entries

from custom_components.innotemp.coordinator import InnotempDataUpdateCoordinator
from custom_components.innotemp.switch import InnotempSwitch
from custom_components.innotemp.select import InnotempInputSelect
from custom_components.innotemp.number import InnotempNumber

ROOM = {"type": "room3", "var": "room3_param1", "label": "Room 3"}
COMP = {"type": "param", "var": "room3_param1"}


def _make_coordinator(data):
    coordinator = MagicMock(spec=InnotempDataUpdateCoordinator)
    coordinator.data = data
    coordinator.control_to_state_map = {}
    coordinator.api_client = MagicMock()
    coordinator.api_client.async_send_command = AsyncMock(return_value=True)
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _config_entry():
    return MagicMock(
        spec=config_entries.ConfigEntry, unique_id="cfg", entry_id="test_entry_id"
    )


@pytest.mark.asyncio
async def test_switch_uses_own_value_for_val_prev_and_updates_state(
    hass: HomeAssistant,
):
    """Switch must send its own current value as the first ``val_prev``."""
    param = "room3_param1_entry1"
    coordinator = _make_coordinator({param: "1"})  # currently ON
    switch = InnotempSwitch(
        coordinator,
        _config_entry(),
        ROOM,
        3,
        COMP,
        param,
        {"unit": "ONOFF", "var": param, "label": "Pump"},
    )
    switch.async_write_ha_state = MagicMock()

    await switch.async_turn_off()

    kwargs = coordinator.api_client.async_send_command.call_args.kwargs
    assert kwargs["room_id"] == 3
    assert kwargs["param"] == param
    assert kwargs["val_new"] == "0"
    # First previous value tried must match the actual current state ("1"),
    # not a blind "0" guess.
    assert kwargs["val_prev_options"][0] == "1"
    # Optimistic update: state reflects the change immediately.
    assert coordinator.data[param] == "0"
    assert switch.is_on is False


@pytest.mark.asyncio
async def test_select_uses_own_value_for_val_prev_and_updates_state(
    hass: HomeAssistant,
):
    """Select must send its own current value as the first ``val_prev``."""
    param = "room3_param1_e2"
    coordinator = _make_coordinator({param: 1})  # currently On
    select = InnotempInputSelect(
        hass,
        coordinator,
        _config_entry(),
        ROOM,
        3,
        COMP,
        param,
        {"unit": "ONOFFAUTO", "var": param, "label": "Mode"},
    )
    select.async_write_ha_state = MagicMock()

    await select.async_select_option("Auto")

    kwargs = coordinator.api_client.async_send_command.call_args.kwargs
    assert kwargs["room_id"] == 3
    assert kwargs["param"] == param
    assert kwargs["val_new"] == 2  # Auto
    assert kwargs["val_prev_options"][0] == 1  # current value first
    assert coordinator.data[param] == 2
    assert select.current_option == "Auto"


@pytest.mark.asyncio
async def test_number_sends_numeric_room_id_and_updates_state(hass: HomeAssistant):
    """Number must send the numeric room id, not the room's string var."""
    param = "room3_param1_n1"
    coordinator = _make_coordinator({param: "18"})
    number = InnotempNumber(
        coordinator,
        _config_entry(),
        ROOM,
        COMP,
        param,
        {"unit": "°C", "var": param, "label": "Setpoint"},
    )
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(21.0)

    kwargs = coordinator.api_client.async_send_command.call_args.kwargs
    # Regression: previously this was "room3_param1" (the string var).
    assert kwargs["room_id"] == 3
    assert kwargs["param"] == param
    assert kwargs["val_new"] == 21.0
    assert kwargs["val_prev_options"][0] == "18"
    assert coordinator.data[param] == 21.0
    assert number.native_value == 21.0
