"""Platform for switch entities."""

from __future__ import annotations

import logging
import re # For stripping HTML

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity

_LOGGER = logging.getLogger(__name__)

def _strip_html(text: str | None) -> str:
    """Remove HTML tags from a string."""
    if text is None:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()


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
    component_data,  # This is the data for a specific component like 'param', 'pump'
    coordinator,
    entry,
    room_attributes, # Attributes of the parent room
    entities_list
):
    """
    Extracts switch entities from a component of room data (e.g., 'param', 'pump').
    Component_data can be a dict or a list of dicts.
    Each dict is expected to have an 'entry' key, which can also be a dict or a list of dicts.
    """
    if not component_data:
        return

    # component_data is the block for 'param', 'pump', etc.
    # It can be a single dict or a list of such blocks (e.g. multiple 'pump' blocks)
    components_to_process = []
    if isinstance(component_data, dict):
        components_to_process.append(component_data)
    elif isinstance(component_data, list):
        components_to_process.extend(component_data)
    else:
        _LOGGER.debug(f"Switch: Unexpected component_data type: {type(component_data)} for room {room_attributes.get('var')}")
        return

    for component_item_data in components_to_process: # e.g., one 'param' block or one 'pump' block
        if not isinstance(component_item_data, dict):
            _LOGGER.debug(f"Switch: Skipping non-dict item in component_data list for room {room_attributes.get('var')}: {component_item_data}")
            continue

        component_attributes = component_item_data.get("@attributes", {})
        # component_context_id = component_attributes.get("var") or component_attributes.get("type") or component_attributes.get("label","unknown_component")


        entry_data = component_item_data.get("entry")
        if not entry_data:
            continue # This component_item_data (e.g. a pump) might not have a direct 'entry' for switches

        entries = []
        if isinstance(entry_data, dict):
            entries.append(entry_data)
        elif isinstance(entry_data, list):
            entries.extend(entry_data)

        for actual_entry in entries: # actual_entry is the switch definition
            if not isinstance(actual_entry, dict):
                _LOGGER.debug(f"Switch: Skipping non-dict entry in entries list for room {room_attributes.get('var')}: {actual_entry}")
                continue

            if actual_entry.get("unit") == "ONOFFAUTO":
                param_id = actual_entry.get("var")
                if not param_id:
                    _LOGGER.warning(f"Switch: Found ONOFFAUTO entry without 'var' (param_id) in room {room_attributes.get('var')}, component {component_attributes}: {actual_entry}")
                    continue

                entities_list.append(
                    InnotempSwitch(
                        coordinator,
                        entry,
                        room_attributes, # Pass full room attributes
                        component_attributes, # Pass full component attributes for device grouping
                        param_id, # This is the switch's own 'var'
                        actual_entry # This is the switch's own data dict
                    )
                )
                _LOGGER.debug(f"Switch: Found ONOFFAUTO switch: room_var {room_attributes.get('var')}, component_var {component_attributes.get('var')}, switch_var {param_id}, data {actual_entry}")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up switch entities based on config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning("Innotemp switch setup: config_data is None, skipping switch entity creation.")
        async_add_entities([])
        return

    entities = []

    if not isinstance(config_data, dict):
        _LOGGER.error(f"Switch: Config_data is not a dictionary as expected. Type: {type(config_data)}. Data: {config_data}")
        async_add_entities([])
        return

    for top_level_key, top_level_value in config_data.items():
        #_LOGGER.debug(f"Switch: Processing top_level_key: {top_level_key}, value type: {type(top_level_value)}")

        actual_room_list = []
        if isinstance(top_level_value, list):
            actual_room_list = top_level_value
        elif isinstance(top_level_value, dict) and top_level_value.get("@attributes",{}).get("type","").startswith("room"):
            actual_room_list.append(top_level_value)

        if not actual_room_list and isinstance(top_level_value, str):
            try:
                import json
                parsed_value = json.loads(top_level_value)
                if isinstance(parsed_value, list):
                    #_LOGGER.debug(f"Switch: Successfully parsed string value for key {top_level_key} into a list.")
                    actual_room_list = parsed_value
            except json.JSONDecodeError:
                _LOGGER.debug(f"Switch: Could not parse string value for key {top_level_key} as JSON list.")

        if not actual_room_list:
            #_LOGGER.debug(f"Switch: No list of rooms found under key '{top_level_key}' or key itself is not a room list.")
            continue

        for room_data_dict in actual_room_list: # room_data_dict is one whole room
            if not isinstance(room_data_dict, dict):
                _LOGGER.warning(f"Switch: Item in room list for key '{top_level_key}' is not a dictionary: {room_data_dict}")
                continue

            room_attributes = room_data_dict.get("@attributes", {})
            if not room_attributes.get("var"): # Ensure room has a usable ID
                _LOGGER.warning(f"Switch: Room missing '@attributes.var': {room_attributes}. Skipping.")
                continue

            #_LOGGER.debug(f"Switch: Processing room: {room_attributes.get('var')}, Label: {room_attributes.get('label')}")

            possible_switch_containers_keys = [
                "param", "pump", "piseq", "mixer", "drink", "radiator", "main"
            ]

            for container_key in possible_switch_containers_keys:
                # component_data is the content of 'param', or 'pump' etc.
                # This can be a dictionary OR a list of dictionaries (e.g. multiple 'pump' items)
                component_data = room_data_dict.get(container_key)
                if component_data:
                    _extract_switches_from_room_component(
                        component_data, # e.g., the dict/list for 'param' or 'pump'
                        coordinator,
                        entry,
                        room_attributes, # Attributes of the parent room
                        entities
                    )

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
        room_attributes: dict,       # Attributes of the parent room
        component_attributes: dict,  # Attributes of the component block (e.g. 'param', 'pump' item)
        param_id: str,               # 'var' of the switch entry itself
        param_data: dict,            # The switch entry's own data dict
    ) -> None:
        """Initialize the switch."""
        self._room_attributes = room_attributes
        self._component_attributes = component_attributes
        self._param_id = param_id # This is the 'var' of the switch itself, used for coordinator.data.get()
        self._param_data = param_data # Contains label, unit for the switch

        # For InnotempCoordinatorEntity, entity_config needs 'param' for unique_id and 'label' for name
        # The 'param' for unique_id should be the switch's own param_id (its 'var')
        # The 'label' should be the switch's own label, stripped of HTML.
        original_label = self._param_data.get("label", f"Switch {self._param_id}")
        cleaned_label = _strip_html(original_label)
        entity_config = {
            "param": self._param_id,
            "label": cleaned_label if cleaned_label else f"Switch {self._param_id}" # Fallback if stripping results in empty
        }
        # The room_id for API calls might be from room_attributes['var'] or a more specific ID
        # For now, let's assume the main room_id from room_attributes['var'] is used for commands.
        # This might need adjustment if commands are more granular.
        self._api_room_id = room_attributes.get("var")


        super().__init__(coordinator, config_entry, entity_config)
        # _attr_name and _attr_unique_id are set by super().__init__ using entity_config

        # Store for API calls if needed, ensure this is the correct room identifier for commands.
        # self._room_id was previously room_context_id, now derived from room_attributes.get("var")
        # It's used by async_turn_on/off
        # We need to ensure this is the correct ID for the API call.
        # The `param_id` used in `super().__init__` is for the entity's unique HA ID.
        # The `param_id` for API calls is `self._param_id`.
        # The `room_id` for API calls is `self._api_room_id`.

        # Old unique_id: f"{config_entry.unique_id}_{self._room_id}_{self._param_id}"
        # The self._room_id in InnotempCoordinatorEntity is set from entity_config["room_id"] if present.
        # Let's ensure our unique ID is truly unique.
        # The base InnotempCoordinatorEntity uses entity_config["param"] for its part of unique_id.
        # So, self.unique_id will be f"{config_entry.unique_id}_{self._param_id}"
        # This should be fine as self._param_id (switch's 'var') is expected to be unique across the controller.

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
