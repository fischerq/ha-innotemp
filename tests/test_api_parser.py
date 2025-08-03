"""Unit tests for custom_components.innotemp.api_parser."""

import pytest
from typing import Any, Dict, Optional, List, Tuple

from custom_components.innotemp.api_parser import (
    strip_html,
    parse_var_enum_string,
    extract_numeric_room_id,
    process_room_config_data,
    create_control_state_map,
    API_VALUE_TO_ONOFFAUTO_OPTION,  # Import if needed for mock processors
    ONOFFAUTO_OPTION_TO_API_VALUE,
    ONOFFAUTO_OPTIONS_LIST,
)


def test_create_control_state_map():
    """Test the create_control_state_map function for mapping controls to states."""
    mock_config_data = {
        "room": [
            {
                "@attributes": {"type": "room001", "var": "R1", "label": "Living Room"},
                # Case 1: Simple 1-to-1 mapping in a component dict
                "pump": {
                    "@attributes": {"type": "pump001", "label": "Heating Pump"},
                    "entry": {
                        "var": "control_pump_1",
                        "label": "Vorlaufpumpe",
                        "unit": "ONOFFAUTO",
                    },
                    "input": {
                        "var": "state_pump_1",
                        "label": "Vorlaufpumpe",
                        "unit": "%",
                    },
                },
                # Case 2: Multiple entries, one matching input
                "mixer": {
                    "@attributes": {"type": "mixer001", "label": "Main Mixer"},
                    "entry": [
                        {"var": "control_mixer_1", "label": "Mischer 1", "unit": "VAR:"},
                        {
                            "var": "control_mixer_2",
                            "label": "Unmatched Mischer",
                            "unit": "VAR:",
                        },
                    ],
                    "input": [
                        {"var": "state_mixer_1", "label": "Mischer 1", "unit": "°C"},
                        {
                            "var": "state_mixer_other",
                            "label": "Some Other State",
                            "unit": "°C",
                        },
                    ],
                },
            },
            {
                "@attributes": {"type": "room002", "var": "R2", "label": "Basement"},
                # Case 3: Component is a list of dicts
                "param": [
                    {
                        "@attributes": {"type": "param001"},
                        "entry": {
                            "var": "control_param_1",
                            "label": "Heizkurve",
                            "unit": "°C",
                        },
                        "input": {
                            "var": "state_param_1",
                            "label": "Heizkurve",
                            "unit": "°C",
                        },
                    },
                    # Case 4: Entry has no label, should not match
                    {
                        "@attributes": {"type": "param002"},
                        "entry": {"var": "control_param_2", "unit": "°C"},
                        "input": {
                            "var": "state_param_2",
                            "label": "Something",
                            "unit": "°C",
                        },
                    },
                    # Case 5: Input has no var, should not be added to map
                    {
                        "@attributes": {"type": "param003"},
                        "entry": {
                            "var": "control_param_3",
                            "label": "NoMatchInputVar",
                            "unit": "°C",
                        },
                        "input": {"label": "NoMatchInputVar", "unit": "°C"},
                    },
                    # Case 6: Entry has no var, should not be added
                    {
                        "@attributes": {"type": "param004"},
                        "entry": {"label": "NoMatchEntryVar", "unit": "°C"},
                        "input": {
                            "var": "state_param_4",
                            "label": "NoMatchEntryVar",
                            "unit": "°C",
                        },
                    },
                    # Case 7: HTML tags in label should be stripped and still match
                    {
                        "@attributes": {"type": "param005"},
                        "entry": {
                            "var": "control_with_html",
                            "label": "<p>HTML Label</p>",
                            "unit": "Pa",
                        },
                        "input": {
                            "var": "state_with_html",
                            "label": "  HTML Label  ",
                            "unit": "Pa",
                        },
                    },
                ],
            },
            # Case 8: A room with no mappable components
            {
                "@attributes": {"type": "room003", "var": "R3"},
                "display": {
                    "input": {"var": "some_display_var", "label": "Display", "unit": "W"}
                },
            },
        ]
    }

    expected_map = {
        "control_pump_1": "state_pump_1",
        "control_mixer_1": "state_mixer_1",
        "control_param_1": "state_param_1",
        "control_with_html": "state_with_html",
    }

    result_map = create_control_state_map(mock_config_data)
    assert result_map == expected_map


