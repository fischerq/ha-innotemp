"""Platform for input_select entities for Innotemp."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional


from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import async_get, EntityRegistry
from homeassistant.helpers.entity_registry import (
    EntityRegistry,
    async_get,
    RegistryEntry,
)

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity
from .api_parser import (
    strip_html,
    process_room_config_data,
    API_VALUE_TO_ONOFFAUTO_OPTION,
    ONOFFAUTO_OPTION_TO_API_VALUE,
    ONOFFAUTO_OPTIONS_LIST,
)

_LOGGER = logging.getLogger(__name__)

# Constants for ONOFFAUTO mapping are now imported from api_parser


def _create_select_entity_data(
    item_data: Dict[str, Any],
    room_attributes: Dict[str, Any],
    numeric_room_id: Optional[int],
    component_attributes: Dict[str, Any],
    component_key_hint: str,
) -> Optional[Dict[str, Any]]:
    """
    Processes an item from config data to determine if it's a select entity.
    Returns a dictionary with necessary data for entity creation if valid, else None.
    """
    if item_data.get("unit") == "ONOFFAUTO":
        param_id = item_data.get("var")
        # Select entities require a numeric_room_id for API calls
        if param_id and numeric_room_id is not None:
            _LOGGER.debug(
                f"Select: Found ONOFFAUTO: room_var {room_attributes.get('var')} (numeric: {numeric_room_id}), "
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
                f"Select: Skipping ONOFFAUTO item {param_id} for room {room_attributes.get('var')} due to missing numeric_room_id."
            )
        else:  # not param_id
            _LOGGER.warning(
                f"Select: Found ONOFFAUTO entry without 'var' (param_id) in room {room_attributes.get('var')}, "
                f"component {component_attributes.get('var', component_attributes.get('type'))} from {component_key_hint}: {item_data}"
            )
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up input_select entities based on config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning(
            "Innotemp select setup: config_data is None, skipping entity creation."
        )
        async_add_entities([])
        return

    _LOGGER.debug(
        "Innotemp select setup: Processing config_data (first 500 chars): %s",
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

    select_entities_data = process_room_config_data(
        config_data=config_data,
        possible_container_keys=possible_containers_keys,
        item_processor=_create_select_entity_data,
    )

    entities = []
    for entity_data in select_entities_data:
        entities.append(
            InnotempInputSelect(
                hass=hass,
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
            "No ONOFFAUTO (select) entities found in Innotemp configuration using new parser."
        )
    else:
        _LOGGER.info(
            f"Found {len(entities)} Innotemp select entities to be added using new parser."
        )

    async_add_entities(entities)


class InnotempInputSelect(InnotempCoordinatorEntity, SelectEntity):
    """Representation of an Innotemp InputSelect entity for ONOFFAUTO controls."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict,  # Contains string 'var' for room identification
        numeric_api_room_id: int,  # Actual numeric ID for API calls
        component_attributes: dict,
        param_id: str,  # 'var' of the ONOFFAUTO entry
        param_data: dict,  # The ONOFFAUTO entry's own data dict
    ):
        """Initialize the Innotemp InputSelect entity."""
        self.hass = hass
        self._room_attributes = room_attributes  # Keep for context if needed elsewhere
        self._component_attributes = component_attributes
        self._param_id = param_id
        self._param_data = param_data

        # Store the correct numeric room ID for API calls
        self._numeric_api_room_id = numeric_api_room_id

        original_label = self._param_data.get("label", f"Control {self._param_id}")
        cleaned_label = strip_html(original_label)

        entity_config = {
            "param": self._param_id,  # Used for unique_id part by parent
            "label": cleaned_label if cleaned_label else f"Control {self._param_id}",
        }
        super().__init__(coordinator, config_entry, entity_config)

        self._attr_options = ONOFFAUTO_OPTIONS_LIST

        _LOGGER.debug(
            f"InnotempInputSelect initialized: name='{self.name}', unique_id='{self.unique_id}', "
            f"param_id='{self._param_id}', numeric_api_room_id='{self._numeric_api_room_id}' (was string: {room_attributes.get('var')})"
        )

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        api_value = self._get_api_value()
        if api_value is None:
            return None  # Or a default option

        # Convert numeric API value to string option
        try:
            # Ensure api_value is treated as an integer for dictionary lookup
            selected_option = API_VALUE_TO_ONOFFAUTO_OPTION.get(int(api_value))
            if selected_option is None:
                _LOGGER.warning(
                    f"InnotempInputSelect.current_option: Unknown API value '{api_value}' for param_id {self._param_id} on entity {self.entity_id}. Not in {API_VALUE_TO_ONOFFAUTO_OPTION}"
                )
            return selected_option
        except (ValueError, TypeError):
            _LOGGER.warning(
                f"InnotempInputSelect.current_option: Could not convert API value '{api_value}' to int for param_id {self._param_id} on entity {self.entity_id}."
            )
            return None

    def _find_corresponding_sensor_entity_id(self) -> str | None:
        """Find the entity_id of the corresponding ONOFFAUTO enum sensor."""
        ent_reg: EntityRegistry = async_get(self.hass)

        # The enum sensor has '_status' appended to its param_id to form a unique ID
        expected_unique_id_suffix = f"{self._param_id}_status"

        for entity_id, registry_entry in ent_reg.entities.items():
            if (
                registry_entry.platform == DOMAIN
                and registry_entry.unique_id.endswith(expected_unique_id_suffix)
            ):
                _LOGGER.debug(
                    f"Found corresponding sensor '{entity_id}' for select entity '{self.entity_id}'."
                )
                return entity_id
        _LOGGER.warning(
            f"Could not find a corresponding sensor for select entity '{self.entity_id}' with unique_id_suffix '{expected_unique_id_suffix}'."
        )
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option not in ONOFFAUTO_OPTION_TO_API_VALUE:
            _LOGGER.error(
                f"Invalid option '{option}' selected for {self.entity_id}. Valid options: {ONOFFAUTO_OPTIONS_LIST}"
            )
            return

        new_api_value = ONOFFAUTO_OPTION_TO_API_VALUE[option]
        previous_api_value: int | None = None
        prev_val_source_info = "no source"

        # New method: Find corresponding sensor and get its state
        sensor_entity_id = self._find_corresponding_sensor_entity_id()
        if sensor_entity_id:
            sensor_state: State | None = self.hass.states.get(sensor_entity_id)
            if sensor_state and sensor_state.state is not None:
                sensor_option = sensor_state.state
                previous_api_value = ONOFFAUTO_OPTION_TO_API_VALUE.get(sensor_option)
                prev_val_source_info = (
                    f"corresponding sensor '{sensor_entity_id}' state '{sensor_option}'"
                )
                if previous_api_value is None:
                    _LOGGER.warning(
                        f"The state '{sensor_option}' of sensor '{sensor_entity_id}' is not in the ONOFFAUTO_OPTION_TO_API_VALUE map."
                    )
            else:
                _LOGGER.warning(
                    f"Could not get state for corresponding sensor '{sensor_entity_id}'. It might be unavailable or has no state."
                )
        else:
            _LOGGER.warning(
                f"No corresponding sensor found for {self.entity_id}. Falling back to using select's own state."
            )

        # Fallback to original method if sensor method fails
        if previous_api_value is None:
            current_displayed_option_str = self.current_option
            prev_val_source_info = f"select entity's own state '{current_displayed_option_str}'"
            if current_displayed_option_str is not None:
                previous_api_value = ONOFFAUTO_OPTION_TO_API_VALUE.get(
                    current_displayed_option_str
                )
            else:
                _LOGGER.warning(
                    f"Cannot determine previous value for {self.entity_id} (param {self._param_id}) from its own state "
                    f"because its current displayed option is None. This suggests coordinator data is missing or invalid. "
                    f"Proceeding with prev_val as None (will be sent as empty string to API)."
                )

        _LOGGER.debug(
            f"Sending command for {self.entity_id}: room_id (numeric) {self._numeric_api_room_id}, param {self._param_id}, "
            f"new_val {new_api_value} (from option '{option}'), prev_val {previous_api_value} (derived from {prev_val_source_info})"
        )

        try:
            success = await self.coordinator.api_client.async_send_command(
                room_id=self._numeric_api_room_id,
                param=self._param_id,
                val_new=new_api_value,
                val_prev=previous_api_value,
            )
            if success:
                _LOGGER.info(
                    f"Successfully sent command for {self.entity_id} to set option to '{option}'."
                )
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(
                    f"Failed to send command for {self.entity_id} to set option to '{option}'."
                )
        except Exception as e:
            _LOGGER.error(
                f"Error sending command for {self.entity_id} to set option to '{option}': {e}",
                exc_info=True,
            )

        # self.async_write_ha_state() # State will be updated by coordinator
