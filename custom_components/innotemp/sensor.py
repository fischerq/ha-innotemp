"""Sensor platform for Innotemp."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, List


from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
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
    get_sensor_attributes_from_unit, # Moved here
    API_VALUE_TO_ONOFFAUTO_OPTION,
    ONOFFAUTO_OPTIONS_LIST,
    API_VALUE_TO_ONOFF_OPTION,
    ONOFF_OPTIONS_LIST
)

_LOGGER = logging.getLogger(__name__)


def _create_sensor_entity_data(
    item_data: Dict[str, Any],
    room_attributes: Dict[str, Any],
    numeric_room_id: Optional[int],
    component_attributes: Dict[str, Any],
    component_key_hint: str,
) -> Optional[Dict[str, Any]]:
    """Processes an item from config data to determine its sensor type and data."""
    param_id = item_data.get("var")
    unit = item_data.get("unit")

    if not (param_id and unit):
        return None

    sensor_type_data = {
        "room_attributes": room_attributes,
        "component_attributes": component_attributes,
        "item_data": item_data,
        "param_id": param_id,
        "unit": unit,
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
                f"Failed to parse VAR: unit string '{unit}' for {param_id}. Treating as regular sensor."
            )
            sensor_type_data["sensor_class"] = "RegularSensor"
    else:
        sensor_type_data["sensor_class"] = "RegularSensor"
    return sensor_type_data


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Innotemp sensors based on a config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning("Innotemp sensor setup: config_data is None, skipping.")
        return

    _LOGGER.debug("Innotemp sensor setup: Processing config_data")

    possible_sensor_containers_keys = [
        "display", "param", "mixer", "pump", "piseq", "radiator", "drink", "main",
    ]

    sensor_entities_data = process_room_config_data(
        config_data=config_data,
        possible_container_keys=possible_sensor_containers_keys,
        item_processor=_create_sensor_entity_data,
    )

    entities: List[SensorEntity] = []
    for entity_data in sensor_entities_data:
        sensor_class_type = entity_data.pop("sensor_class")

        common_args = {
            "coordinator": coordinator,
            "config_entry": entry,
            "room_attributes": entity_data["room_attributes"],
            "component_attributes": entity_data["component_attributes"],
            "sensor_data": entity_data["item_data"],
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

    if entities:
        _LOGGER.info(f"Found {len(entities)} Innotemp sensor entities.")
    else:
        _LOGGER.info("No Innotemp sensor entities found.")

    async_add_entities(entities)


class InnotempSensor(InnotempCoordinatorEntity, SensorEntity):
    """Representation of a generic Innotemp Sensor."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,
        component_attributes: dict,
        sensor_data: dict,
    ) -> None:
        """Initialize the sensor."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_data = sensor_data

        param_id = self._param_data.get("var")
        original_label = self._param_data.get("label", f"Sensor {param_id}")
        cleaned_label = strip_html(original_label)

        entity_config = {
            "param": param_id,
            "label": cleaned_label if cleaned_label else f"Sensor {param_id}",
        }
        super().__init__(coordinator, config_entry, entity_config)

        self._attr_native_unit_of_measurement = self._param_data.get("unit")
        self._param_id = param_id

        sensor_attrs = get_sensor_attributes_from_unit(self.native_unit_of_measurement)

        if dc_str := sensor_attrs.get("device_class"):
            try:
                self._attr_device_class = SensorDeviceClass(dc_str)
            except ValueError:
                _LOGGER.warning(f"Invalid SensorDeviceClass string '{dc_str}' for {self.name}")
                self._attr_device_class = None
        else:
            self._attr_device_class = None

        if sc_str := sensor_attrs.get("state_class"):
            try:
                self._attr_state_class = SensorStateClass(sc_str)
            except ValueError:
                _LOGGER.warning(f"Invalid SensorStateClass string '{sc_str}' for {self.name}")
                self._attr_state_class = SensorStateClass.MEASUREMENT # Default fallback
        else:
             self._attr_state_class = SensorStateClass.MEASUREMENT

        _LOGGER.debug(
            f"InnotempSensor initialized: {self.name} ({self.unique_id}), unit: {self.native_unit_of_measurement}"
        )

    @property
    def native_value(self):
        """Return the state of the sensor."""
        api_value = self._get_api_value()
        if api_value is None:
            return None

        value_str = str(api_value)
        is_numeric_sensor_type = self.device_class in [
            SensorDeviceClass.TEMPERATURE,
            SensorDeviceClass.HUMIDITY,
            SensorDeviceClass.POWER,
            SensorDeviceClass.DURATION,
        ] or str(self.native_unit_of_measurement).lower() in ["°c", "c", "%", "k.w", "kw", "s"]

        if is_numeric_sensor_type and value_str.lower() == "nan":
            return None

        if is_numeric_sensor_type:
            try:
                return float(value_str)
            except (ValueError, TypeError):
                _LOGGER.warning(
                    f"Could not convert sensor value '{value_str}' to float for {self.entity_id}."
                )
                return None
        return value_str

    # state_class is now set in __init__ using the helper
    # @property
    # def state_class(self):
    #     """Return the state class of the sensor."""
    #     return None


class InnotempEnumSensor(InnotempCoordinatorEntity, SensorEntity):
    """Representation of an Innotemp ENUM Sensor for ONOFFAUTO states."""
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ONOFFAUTO_OPTIONS_LIST

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,
        component_attributes: dict,
        sensor_data: dict,
    ) -> None:
        """Initialize the ENUM sensor."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_data = sensor_data
        self._param_id = self._param_data.get("var")

        original_label = self._param_data.get("label", f"Status {self._param_id}")
        cleaned_label = strip_html(original_label)

        entity_config = {
            "param": f"{self._param_id}_status",
            "label": f"{cleaned_label} Status",
        }
        super().__init__(coordinator, config_entry, entity_config)
        _LOGGER.debug(f"InnotempEnumSensor initialized: {self.name}")

    @property
    def native_value(self) -> str | None:
        """Return the current string state of the sensor."""
        api_value = self._get_api_value()
        if api_value is None:
            return None
        try:
            selected_option = API_VALUE_TO_ONOFFAUTO_OPTION.get(int(api_value))
            if selected_option is None:
                _LOGGER.warning(f"Unknown API value '{api_value}' for ONOFFAUTO enum sensor {self.entity_id}.")
            return selected_option
        except (ValueError, TypeError):
            _LOGGER.warning(f"Could not convert API value '{api_value}' to int for ONOFFAUTO enum sensor {self.entity_id}.")
            return None