# Basic test for file creation. More tests will be added in subsequent steps.

# Basic test for file creation. More tests will be added in subsequent steps.


def test_file_creation():
    """Dummy test to ensure file is created and pytest can find it."""
    assert True


# Tests for strip_html
@pytest.mark.parametrize(
    "input_text, expected_output",
    [
        (None, ""),
        ("", ""),
        ("Hello World", "Hello World"),
        ("<p>Hello World</p>", "Hello World"),
        ("Hello<br/>World", "HelloWorld"),
        ("  <p>  Hello   World  </p>  ", "Hello   World"),
        ("No HTML here", "No HTML here"),
        ("Text with <a href='#'>link</a> and <b>bold</b>.", "Text with link and bold."),
        ("Leading space <p>text</p>", "Leading space text"),
        ("<p>text</p> Trailing space", "text Trailing space"),
    ],
)
def test_strip_html(input_text: Optional[str], expected_output: str):
    """Test the strip_html function with various inputs."""
    assert strip_html(input_text) == expected_output


# Tests for parse_var_enum_string
@pytest.mark.parametrize(
    "input_string, expected_result",
    [
        (
            "VAR:AUTO(2):0%(0):25%(0.25):50%(0.5):75%(0.75):100%(1):",
            (
                {
                    "2": "AUTO",
                    "0": "0%",
                    "0.25": "25%",
                    "0.5": "50%",
                    "0.75": "75%",
                    "1": "100%",
                },
                {
                    "AUTO": "2",
                    "0%": "0",
                    "25%": "0.25",
                    "50%": "0.5",
                    "75%": "0.75",
                    "100%": "1",
                },
                ["AUTO", "0%", "25%", "50%", "75%", "100%"],
            ),
        ),
        (
            "VAR:AN(eq0):AUS(eq1):TEXT(some_val):",
            (
                {"0": "AN", "1": "AUS", "some_val": "TEXT"},
                {"AN": "0", "AUS": "1", "TEXT": "some_val"},
                ["AN", "AUS", "TEXT"],
            ),
        ),
        (
            "VAR:Normal(0):Boost(1):",
            (
                {"0": "Normal", "1": "Boost"},
                {"Normal": "0", "Boost": "1"},
                ["Normal", "Boost"],
            ),
        ),
        (
            "VAR:Test&amp;Name(val1):Another(val2):",  # HTML entity in name
            (
                {"val1": "Test&Name", "val2": "Another"},
                {"Test&Name": "val1", "Another": "val2"},
                ["Test&Name", "Another"],
            ),
        ),
        ("VAR::", None),  # Empty content
        ("VAR:InvalidFormat:", None),  # Part doesn't match pattern
        ("VAR:NoVal():", None),  # Part doesn't match pattern (empty value)
            (
                "VAR:NoName(val):",
                (
                    {"val": "NoName"},
                    {"NoName": "val"},
                    ["NoName"],
                ),
            ),  # This is actually valid, the test was wrong
        ("VAR:NoBrackets:", None),  # Part doesn't match pattern
        ("VAR:One(1)Two(2):", None),  # Invalid part format (no colon separator)
        (
            "VAR:One(1)::Two(2):",  # Empty part between valid ones
            (
                {"1": "One", "2": "Two"},
                {"One": "1", "Two": "2"},
                ["One", "Two"],
            ),
        ),
        ("INVALID_PREFIX:AUTO(2):", None),  # Wrong prefix
        ("VAR:AUTO(2)", None),  # Missing trailing colon
        (None, None),
        ("", None),
    ],
)
def test_parse_var_enum_string(
    input_string: Optional[str],
    expected_result: Optional[Tuple[Dict[str, str], Dict[str, str], List[str]]],
):
    """Test the parse_var_enum_string function."""
    if expected_result is None:
        assert parse_var_enum_string(input_string) is None
    else:
        value_to_name, name_to_value, options = expected_result
        parsed_output = parse_var_enum_string(input_string)
        assert parsed_output is not None
        actual_value_to_name, actual_name_to_value, actual_options = parsed_output
        assert actual_value_to_name == value_to_name
        assert actual_name_to_value == name_to_value
        assert sorted(actual_options) == sorted(
            options
        )  # Order of options might not be guaranteed


