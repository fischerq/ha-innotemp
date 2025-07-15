"""Sensor platform for Innotemp."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, List, Tuple


from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
    # SensorEntityDescription, # Not used directly in this refactor
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator
from .coordinator import InnotempCoordinatorEntity
from .api_parser import (
    strip_html,
    process_room_config_data,
    parse_var_enum_string,
    API_VALUE_TO_ONOFFAUTO_OPTION,
    # ONOFFAUTO_OPTION_TO_API_VALUE, # Not needed for sensor
    ONOFFAUTO_OPTIONS_LIST,
    API_VALUE_TO_ONOFF_OPTION,
    ONOFF_OPTIONS_LIST,
)

_LOGGER = logging.getLogger(__name__)

# Constants for ONOFFAUTO and ONOFF mapping are now imported from api_parser


def _create_sensor_entity_data(
    item_data: Dict[str, Any],
    room_attributes: Dict[str, Any],
    numeric_room_id: Optional[int],  # Sensors generally don't need numeric_room_id
    component_attributes: Dict[str, Any],
    component_key_hint: str,  # e.g. "display.input", "param"
) -> Optional[Dict[str, Any]]:
    """
    Processes an item from config data to determine if it's a sensor entity
    and what type of sensor it is.
    Returns a dictionary with necessary data for entity creation if valid, else None.
    """
    param_id = item_data.get("var")
    unit = item_data.get("unit")

    if not (param_id and unit):
        # Log only if it looked like it could have been an entity but is missing crucial parts
        if param_id or unit:  # If at least one was present
            _LOGGER.debug(
                f"Sensor: Skipping item (missing var or unit): {item_data} in room {room_attributes.get('var')}, "
                f"component {component_attributes.get('var', component_attributes.get('type'))} from {component_key_hint}"
            )
        return None

    sensor_type_data = {
        "room_attributes": room_attributes,
        "component_attributes": component_attributes,
        "item_data": item_data,  # contains label, var, unit
        "param_id": param_id,
        "unit": unit,
        "component_key_hint": component_key_hint,
    }

    if unit == "ONOFFAUTO":
        sensor_type_data["sensor_class"] = "EnumSensor"
    elif unit == "ONOFF":
        sensor_type_data["sensor_class"] = "OnOffSensor"
    elif unit.startswith("VAR:") and unit.endswith(":"):
        parsed_enum = parse_var_enum_string(unit)
        if parsed_enum:
            value_map, _, options_list = parsed_enum
            sensor_type_data["sensor_class"] = "DynamicEnumSensor"
            sensor_type_data["value_map"] = value_map
            sensor_type_data["options_list"] = options_list
        else:
            _LOGGER.warning(
                f"Failed to parse VAR: unit string '{unit}' for {param_id} from {component_key_hint}. "
                f"Treating as regular sensor."
            )
            sensor_type_data["sensor_class"] = "RegularSensor"  # Fallback
    else:
        sensor_type_data["sensor_class"] = "RegularSensor"

    _LOGGER.debug(
        f"Sensor: Found potential {sensor_type_data['sensor_class']}: room_var {room_attributes.get('var')}, "
        f"component_var {component_attributes.get('var', component_attributes.get('type'))}, item_var {param_id}, "
        f"unit '{unit}', source_hint: {component_key_hint}"
    )
    return sensor_type_data


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Innotemp sensors based on a config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning(
            "Innotemp sensor setup: config_data is None, skipping entity creation."
        )
        async_add_entities([])
        return

    _LOGGER.debug(
        "Innotemp sensor setup: Processing config_data (first 500 chars): %s",
        str(config_data)[:500],
    )

    # Keys for components within a room that might contain 'input', 'output', or direct sensor items
    possible_sensor_containers_keys = [
        "display",
        "param",
        "mixer",
        "pump",
        "piseq",
        "radiator",
        "drink",
        "main",
    ]

    sensor_entities_data = process_room_config_data(
        config_data=config_data,
        possible_container_keys=possible_sensor_containers_keys,
        item_processor=_create_sensor_entity_data,
    )

    entities: List[SensorEntity] = []
    for entity_data in sensor_entities_data:
        sensor_class_type = entity_data.pop(
            "sensor_class"
        )  # Get and remove to avoid passing to constructor

        common_args = {
            "coordinator": coordinator,
            "config_entry": entry,
            "room_attributes": entity_data["room_attributes"],
            "component_attributes": entity_data["component_attributes"],
            "sensor_data": entity_data["item_data"],  # This is the original item_data
        }

        if sensor_class_type == "EnumSensor":
            entities.append(InnotempEnumSensor(**common_args))
        elif sensor_class_type == "OnOffSensor":
            entities.append(InnotempOnOffSensor(**common_args))
        elif sensor_class_type == "DynamicEnumSensor":
            entities.append(
                InnotempDynamicEnumSensor(
                    **common_args,
                    value_to_name_map=entity_data["value_map"],
                    options=entity_data["options_list"],
                )
            )
        elif sensor_class_type == "RegularSensor":
            entities.append(InnotempSensor(**common_args))
        else:
            _LOGGER.warning(
                f"Unknown sensor class type: {sensor_class_type} for {entity_data.get('param_id')}"
            )

    if not entities:
        _LOGGER.info(
            "No Innotemp sensor entities were created after parsing with new logic."
        )
    else:
        _LOGGER.info(
            f"Successfully created {len(entities)} Innotemp sensor entities with new logic."
        )

    async_add_entities(entities)


class InnotempSensor(InnotempCoordinatorEntity, SensorEntity):
    """Representation of an Innotemp Sensor."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,  # Attributes of the parent room
        component_attributes: dict,  # Attributes of the component block (e.g. 'display')
        sensor_data: dict,  # The sensor's own data dict {'var':..., 'unit':..., 'label':...}
    ) -> None:
        """Initialize the sensor."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_data = sensor_data  # Renaming for clarity, was param_data

        param_id = self._param_data.get("var")
        original_label = self._param_data.get("label", f"Sensor {param_id}")
        cleaned_label = strip_html(original_label)

        # entity_config for InnotempCoordinatorEntity expects 'param' for unique_id part
        entity_config = {
            "param": param_id,
            "label": cleaned_label if cleaned_label else f"Sensor {param_id}",
        }
        super().__init__(coordinator, config_entry, entity_config)

        # _attr_name is already set by InnotempCoordinatorEntity using entity_config['label']
        # self._attr_name = param_data.get("label", f"Innotemp Sensor {param_id}")

        # _attr_unique_id is also set by InnotempCoordinatorEntity
        # self._attr_unique_id = f"{config_entry.unique_id}_{param_id}"

        self._attr_native_unit_of_measurement = self._param_data.get(
            "unit"
        )  # Use self._param_data
        self._param_id = param_id  # Store the 'var' to fetch data from coordinator

        # Attempt to map units to device classes or set state class
        unit = str(self._attr_native_unit_of_measurement).lower()
        if unit == "°c" or unit == "c":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit == "%":
            self._attr_device_class = (
                SensorDeviceClass.HUMIDITY
            )  # Could also be battery, power factor etc.
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit == "k.w" or unit == "kw":  # Check for 'k.w' from user data
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif unit == "s":  # seconds
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_state_class = (
                SensorStateClass.MEASUREMENT
            )  # Or total increasing if it's an uptime counter
        # Add more unit mappings to device_class and state_class as needed
        # e.g. kWh for energy, V for voltage, A for current, etc.
        else:
            self._attr_device_class = None  # Generic sensor
            self._attr_state_class = (
                SensorStateClass.MEASUREMENT
            )  # Default to measurement

        _LOGGER.debug(
            f"InnotempSensor initialized: name='{self.name}', unique_id='{self.unique_id}', unit='{self.native_unit_of_measurement}', param_id='{self._param_id}', device_class='{self.device_class}', state_class='{self.state_class}'"
        )

    @property
    def native_value(self):
        """Return the state of the sensor."""
        api_value = self._get_api_value()
        if api_value is None:
            return None

        # Handle 'nan' specifically for numeric sensors before attempting float conversion
        value_str = str(api_value)
        is_numeric_sensor_type = self.device_class in [
            SensorDeviceClass.TEMPERATURE,
            SensorDeviceClass.HUMIDITY,
            SensorDeviceClass.POWER,
            SensorDeviceClass.DURATION,
            # Add other numeric device classes if relevant
        ] or str(self.native_unit_of_measurement).lower() in [
            "°c",
            "c",
            "%",
            "k.w",
            "kw",
            "s",
        ]

        if is_numeric_sensor_type and value_str.lower() == "nan":
            _LOGGER.debug(
                f"InnotempSensor.native_value: Received 'nan' for numeric sensor {self.entity_id} (param_id: {self._param_id}). Returning None."
            )
            return None

        # Attempt to convert to float if it's a known numeric type
        if is_numeric_sensor_type:
            try:
                return float(value_str)
            except (ValueError, TypeError):
                _LOGGER.warning(
                    f"Could not convert sensor value '{value_str}' to float for {self.entity_id} (param_id: {self._param_id}, unit: {self.native_unit_of_measurement}). Returning as is (if non-numeric type) or None (if conversion was expected)."
                )
                # If it was expected to be numeric but couldn't convert (and wasn't 'nan'),
                # returning None might be safer than returning a string that HA might misinterpret.
                return None

        return value_str  # Return raw string value if no specific conversion logic or not numeric

    @property
    def state_class(self):
        """Return the state class of the sensor."""
        # Define state class based on param_data if available, e.g., Measurement, Total
        # For now, returning None, adjust as needed
        return None


class InnotempEnumSensor(InnotempCoordinatorEntity, SensorEntity):
    """Representation of an Innotemp ENUM Sensor for ONOFFAUTO states."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ONOFFAUTO_OPTIONS_LIST

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,
        component_attributes: dict,  # Attributes of the component block (e.g. 'display', 'param')
        sensor_data: dict,  # The sensor's own data dict {'var':..., 'unit':..., 'label':...}
    ) -> None:
        """Initialize the ENUM sensor."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_data = sensor_data
        self._param_id = self._param_data.get("var")

        original_label = self._param_data.get("label", f"Status {self._param_id}")
        cleaned_label = strip_html(original_label)

        # Modify label/param for unique ID within InnotempCoordinatorEntity
        # Append '_status' to the param_id for the superclass to create a unique entity ID
        # The label should also reflect it's a status/enum sensor
        entity_config = {
            "param": f"{self._param_id}_status",  # Ensures unique_id is different from the select entity
            "label": f"{cleaned_label} Status",
        }
        super().__init__(coordinator, config_entry, entity_config)

        _LOGGER.debug(
            f"InnotempEnumSensor initialized: name='{self.name}', unique_id='{self.unique_id}', options='{self.options}', param_id='{self._param_id}'"
        )

    @property
    def native_value(self) -> str | None:
        """Return the current string state of the sensor."""
        api_value = self._get_api_value()
        if api_value is None:
            return None

        try:
            # Ensure api_value is treated as an integer for dictionary lookup
            selected_option = API_VALUE_TO_ONOFFAUTO_OPTION.get(int(api_value))
            if selected_option is None:
                _LOGGER.warning(
                    f"InnotempEnumSensor.native_value: Unknown API value '{api_value}' for param_id {self._param_id} on entity {self.entity_id}. Not in {API_VALUE_TO_ONOFFAUTO_OPTION}"
                )
            return selected_option
        except (ValueError, TypeError):
            _LOGGER.warning(
                f"InnotempEnumSensor.native_value: Could not convert API value '{api_value}' to int for param_id {self._param_id} on entity {self.entity_id}."
            )
            return None

    # Ensure InnotempEnumSensor uses its _attr_device_class = SensorDeviceClass.ENUM
    # by NOT overriding the device_class property here. If this property method exists
    # and returns something else (or None), it will take precedence over _attr_device_class.
    #
    # @property
    # def device_class(self):
    #     """Return the device class of the sensor."""
    #     # This was incorrectly returning None, overriding _attr_device_class.
    #     # For Enum sensors, we rely on _attr_device_class = SensorDeviceClass.ENUM.
    #     return SensorDeviceClass.ENUM # Or better, remove this property from InnotempEnumSensor


class InnotempOnOffSensor(InnotempCoordinatorEntity, SensorEntity):
    """Representation of an Innotemp Sensor for ONOFF states."""

    _attr_device_class = SensorDeviceClass.ENUM
    # Define the human-readable options
    _attr_options = ONOFF_OPTIONS_LIST

    # API_VALUE_TO_ONOFF_OPTION is imported from api_parser

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,
        component_attributes: dict,
        sensor_data: dict,
    ) -> None:
        """Initialize the ONOFF sensor."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_data = sensor_data
        self._param_id = self._param_data.get("var")

        original_label = self._param_data.get("label", f"State {self._param_id}")
        cleaned_label = strip_html(original_label)

        entity_config = {
            "param": f"{self._param_id}_onoff_status",  # Ensures unique_id
            "label": cleaned_label,
        }
        super().__init__(coordinator, config_entry, entity_config)

        self._attr_native_unit_of_measurement = None  # ENUMs don't have a unit

        _LOGGER.debug(
            f"InnotempOnOffSensor initialized: name='{self.name}', unique_id='{self.unique_id}', "
            f"options='{self.options}', param_id='{self._param_id}'"
        )

    @property
    def native_value(self) -> str | None:
        """Return the current string state of the sensor (On/Off)."""
        api_value_from_coord = self._get_api_value()
        if api_value_from_coord is None:
            return None

        # Convert API value to string to handle various numeric types (int, float, string)
        api_value_str = str(api_value_from_coord)

        selected_option = API_VALUE_TO_ONOFF_OPTION.get(api_value_str)

        if selected_option is None:
            _LOGGER.warning(
                f"InnotempOnOffSensor.native_value: Unknown API value '{api_value_str}' for ONOFF sensor param_id {self._param_id} on entity {self.entity_id}. Not in {API_VALUE_TO_ONOFF_OPTION}"
            )
            return None  # Or some other default like "Unknown"

        return selected_option


