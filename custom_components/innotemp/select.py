"""Platform for select entities for Innotemp."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

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


def _create_select_entity_data(
    item_data: Dict[str, Any],
    room_attributes: Dict[str, Any],
    numeric_room_id: Optional[int],
    component_attributes: Dict[str, Any],
    component_key_hint: str,
) -> Optional[Dict[str, Any]]:
    """Processes an item from config data to determine if it's a select entity."""
    param_id = item_data.get("var")
    # Select entities require a numeric_room_id for API calls.
    if item_data.get("unit") == "ONOFFAUTO" and param_id and numeric_room_id is not None:
        return {
            "room_attributes": room_attributes,
            "numeric_room_id": numeric_room_id,
            "component_attributes": component_attributes,
            "param_id": param_id,
            "param_data": item_data,
        }
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up select entities based on a config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning("Innotemp select setup: config_data is None, skipping.")
        return

    _LOGGER.debug("Innotemp select setup: Processing config_data")

    possible_containers_keys = [
        "param", "pump", "piseq", "mixer", "drink", "radiator", "main"
    ]

    select_entities_data = process_room_config_data(
        config_data=config_data,
        possible_container_keys=possible_containers_keys,
        item_processor=_create_select_entity_data,
    )

    entities = [
        InnotempInputSelect(
            coordinator=coordinator,
            config_entry=entry,
            room_attributes=entity_data["room_attributes"],
            numeric_api_room_id=entity_data["numeric_room_id"],
            component_attributes=entity_data["component_attributes"],
            param_id=entity_data["param_id"],
            param_data=entity_data["param_data"],
        )
        for entity_data in select_entities_data
    ]

    if entities:
        _LOGGER.info(f"Found {len(entities)} Innotemp select entities.")
    else:
        _LOGGER.info("No ONOFFAUTO (select) entities found in Innotemp configuration.")

    async_add_entities(entities)


class InnotempInputSelect(InnotempCoordinatorEntity, SelectEntity):
    """Representation of an Innotemp Select entity for ONOFFAUTO controls."""

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
        """Initialize the Innotemp Select entity."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_id = param_id
        self._param_data = param_data
        self._numeric_api_room_id = numeric_api_room_id

        original_label = self._param_data.get("label", f"Control {self._param_id}")
        cleaned_label = strip_html(original_label)

        entity_config = {
            "param": self._param_id,
            "label": cleaned_label if cleaned_label else f"Control {self._param_id}",
        }
        super().__init__(coordinator, config_entry, entity_config)

        self._attr_options = ONOFFAUTO_OPTIONS_LIST
        _LOGGER.debug(f"InnotempInputSelect initialized: {self.name} ({self.unique_id})")

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        api_value = self._get_api_value()
        if api_value is None:
            return None

        try:
            selected_option = API_VALUE_TO_ONOFFAUTO_OPTION.get(int(api_value))
            if selected_option is None:
                _LOGGER.warning(f"Unknown API value '{api_value}' for select entity {self.entity_id}.")
            return selected_option
        except (ValueError, TypeError):
            _LOGGER.warning(f"Could not convert API value '{api_value}' to int for select entity {self.entity_id}.")
            return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option not in ONOFFAUTO_OPTION_TO_API_VALUE:
            _LOGGER.error(f"Invalid option '{option}' selected for {self.entity_id}.")
            return

        new_api_value = ONOFFAUTO_OPTION_TO_API_VALUE[option]
        previous_api_value = self._get_api_value()

        _LOGGER.debug(
            f"Sending command for {self.entity_id}: room_id {self._numeric_api_room_id}, param {self._param_id}, "
            f"new_val {new_api_value}, prev_val {previous_api_value}"
        )

        try:
            success = await self.coordinator.api_client.async_send_command(
                room_id=self._numeric_api_room_id,
                param=self._param_id,
                val_new=new_api_value,
                val_prev=previous_api_value,
            )
            if success:
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