# Tests for extract_numeric_room_id
@pytest.mark.parametrize(
    "room_attributes, expected_id",
    [
        ({"type": "room001", "var": "RM_0001_NAME"}, 1),
        ({"type": "room123", "var": "RM_0123_NAME"}, 123),
        ({"type": "room0", "var": "RM_0000_NAME"}, 0),
        ({"type": "room_no_number", "var": "RM_X_NAME"}, None),
        (
            {"type": "prefix_room005", "var": "RM_005_NAME"},
            5,
        ),  # type might have other prefixes
        ({"var": "RM_0001_NAME"}, None),  # Missing 'type'
        ({}, None),  # Empty attributes
        ({"type": "roomABC", "var": "RM_ABC_NAME"}, None),  # Non-numeric id in type
        ({"type": "room007"}, 7),  # Missing 'var', but 'type' is valid
        (None, None),  # None input (though function expects dict)
    ],
)
def test_extract_numeric_room_id(
    room_attributes: Optional[Dict[str, Any]], expected_id: Optional[int]
):
    """Test the extract_numeric_room_id function."""
    if (
        room_attributes is None
    ):  # Handle None input case for robustness, though type hint expects Dict
        assert extract_numeric_room_id(room_attributes) is None
    else:
        assert extract_numeric_room_id(room_attributes) == expected_id


# Tests for process_room_config_data


# Mock item_processor for testing process_room_config_data
def mock_item_processor(
    item_data: Dict[str, Any],
    room_attributes: Dict[str, Any],
    numeric_room_id: Optional[int],
    component_attributes: Dict[str, Any],
    component_key_hint: str,
) -> Optional[Dict[str, Any]]:
    """
    A mock item processor that returns a dictionary if 'var' and 'unit' exist in item_data,
    otherwise None. It includes all received args in its return dict for easy assertion.
    """
    if item_data.get("var") and item_data.get("unit"):
        return {
            "item_data": item_data,
            "room_attributes": room_attributes,
            "numeric_room_id": numeric_room_id,
            "component_attributes": component_attributes,
            "component_key_hint": component_key_hint,
            "processed_by": "mock_item_processor",
        }
    return None


def mock_select_processor(
    item_data: Dict[str, Any],
    room_attributes: Dict[str, Any],
    numeric_room_id: Optional[int],
    component_attributes: Dict[str, Any],
    component_key_hint: str,
) -> Optional[Dict[str, Any]]:
    """Mock processor for select-like items that require numeric_room_id."""
    if (
        item_data.get("unit") == "ONOFFAUTO"
        and item_data.get("var")
        and numeric_room_id is not None
    ):
        return {
            "item_data": item_data,
            "room_attributes": room_attributes,
            "numeric_room_id": numeric_room_id,  # Crucial for select
            "component_attributes": component_attributes,
            "component_key_hint": component_key_hint,
            "processed_by": "mock_select_processor",
        }
    return None


