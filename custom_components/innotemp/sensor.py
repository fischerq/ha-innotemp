"""Sensor platform for Innotemp."""

from __future__ import annotations

import logging
import json # For parsing string values in config_data if necessary
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator
from .coordinator import (
    InnotempCoordinatorEntity,
)  # Assuming InnotempCoordinatorEntity is in coordinator.py

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Innotemp sensors based on a config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning(
            "Innotemp sensor setup: config_data is None, skipping sensor entity creation."
        )
        # Depending on HA best practices, simply returning might be enough if __init__ already returned False.
        # However, explicitly handling it here ensures no further processing for this platform.
        async_add_entities([])  # Add no entities
        return

    api = coordinator.api_client
    entities = []

    _LOGGER.debug("Innotemp sensor setup: Received config_data: %s", config_data)

    # Assuming config holds a list of sensor parameters from async_get_config
    # This current logic is likely incorrect given the known config_data structure
    sensors_from_config = config_data.get("sensors", {})
    if not sensors_from_config:
        _LOGGER.debug("Innotemp sensor setup: No 'sensors' key found in config_data or it's empty. Will attempt to parse known structure.")
    else:
        _LOGGER.debug("Innotemp sensor setup: Found 'sensors' key in config_data. Processing %s items.", len(sensors_from_config))
        for param_id, param_data in sensors_from_config.items():
            _LOGGER.debug("Innotemp sensor setup: Creating sensor from 'sensors' block: param_id=%s, param_data=%s", param_id, param_data)
            entities.append(InnotempSensor(coordinator, entry, param_id, param_data))

