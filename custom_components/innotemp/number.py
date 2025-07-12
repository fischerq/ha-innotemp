"""Platform for number entities for Innotemp."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity
from .api_parser import (
    strip_html,
    process_room_config_data,
    is_number_item,
    get_number_attributes_from_unit,
)

_LOGGER = logging.getLogger(__name__)


def _create_number_entity_data(
    item_data: Dict[str, Any],
    room_attributes: Dict[str, Any],
    numeric_room_id: Optional[int],
    component_attributes: Dict[str, Any],
    component_key_hint: str,
) -> Optional[Dict[str, Any]]:
    """Processes an item from config data to determine if it's a number entity."""
    if is_number_item(item_data):
        return {
            "room_attributes": room_attributes,
            "component_attributes": component_attributes,
            "param_id": item_data.get("var"),
            "param_data": item_data,
        }
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up number entities based on a config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning("Innotemp number setup: config_data is None, skipping.")
        return

    _LOGGER.debug("Innotemp number setup: Processing config_data")

    possible_containers_keys = [
        "param", "mixer", "piseq", "radiator", "drink", "main",
    ]

    number_entities_data = process_room_config_data(
        config_data=config_data,
        possible_container_keys=possible_containers_keys,
        item_processor=_create_number_entity_data,
    )

    entities = [
        InnotempNumber(
            coordinator=coordinator,
            config_entry=entry,
            room_attributes=entity_data["room_attributes"],
            component_attributes=entity_data["component_attributes"],
            param_id=entity_data["param_id"],
            param_data=entity_data["param_data"],
        )
        for entity_data in number_entities_data
    ]

    if entities:
        _LOGGER.info(f"Found {len(entities)} Innotemp Number entities.")
    else:
        _LOGGER.info("No Number entities found in Innotemp configuration.")

    async_add_entities(entities)


class InnotempNumber(InnotempCoordinatorEntity, NumberEntity):
    """Representation of an Innotemp Number entity."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,
        component_attributes: dict,
        param_id: str,
        param_data: dict,
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

        self._api_room_id = room_attributes.get("var")
        self._attr_native_unit_of_measurement = self._param_data.get("unit")

        num_attrs = get_number_attributes_from_unit(self.native_unit_of_measurement)

        if device_class_str := num_attrs.get("device_class"):
             try:
                self._attr_device_class = NumberDeviceClass(device_class_str)
             except ValueError:
                _LOGGER.warning(f"Invalid NumberDeviceClass string '{device_class_str}' for {self.name}")
                self._attr_device_class = None
        else:
            self._attr_device_class = None

        self._attr_native_min_value = num_attrs.get("native_min_value")
        self._attr_native_max_value = num_attrs.get("native_max_value")
        self._attr_native_step = num_attrs.get("native_step")

        _LOGGER.debug(
            f"InnotempNumber initialized: {self.name} ({self.unique_id}) with unit {self.native_unit_of_measurement}"
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
                f"Could not convert number value '{api_value}' to float for {self.entity_id}."
            )
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        previous_api_value = self._get_api_value()
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
