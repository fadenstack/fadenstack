"""A number or boolean a model quoted reaches the tool in its declared type.

Under load on the dev stack (2026-09-26) qwen-chat called ``get-sum`` with
``{"a": 17, "b": "25"}`` 82 times in 121; the MCP server validates against
its schema and refused every one.
"""

from __future__ import annotations

from typing import Any

import pytest

from llm_port_mcp.services.arguments import coerce_to_schema

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "a": {"type": "number"},
        "b": {"type": "number"},
        "count": {"type": "integer"},
        "loud": {"type": "boolean"},
        "message": {"type": "string"},
        "either": {"type": ["string", "number"]},
    },
}


@pytest.mark.parametrize(
    ("given", "sent"),
    [
        ({"a": 17, "b": "25"}, {"a": 17, "b": 25}),
        ({"a": " 2.5 ", "b": "-3"}, {"a": 2.5, "b": -3}),
        ({"count": "4"}, {"count": 4}),
        ({"loud": "True"}, {"loud": True}),
        ({"loud": "false"}, {"loud": False}),
    ],
)
def test_a_quoted_value_gets_its_declared_type(
    given: dict[str, Any],
    sent: dict[str, Any],
) -> None:
    """Numbers, integers and booleans written as strings."""
    assert coerce_to_schema(given, SCHEMA) == sent


@pytest.mark.parametrize(
    "given",
    [
        {"message": "25"},  # a string is what it wants
        {"either": "25"},  # a string is allowed
        {"a": "twenty-five"},  # not a number: the server says so
        {"count": "4.5"},  # not an integer
        {"a": "nan"},  # not a finite number
        {"loud": "yes"},  # not a boolean
        {"unknown": "1"},  # not in the schema
    ],
)
def test_anything_else_is_left_as_written(given: dict[str, Any]) -> None:
    """Only what reads exactly as the declared type is changed."""
    assert coerce_to_schema(given, SCHEMA) == given


def test_without_a_schema_nothing_changes() -> None:
    """A tool whose schema has no properties."""
    assert coerce_to_schema({"a": "1"}, None) == {"a": "1"}
    assert coerce_to_schema({"a": "1"}, {"type": "object"}) == {"a": "1"}