@pytest.mark.parametrize(
    "config_data, possible_container_keys, item_processor_func, expected_count, expected_hints_and_vars",
    [
        # 1. Empty config_data
        ({}, ["param"], mock_item_processor, 0, []),
        # 2. Config_data is not a dictionary (should be handled by the function's initial check)
        ([], ["param"], mock_item_processor, 0, []),  # Example of wrong type
        ("not a dict", ["param"], mock_item_processor, 0, []),  # Example of wrong type
        # 3. Basic valid: single room, single component (dict), single entry
        (
            {
                "roomlist": [  # Assuming top-level key is 'roomlist'
                    {
                        "@attributes": {
                            "type": "room001",
                            "var": "ROOM_A_VAR",
                            "label": "Room A",
                        },
                        "param": {  # Component container
                            "@attributes": {
                                "type": "compType1",
                                "var": "COMP_A_VAR",
                                "label": "Component A",
                            },
                            "entry": {"var": "P1", "unit": "C", "label": "Temp Sensor"},
                        },
                    }
                ]
            },
            ["param"],
            mock_item_processor,
            1,
            [
                ("param.entry", "P1")
            ],  # component_key_hint simplified for this basic test structure
        ),
        # 4. JSON string for room list
        (
            {
                "roomlist_json": """
                    [
                        {
                            "@attributes": {"type": "room002", "var": "ROOM_B_VAR", "label": "Room B"},
                            "display": {
                                "@attributes": {"type": "dispType", "label": "Display B"},
                                "input": {"var": "S1", "unit": "V", "label": "Voltage"}
                            }
                        }
                    ]
                """
            },
            ["display"],  # Container key for this test
            mock_item_processor,
            1,
            [("display.input", "S1")],
        ),
        # 5. Multiple rooms, multiple components, items in entry/input/output
        (
            {
                "rooms": [
                    {  # Room 1
                        "@attributes": {
                            "type": "room003",
                            "var": "R3",
                            "label": "Room 3",
                        },
                        "comp1": {  # Component 1 in Room 1
                            "@attributes": {"label": "Comp1R3"},
                            "entry": [
                                {
                                    "var": "P3_1",
                                    "unit": "ONOFFAUTO",
                                },  # Processed by select_processor
                                {
                                    "var": "P3_2",
                                    "unit": "kWh",
                                },  # Processed by item_processor
                            ],
                        },
                        "comp2": {  # Component 2 in Room 1 (direct item)
                            "@attributes": {"label": "Comp2R3_Direct"},
                            "var": "S3_Direct",
                            "unit": "direct_unit",  # Direct item
                        },
                    },
                    {  # Room 2
                        "@attributes": {
                            "type": "room004",
                            "var": "R4",
                            "label": "Room 4",
                        },
                        "comp_list": [  # List of components
                            {
                                "@attributes": {"label": "CompList1R4"},
                                "input": {"var": "S4_1", "unit": "A"},
                                "output": {"var": "S4_2", "unit": "rpm"},
                            },
                            {  # Another component in the list, no processable items
                                "@attributes": {"label": "CompList2R4_Empty"}
                            },
                        ],
                    },
                ]
            },
            ["comp1", "comp2", "comp_list"],  # All possible container keys
            mock_select_processor,  # Using select processor first
            1,  # P3_1 from comp1.entry (ONOFFAUTO and room003 gives numeric_id)
            [("comp1.entry", "P3_1")],
        ),
        # 6. Same as 5, but with generic mock_item_processor to catch others
        (
            {
                "rooms": [
                    {  # Room 1
                        "@attributes": {
                            "type": "room003",
                            "var": "R3",
                            "label": "Room 3",
                        },
                        "comp1": {  # Component 1 in Room 1
                            "@attributes": {"label": "Comp1R3"},
                            "entry": [
                                {
                                    "var": "P3_1",
                                    "unit": "ONOFFAUTO",
                                },  # Skipped by generic if numeric_id matters implicitly
                                {"var": "P3_2", "unit": "kWh"},
                            ],
                        },
                        "comp2": {  # Component 2 in Room 1 (direct item)
                            "@attributes": {"label": "Comp2R3_Direct"},
                            "var": "S3_Direct",
                            "unit": "direct_unit",
                        },
                    },
                    {  # Room 2
                        "@attributes": {
                            "type": "room004",
                            "var": "R4",
                            "label": "Room 4",
                        },
                        "comp_list": [  # List of components
                            {
                                "@attributes": {"label": "CompList1R4"},
                                "input": {"var": "S4_1", "unit": "A"},
                                "output": {"var": "S4_2", "unit": "rpm"},
                            },
                            {"@attributes": {"label": "CompList2R4_Empty"}},
                        ],
                    },
                ]
            },
            ["comp1", "comp2", "comp_list"],
            mock_item_processor,  # Generic processor
            5,  # P3_1, P3_2, S3_Direct, S4_1, S4_2 are all processed
            sorted(
                [
                    ("comp1.entry", "P3_1"),
                    ("comp1.entry", "P3_2"),
                    ("comp2", "S3_Direct"),  # Direct item, hint is container_key
                    ("comp_list.input", "S4_1"),
                    ("comp_list.output", "S4_2"),
                ]
            ),
        ),
        # 7. Test item_processor returning None for some items
        (
            {
                "roomlist": [
                    {
                        "@attributes": {
                            "type": "room005",
                            "var": "R5",
                            "label": "Room 5",
                        },
                        "param": {
                            "entry": [
                                {"var": "P5_OK", "unit": "C"},
                                {
                                    "var": "P5_NO_UNIT"
                                },  # Will be skipped by mock_item_processor
                                {"unit": "C", "label": "P5_NO_VAR"},  # Skipped
                            ]
                        },
                    }
                ]
            },
            ["param"],
            mock_item_processor,
            1,
            [("param.entry", "P5_OK")],
        ),
        # 8. Top level value is a single room dict, not a list
        (
            {
                "my_single_room": {  # This key itself is not a list, but its value is a room
                    "@attributes": {
                        "type": "room006",
                        "var": "ROOM_C_VAR",
                        "label": "Room C",
                    },
                    "main": {
                        "@attributes": {"type": "mainType", "label": "Main C"},
                        "entry": {"var": "M1", "unit": "Pa", "label": "Pressure"},
                    },
                }
            },
            ["main"],
            mock_item_processor,
            1,
            [("main.entry", "M1")],
        ),
        # 9. No rooms found under any top-level key
        (
            {"some_other_data": {"info": "details"}},
            ["param"],
            mock_item_processor,
            0,
            [],
        ),
        # 10. Room without 'var' in attributes (should be skipped)
        (
            {
                "roomlist": [
                    {
                        "@attributes": {"type": "room007", "label": "Room No Var"},
                        "param": {"entry": {"var": "P7", "unit": "lux"}},
                    }
                ]
            },
            ["param"],
            mock_item_processor,
            0,
            [],
        ),
    ],
)
def test_process_room_config_data(
    config_data: Any,
    possible_container_keys: List[str],
    item_processor_func: Any,  # Callable type hint was complex for parametrize
    expected_count: int,
    expected_hints_and_vars: List[Tuple[str, str]],
):
    """Test the process_room_config_data function with various scenarios."""
    results = process_room_config_data(
        config_data, possible_container_keys, item_processor_func
    )
    assert len(results) == expected_count

    if expected_count > 0 and expected_hints_and_vars:
        # Verify specifics of the processed items if needed
        # This part can be tricky due to the structure of expected_hints_and_vars
        # For now, just checking count. More detailed checks can be added if a specific scenario fails.
        # Let's try to match based on component_key_hint and item_data.var
        processed_details = sorted(
            [(res["component_key_hint"], res["item_data"]["var"]) for res in results]
        )
        assert processed_details == sorted(expected_hints_and_vars)

        # Check if numeric_room_id was passed correctly for the select_processor case
        if (
            item_processor_func.__name__ == "mock_select_processor"
            and expected_count > 0
        ):
            for res in results:
                assert res["processed_by"] == "mock_select_processor"
                assert res["numeric_room_id"] is not None
                # Example: check a specific room ID if test case implies it
                if res["room_attributes"]["var"] == "R3":  # From test case 5
                    assert res["numeric_room_id"] == 3
    elif expected_count == 0:
        assert not results  # Ensure results list is empty
