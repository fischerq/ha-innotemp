"""Platform for switch entities."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up switch entities based on config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning(
            "Innotemp switch setup: config_data is None, skipping switch entity creation."
        )
        async_add_entities([])  # Add no entities
        return

    entities = []

# Helper function to extract switches from various parts of room data
def _extract_switches_from_room_component(
    component_data, coordinator, entry, room_context_id, entities_list
):
    """
    Extracts switch entities from a component of room data (e.g., 'param', 'pump').
    Component_data can be a dict or a list of dicts.
    Each dict is expected to have an 'entry' key, which can also be a dict or a list of dicts.
    """
    if not component_data:
        return

    components_to_check = []
    if isinstance(component_data, dict):
        components_to_check.append(component_data)
    elif isinstance(component_data, list):
        components_to_check.extend(component_data)
    else:
        _LOGGER.debug(f"Unexpected component_data type: {type(component_data)} for room {room_context_id}")
        return

    for item in components_to_check:
        if not isinstance(item, dict):
            _LOGGER.debug(f"Skipping non-dict item in component_data list for room {room_context_id}: {item}")
            continue

        entry_data = item.get("entry")
        if not entry_data:
            continue

        entries = []
        if isinstance(entry_data, dict):
            entries.append(entry_data)
        elif isinstance(entry_data, list):
            entries.extend(entry_data)

        for actual_entry in entries:
            if not isinstance(actual_entry, dict):
                _LOGGER.debug(f"Skipping non-dict entry in entries list for room {room_context_id}: {actual_entry}")
                continue

            if actual_entry.get("unit") == "ONOFFAUTO":
                param_id = actual_entry.get("var")
                if not param_id:
                    _LOGGER.warning(f"Found ONOFFAUTO entry without 'var' (param_id) in room {room_context_id}: {actual_entry}")
                    continue

                # param_data for the InnotempSwitch is the entry itself
                entities_list.append(
                    InnotempSwitch(coordinator, entry, room_context_id, param_id, actual_entry)
                )
                _LOGGER.debug(f"Found ONOFFAUTO switch: room {room_context_id}, param_id {param_id}, data {actual_entry}")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up switch entities based on config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning(
            "Innotemp switch setup: config_data is None, skipping switch entity creation."
        )
        async_add_entities([])
        return

    entities = []

    # The top-level config_data is expected to be a dictionary.
    # One of its keys (e.g., "room") might contain a list of actual room configurations.
    if not isinstance(config_data, dict):
        _LOGGER.error(f"Config_data is not a dictionary as expected. Type: {type(config_data)}. Data: {config_data}")
        async_add_entities([])
        return

    for top_level_key, top_level_value in config_data.items():
        _LOGGER.debug(f"Processing top_level_key: {top_level_key}, value type: {type(top_level_value)}")

        actual_room_list = []
        if isinstance(top_level_value, list):
            actual_room_list = top_level_value
        elif isinstance(top_level_value, dict) and top_level_key == "room" and isinstance(top_level_value.get("item"), list):
            # Handling cases where rooms might be nested further, e.g. config_data['room']['item'] = [...]
            # This is speculative based on common XML/JSON patterns, adjust if direct list is always config_data['room']
            actual_room_list = top_level_value.get("item")
        elif isinstance(top_level_value, dict) and top_level_value.get("@attributes",{}).get("type","").startswith("room"):
             # If the top_level_value itself is a single room dictionary (e.g. if config_data was just one room)
            actual_room_list.append(top_level_value)


        if not actual_room_list:
            _LOGGER.debug(f"No list of rooms found under key '{top_level_key}' or key itself is not a room list.")
            # If top_level_value was a string and parsed into a list, handle that too.
            if isinstance(top_level_value, str):
                try:
                    import json
                    parsed_value = json.loads(top_level_value)
                    if isinstance(parsed_value, list):
                        _LOGGER.debug(f"Successfully parsed string value for key {top_level_key} into a list.")
                        actual_room_list = parsed_value
                    else:
                        _LOGGER.debug(f"Parsed string for key {top_level_key} is not a list: {type(parsed_value)}")
                except json.JSONDecodeError:
                    _LOGGER.debug(f"Could not parse string value for key {top_level_key} as JSON list.")

            if not actual_room_list: # Check again if parsing populated it
                 # Before skipping, check if this top_level_value might be a single room dict directly
                if isinstance(top_level_value, dict) and top_level_value.get("@attributes",{}).get("type","").startswith("room"):
                    _LOGGER.debug(f"Treating top_level_key '{top_level_key}' value as a single room dictionary.")
                    actual_room_list = [top_level_value] # Treat as a list with one room
                else:
                    continue # Not a list of rooms, and not a single room dict, skip this top-level item

        for room_data_dict in actual_room_list:
            if not isinstance(room_data_dict, dict):
                _LOGGER.warning(f"Item in room list for key '{top_level_key}' is not a dictionary: {room_data_dict}")
                continue

            room_attributes = room_data_dict.get("@attributes", {})
            room_context_id = room_attributes.get("var", room_attributes.get("label", f"unknown_room_{top_level_key}"))
            _LOGGER.debug(f"Processing room: {room_context_id}, Data: {room_data_dict}")

            # Look for switches in various known sections within a room
            # Common sections that might contain 'entry' with 'unit': 'ONOFFAUTO'
            # are 'param', 'pump', 'piseq', 'mixer', 'drink', 'radiator', 'main' (for room main controls)

            possible_switch_containers = [
                "param", "pump", "piseq", "mixer", "drink", "radiator", "main"
                # Add other potential container keys if discovered
            ]

            for container_key in possible_switch_containers:
                component_data = room_data_dict.get(container_key)
                if component_data:
                    _extract_switches_from_room_component(
                        component_data, coordinator, entry, room_context_id, entities
                    )

            # Special handling for 'display' as it can also have 'entry' like structures,
            # though less common for direct control switches.
            # Example from user data does not show switches in 'display', but being thorough.
            # display_data = room_data_dict.get("display")
            # if display_data:
            # _extract_switches_from_room_component(
            # display_data, coordinator, entry, room_context_id, entities
            # )


    if not entities:
        _LOGGER.info("No ONOFFAUTO switch entities found in Innotemp configuration.")
    else:
        _LOGGER.info(f"Found {len(entities)} Innotemp switch entities.")
    async_add_entities(entities)


class InnotempSwitch(InnotempCoordinatorEntity, SwitchEntity):
    """Representation of an Innotemp Switch."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_id: str,
        param_id: str,
        param_data: dict,
    ) -> None:
        """Initialize the switch."""
        entity_config = {
            "param": param_id,
            "label": param_data.get("label", f"Innotemp Switch {param_id}"),
            "room_id": room_id,
            # Add any other relevant parts of param_data if needed by base class
        }
        super().__init__(coordinator, config_entry, entity_config)
        self._room_id = room_id
        self._param_id = param_id
        self._attr_unique_id = (
            f"{config_entry.unique_id}_{self._room_id}_{self._param_id}"
        )
        self._attr_name = entity_config["label"]

        # Get initial state from the coordinator's data
        self._update_state_from_coordinator()

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        # Ensure coordinator data is available before proceeding
        if self.coordinator.data is None:
            _LOGGER.debug(
                "InnotempSwitch.is_on: Coordinator data is None for entity %s (param_id: %s). Returning None for state.",
                self.entity_id,
                self._param_id
            )
            return None

        current_value = self.coordinator.data.get(self._param_id)
        if current_value is None:
            _LOGGER.debug(
                "InnotempSwitch.is_on: Param_id %s not found in coordinator data for entity %s. Data: %s. Returning None for state.",
                self._param_id,
                self.entity_id,
                self.coordinator.data
            )
            return None

        # Assuming 1 is ON, 0 is OFF. Adapt if API uses different values.
        return current_value == 1

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        if self.coordinator.data is None:
            _LOGGER.warning(f"Cannot turn on {self.entity_id}, coordinator data is None.")
            return
        current_value = self.coordinator.data.get(self._param_id)
        if current_value is None:
            _LOGGER.warning(f"Cannot turn on {self.entity_id}, current state for param {self._param_id} unknown in coordinator data.")
            return

        # Assuming 1 is ON, 0 is OFF, 2 is AUTO
        if current_value != 1:
            await self.coordinator.api_client.async_send_command(
                room_id=self._room_id,
                param=self._param_id,
                val_new=1,
                val_prev=current_value,
            )
            # The state will be updated by the SSE listener
            # self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        if self.coordinator.data is None:
            _LOGGER.warning(f"Cannot turn off {self.entity_id}, coordinator data is None.")
            return
        current_value = self.coordinator.data.get(self._param_id)
        if current_value is None:
            _LOGGER.warning(f"Cannot turn off {self.entity_id}, current state for param {self._param_id} unknown in coordinator data.")
            return

        # Assuming 1 is ON, 0 is OFF, 2 is AUTO
        if current_value != 0:
            await self.coordinator.api_client.async_send_command(
                room_id=self._room_id,
                param=self._param_id,
                val_new=0,
                val_prev=current_value,
            )
            # The state will be updated by the SSE listener
            # self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state_from_coordinator()
        self.async_write_ha_state()
        super()._handle_coordinator_update()

    def _update_state_from_coordinator(self) -> None:
        """Update the entity's state from coordinator data."""
        # The base InnotempEntity might handle the core data lookup
        # Here, we specifically handle the switch state mapping
        pass  # State is handled by the is_on property
