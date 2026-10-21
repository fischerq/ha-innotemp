"""Platform for input_select entities for Innotemp."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional


from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity
from .api_parser import (
    coerce_api_int,
    strip_html,
    process_room_config_data,
    API_VALUE_TO_ONOFFAUTO_OPTION,
    ONOFFAUTO_OPTION_TO_API_VALUE,
    ONOFFAUTO_OPTIONS_LIST,
)

_LOGGER = logging.getLogger(__name__)

# Keys of a room's components that may hold this platform's items.
CONTAINER_KEYS: list[str] = [
    "param",
    "pump",
    "piseq",
    "mixer",
    "drink",
    "radiator",
    "main",
]

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

    select_entities_data = process_room_config_data(
        config_data=config_data,
        possible_container_keys=CONTAINER_KEYS,
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
        room_id_var = room_attributes.get("var", "NO_ROOM_ID")
        component_id = component_attributes.get("var") or component_attributes.get(
            "type", "NO_COMP_ID"
        )

        new_label = (
            f"{room_id_var} - {component_id} - {cleaned_label}"
            if cleaned_label
            else f"{room_id_var} - {component_id} - Control {self._param_id}"
        )

        entity_config = {
            "param": self._param_id,  # Used for unique_id part by parent
            "label": new_label,
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

        # Convert the API value to an int for the lookup. Values may arrive as
        # "1", 1, or "1.0" (int("1.0") raises, which made the option unknown).
        int_value = coerce_api_int(api_value)
        if int_value is None:
            _LOGGER.warning(
                f"InnotempInputSelect.current_option: Could not convert API value '{api_value}' to int for param_id {self._param_id} on entity {self.entity_id}."
            )
            return None

        selected_option = API_VALUE_TO_ONOFFAUTO_OPTION.get(int_value)
        if selected_option is None:
            _LOGGER.warning(
                f"InnotempInputSelect.current_option: Unknown API value '{api_value}' for param_id {self._param_id} on entity {self.entity_id}. Not in {API_VALUE_TO_ONOFFAUTO_OPTION}"
            )
        return selected_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option not in ONOFFAUTO_OPTION_TO_API_VALUE:
            raise HomeAssistantError(
                f"Invalid option '{option}' for {self.entity_id}; valid options: "
                f"{ONOFFAUTO_OPTIONS_LIST}"
            )

        await self._async_write_value(
            self._numeric_api_room_id,
            ONOFFAUTO_OPTION_TO_API_VALUE[option],
            use_state_fallback=True,
        )
