"""Regression tests for the writable control platforms (switch/select/number).

These cover the command (write) path, which was broken in several ways:

* ``val_prev`` was read from the wrong variable (the mapped state var instead of
  the control's own value), so the controller's optimistic-locking check failed
  and the change was silently dropped.
* ``number`` sent the room's string ``var`` as ``room_id`` instead of the
  numeric room id required by ``value.save.php``.
* No optimistic state update meant the UI snapped back to the old value after a
  successful command.
* ``val_prev`` used to be brute-forced over 0/1/'' which defeats the
  controller's optimistic locking; now exactly the known current value is sent.
* Failed writes were only logged; they now raise HomeAssistantError.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant import config_entries

from custom_components.innotemp.api import InnotempCommandError
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
    # val_prev is exactly the current state, sent once (no guessing).
    assert kwargs["val_prev"] == "1"
    coordinator.api_client.async_send_command.assert_awaited_once()
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
    assert kwargs["val_prev"] == 1  # current value
    coordinator.api_client.async_send_command.assert_awaited_once()
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
    # Integral floats are sent as plain ints (val_new=21, not 21.0), matching
    # what the controller's own web UI sends.
    assert kwargs["val_new"] == 21
    assert kwargs["val_prev"] == "18"
    assert coordinator.data[param] == 21
    assert number.native_value == 21.0


def _make_switch(coordinator, param="room3_param1_entry1"):
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
    return switch


@pytest.mark.parametrize(
    ("api_value", "expected"),
    [
        ("1", True),
        ("1.0", True),  # SSE delivers float-formatted strings
        (1, True),
        (1.0, True),
        ("0", False),
        ("0.0", False),
        (0, False),
        ("garbage", None),
    ],
)
def test_switch_is_on_handles_mixed_value_formats(api_value, expected):
    """``is_on`` must interpret "1.0"/1.0/1 all as on, not just "1"."""
    param = "room3_param1_entry1"
    coordinator = _make_coordinator({param: api_value})
    switch = _make_switch(coordinator, param)
    assert switch.is_on is expected


@pytest.mark.asyncio
async def test_switch_passes_raw_prev_value(hass: HomeAssistant):
    """The raw known value is passed; the API client formats it ("1.0" -> "1")."""
    param = "room3_param1_entry1"
    coordinator = _make_coordinator({param: "1.0"})  # currently ON, float format
    switch = _make_switch(coordinator, param)

    await switch.async_turn_off()

    kwargs = coordinator.api_client.async_send_command.call_args.kwargs
    assert kwargs["val_prev"] == "1.0"


@pytest.mark.asyncio
async def test_switch_falls_back_to_mapped_state_var_for_val_prev(
    hass: HomeAssistant,
):
    """If the control var has no value yet, the mapped state var is used."""
    param = "room3_param1_entry1"
    coordinator = _make_coordinator({"room3_state": "0"})
    coordinator.control_to_state_map = {param: "room3_state"}
    switch = _make_switch(coordinator, param)

    await switch.async_turn_on()

    kwargs = coordinator.api_client.async_send_command.call_args.kwargs
    assert kwargs["val_prev"] == "0"
    assert kwargs["val_new"] == "1"
    assert switch.is_on is True


@pytest.mark.parametrize(
    ("api_value", "expected"),
    [
        (1, "On"),
        ("1", "On"),
        ("2.0", "Auto"),  # SSE delivers float-formatted strings
        (0.0, "Off"),
        ("garbage", None),
    ],
)
def test_select_current_option_handles_mixed_value_formats(
    hass: HomeAssistant, api_value, expected
):
    """``current_option`` must handle "2.0"-style values (int() alone raises)."""
    param = "room3_param1_e2"
    coordinator = _make_coordinator({param: api_value})
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
    assert select.current_option == expected


@pytest.mark.asyncio
async def test_select_normalizes_float_prev_value(hass: HomeAssistant):
    """A float-formatted current value must be sent as its int API form."""
    param = "room3_param1_e2"
    coordinator = _make_coordinator({param: "1.0"})  # currently On, float format
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
    assert kwargs["val_prev"] == "1.0"


@pytest.mark.asyncio
async def test_select_invalid_option_raises(hass: HomeAssistant):
    param = "room3_param1_e2"
    coordinator = _make_coordinator({param: "1"})
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
    with pytest.raises(HomeAssistantError):
        await select.async_select_option("Turbo")
    coordinator.api_client.async_send_command.assert_not_called()


@pytest.mark.asyncio
async def test_number_keeps_fractional_value(
    hass: HomeAssistant,
):
    """Fractional values are sent as-is; val_prev is the known value."""
    param = "room3_param1_n1"
    coordinator = _make_coordinator({param: "18.0"})
    number = InnotempNumber(
        coordinator,
        _config_entry(),
        ROOM,
        COMP,
        param,
        {"unit": "°C", "var": param, "label": "Setpoint"},
    )
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(21.5)

    kwargs = coordinator.api_client.async_send_command.call_args.kwargs
    assert kwargs["val_new"] == 21.5
    assert kwargs["val_prev"] == "18.0"
    coordinator.api_client.async_send_command.assert_awaited_once()


@pytest.mark.asyncio
async def test_number_without_prev_value_still_sends_command(hass: HomeAssistant):
    """With no known previous value, val_prev is None (sent as '')."""
    param = "room3_param1_n1"
    coordinator = _make_coordinator({})
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
    assert kwargs["val_prev"] is None


@pytest.mark.asyncio
async def test_switch_failed_command_does_not_update_state(hass: HomeAssistant):
    """A rejected command raises and must not optimistically flip the state."""
    param = "room3_param1_entry1"
    coordinator = _make_coordinator({param: "1"})
    coordinator.api_client.async_send_command = AsyncMock(
        side_effect=InnotempCommandError("rejected")
    )
    switch = _make_switch(coordinator, param)

    with pytest.raises(HomeAssistantError, match="rejected"):
        await switch.async_turn_off()

    assert coordinator.data[param] == "1"
    assert switch.is_on is True
    switch.async_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_number_failed_command_raises(hass: HomeAssistant):
    param = "room3_param1_n1"
    coordinator = _make_coordinator({param: "18"})
    coordinator.api_client.async_send_command = AsyncMock(
        side_effect=InnotempCommandError("nope")
    )
    number = InnotempNumber(
        coordinator,
        _config_entry(),
        ROOM,
        COMP,
        param,
        {"unit": "°C", "var": param, "label": "Setpoint"},
    )
    number.async_write_ha_state = MagicMock()

    with pytest.raises(HomeAssistantError):
        await number.async_set_native_value(21.0)
    assert coordinator.data[param] == "18"
