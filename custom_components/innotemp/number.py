"""Platform for number entities for Innotemp."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional


from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import UnitOfTemperature, PERCENTAGE  # For units

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity
from .api_parser import strip_html, process_room_config_data

_LOGGER = logging.getLogger(__name__)


def _is_potential_number_entity(item_data: Dict[str, Any]) -> bool:
    """Checks if the item_data represents a potential number entity."""
    unit = item_data.get("unit")
    param_id = item_data.get("var")
    # access = item_data.get("access", "user") # Could be used later if needed

    if not (
        param_id and unit and unit != "ONOFFAUTO"
    ):  # and access in ["user", "admin"]
        return False

    # Check for known numeric units
    if (
        unit
        in [
            UnitOfTemperature.CELSIUS,
            UnitOfTemperature.FAHRENHEIT,
            PERCENTAGE,
            "K",
            "s",
            "min",
            "h",
            "bar",
            "rpm",
        ]
        or "%" in unit
    ):
        return True

    # Broad assumption for now: if it has a unit, is not ONOFFAUTO, and is in an 'entry',
    # it's a number. This might need refinement.
    _LOGGER.debug(
        f"Number: Entry with unit '{unit}' for param_id '{param_id}' treated as potential number (further checks in class)."
    )
    return True


def _create_number_entity_data(
    item_data: Dict[str, Any],
    room_attributes: Dict[str, Any],
    numeric_room_id: Optional[
        int
    ],  # Numbers use string room_id from room_attributes.get("var")
    component_attributes: Dict[str, Any],
    component_key_hint: str,
) -> Optional[Dict[str, Any]]:
    """
    Processes an item from config data to determine if it's a number entity.
    Returns a dictionary with necessary data for entity creation if valid, else None.
    """
    if _is_potential_number_entity(item_data):
        param_id = item_data.get("var")
        _LOGGER.debug(
            f"Number: Found potential number entity: room_var {room_attributes.get('var')}, "
            f"component_var {component_attributes.get('var', component_attributes.get('type'))}, "
            f"item_var {param_id}, data {item_data}, source_hint: {component_key_hint}"
        )
        return {
            "room_attributes": room_attributes,
            "component_attributes": component_attributes,
            "param_id": param_id,
            "param_data": item_data,
        }
    else:
        unit = item_data.get("unit")
        param_id = item_data.get("var")
        if param_id and unit:  # Log only if it looked like it could have been an entity
            _LOGGER.debug(
                f"Number: Entry unit '{unit}' for {param_id} from {component_key_hint} not recognized as numeric or is ONOFFAUTO."
            )
    return None


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

    _LOGGER.debug(
        "Innotemp number setup: Processing config_data (first 500 chars): %s",
        str(config_data)[:500],
    )

    possible_containers_keys = [
        "param",
        "mixer",
        "piseq",
        "radiator",
        "drink",
        "main",
        # 'pump', 'display' usually have 'input'/'output' for sensors,
        # less likely 'entry' for numbers
    ]

    number_entities_data = process_room_config_data(
        config_data=config_data,
        possible_container_keys=possible_containers_keys,
        item_processor=_create_number_entity_data,
    )

    entities = []
    for entity_data in number_entities_data:
        entities.append(
            InnotempNumber(
                coordinator=coordinator,
                config_entry=entry,  # Pass the main config_entry
                room_attributes=entity_data["room_attributes"],
                component_attributes=entity_data["component_attributes"],
                param_id=entity_data["param_id"],
                param_data=entity_data["param_data"],
            )
        )

    if not entities:
        _LOGGER.info(
            "No Number entities found in Innotemp configuration using new parser."
        )
    else:
        _LOGGER.info(
            f"Found {len(entities)} Innotemp Number entities to be added using new parser."
        )

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
        cleaned_label = strip_html(original_label)

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
        api_value = self._get_api_value()
        if api_value is None:
            return None

        try:
            return float(api_value)
        except (ValueError, TypeError):
            _LOGGER.warning(
                f"Could not convert number value '{api_value}' to float for {self.entity_id} (param_id: {self._param_id})."
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
