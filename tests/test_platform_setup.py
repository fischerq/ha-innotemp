"""Tests for entity discovery in each platform's ``async_setup_entry``.

They run the real discovery over ``tests/roomconf_test_data.json`` (a
sanitised roomconf structure) instead of constructing entities by hand.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from homeassistant.components.number import NumberDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.core import HomeAssistant

from custom_components.innotemp import number, select, sensor, switch
from custom_components.innotemp.const import DOMAIN
from custom_components.innotemp.coordinator import InnotempDataUpdateCoordinator
from custom_components.innotemp.number import InnotempNumber
from custom_components.innotemp.sensor import (
    InnotempDynamicEnumSensor,
    InnotempEnumSensor,
    InnotempOnOffSensor,
    InnotempSensor,
)

ROOMCONF = json.loads(
    (Path(__file__).parent / "roomconf_test_data.json").read_text(encoding="utf-8")
)


def _setup_hass_data(hass, config, data=None):
    coordinator = MagicMock(spec=InnotempDataUpdateCoordinator)
    coordinator.data = data or {}
    coordinator.control_to_state_map = {}
    entry = MagicMock(unique_id="192.168.1.5", entry_id="entry1")
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "config": config,
    }
    return entry, coordinator


async def _discover(hass, platform, config=ROOMCONF, data=None):
    entry, _ = _setup_hass_data(hass, config, data)
    added = []
    await platform.async_setup_entry(hass, entry, added.extend)
    return {e._param_id: e for e in added}


@pytest.mark.asyncio
async def test_sensor_discovery(hass: HomeAssistant):
    entities = await _discover(
        hass, sensor, data={"room1_display1_inp1": "45.5", "room4_drink1_inp5": "1"}
    )

    assert len(entities) == 20
    temp = entities["room1_display1_inp1"]
    assert isinstance(temp, InnotempSensor)
    assert temp.unique_id == "192.168.1.5_room1_display1_inp1"
    assert temp.device_class == SensorDeviceClass.TEMPERATURE
    assert temp.native_value == 45.5
    assert isinstance(entities["room1_param1_entry1"], InnotempEnumSensor)
    assert isinstance(entities["room2_mixer1_entry1"], InnotempDynamicEnumSensor)
    assert isinstance(entities["room4_drink1_inp5"], InnotempOnOffSensor)
    assert entities["room4_drink1_inp5"].native_value == "On"
    # Sensors without a value yet report unknown rather than crashing.
    assert entities["room3_main_out1"].native_value is None


@pytest.mark.asyncio
async def test_number_discovery(hass: HomeAssistant):
    entities = await _discover(hass, number, data={"room3_main_entry1": "55"})

    # NOTE: discovery currently also turns every numeric input/output into a
    # writable number (e.g. measured temperatures), and the VAR: enum entry
    # into a number. Pinned here so a change is deliberate; verify against a
    # real controller with scripts/smoke_test.py before narrowing it.
    assert len(entities) == 14
    setpoint = entities["room3_main_entry1"]
    assert isinstance(setpoint, InnotempNumber)
    assert setpoint.unique_id == "192.168.1.5_room3_main_entry1"
    assert setpoint.device_class == NumberDeviceClass.TEMPERATURE
    assert setpoint.native_value == 55.0
    assert setpoint._api_room_id == 3
    assert entities["room2_mixer1_out1"].native_max_value == 100.0
    assert "room1_param1_entry1" not in entities  # ONOFFAUTO is a select


@pytest.mark.asyncio
async def test_select_and_switch_discovery(hass: HomeAssistant):
    selects = await _discover(hass, select)
    switches = await _discover(hass, switch)

    assert set(selects) == {"room1_param1_entry1", "room2_pump1_entry1"}
    assert selects["room2_pump1_entry1"]._numeric_api_room_id == 2
    assert set(switches) == {"room4_drink1_inp5"}
    assert switches["room4_drink1_inp5"]._numeric_api_room_id == 4


@pytest.mark.parametrize("platform", [sensor, number, select, switch])
@pytest.mark.asyncio
async def test_discovery_with_no_config(hass: HomeAssistant, platform):
    entities = await _discover(hass, platform, config=None)
    assert entities == {}


@pytest.mark.parametrize("platform", [sensor, number, select, switch])
@pytest.mark.asyncio
async def test_discovery_with_unexpected_config_shape(hass: HomeAssistant, platform):
    entities = await _discover(hass, platform, config={"room": "not json"})
    assert entities == {}
