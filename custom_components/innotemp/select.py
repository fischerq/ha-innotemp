"""Platform for input_select entities for Innotemp."""

from __future__ import annotations

import logging
import re  # For stripping HTML
import json  # For parsing string values in config_data if necessary (in async_setup_entry)

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity

_LOGGER = logging.getLogger(__name__)

# Mapping from API values to human-readable options and vice-versa
# Assuming: 0 = Off, 1 = On, 2 = Auto. This might need confirmation or to be made more flexible.
API_VALUE_TO_OPTION = {
    0: "Off",
    1: "On",
    2: "Auto",
}
OPTION_TO_API_VALUE = {v: k for k, v in API_VALUE_TO_OPTION.items()}
OPTIONS_LIST = list(API_VALUE_TO_OPTION.values())


def _strip_html(text: str | None) -> str:
    """Remove HTML tags from a string."""
    if text is None:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def _extract_input_selects_from_room_component(
    component_data,  # This is the data for a specific component like 'param', 'pump'
    coordinator,
    entry,
    room_attributes,  # Attributes of the parent room (contains 'var': string_room_id)
    numeric_room_id: int, # The actual numeric room ID to be used for API calls
    entities_list,
):
    """
    Extracts input_select entities (from ONOFFAUTO units) from a component of room data.
    Component_data can be a dict or a list of dicts.
    Each dict is expected to have an 'entry' key, which can also be a dict or a list of dicts.
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
            f"InputSelect: Unexpected component_data type: {type(component_data)} for room {room_attributes.get('var')}"
        )
        return

    for component_item_data in components_to_process:
        if not isinstance(component_item_data, dict):
            _LOGGER.debug(
                f"InputSelect: Skipping non-dict item in component_data list for room {room_attributes.get('var')}: {component_item_data}"
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
                    f"InputSelect: Skipping non-dict entry in entries list for room {room_attributes.get('var')}: {actual_entry}"
                )
                continue

            if actual_entry.get("unit") == "ONOFFAUTO":
                param_id = actual_entry.get("var")
                if not param_id:
                    _LOGGER.warning(
                        f"InputSelect: Found ONOFFAUTO entry without 'var' (param_id) in room {room_attributes.get('var')}, component {component_attributes}: {actual_entry}"
                    )
                    continue

                # entities_list.append(
                # InnotempInputSelect( # This class will be defined next
                # coordinator,
                # entry,
                # room_attributes,
                # component_attributes,
                # param_id,
                # actual_entry
                entities_list.append(
                    InnotempInputSelect(  # This class will be defined next
                        coordinator,
                        entry,
                        room_attributes,
                        numeric_room_id, # Pass the numeric room ID
                        component_attributes,
                        param_id,
                        actual_entry,
                    )
                )
                _LOGGER.debug(
                    f"InputSelect: Found ONOFFAUTO (potential input_select): room_var {room_attributes.get('var')} (numeric: {numeric_room_id}), component_var {component_attributes.get('var')}, item_var {param_id}, data {actual_entry}"
                )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up input_select entities based on config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning(
            "Innotemp input_select setup: config_data is None, skipping entity creation."
        )
        async_add_entities([])
        return

    entities = []
    _LOGGER.debug(
        "Innotemp input_select setup: Received config_data (first 500 chars): %s",
        str(config_data)[:500],
    )

    if not isinstance(config_data, dict):
        _LOGGER.error(
            f"InputSelect: Config_data is not a dictionary as expected. Type: {type(config_data)}. Data: {config_data}"
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
                    f"InputSelect: Could not parse string value for key {top_level_key} as JSON list."
                )

        if not actual_room_list:
            continue

        for room_data_dict in actual_room_list:
            if not isinstance(room_data_dict, dict):
                _LOGGER.warning(
                    f"InputSelect: Item in room list for key '{top_level_key}' is not a dictionary: {room_data_dict}"
                )
                continue

            room_attributes = room_data_dict.get("@attributes", {})
            if not room_attributes.get("var"):
                _LOGGER.warning(
                    f"InputSelect: Room missing '@attributes.var': {room_attributes}. Skipping."
                )
                continue

            possible_containers_keys = [
                "param",
                "pump",
                "piseq",
                "mixer",
                "drink",
                "radiator",
                "main",
            ]

            numeric_room_id_for_api = room_data_dict.get("room_id")
            if numeric_room_id_for_api is None:
                _LOGGER.warning(
                    f"InputSelect: Room '{room_attributes.get('var')}' does not have a numeric 'room_id' field in its main data object: {room_data_dict}. Skipping select entities for this room."
                )
                continue

            for container_key in possible_containers_keys:
                component_data = room_data_dict.get(container_key)
                if component_data:
                    _extract_input_selects_from_room_component(
                        component_data, coordinator, entry, room_attributes, numeric_room_id_for_api, entities
                    )

    if not entities:
        _LOGGER.info(
            "No ONOFFAUTO (input_select) entities found in Innotemp configuration."
        )
    else:
        _LOGGER.info(
            f"Found {len(entities)} Innotemp input_select entities to be added."
        )

    async_add_entities(entities)


class InnotempInputSelect(InnotempCoordinatorEntity, SelectEntity):
    """Representation of an Innotemp InputSelect entity for ONOFFAUTO controls."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_attributes: dict, # Contains string 'var' for room identification
        numeric_api_room_id: int, # Actual numeric ID for API calls
        component_attributes: dict,
        param_id: str,  # 'var' of the ONOFFAUTO entry
        param_data: dict,  # The ONOFFAUTO entry's own data dict
    ):
        """Initialize the Innotemp InputSelect entity."""
        self._room_attributes = room_attributes # Keep for context if needed elsewhere
        self._component_attributes = component_attributes
        self._param_id = param_id
        self._param_data = param_data

        # Store the correct numeric room ID for API calls
        self._numeric_api_room_id = numeric_api_room_id

        original_label = self._param_data.get("label", f"Control {self._param_id}")
        cleaned_label = _strip_html(original_label)

        entity_config = {
            "param": self._param_id, # Used for unique_id part by parent
            "label": cleaned_label if cleaned_label else f"Control {self._param_id}",
        }
        super().__init__(coordinator, config_entry, entity_config)

        self._attr_options = OPTIONS_LIST  # ["Off", "On", "Auto"]

        _LOGGER.debug(
            f"InnotempInputSelect initialized: name='{self.name}', unique_id='{self.unique_id}', "
            f"param_id='{self._param_id}', numeric_api_room_id='{self._numeric_api_room_id}' (was string: {room_attributes.get('var')})"
        )

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        if self.coordinator.data is None:
            _LOGGER.debug(
                f"InnotempInputSelect.current_option: Coordinator data is None for entity {self.entity_id} (param_id: {self._param_id})."
            )
            return (
                None  # Or a default option if that makes sense, e.g., OPTIONS_LIST[0]
            )

        api_value = self.coordinator.data.get(self._param_id)
        if api_value is None:
            _LOGGER.debug(
                f"InnotempInputSelect.current_option: Param_id {self._param_id} not found in coordinator data for entity {self.entity_id}."
            )
            return None

        # Convert numeric API value to string option
        try:
            # Ensure api_value is treated as an integer for dictionary lookup
            selected_option = API_VALUE_TO_OPTION.get(int(api_value))
            if selected_option is None:
                _LOGGER.warning(
                    f"InnotempInputSelect.current_option: Unknown API value '{api_value}' for param_id {self._param_id} on entity {self.entity_id}. Not in {API_VALUE_TO_OPTION}"
                )
            return selected_option
        except (ValueError, TypeError):
            _LOGGER.warning(
                f"InnotempInputSelect.current_option: Could not convert API value '{api_value}' to int for param_id {self._param_id} on entity {self.entity_id}."
            )
            return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option not in OPTION_TO_API_VALUE:
            _LOGGER.error(
                f"Invalid option '{option}' selected for {self.entity_id}. Valid options: {OPTIONS_LIST}"
            )
            return

        new_api_value = OPTION_TO_API_VALUE[option]

        # Get previous value for the API call
        # It's crucial that self.coordinator.data is available here.
        # If not, the command might fail or use an incorrect previous value.
        previous_api_value = None
        if self.coordinator.data is not None:
            previous_api_value = self.coordinator.data.get(self._param_id)

        if previous_api_value is None:
            _LOGGER.warning(
                f"Cannot determine previous value for {self.entity_id} (param {self._param_id}) when setting to '{option}'. "
                "API command might be incomplete or use a default previous value if supported by API."
                # Consider if we should prevent the command or send a conventional 'unknown' previous value
            )
            # For safety, one might choose to not send the command if prev value is critical and unknown.
            # However, many APIs might accept a command without a perfect previous value.
            # For now, we'll proceed, but this is a point of potential improvement/config.
            # Setting previous_api_value to the new_api_value if unknown might be a safe bet for some APIs
            # to indicate no change if it was already in that state, or just send what we have.
            # Let's assume the API can handle it or that data will refresh shortly.
            # A common pattern is to use the current state if known, or a sentinel if not.
            # For now, we'll pass what we have (which might be None).

        _LOGGER.debug(
            f"Sending command for {self.entity_id}: room_id (numeric) {self._numeric_api_room_id}, param {self._param_id}, "
            f"new_val {new_api_value} (from option '{option}'), prev_val {previous_api_value}"
        )

        try:
            success = await self.coordinator.api_client.async_send_command(
                room_id=self._numeric_api_room_id, # Use the stored numeric room ID
                param=self._param_id,
                val_new=new_api_value,
                val_prev=previous_api_value,
            )
            if success:
                _LOGGER.info(
                    f"Successfully sent command for {self.entity_id} to set option to '{option}'."
                )
                # Optionally, immediately update coordinator data if API confirms change,
                # or rely on SSE/next poll. For now, rely on external update.
                # Example of immediate update (if your API confirms state synchronously):
                # current_data = self.coordinator.data.copy() if self.coordinator.data else {}
                # current_data[self._param_id] = new_api_value
                # self.coordinator.async_set_updated_data(current_data)
                await self.coordinator.async_request_refresh()  # Request a refresh
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
