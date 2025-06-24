"""Tests for the Innotemp sensor platform."""

from unittest.mock import MagicMock, PropertyMock
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.innotemp.sensor import async_setup_entry, InnotempSensor
from custom_components.innotemp.const import DOMAIN


@pytest.fixture
def mock_coordinator_success(hass: HomeAssistant):
    """Fixture for a DataUpdateCoordinator with successful sensor data."""
    coordinator = MagicMock(spec=DataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.data = {
        "sensors": [
            {"param": "temp_sensor", "label": "Living Room Temperature (°C)"},
            {"param": "humidity_sensor", "label": "Living Room Humidity (%)"},
            {"param": "power_sensor", "label": "Power Usage (kW)"},
            {
                "param": "generic_sensor",
                "label": "Generic Value",
            },  # Sensor with no unit in label
        ],
        "temp_sensor": "22.5",
        "humidity_sensor": "45.0",
        "power_sensor": "1.2",
        "generic_sensor": "100",
    }
    # Mock config_entry for device_info generation
    config_entry_mock = MagicMock(
        spec=config_entries.ConfigEntry, entry_id="test_entry_id"
    )
    type(coordinator).config_entry = PropertyMock(return_value=config_entry_mock)

    # Simulate what the main integration setup would do
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][config_entry_mock.entry_id] = {
        "coordinator": coordinator,
        "config": coordinator.data,  # Pass the coordinator's data as 'config' for sensor setup
    }
    return coordinator


@pytest.fixture
def mock_coordinator_failure(hass: HomeAssistant):
    """Fixture for a DataUpdateCoordinator with missing or problematic sensor data."""
    coordinator = MagicMock(spec=DataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.data = {
        "sensors": [
            {"param": "temp_sensor_missing_data", "label": "Attic Temperature (°C)"},
            {
                "param": "sensor_no_label_data",
                "label": "No Label Sensor",
            },  # Data might be missing
            {"param": "sensor_none_value", "label": "Sensor With None (°C)"},
        ],
        # "temp_sensor_missing_data" key is intentionally missing from data for one test
        "sensor_none_value": None,  # Explicit None value
    }
    config_entry_mock = MagicMock(
        spec=config_entries.ConfigEntry, entry_id="test_entry_id_failure"
    )
    type(coordinator).config_entry = PropertyMock(return_value=config_entry_mock)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][config_entry_mock.entry_id] = {
        "coordinator": coordinator,
        "config": coordinator.data,
    }
    return coordinator


