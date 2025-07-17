# custom_components/innotemp/api_parser.py
"""Utility functions for parsing Innotemp API configuration data."""

import logging
import re
import html
import json
from typing import Any, Callable, TypeVar, Tuple, List, Dict, Optional

_LOGGER = logging.getLogger(__name__)

# Shared ONOFFAUTO mapping
API_VALUE_TO_ONOFFAUTO_OPTION: Dict[int, str] = {
    0: "Off",
    1: "On",
    2: "Auto",
}
ONOFFAUTO_OPTION_TO_API_VALUE: Dict[str, int] = {
    v: k for k, v in API_VALUE_TO_ONOFFAUTO_OPTION.items()
}
ONOFFAUTO_OPTIONS_LIST: List[str] = list(API_VALUE_TO_ONOFFAUTO_OPTION.values())

# Shared ONOFF mapping (used by OnOffSensor)
API_VALUE_TO_ONOFF_OPTION: Dict[str, str] = {
    "0": "Off",
    "0.0": "Off",
    "1": "On",
    "1.0": "On",
}
ONOFF_OPTION_TO_API_VALUE: Dict[str, str] = (
    {  # Not strictly needed for sensor but good for completeness
        v: k
        for k, v in API_VALUE_TO_ONOFF_OPTION.items()  # This will be {"Off": "0.0", "On": "1.0"} or similar based on dict order
    }
)
ONOFF_OPTIONS_LIST: List[str] = ["Off", "On"]


def strip_html(text: str | None) -> str:
    """Remove HTML tags from a string."""
    if text is None:
        return ""
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_var_enum_string(
    unit_string: str,
) -> Optional[Tuple[Dict[str, str], Dict[str, str], List[str]]]:
    """
    Parses a 'VAR:'-style enum string.
    Example: "VAR:AUTO(2):0%(0):25%(0.25):50%(0.5):75%(0.75):100%(1):"
    or "VAR:AN(eq0):AUS(eq1):"
    Returns: (value_to_name_map, name_to_value_map, options_list) or None if parsing fails.
    Keys in value_to_name_map will be strings like "0", "1", "0.25" corresponding to API values.
    Names (values in value_to_name_map and items in options_list) will have HTML entities decoded.
    """
    if (
        not unit_string
        or not unit_string.startswith("VAR:")
        or not unit_string.endswith(":")
    ):
        _LOGGER.debug(f"Invalid VAR: enum string format (prefix/suffix): {unit_string}")
        return None

    parts = unit_string[4:-1].split(":")
    if not parts or all(not p for p in parts):
        _LOGGER.debug(
            f"No valid parts found in VAR: enum string after split: {unit_string}"
        )
        return None

    value_to_name: Dict[str, str] = {}
    name_to_value: Dict[str, str] = {}
    options: List[str] = []

    pattern = re.compile(r"([^()]+)\(([^()]+)\)")

    for part in parts:
        if not part.strip():
            continue
        match = pattern.fullmatch(part)
        if match:
            name_raw, value_from_config_str = match.groups()
            name = html.unescape(name_raw)
            api_value_key_for_map = value_from_config_str

            if value_from_config_str.startswith("eq"):
                numeric_part_after_eq = value_from_config_str[2:]
                if numeric_part_after_eq or numeric_part_after_eq == "0":
                    api_value_key_for_map = numeric_part_after_eq

            value_to_name[api_value_key_for_map] = name
            name_to_value[name] = api_value_key_for_map
            options.append(name)
        else:
            _LOGGER.warning(
                f"Could not parse VAR: enum part: '{part}' from string '{unit_string}' using regex."
            )

    if not options:
        _LOGGER.warning(
            f"No options were extracted from VAR: enum string: {unit_string}"
        )
        return None

    return value_to_name, name_to_value, options


def extract_numeric_room_id(room_attributes: Dict[str, Any]) -> Optional[int]:
    """
    Extracts the numeric room ID from room attributes.
    Example room_attributes: {'type': 'room003', 'var': 'RM_0003_NAME', ...}
    """
    room_type_str = room_attributes.get("type")
    if room_type_str:
        match = re.search(r"room(\d+)", room_type_str)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                _LOGGER.warning(
                    f"Could not parse numeric ID from room type '{room_type_str}' for room var '{room_attributes.get('var')}'."
                )
                return None
        else:
            _LOGGER.warning(
                f"Could not find numeric pattern in room type '{room_type_str}' for room var '{room_attributes.get('var')}'."
            )
            return None
    else:
        _LOGGER.warning(
            f"Room type missing in attributes for room var '{room_attributes.get('var')}'. Attributes: {room_attributes}."
        )
        return None


ENTITY_DATA_T = TypeVar("ENTITY_DATA_T")  # Generic type for entity specific data

