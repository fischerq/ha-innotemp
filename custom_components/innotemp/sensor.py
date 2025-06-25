"""Sensor platform for Innotemp."""

from __future__ import annotations

import logging
import json # For parsing string values in config_data if necessary
import re # For stripping HTML
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

def _strip_html(text: str | None) -> str:
    """Remove HTML tags from a string."""
    if text is None:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()


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
    component_data,  # Data for a specific component like 'display', 'param'
    coordinator,
    entry,
    room_attributes, # Attributes of the parent room
    entities_list,
    component_key_hint="" # e.g. "display", "param" - the key for component_data in room_data_dict
):
    """
    Extracts sensor entities from a component of room data.
    Component_data can be a dict or a list of dicts (e.g. multiple 'display' blocks).
    Sensors are often found in 'input' or 'output' lists/dicts within these components.
    """
    if not component_data:
        return

    # component_data could be, for example, the content of room_data_dict['display']
    # This itself can be a list of display blocks, or a single display block dict.
    component_items_to_process = []
    if isinstance(component_data, dict):
        component_items_to_process.append(component_data)
    elif isinstance(component_data, list):
        component_items_to_process.extend(component_data)
    else:
        _LOGGER.debug(f"Sensor: Unexpected component_data type: {type(component_data)} for room {room_attributes.get('var')}, hint: {component_key_hint}")
        return

    for component_item_data in component_items_to_process: # e.g., one 'display' block dict
        if not isinstance(component_item_data, dict):
            _LOGGER.debug(f"Sensor: Skipping non-dict item in component_data list for room {room_attributes.get('var')}, hint: {component_key_hint}: {component_item_data}")
            continue

        # This is the attributes of the specific component block, like a 'display' item's attributes
        current_component_attributes = component_item_data.get("@attributes", {})

        # Sensors are commonly found in 'input' arrays within components like 'display', 'param', etc.
        input_list = component_item_data.get("input")
        if isinstance(input_list, list):
            for sensor_candidate_data in input_list: # sensor_candidate_data is the sensor's own dict
                if not isinstance(sensor_candidate_data, dict):
                    _LOGGER.debug(f"Sensor: Skipping non-dict input_item in input_list for room {room_attributes.get('var')}, component {current_component_attributes}: {sensor_candidate_data}")
                    continue

                if "var" in sensor_candidate_data and "unit" in sensor_candidate_data and sensor_candidate_data.get("unit") != "ONOFFAUTO":
                    param_id = sensor_candidate_data.get("var")
                    # Fallback for label if not present in sensor_candidate_data, though unlikely
                    label = sensor_candidate_data.get("label", f"Sensor {param_id}")
                    unit = sensor_candidate_data.get("unit")

                    if not param_id or not unit or not label: # Basic check
                        _LOGGER.debug(f"Sensor: Skipping input_item (missing var, unit, or label): {sensor_candidate_data} in room {room_attributes.get('var')}, component {current_component_attributes}")
                        continue

                    _LOGGER.debug(f"Sensor: Found potential sensor (from input list of {component_key_hint} '{current_component_attributes.get('label')}'): room_var {room_attributes.get('var')}, sensor_var {param_id}, data {sensor_candidate_data}")
                    entities_list.append(
                        InnotempSensor(
                            coordinator,
                            entry,
                            room_attributes,
                            current_component_attributes, # Attributes of the block containing this sensor (e.g. 'display' block)
                            sensor_candidate_data # The sensor's own data dict {'var': ..., 'unit': ..., 'label': ...}
                        )
                    )

        # Also check 'output' lists/dicts, as they might contain readable sensor values
        output_data = component_item_data.get("output")
        if output_data:
            outputs_to_process = []
            if isinstance(output_data, dict):
                outputs_to_process.append(output_data)
            elif isinstance(output_data, list):
                outputs_to_process.extend(output_data)

            for sensor_candidate_data in outputs_to_process: # sensor_candidate_data is the sensor's own dict
                if not isinstance(sensor_candidate_data, dict):
                    _LOGGER.debug(f"Sensor: Skipping non-dict output_item for room {room_attributes.get('var')}, component {current_component_attributes}: {sensor_candidate_data}")
                    continue

                if "var" in sensor_candidate_data and "unit" in sensor_candidate_data and sensor_candidate_data.get("unit") != "ONOFFAUTO":
                    param_id = sensor_candidate_data.get("var")
                    label = sensor_candidate_data.get("label", f"Sensor {param_id}")
                    unit = sensor_candidate_data.get("unit")

                    if not param_id or not unit or not label:
                        _LOGGER.debug(f"Sensor: Skipping output_item (missing var, unit or label): {sensor_candidate_data} in room {room_attributes.get('var')}, component {current_component_attributes}")
                        continue

                    _LOGGER.debug(f"Sensor: Found potential sensor (from output of {component_key_hint} '{current_component_attributes.get('label')}'): room_var {room_attributes.get('var')}, sensor_var {param_id}, data {sensor_candidate_data}")
                    entities_list.append(
                        InnotempSensor(
                            coordinator,
                            entry,
                            room_attributes,
                            current_component_attributes, # Attributes of the block containing this sensor
                            sensor_candidate_data # The sensor's own data dict
                        )
                    )

        # Fallback: If the component_item_data itself (e.g. a direct item in 'mixer' list not having 'input'/'output' sub-keys)
        # has 'var' and 'unit', it might be a sensor. This is less common for complex components but possible for simpler ones.
        # This was Scenario 1 before, let's refine it.
        # This check should only apply if we haven't already processed inputs/outputs from this component_item_data,
        # to avoid double-adding if a sensor is defined both directly and in an input list.
        # However, typical structure is component_item_data -> input/output -> sensor_candidate_data.
        # A direct sensor at component_item_data level would mean component_item_data IS sensor_candidate_data.
        if not input_list and not output_data: # Only if no 'input' or 'output' sub-keys were processed
            if "var" in component_item_data and "unit" in component_item_data and component_item_data.get("unit") != "ONOFFAUTO":
                param_id = component_item_data.get("var")
                label = component_item_data.get("label", f"Sensor {param_id}")
                unit = component_item_data.get("unit")

                if not param_id or not unit or not label:
                    _LOGGER.debug(f"Sensor: Skipping component_item_data (missing var, unit or label): {component_item_data} in room {room_attributes.get('var')}")
                else:
                    _LOGGER.debug(f"Sensor: Found potential sensor (direct component item {component_key_hint} '{current_component_attributes.get('label')}'): room_var {room_attributes.get('var')}, sensor_var {param_id}, data {component_item_data}")
                    entities_list.append(
                        InnotempSensor(
                            coordinator,
                            entry,
                            room_attributes,
                            current_component_attributes, # In this case, component_item_data's attributes are the component's attributes
                            component_item_data # And component_item_data is also the sensor's data
                        )
                    )


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
    _LOGGER.debug("Innotemp sensor setup: Received config_data (first 500 chars): %s", str(config_data)[:500])

    if not isinstance(config_data, dict):
        _LOGGER.error(f"Sensor: Config_data is not a dictionary as expected. Type: {type(config_data)}. Data: {config_data}")
        async_add_entities([])
        return

    for top_level_key, top_level_value in config_data.items():
        #_LOGGER.debug(f"Sensor: Processing top_level_key: {top_level_key}, value type: {type(top_level_value)}")

        actual_room_list = []
        if isinstance(top_level_value, list):
            actual_room_list = top_level_value
        elif isinstance(top_level_value, dict) and top_level_value.get("@attributes",{}).get("type","").startswith("room"):
            actual_room_list.append(top_level_value)

        if not actual_room_list and isinstance(top_level_value, str):
            try:
                import json
                parsed_value = json.loads(top_level_value)
                if isinstance(parsed_value, list):
                    #_LOGGER.debug(f"Sensor: Successfully parsed string value for key {top_level_key} into a list.")
                    actual_room_list = parsed_value
            except json.JSONDecodeError:
                _LOGGER.debug(f"Sensor: Could not parse string value for key {top_level_key} as JSON list.")

        if not actual_room_list:
            #_LOGGER.debug(f"Sensor: No list of rooms found under key '{top_level_key}' or key itself is not a room list.")
            continue

        for room_data_dict in actual_room_list: # room_data_dict is one whole room
            if not isinstance(room_data_dict, dict):
                _LOGGER.warning(f"Sensor: Item in room list for key '{top_level_key}' is not a dictionary: {room_data_dict}")
                continue

            room_attributes = room_data_dict.get("@attributes", {})
            if not room_attributes.get("var"): # Ensure room has a usable ID
                _LOGGER.warning(f"Sensor: Room missing '@attributes.var': {room_attributes}. Skipping.")
                continue

            #_LOGGER.debug(f"Sensor: Processing room: {room_attributes.get('var')}, Label: {room_attributes.get('label')}")

            possible_sensor_containers_keys = [
                "display", "param", "mixer", "pump", "piseq", "radiator", "drink", "main"
            ]

            for container_key in possible_sensor_containers_keys:
                # component_data is the content of 'display', 'param', etc.
                # This can be a dictionary OR a list of dictionaries
                component_data = room_data_dict.get(container_key)
                if component_data:
                    _extract_sensors_from_room_component(
                        component_data, # e.g., the dict/list for 'display' or 'param'
                        coordinator,
                        entry,
                        room_attributes, # Attributes of the parent room
                        entities,
                        container_key # Pass the key name for better logging
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
        room_attributes: dict,      # Attributes of the parent room
        component_attributes: dict, # Attributes of the component block (e.g. 'display')
        sensor_data: dict,          # The sensor's own data dict {'var':..., 'unit':..., 'label':...}
    ) -> None:
        """Initialize the sensor."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_data = sensor_data # Renaming for clarity, was param_data

        param_id = self._param_data.get("var")
        original_label = self._param_data.get("label", f"Sensor {param_id}")
        cleaned_label = _strip_html(original_label)

        # entity_config for InnotempCoordinatorEntity expects 'param' for unique_id part
        entity_config = {"param": param_id, "label": cleaned_label if cleaned_label else f"Sensor {param_id}"}
        super().__init__(coordinator, config_entry, entity_config)

        # _attr_name is already set by InnotempCoordinatorEntity using entity_config['label']
        # self._attr_name = param_data.get("label", f"Innotemp Sensor {param_id}")

        # _attr_unique_id is also set by InnotempCoordinatorEntity
        # self._attr_unique_id = f"{config_entry.unique_id}_{param_id}"

        self._attr_native_unit_of_measurement = self._param_data.get("unit") # Use self._param_data
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