@pytest.mark.asyncio
async def test_sensor_setup_and_state_success(
    hass: HomeAssistant, mock_coordinator_success
):
    """Test successful sensor setup, state, unit, and device class."""
    add_entities_callback = MagicMock(spec=AddEntitiesCallback)

    # Setup sensors
    await async_setup_entry(
        hass, mock_coordinator_success.config_entry, add_entities_callback
    )

    add_entities_callback.assert_called_once()
    entities = add_entities_callback.call_args[0][0]
    assert len(entities) == 4  # Based on mock_coordinator_success data

    # Test each sensor created
    # Sensor 1: Temperature
    temp_sensor_config = mock_coordinator_success.data["sensors"][0]
    temp_sensor_entity = next(
        e for e in entities if e.entity_description.key == temp_sensor_config["param"]
    )
    assert isinstance(temp_sensor_entity, InnotempSensor)
    assert (
        temp_sensor_entity.name == "Living Room Temperature (°C)"
    )  # Full label as name
    assert (
        temp_sensor_entity.unique_id
        == f"{mock_coordinator_success.config_entry.entry_id}-{temp_sensor_config['param']}"
    )
    assert temp_sensor_entity.state == 22.5
    assert temp_sensor_entity.unit_of_measurement == "°C"
    assert temp_sensor_entity.device_class == "temperature"
    assert temp_sensor_entity.device_info is not None
    assert temp_sensor_entity.device_info["identifiers"] == {
        (DOMAIN, mock_coordinator_success.config_entry.entry_id)
    }

    # Sensor 2: Humidity
    humidity_sensor_config = mock_coordinator_success.data["sensors"][1]
    humidity_sensor_entity = next(
        e
        for e in entities
        if e.entity_description.key == humidity_sensor_config["param"]
    )
    assert humidity_sensor_entity.name == "Living Room Humidity (%)"
    assert humidity_sensor_entity.state == 45.0
    assert humidity_sensor_entity.unit_of_measurement == "%"
    assert humidity_sensor_entity.device_class == "humidity"

    # Sensor 3: Power
    power_sensor_config = mock_coordinator_success.data["sensors"][2]
    power_sensor_entity = next(
        e for e in entities if e.entity_description.key == power_sensor_config["param"]
    )
    assert power_sensor_entity.name == "Power Usage (kW)"
    assert power_sensor_entity.state == 1.2
    assert power_sensor_entity.unit_of_measurement == "kW"
    assert power_sensor_entity.device_class == "power"

    # Sensor 4: Generic (no unit in label)
    generic_sensor_config = mock_coordinator_success.data["sensors"][3]
    generic_sensor_entity = next(
        e
        for e in entities
        if e.entity_description.key == generic_sensor_config["param"]
    )
    assert generic_sensor_entity.name == "Generic Value"
    assert generic_sensor_entity.state == 100  # Assuming it converts to float/int
    assert generic_sensor_entity.unit_of_measurement is None
    assert generic_sensor_entity.device_class is None


@pytest.mark.asyncio
async def test_sensor_state_failure(hass: HomeAssistant, mock_coordinator_failure):
    """Test sensor states for unavailable or problematic data."""
    add_entities_callback = MagicMock(spec=AddEntitiesCallback)

    await async_setup_entry(
        hass, mock_coordinator_failure.config_entry, add_entities_callback
    )

    add_entities_callback.assert_called_once()
    entities = add_entities_callback.call_args[0][0]
    assert len(entities) == 3  # Based on mock_coordinator_failure data

    # Sensor 1: Data key missing in coordinator.data
    missing_data_sensor_config = mock_coordinator_failure.data["sensors"][0]
    missing_data_sensor_entity = next(
        e
        for e in entities
        if e.entity_description.key == missing_data_sensor_config["param"]
    )
    assert missing_data_sensor_entity.name == "Attic Temperature (°C)"
    assert missing_data_sensor_entity.state is None  # Should be None as key is missing
    assert (
        missing_data_sensor_entity.unit_of_measurement == "°C"
    )  # Unit should still be parsed
    assert missing_data_sensor_entity.device_class == "temperature"

    # Sensor 2: Data might be missing or param not in coordinator.data (similar to above)
    # This depends on how InnotempSensor handles get(param, None)
    no_label_data_sensor_config = mock_coordinator_failure.data["sensors"][1]
    no_label_data_sensor_entity = next(
        e
        for e in entities
        if e.entity_description.key == no_label_data_sensor_config["param"]
    )
    assert no_label_data_sensor_entity.name == "No Label Sensor"
    assert (
        no_label_data_sensor_entity.state is None
    )  # Assuming 'sensor_no_label_data' key is missing
    assert no_label_data_sensor_entity.unit_of_measurement is None  # No unit in label
    assert no_label_data_sensor_entity.device_class is None

    # Sensor 3: Value is explicitly None in coordinator.data
    none_value_sensor_config = mock_coordinator_failure.data["sensors"][2]
    none_value_sensor_entity = next(
        e
        for e in entities
        if e.entity_description.key == none_value_sensor_config["param"]
    )
    assert none_value_sensor_entity.name == "Sensor With None (°C)"
    assert none_value_sensor_entity.state is None  # Value is None
    assert none_value_sensor_entity.unit_of_measurement == "°C"
    assert none_value_sensor_entity.device_class == "temperature"
