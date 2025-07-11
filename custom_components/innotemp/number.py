"""Platform for number entities for Innotemp."""

from __future__ import annotations

import logging
import re  # For stripping HTML
import json  # For parsing string values in config_data if necessary

from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import UnitOfTemperature, PERCENTAGE  # For units

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity

_LOGGER = logging.getLogger(__name__)


def _strip_html(text: str | None) -> str:
    """Remove HTML tags from a string."""
    if text is None:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def _extract_numbers_from_room_component(
    component_data, coordinator, entry, room_attributes, entities_list
):
    """
    Extracts number entities from 'entry' items within a component of room data.
    Number entities are identified by having a numeric unit (not ONOFFAUTO).
    """
    if not component_data:
        return

    components_to_process = []
    if isinstance(component_data, dict):
        components_to_process.append(component_data)
    elif isinstance(component_data, list):
        components_to_process.extend(component_data)
    else:
        _LOGGER.debug(
            f"Number: Unexpected component_data type: {type(component_data)} for room {room_attributes.get('var')}"
        )
        return

    for component_item_data in components_to_process:
        if not isinstance(component_item_data, dict):
            _LOGGER.debug(
                f"Number: Skipping non-dict item in component_data list for room {room_attributes.get('var')}: {component_item_data}"
            )
            continue

        component_attributes = component_item_data.get("@attributes", {})
        entry_data = component_item_data.get("entry")
        if not entry_data:
            continue

        entries = []
        if isinstance(entry_data, dict):
            entries.append(entry_data)
        elif isinstance(entry_data, list):
            entries.extend(entry_data)

        for actual_entry in entries:
            if not isinstance(actual_entry, dict):
                _LOGGER.debug(
                    f"Number: Skipping non-dict entry in entries list for room {room_attributes.get('var')}: {actual_entry}"
                )
                continue

            unit = actual_entry.get("unit")
            param_id = actual_entry.get("var")
            access = actual_entry.get("access", "user")  # Default to user access if not specified

            # Condition for a settable number: has 'var', 'unit' is not ONOFFAUTO, and unit suggests numeric.
            # And typically 'access' is 'user' or 'admin'.
            # For now, we consider any non-ONOFFAUTO unit in an 'entry' as a potential number.
            # We might need to refine this if some entries are read-only displays.
            if (
                param_id and unit and unit != "ONOFFAUTO"
            ):  # and access in ["user", "admin"]:
                # Further check if unit is numeric, e.g. by trying to assign device_class or known units
                is_potential_number = False
                if unit in [
                    UnitOfTemperature.CELSIUS,
                    UnitOfTemperature.FAHRENHEIT,
                    PERCENTAGE,
                    "K",
                    "s",
                    "min",
                    "h",
                    "bar",
                    "rpm",
                ]:  # Add more known numeric units
                    is_potential_number = True
                elif "%" in unit:  # Handle units like "0..100%"
                    is_potential_number = True
                # Add a check for generic numbers if unit doesn't match above but entry implies settable
                # For now, if it has a unit and is not ONOFFAUTO, and is in an 'entry', assume it's a number.
                else:
                    # Could try a generic float conversion test on a sample value if available,
                    # but that's complex for setup. Assume if it has a unit and is an 'entry', it's settable.
                    # This might need refinement based on actual device data to avoid read-only entries.
                    _LOGGER.debug(
                        f"Number: Entry with unit '{unit}' for param_id '{param_id}' treated as potential number (further checks in class)."
                    )
                    is_potential_number = True  # Broad assumption for now

                if is_potential_number:
                    _LOGGER.debug(
                        f"Number: Found potential number entity: room_var {room_attributes.get('var')}, "
                        f"component_var {component_attributes.get('var', component_attributes.get('type'))}, "
                        f"item_var {param_id}, data {actual_entry}"
                    )
                    # Placeholder for InnotempNumber instantiation
                    entities_list.append(
                        InnotempNumber(
                            coordinator,
                            entry,
                            room_attributes,
                            component_attributes,
                            param_id,
                            actual_entry,
                        )
                    )
                else:
                    _LOGGER.debug(
                        f"Number: Entry unit '{unit}' for {param_id} not recognized as numeric or is ONOFFAUTO."
                    )
            # else:
            #     _LOGGER.debug(f"Number: Entry {param_id} skipped (no unit, or ONOFFAUTO, or access issue). Unit: {unit}, Access: {access}")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up number entities based on config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning(
            "Innotemp number setup: config_data is None, skipping entity creation."
        )
        async_add_entities([])
        return

    entities = []
    _LOGGER.debug(
        "Innotemp number setup: Received config_data (first 500 chars): %s",
        str(config_data)[:500],
    )

    if not isinstance(config_data, dict):
        _LOGGER.error(
            f"Number: Config_data is not a dictionary. Type: {type(config_data)}. Data: {config_data}"
        )
        async_add_entities([])
        return

    for top_level_key, top_level_value in config_data.items():
        actual_room_list = []
        if isinstance(top_level_value, list):
            actual_room_list = top_level_value
        elif isinstance(top_level_value, dict) and top_level_value.get(
            "@attributes", {}
        ).get("type", "").startswith("room"):
            actual_room_list.append(top_level_value)

        if not actual_room_list and isinstance(top_level_value, str):
            try:
                parsed_value = json.loads(top_level_value)
                if isinstance(parsed_value, list):
                    actual_room_list = parsed_value
            except json.JSONDecodeError:
                _LOGGER.debug(
                    f"Number: Could not parse string value for key {top_level_key} as JSON list."
                )

        if not actual_room_list:
            continue

        for room_data_dict in actual_room_list:
            if not isinstance(room_data_dict, dict):
                _LOGGER.warning(
                    f"Number: Item in room list for key '{top_level_key}' is not a dict: {room_data_dict}"
                )
                continue

            room_attributes = room_data_dict.get("@attributes", {})
            if not room_attributes.get("var"):
                _LOGGER.warning(
                    f"Number: Room missing '@attributes.var': {room_attributes}. Skipping."
                )
                continue

            # These are keys for components within a room that might contain 'entry' sub-keys for numbers
            possible_containers_keys = [
                "param",
                "mixer",
                "piseq",
                "radiator",
                "drink",
                "main",
                # 'pump', 'display' usually have 'input'/'output' for sensors, less likely 'entry' for numbers
            ]

            for container_key in possible_containers_keys:
                component_data = room_data_dict.get(container_key)
                if component_data:
                    _extract_numbers_from_room_component(
                        component_data, coordinator, entry, room_attributes, entities
                    )

    if not entities:
        _LOGGER.info(
            "No Number entities found in Innotemp configuration with current logic."
        )
    else:
        _LOGGER.info(f"Found {len(entities)} Innotemp Number entities to be added.")

    async_add_entities(entities)


