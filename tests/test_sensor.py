"""Tests for the Innotemp sensor platform.

These exercise the current sensor entities directly (the previous version of
this module asserted against an obsolete ``coordinator.data["sensors"]`` /
``entity_description`` architecture that no longer exists).
"""

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant import config_entries
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.innotemp.coordinator import InnotempDataUpdateCoordinator
from custom_components.innotemp.sensor import (
    InnotempSensor,
    InnotempOnOffSensor,
    InnotempEnumSensor,
)

ROOM = {"type": "room3", "var": "RM", "label": "Living Room"}
COMP = {"type": "display", "var": "disp1"}


def _coordinator(data):
    coordinator = MagicMock(spec=InnotempDataUpdateCoordinator)
    coordinator.data = data
    return coordinator


def _config_entry():
    return MagicMock(
        spec=config_entries.ConfigEntry, unique_id="cfg", entry_id="test_entry_id"
    )


@pytest.mark.asyncio
async def test_regular_numeric_sensor(hass: HomeAssistant):
    """A numeric sensor parses its value, unit and device class."""
    coordinator = _coordinator({"temp1": "22.5"})
    entity = InnotempSensor(
        coordinator,
        _config_entry(),
        ROOM,
        COMP,
        {"var": "temp1", "unit": "°C", "label": "Temperature"},
    )

    assert entity.name == "RM - disp1 - Temperature"
    assert entity.unique_id == "cfg_temp1"
    assert entity.native_value == 22.5
    assert entity.native_unit_of_measurement == "°C"
    assert entity.device_class == SensorDeviceClass.TEMPERATURE


@pytest.mark.asyncio
async def test_sensor_missing_value_is_none(hass: HomeAssistant):
    """A sensor whose var is absent from coordinator data reports None."""
    coordinator = _coordinator({"other": "1"})
    entity = InnotempSensor(
        coordinator,
        _config_entry(),
        ROOM,
        COMP,
        {"var": "temp1", "unit": "°C", "label": "Attic"},
    )

    assert entity.native_value is None
    # Unit / device class are still derived from the config.
    assert entity.native_unit_of_measurement == "°C"
    assert entity.device_class == SensorDeviceClass.TEMPERATURE


@pytest.mark.asyncio
async def test_generic_sensor_returns_raw_string(hass: HomeAssistant):
    """A sensor with a non-numeric unit returns the raw value as a string."""
    coordinator = _coordinator({"gen1": "100"})
    entity = InnotempSensor(
        coordinator,
        _config_entry(),
        ROOM,
        COMP,
        {"var": "gen1", "unit": "x", "label": "Generic"},
    )

    assert entity.native_value == "100"
    assert entity.device_class is None


@pytest.mark.asyncio
async def test_onoff_sensor_maps_to_text(hass: HomeAssistant):
    """An ONOFF sensor maps the raw API value to On/Off."""
    coordinator = _coordinator({"oo1": "1"})
    entity = InnotempOnOffSensor(
        coordinator,
        _config_entry(),
        ROOM,
        COMP,
        {"var": "oo1", "unit": "ONOFF", "label": "Pump"},
    )

    assert entity.name == "RM - disp1 - Pump"
    assert entity.unique_id == "cfg_oo1_onoff_status"
    assert entity.native_value == "On"
    assert entity.options == ["Off", "On"]
    assert entity.device_class == SensorDeviceClass.ENUM


@pytest.mark.asyncio
async def test_enum_sensor_maps_to_text(hass: HomeAssistant):
    """An ONOFFAUTO sensor maps the raw API value to Off/On/Auto."""
    coordinator = _coordinator({"en1": 2})
    entity = InnotempEnumSensor(
        coordinator,
        _config_entry(),
        ROOM,
        COMP,
        {"var": "en1", "unit": "ONOFFAUTO", "label": "Mode"},
    )

    assert entity.unique_id == "cfg_en1_status"
    assert entity.native_value == "Auto"
    assert entity.options == ["Off", "On", "Auto"]
    assert entity.device_class == SensorDeviceClass.ENUM
