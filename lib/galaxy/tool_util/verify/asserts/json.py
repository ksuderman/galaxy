import json
import subprocess
from typing import (
    Any,
    Callable,
    cast,
)

PropertyVisitor = Callable[[str, Any], Any]


def any_in_tree(f: PropertyVisitor, json_tree: Any):
    if isinstance(json_tree, list):
        for element in json_tree:
            if any_in_tree(f, element):
                return True

    elif isinstance(json_tree, dict):
        for key, value in json_tree.items():
            if f(cast(str, key), value):
                return True
            elif any_in_tree(f, value):
                return True

    return False


def assert_has_json_property_with_value(
    output,
    property: str,
    value: str,
):
    """Assert JSON tree contains the specified property with specified JSON-ified value."""
    output_json = assert_json_and_load(output)
    expected_value = assert_json_and_load(value)

    def is_property(key, value):
        return key == property and value == expected_value

    assert any_in_tree(is_property, output_json), f"Failed to find property [{property}] with JSON value [{value}]"


def assert_has_json_property_with_text(
    output,
    property: str,
    text: str,
):
    """Assert JSON tree contains the specified property with specified JSON-ified value."""
    output_json = assert_json_and_load(output)

    def is_property(key, value):
        return key == property and value == text

    assert any_in_tree(is_property, output_json), f"Failed to find property [{property}] with text [{text}]"


def assert_json_and_load(json_str: str):
    try:
        return json.loads(json_str)
    except Exception:
        raise AssertionError(f"Failed to parse JSON from {json_str[0:1024]}.")


def assert_json_query(
    json_str,
    query: str,
    expected: str,
):
    # We don't need the json loaded, but it is useful to ensure it is valid.
    assert_json_and_load(json_str)
    p = subprocess.run(['jq', query], input=json_str, check=True, text=True, capture_output=True, shell=False)
    actual = p.stdout.strip()
    assert actual == expected, f"Expected jq expression [{query}] to return [{expected}], but got [{actual}]."