class InnotempNumber(InnotempCoordinatorEntity, NumberEntity):
    """Representation of an Innotemp Number entity for settable numeric values."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,
        component_attributes: dict,
        param_id: str,  # 'var' of the numeric entry
        param_data: dict,  # The numeric entry's own data dict
    ):
        """Initialize the Innotemp Number entity."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_id = param_id
        self._param_data = param_data

        original_label = self._param_data.get("label", f"Setting {self._param_id}")
        cleaned_label = _strip_html(original_label)

        entity_config = {
            "param": self._param_id,
            "label": cleaned_label if cleaned_label else f"Setting {self._param_id}",
        }
        super().__init__(coordinator, config_entry, entity_config)

        self._api_room_id = room_attributes.get("var")  # For API calls
        self._attr_native_unit_of_measurement = self._param_data.get("unit")

        # Set device class and default min/max/step based on unit
        unit = str(self.native_unit_of_measurement).lower()
        if unit == UnitOfTemperature.CELSIUS or unit == "c" or unit == "°c":
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_min_value = 5.0
            self._attr_native_max_value = 35.0
            self._attr_native_step = 0.5
        elif unit == PERCENTAGE or "%" in unit:
            self._attr_device_class = (
                None  # No specific NumberDeviceClass for generic percentage
            )
            self._attr_native_min_value = 0.0
            self._attr_native_max_value = 100.0
            self._attr_native_step = 1.0
        elif unit == "k":  # Assuming Kelvin for temperature differences/spreads
            self._attr_device_class = (
                None  # No direct NumberDeviceClass for Kelvin spread
            )
            self._attr_native_min_value = 0.0
            self._attr_native_max_value = 20.0  # Example range for a spread
            self._attr_native_step = 0.1
        else:  # Generic number
            self._attr_device_class = None
            self._attr_native_min_value = 0.0
            self._attr_native_max_value = 1000.0  # Default large range
            self._attr_native_step = 1.0

        # TODO: Look for min/max/step in self._param_data if device provides them

        _LOGGER.debug(
            f"InnotempNumber initialized: name='{self.name}', unique_id='{self.unique_id}', "
            f"param_id='{self._param_id}', unit='{self.native_unit_of_measurement}', "
            f"min={self.native_min_value}, max={self.native_max_value}, step={self.native_step}, "
            f"device_class='{self.device_class}'"
        )

    @property
    def native_value(self) -> float | None:
        """Return the current numeric value."""
        if self.coordinator.data is None:
            _LOGGER.debug(
                f"InnotempNumber.native_value: Coordinator data is None for {self.entity_id} (param_id: {self._param_id})."
            )
            return None

        value = self.coordinator.data.get(self._param_id)
        if value is None:
            _LOGGER.debug(
                f"InnotempNumber.native_value: Param_id {self._param_id} not found in coordinator data for {self.entity_id}."
            )
            return None

        try:
            return float(value)
        except (ValueError, TypeError):
            _LOGGER.warning(
                f"Could not convert number value '{value}' to float for {self.entity_id} (param_id: {self._param_id})."
            )
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        previous_api_value = None
        if self.coordinator.data is not None:
            previous_api_value = self.coordinator.data.get(self._param_id)

        # The API might expect integers or specific string formatting for numbers
        # For now, sending as is, assuming API handles float or simple string conversion.
        # If API expects int, use int(value).
        # If specific precision is needed, format value: f"{value:.1f}"
        api_value_to_send = value

        _LOGGER.debug(
            f"Sending command for {self.entity_id}: room {self._api_room_id}, param {self._param_id}, "
            f"new_val {api_value_to_send}, prev_val {previous_api_value}"
        )

        try:
            success = await self.coordinator.api_client.async_send_command(
                room_id=self._api_room_id,
                param=self._param_id,
                val_new=api_value_to_send,
                val_prev=previous_api_value,
            )
            if success:
                _LOGGER.info(
                    f"Successfully sent command for {self.entity_id} to set value to {value}."
                )
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(
                    f"Failed to send command for {self.entity_id} to set value to {value}."
                )
        except Exception as e:
            _LOGGER.error(
                f"Error sending command for {self.entity_id} to set value to {value}: {e}",
                exc_info=True,
            )
