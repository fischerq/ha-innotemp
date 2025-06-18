"""Tests for the Innotemp sensor platform."""

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from custom_components.innotemp.sensor import async_setup_entry, InnotempSensor


async def test_sensor_setup_entry(
    hass: HomeAssistant, mock_config_entry, mock_coordinator
):
    """Test sensor setup entry."""
    add_entities = MagicMock(spec=AddEntitiesCallback)

    mock_coordinator.data = {
        "sensors": [
            {"param": "001_d_display002inp1", "label": "Pufferspeicher Oben (°C)"},
            {"param": "003_d_display001inp4", "label": "Batterie SOC (%)"},
        ]
    }

    await async_setup_entry(hass, mock_config_entry, add_entities)

    assert add_entities.call_count == 1
    entities = add_entities.call_args[0][0]
    assert len(entities) == 2
    assert isinstance(entities[0], InnotempSensor)
    assert isinstance(entities[1], InnotempSensor)

    assert entities[0].name == "Pufferspeicher Oben (°C)"
    assert entities[0].unique_id == "test_entry_id-001_d_display002inp1"
    assert entities[0].device_info is not None
    assert entities[1].name == "Batterie SOC (%)"
    assert entities[1].unique_id == "test_entry_id-003_d_display001inp4"
    assert entities[1].device_info is not None


async def test_sensor_state(hass: HomeAssistant, mock_coordinator):
    """Test sensor state."""
    mock_coordinator.data = {
        "sensors": [
            {"param": "001_d_display002inp1", "label": "Pufferspeicher Oben (°C)"},
        ],
        "001_d_display002inp1": "55.0",
    }
    sensor = InnotempSensor(mock_coordinator, mock_coordinator.data["sensors"][0])

    assert sensor.state == 55.0
    assert sensor.unit_of_measurement == "°C"
    assert sensor.device_class == "temperature"


async def test_sensor_state_no_unit(hass: HomeAssistant, mock_coordinator):
    """Test sensor state when no unit is provided."""
    mock_coordinator.data = {
        "sensors": [
            {"param": "003_d_display001inp4", "label": "Batterie SOC"},
        ],
        "003_d_display001inp4": "95",
    }
    sensor = InnotempSensor(mock_coordinator, mock_coordinator.data["sensors"][0])

    assert sensor.state == 95
    assert sensor.unit_of_measurement is None
    assert sensor.device_class is None


async def test_sensor_state_not_available(hass: HomeAssistant, mock_coordinator):
    """Test sensor state when data is not available."""
    mock_coordinator.data = {
        "sensors": [
            {"param": "001_d_display002inp1", "label": "Pufferspeicher Oben (°C)"},
        ],
        "001_d_display002inp1": None,
    }
    sensor = InnotempSensor(mock_coordinator, mock_coordinator.data["sensors"][0])

    assert sensor.state is None