# Helper function to extract sensors from various parts of room data
def _extract_sensors_from_room_component(
    component_data, coordinator, entry, room_context_id, entities_list, component_key_hint=""
):
    """
    Extracts sensor entities from a component of room data.
    Component_data can be a dict or a list of dicts.
    Sensors are often found in 'input' lists within these components.
    """
    if not component_data:
        return

    items_to_check = []
    if isinstance(component_data, dict):
        items_to_check.append(component_data)
    elif isinstance(component_data, list):
        items_to_check.extend(component_data)
    else:
        _LOGGER.debug(f"Sensor: Unexpected component_data type: {type(component_data)} for room {room_context_id}, hint: {component_key_hint}")
        return

    for item in items_to_check:
        if not isinstance(item, dict):
            _LOGGER.debug(f"Sensor: Skipping non-dict item in component_data list for room {room_context_id}, hint: {component_key_hint}: {item}")
            continue

        # Sensors are commonly found in 'input' arrays within components like 'display', 'param', etc.
        # Also, some components might directly be sensor definitions if they have 'var' and 'unit'.

        # Scenario 1: The item itself is a sensor definition (e.g. from a list of inputs)
        if "var" in item and "unit" in item and item.get("unit") != "ONOFFAUTO":
            param_id = item.get("var")
            label = item.get("label", f"Sensor {param_id}")
            unit = item.get("unit")

            # Basic check to avoid creating sensors from empty/placeholder vars or units
            if not param_id or not unit or not label:
                _LOGGER.debug(f"Sensor: Skipping item, missing var, unit or label: {item} in room {room_context_id}")
                continue

            _LOGGER.debug(f"Sensor: Found potential sensor (direct item): room {room_context_id}, param_id {param_id}, data {item}")
            entities_list.append(InnotempSensor(coordinator, entry, param_id, item))

        # Scenario 2: The item contains an 'input' list (e.g. a 'display' or 'param' block)
        input_list = item.get("input")
        if isinstance(input_list, list):
            for input_item in input_list:
                if not isinstance(input_item, dict):
                    _LOGGER.debug(f"Sensor: Skipping non-dict input_item in input_list for room {room_context_id}: {input_item}")
                    continue

                if "var" in input_item and "unit" in input_item and input_item.get("unit") != "ONOFFAUTO":
                    param_id = input_item.get("var")
                    label = input_item.get("label", f"Sensor {param_id}") # Use input_item's label
                    unit = input_item.get("unit")

                    if not param_id or not unit or not label:
                        _LOGGER.debug(f"Sensor: Skipping input_item, missing var, unit or label: {input_item} in room {room_context_id}")
                        continue

                    _LOGGER.debug(f"Sensor: Found potential sensor (from input list): room {room_context_id}, param_id {param_id}, data {input_item}")
                    entities_list.append(InnotempSensor(coordinator, entry, param_id, input_item))

        # Scenario 3: The item contains an 'output' list/dict (e.g. a 'piseq' block might have readable outputs)
        output_data = item.get("output")
        if output_data:
            outputs_to_process = []
            if isinstance(output_data, dict): # If 'output' is a single dict
                outputs_to_process.append(output_data)
            elif isinstance(output_data, list): # If 'output' is a list of dicts
                outputs_to_process.extend(output_data)

            for output_item in outputs_to_process:
                if not isinstance(output_item, dict):
                    _LOGGER.debug(f"Sensor: Skipping non-dict output_item for room {room_context_id}: {output_item}")
                    continue

                if "var" in output_item and "unit" in output_item and output_item.get("unit") != "ONOFFAUTO":
                    param_id = output_item.get("var")
                    label = output_item.get("label", f"Sensor {param_id}")
                    unit = output_item.get("unit")

                    if not param_id or not unit or not label: # Ensure essential fields
                        _LOGGER.debug(f"Sensor: Skipping output_item, missing var, unit or label: {output_item} in room {room_context_id}")
                        continue

                    _LOGGER.debug(f"Sensor: Found potential sensor (from output list/dict): room {room_context_id}, param_id {param_id}, data {output_item}")
                    entities_list.append(InnotempSensor(coordinator, entry, param_id, output_item))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Innotemp sensors based on a config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning("Innotemp sensor setup: config_data is None, skipping sensor entity creation.")
        async_add_entities([])
        return

    entities = []
    _LOGGER.debug("Innotemp sensor setup: Received config_data: %s", config_data)

    if not isinstance(config_data, dict):
        _LOGGER.error(f"Sensor: Config_data is not a dictionary as expected. Type: {type(config_data)}. Data: {config_data}")
        async_add_entities([])
        return

    for top_level_key, top_level_value in config_data.items():
        _LOGGER.debug(f"Sensor: Processing top_level_key: {top_level_key}, value type: {type(top_level_value)}")

        actual_room_list = []
        if isinstance(top_level_value, list): # e.g. config_data["room"] = [room1, room2]
            actual_room_list = top_level_value
        elif isinstance(top_level_value, dict) and top_level_value.get("@attributes",{}).get("type","").startswith("room"):
            # If the top_level_value itself is a single room dictionary
            actual_room_list.append(top_level_value)
        # Add other conditions if rooms can be nested differently, e.g. under config_data["foo"]["room"]

        if not actual_room_list and isinstance(top_level_value, str): # Try parsing if string
            try:
                import json
                parsed_value = json.loads(top_level_value)
                if isinstance(parsed_value, list):
                    _LOGGER.debug(f"Sensor: Successfully parsed string value for key {top_level_key} into a list.")
                    actual_room_list = parsed_value
            except json.JSONDecodeError:
                _LOGGER.debug(f"Sensor: Could not parse string value for key {top_level_key} as JSON list.")

        if not actual_room_list:
            _LOGGER.debug(f"Sensor: No list of rooms found under key '{top_level_key}' or key itself is not a room list.")
            continue

        for room_data_dict in actual_room_list:
            if not isinstance(room_data_dict, dict):
                _LOGGER.warning(f"Sensor: Item in room list for key '{top_level_key}' is not a dictionary: {room_data_dict}")
                continue

            room_attributes = room_data_dict.get("@attributes", {})
            room_context_id = room_attributes.get("var", room_attributes.get("label", f"unknown_room_via_{top_level_key}"))
            _LOGGER.debug(f"Sensor: Processing room: {room_context_id}, Data: {room_data_dict}")

            # Define sections within a room that might contain sensor definitions
            # Sensors are often in 'input' lists, or 'output' lists/dicts for readable values.
            # 'display', 'param', 'mixer', 'pump', 'piseq', 'radiator', 'drink', 'main'
            possible_sensor_containers_keys = [
                "display", "param", "mixer", "pump", "piseq", "radiator", "drink", "main"
            ]

            for container_key in possible_sensor_containers_keys:
                component_data = room_data_dict.get(container_key)
                if component_data:
                    _extract_sensors_from_room_component(
                        component_data, coordinator, entry, room_context_id, entities, container_key
                    )

    if not entities:
        _LOGGER.info("No Innotemp sensor entities were created after parsing.")
    else:
        _LOGGER.info(f"Successfully created {len(entities)} Innotemp sensor entities.")

    async_add_entities(entities)


