"""Platform for switch entities for Innotemp."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional


from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity
from .api_parser import (
    strip_html,
    process_room_config_data,
    API_VALUE_TO_ONOFF_OPTION,
    ONOFF_OPTION_TO_API_VALUE,
)

_LOGGER = logging.getLogger(__name__)


def _create_switch_entity_data(
    item_data: Dict[str, Any],
    room_attributes: Dict[str, Any],
    numeric_room_id: Optional[int],
    component_attributes: Dict[str, Any],
    component_key_hint: str,
) -> Optional[Dict[str, Any]]:
    """
    Processes an item from config data to determine if it's a switch entity.
    Returns a dictionary with necessary data for entity creation if valid, else None.
    """
    if item_data.get("unit") == "ONOFF":
        param_id = item_data.get("var")
        # Switch entities require a numeric_room_id for API calls
        if param_id and numeric_room_id is not None:
            _LOGGER.debug(
                f"Switch: Found ONOFF: room_var {room_attributes.get('var')} (numeric: {numeric_room_id}), "
                f"component_var {component_attributes.get('var', component_attributes.get('type'))}, "
                f"item_var {param_id}, source_hint: {component_key_hint}"
            )
            return {
                "room_attributes": room_attributes,
                "numeric_room_id": numeric_room_id,
                "component_attributes": component_attributes,
                "param_id": param_id,
                "param_data": item_data,
            }
        elif not numeric_room_id:
            _LOGGER.debug(
                f"Switch: Skipping ONOFF item {param_id} for room {room_attributes.get('var')} due to missing numeric_room_id."
            )
        else:  # not param_id
            _LOGGER.warning(
                f"Switch: Found ONOFF entry without 'var' (param_id) in room {room_attributes.get('var')}, "
                f"component {component_attributes.get('var', component_attributes.get('type'))} from {component_key_hint}: {item_data}"
            )
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up switch entities based on config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning(
            "Innotemp switch setup: config_data is None, skipping entity creation."
        )
        async_add_entities([])
        return

    _LOGGER.debug(
        "Innotemp switch setup: Processing config_data (first 500 chars): %s",
        str(config_data)[:500],
    )

    possible_containers_keys = [
        "param",
        "pump",
        "piseq",
        "mixer",
        "drink",
        "radiator",
        "main",
    ]

    switch_entities_data = process_room_config_data(
        config_data=config_data,
        possible_container_keys=possible_containers_keys,
        item_processor=_create_switch_entity_data,
    )

    entities = []
    for entity_data in switch_entities_data:
        entities.append(
            InnotempSwitch(
                coordinator=coordinator,
                config_entry=entry,
                room_attributes=entity_data["room_attributes"],
                numeric_api_room_id=entity_data["numeric_room_id"],
                component_attributes=entity_data["component_attributes"],
                param_id=entity_data["param_id"],
                param_data=entity_data["param_data"],
            )
        )

    if not entities:
        _LOGGER.info(
            "No ONOFF (switch) entities found in Innotemp configuration using new parser."
        )
    else:
        _LOGGER.info(
            f"Found {len(entities)} Innotemp switch entities to be added using new parser."
        )

    async_add_entities(entities)


class InnotempSwitch(InnotempCoordinatorEntity, SwitchEntity):
    """Representation of an Innotemp Switch entity for ONOFF controls."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,
        numeric_api_room_id: int,
        component_attributes: dict,
        param_id: str,
        param_data: dict,
    ):
        """Initialize the Innotemp Switch entity."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_id = param_id
        self._param_data = param_data

        self._numeric_api_room_id = numeric_api_room_id

        original_label = self._param_data.get("label", f"Switch {self._param_id}")
        cleaned_label = strip_html(original_label)
        room_id_var = room_attributes.get("var", "NO_ROOM_ID")
        component_id = component_attributes.get("var") or component_attributes.get(
            "type", "NO_COMP_ID"
        )

        new_label = (
            f"{room_id_var} - {component_id} - {cleaned_label}"
            if cleaned_label
            else f"{room_id_var} - {component_id} - Switch {self._param_id}"
        )

        entity_config = {
            "param": self._param_id,
            "label": new_label,
        }
        super().__init__(coordinator, config_entry, entity_config)

        _LOGGER.debug(
            f"InnotempSwitch initialized: name='{self.name}', unique_id='{self.unique_id}', "
            f"param_id='{self._param_id}', numeric_api_room_id='{self._numeric_api_room_id}'"
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        api_value = self._get_api_value()
        if api_value is None:
            return None

        # API value is expected to be "1" or "0" (as string) or possibly int
        return str(api_value) == "1"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._send_switch_command(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._send_switch_command(False)

    async def _send_switch_command(self, turn_on: bool) -> None:
        """Helper to send the turn on/off command."""
        target_value_str = "1" if turn_on else "0" # "1" for On, "0" for Off

        # We need the API value (often the same as what we deduced)
        # Assuming ONOFF_OPTION_TO_API_VALUE maps "On" -> "1", "Off" -> "0"
        # Let's verify mapping:
        # API_VALUE_TO_ONOFF_OPTION: {"0": "Off", "1": "On"}
        # ONOFF_OPTION_TO_API_VALUE: {"Off": "0", "On": "1"} (roughly)

        # If we use ONOFF_OPTION_TO_API_VALUE:
        target_option = "On" if turn_on else "Off"
        # Need to find matching key in ONOFF_OPTION_TO_API_VALUE
        # Since ONOFF_OPTION_TO_API_VALUE is constructed by reversing API_VALUE_TO_ONOFF_OPTION
        # which has multiple keys for same value (0, 0.0), it might pick one.
        # But for sending, "0" and "1" are safest for Innotemp usually.

        # Let's use strict "0" and "1" as they are standard Innotemp boolean values.
        val_new = target_value_str

        # Previous value handling
        previous_api_value: Any | None = None
        state_var = self.coordinator.control_to_state_map.get(self._param_id)
        if state_var and self.coordinator.data:
            previous_api_value = self.coordinator.data.get(state_var)

        val_prev_options = []
        if previous_api_value is not None:
            val_prev_options.append(previous_api_value)

        # Add the opposite value as a fallback option for prev value
        # If we are turning ON, current state might be OFF ("0").
        # If we are turning OFF, current state might be ON ("1").
        # But we should also include both just in case.
        possible_values = ["0", "1"]
        for val in possible_values:
            if val not in val_prev_options:
                val_prev_options.append(val)

        if None not in val_prev_options:
            val_prev_options.append(None)

        _LOGGER.debug(
            f"Sending command for {self.entity_id}: room_id {self._numeric_api_room_id}, param {self._param_id}, "
            f"new_val {val_new}, prev_val_options {val_prev_options}"
        )

        try:
            success = await self.coordinator.api_client.async_send_command(
                room_id=self._numeric_api_room_id,
                param=self._param_id,
                val_new=val_new,
                val_prev_options=val_prev_options,
            )
            if success:
                _LOGGER.info(
                    f"Successfully sent command for {self.entity_id} to turn {'ON' if turn_on else 'OFF'}."
                )
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(
                    f"Failed to send command for {self.entity_id} to turn {'ON' if turn_on else 'OFF'}."
                )
        except Exception as e:
            _LOGGER.error(
                f"Error sending command for {self.entity_id} to turn {'ON' if turn_on else 'OFF'}: {e}",
                exc_info=True,
            )