class InnotempDynamicEnumSensor(InnotempCoordinatorEntity, SensorEntity):
    """Representation of an Innotemp Sensor with dynamically parsed ENUM options."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,
        component_attributes: dict,
        sensor_data: dict,
        value_to_name_map: Dict[str, str],
        options: List[str],
    ) -> None:
        """Initialize the dynamic ENUM sensor."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_data = sensor_data
        self._param_id = self._param_data.get("var")

        self._value_to_name_map = value_to_name_map
        self._attr_options = options

        original_label = self._param_data.get("label", f"Setting {self._param_id}")
        cleaned_label = strip_html(original_label)

        # Append '_setting' or similar to param_id for unique entity ID if it might clash
        # with other entities (e.g. a select entity if this is also controllable)
        # For now, assume it's a read-only sensor state.
        entity_config = {
            "param": f"{self._param_id}_dynenum",  # Ensure unique_id
            "label": cleaned_label,  # Label it clearly
        }
        super().__init__(coordinator, config_entry, entity_config)

        # No native_unit_of_measurement for ENUM type sensors.
        self._attr_native_unit_of_measurement = None

        _LOGGER.debug(
            f"InnotempDynamicEnumSensor initialized: name='{self.name}', unique_id='{self.unique_id}', "
            f"options='{self.options}', param_id='{self._param_id}', map='{self._value_to_name_map}'"
        )

    @property
    def native_value(self) -> str | None:
        """Return the current string state of the sensor."""
        api_value_from_coord = self._get_api_value()
        if api_value_from_coord is None:
            return None

        # API value can be number or string. Ensure we use string for map lookup.
        api_value_str = str(api_value_from_coord)

        selected_option = self._value_to_name_map.get(api_value_str)

        if selected_option is None:
            _LOGGER.warning(
                f"InnotempDynamicEnumSensor.native_value: Unknown API value '{api_value_str}' for param_id {self._param_id} on entity {self.entity_id}. Not in map {self._value_to_name_map}"
            )
            # Fallback to raw value or a special string like "Unknown"
            # For now, return None or the raw string to indicate an issue.
            return None  # Or api_value_str if preferred to show the raw unmapped value

        return selected_option