# Define a callback type for processing an individual item (entry, input, output)
ItemProcessorCallback = Callable[
    [
        Dict[str, Any],  # item_data (e.g., content of an 'entry' or 'input')
        Dict[str, Any],  # room_attributes
        Optional[int],  # numeric_room_id (can be None if not applicable or not found)
        Dict[str, Any],  # component_attributes
        str,  # component_key_hint (e.g., "display", "param")
    ],
    Optional[ENTITY_DATA_T],  # Return some data or None if item is not relevant
]


def process_room_config_data(
    config_data: Dict[str, Any],
    possible_container_keys: List[str],
    item_processor: ItemProcessorCallback[ENTITY_DATA_T],
    # item_location_keys: List[str], # e.g., ["entry"] for select/number, ["input", "output"] for sensor
    # item_filter_callback: Callable[[Dict[str, Any]], bool], # Callback to check if an item is relevant
    # entity_creator_callback: Callable[[Dict[str, Any], Dict[str, Any], Optional[int], Dict[str, Any]], ENTITY_DATA_T],
) -> List[ENTITY_DATA_T]:
    """
    Generic parser for Innotemp configuration data to extract entities.

    Args:
        config_data: The raw configuration data from the coordinator.
        possible_container_keys: List of keys within a room that might contain items
                                 (e.g., ["param", "mixer", "display"]).
        item_processor: A callback function that takes (item_data, room_attributes,
                        numeric_room_id, component_attributes, component_key_hint)
                        and returns entity-specific data if the item is relevant, or None.
    Returns:
        A list of entity-specific data extracted by the item_processor.
    """
    processed_entities_data: List[ENTITY_DATA_T] = []

    if not isinstance(config_data, dict):
        _LOGGER.error(
            f"Config_data is not a dictionary. Type: {type(config_data)}. Data: {str(config_data)[:500]}"
        )
        return processed_entities_data

    for top_level_key, top_level_value in config_data.items():
        actual_room_list: List[Dict[str, Any]] = []
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
                    f"Could not parse string value for key {top_level_key} as JSON list."
                )

        if not actual_room_list:
            continue

        for room_data_dict in actual_room_list:
            if not isinstance(room_data_dict, dict):
                _LOGGER.warning(
                    f"Item in room list for key '{top_level_key}' is not a dict: {room_data_dict}"
                )
                continue

            room_attributes = room_data_dict.get("@attributes", {})
            if not room_attributes.get("var"):
                _LOGGER.warning(
                    f"Room missing '@attributes.var': {room_attributes}. Skipping."
                )
                continue

            numeric_room_id = extract_numeric_room_id(room_attributes)
            # numeric_room_id can be None, the callback will handle it if it needs it.

            for container_key in possible_container_keys:
                component_data_container = room_data_dict.get(container_key)
                if not component_data_container:
                    continue

                # component_data_container can be a dict or a list of dicts
                component_items_to_process: List[Dict[str, Any]] = []
                if isinstance(component_data_container, dict):
                    component_items_to_process.append(component_data_container)
                elif isinstance(component_data_container, list):
                    component_items_to_process.extend(component_data_container)
                else:
                    _LOGGER.debug(
                        f"Unexpected component_data_container type: {type(component_data_container)} "
                        f"for room {room_attributes.get('var')}, container_key {container_key}"
                    )
                    continue

                for (
                    component_item_data
                ) in component_items_to_process:  # e.g., one 'display' block
                    if not isinstance(component_item_data, dict):
                        _LOGGER.debug(
                            f"Skipping non-dict item in component_data_container list for room "
                            f"{room_attributes.get('var')}, container_key {container_key}: {component_item_data}"
                        )
                        continue

                    component_attributes = component_item_data.get("@attributes", {})

                    # Determine where to look for actual entity definitions (entry, input, output)
                    # This part needs to be flexible based on entity type (number, select, sensor)

                    # For numbers and selects, items are usually in "entry"
                    entry_data_list = component_item_data.get("entry")
                    if entry_data_list:
                        actual_entries: List[Dict[str, Any]] = []
                        if isinstance(entry_data_list, dict):
                            actual_entries.append(entry_data_list)
                        elif isinstance(entry_data_list, list):
                            actual_entries.extend(entry_data_list)

                        for actual_item_data in actual_entries:
                            if not isinstance(actual_item_data, dict):
                                continue
                            processed_data = item_processor(
                                actual_item_data,
                                room_attributes,
                                numeric_room_id,
                                component_attributes,
                                container_key,
                            )
                            if processed_data:
                                processed_entities_data.append(processed_data)

                    # For sensors, items are usually in "input" or "output"
                    for sub_key in ["input", "output"]:
                        sub_item_data_list = component_item_data.get(sub_key)
                        if sub_item_data_list:
                            actual_sub_items: List[Dict[str, Any]] = []
                            if isinstance(sub_item_data_list, dict):
                                actual_sub_items.append(sub_item_data_list)
                            elif isinstance(sub_item_data_list, list):
                                actual_sub_items.extend(sub_item_data_list)

                            for actual_item_data in actual_sub_items:
                                if not isinstance(actual_item_data, dict):
                                    continue
                                processed_data = item_processor(
                                    actual_item_data,
                                    room_attributes,
                                    numeric_room_id,
                                    component_attributes,
                                    f"{container_key}.{sub_key}",
                                )
                                if processed_data:
                                    processed_entities_data.append(processed_data)

                    # Fallback: Process the component_item_data itself if it has no "entry", "input", or "output"
                    # and the item_processor is designed to handle this (e.g. for direct sensors not in input/output)
                    if (
                        not entry_data_list
                        and not component_item_data.get("input")
                        and not component_item_data.get("output")
                    ):
                        processed_data = item_processor(
                            component_item_data,
                            room_attributes,
                            numeric_room_id,
                            component_attributes,
                            container_key,
                        )
                        if processed_data:
                            processed_entities_data.append(processed_data)

    return processed_entities_data