class InnotempOnOffSensor(InnotempCoordinatorEntity, SensorEntity):
    """Representation of an Innotemp Sensor for ONOFF states."""
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ONOFF_OPTIONS_LIST

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
            "param": f"{self._param_id}_onoff_status",
            "label": cleaned_label,
        }
        super().__init__(coordinator, config_entry, entity_config)
        self._attr_native_unit_of_measurement = None
        _LOGGER.debug(f"InnotempOnOffSensor initialized: {self.name}")

    @property
    def native_value(self) -> str | None:
        """Return the current string state of the sensor (On/Off)."""
        api_value_from_coord = self._get_api_value()
        if api_value_from_coord is None:
            return None
        api_value_str = str(api_value_from_coord)
        selected_option = API_VALUE_TO_ONOFF_OPTION.get(api_value_str)
        if selected_option is None:
            _LOGGER.warning(f"Unknown API value '{api_value_str}' for ONOFF sensor {self.entity_id}.")
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
        entity_config = {
            "param": f"{self._param_id}_dynenum",
            "label": cleaned_label,
        }
        super().__init__(coordinator, config_entry, entity_config)
        self._attr_native_unit_of_measurement = None
        _LOGGER.debug(f"InnotempDynamicEnumSensor initialized: {self.name}")

    @property
    def native_value(self) -> str | None:
        """Return the current string state of the sensor."""
        api_value_from_coord = self._get_api_value()
        if api_value_from_coord is None:
            return None
        api_value_str = str(api_value_from_coord)
        selected_option = self._value_to_name_map.get(api_value_str)
        if selected_option is None:
            _LOGGER.warning(f"Unknown API value '{api_value_str}' for dynamic enum sensor {self.entity_id}.")
        return selected_option