class InnotempSensor(InnotempCoordinatorEntity, SensorEntity):
    """Representation of an Innotemp Sensor."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        param_id: str, # This is the 'var' from the config
        param_data: dict, # This is the dict containing 'var', 'unit', 'label'
    ) -> None:
        """Initialize the sensor."""
        # entity_config for InnotempCoordinatorEntity expects 'param' for unique_id part
        entity_config = {"param": param_id, "label": param_data.get("label", f"Innotemp Sensor {param_id}")}
        super().__init__(coordinator, config_entry, entity_config)

        # _attr_name is already set by InnotempCoordinatorEntity using entity_config['label']
        # self._attr_name = param_data.get("label", f"Innotemp Sensor {param_id}")

        # _attr_unique_id is also set by InnotempCoordinatorEntity
        # self._attr_unique_id = f"{config_entry.unique_id}_{param_id}"

        self._attr_native_unit_of_measurement = param_data.get("unit")
        self._param_id = param_id # Store the 'var' to fetch data from coordinator

        # Attempt to map units to device classes or set state class
        unit = str(self._attr_native_unit_of_measurement).lower()
        if unit == "°c" or unit == "c":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit == "%":
            self._attr_device_class = SensorDeviceClass.HUMIDITY # Could also be battery, power factor etc.
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit == "k.w" or unit == "kw": # Check for 'k.w' from user data
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit == "s": # seconds
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_state_class = SensorStateClass.MEASUREMENT # Or total increasing if it's an uptime counter
        # Add more unit mappings to device_class and state_class as needed
        # e.g. kWh for energy, V for voltage, A for current, etc.
        else:
            self._attr_device_class = None # Generic sensor
            self._attr_state_class = SensorStateClass.MEASUREMENT # Default to measurement

        _LOGGER.debug(f"InnotempSensor initialized: name='{self.name}', unique_id='{self.unique_id}', unit='{self.native_unit_of_measurement}', param_id='{self._param_id}', device_class='{self.device_class}', state_class='{self.state_class}'")


    @property
    def native_value(self):
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            _LOGGER.debug(f"InnotempSensor.native_value: Coordinator data is None for entity {self.entity_id} (param_id: {self._param_id}). Returning None.")
            return None

        value = self.coordinator.data.get(self._param_id)
        if value is None:
            _LOGGER.debug(f"InnotempSensor.native_value: Param_id {self._param_id} not found in coordinator data for entity {self.entity_id}. Data: {self.coordinator.data}. Returning None.")
            return None

        # Attempt to convert to float if possible, for units like °C, %, kW
        # Handle known string constants like "ONOFF", "AUTO" etc. if they are non-numeric sensors
        # For now, basic float conversion for common numeric units
        unit = str(self.native_unit_of_measurement).lower()
        if unit in ["°c", "c", "%", "k.w", "kw", "s"]: # Add other numeric units
            try:
                return float(value)
            except (ValueError, TypeError):
                _LOGGER.warning(f"Could not convert sensor value '{value}' to float for {self.entity_id} (param_id: {self._param_id}, unit: {unit}). Returning as is.")
                return value # Return original string if conversion fails

        return value # Return raw value if no specific conversion logic

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