# Example of how an item_processor callback might look (will be defined in each platform file)
# def _example_select_item_processor(
#     item_data: Dict[str, Any],
#     room_attributes: Dict[str, Any],
#     numeric_room_id: Optional[int],
#     component_attributes: Dict[str, Any],
#     component_key_hint: str
# ) -> Optional[Dict[str, Any]]: # Returns data needed to create a Select entity
#     if item_data.get("unit") == "ONOFFAUTO" and item_data.get("var") and numeric_room_id is not None:
#         return {
#             "item_data": item_data,
#             "room_attributes": room_attributes,
#             "numeric_room_id": numeric_room_id,
#             "component_attributes": component_attributes,
#             "param_id": item_data.get("var")
#         }
#     return None


# Function to extract initial states from the full config_data
def extract_initial_states(config_data_full: dict) -> dict:
    """
    Parses the full config_data from async_get_config() and extracts a flat
    dictionary of param_id: value pairs for initial coordinator state.
    """
    initial_states = {}

    def recurse_extract(data_node):
        if isinstance(data_node, dict):
            param_id = data_node.get("var")
            unit = data_node.get("unit")
            current_value = None

            if param_id and unit:  # Only consider nodes that look like parameters
                # Prioritize '#text' as it's common for text content in XML-like dicts
                if "#text" in data_node:
                    current_value = data_node["#text"]
                elif "value" in data_node:  # Check 'value' attribute
                    current_value = data_node["value"]
                elif "val" in data_node:  # Check 'val' attribute
                    current_value = data_node["val"]
                # If the node itself is a simple string/number and no other value found,
                # this case is harder to generically identify without more structure knowledge.
                # For now, we rely on explicit value keys or #text.

            if param_id and current_value is not None:
                # Store values as strings, similar to how SSE might deliver them
                initial_states[param_id] = str(current_value)
                _LOGGER.debug(
                    f"Found initial state for param {param_id}: {current_value}"
                )

            # Recursively process child dictionary values or list items
            for key, value in data_node.items():
                if key.startswith("@"):  # Skip XML-like attributes
                    continue
                recurse_extract(value)

        elif isinstance(data_node, list):
            for item in data_node:
                recurse_extract(item)

    # Start recursion from the top level of config_data_full
    if isinstance(config_data_full, dict):
        for top_key, top_value in config_data_full.items():
            _LOGGER.debug(f"Extracting initial states from top_key: {top_key}")
            recurse_extract(top_value)
    else:
        _LOGGER.warning(
            f"extract_initial_states: config_data_full is not a dict, type: {type(config_data_full)}"
        )

    _LOGGER.info(f"Extracted {len(initial_states)} initial states for the coordinator.")
    return initial_states

# def _example_sensor_item_processor(
#     item_data: Dict[str, Any],
#     room_attributes: Dict[str, Any],
#     numeric_room_id: Optional[int], # Sensors might not need this
#     component_attributes: Dict[str, Any],
#     component_key_hint: str # e.g. "display.input"
# ) -> Optional[Dict[str, Any]]:
#     if item_data.get("var") and item_data.get("unit"):
#         # Further logic to decide sensor type (regular, enum, onoff, dynamic_enum)
#         return {
#             "item_data": item_data,
#             "room_attributes": room_attributes,
#             "component_attributes": component_attributes,
#             "param_id": item_data.get("var"),
#             "unit": item_data.get("unit")
#             # ... other details needed for sensor instantiation
#         }
#     return None
