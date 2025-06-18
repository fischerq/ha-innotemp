"""Sensor platform for Innotemp."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator
from .coordinator import (
    InnotempCoordinatorEntity,
)  # Assuming InnotempCoordinatorEntity is in coordinator.py


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Innotemp sensors based on a config entry."""
    coordinator: InnotempDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    api = coordinator.api_client
    entities = []

    # Assuming config holds a list of sensor parameters from async_get_config
    for param_id, param_data in coordinator.config.get("sensors", {}).items():
        entities.append(InnotempSensor(coordinator, param_id, param_data))

    async_add_entities(entities)


class InnotempSensor(InnotempCoordinatorEntity, SensorEntity):
    """Representation of an Innotemp Sensor."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        param_id: str,
        param_data: dict,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator, coordinator.config_entry, {"param": param_id, **param_data}
        )  # Pass config_entry and entity_config
        self._attr_name = param_data.get("label", f"Innotemp Sensor {param_id}")
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{param_id}"
        self._attr_native_unit_of_measurement = param_data.get("unit")
        # Add other relevant sensor attributes based on param_data

    @property
    def native_value(self):
        """Return the state of the sensor."""
        # The coordinator's data is a dictionary where keys are parameter IDs
        return self.coordinator.data.get(self._param_id)

    @property
    def state_class(self):
        """Return the state class of the sensor."""
        # Define state class based on param_data if available, e.g., Measurement, Total
        # For now, returning None, adjust as needed
        return None

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        # Define device class based on param_data if available, e.g., temperature, humidity
        # For now, returning None, adjust as needed
        return None
