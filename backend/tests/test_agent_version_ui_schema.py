"""``ui.max_running_sessions`` on an agent version: absent, or a whole number from 1 to 5. No database."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from faden_backend.web.api.agents.schema import AgentVersionFields, AgentVersionInput, CreateAgentRequest


@pytest.mark.parametrize("value", [1, 2, 3, 4, 5])
def test_accepts_one_to_five(value: int) -> None:
    assert AgentVersionInput(ui={"max_running_sessions": value}).ui["max_running_sessions"] == value


def test_absent_is_allowed_and_other_keys_untouched() -> None:
    assert AgentVersionInput().ui == {}
    assert AgentVersionInput(ui={"greeting": "Hi"}).ui == {"greeting": "Hi"}


@pytest.mark.parametrize("value", [0, 6, -1, 100, True, False, 2.0, 2.5, "3", None, [3]])
def test_refuses_everything_else(value: object) -> None:
    with pytest.raises(ValidationError, match="max_running_sessions must be a whole number from 1 to 5"):
        AgentVersionInput(ui={"max_running_sessions": value})


def test_create_request_applies_it() -> None:
    with pytest.raises(ValidationError):
        CreateAgentRequest(slug="a", name="A", version={"ui": {"max_running_sessions": 9}})
    assert CreateAgentRequest(slug="a", name="A", version={"ui": {"max_running_sessions": 4}})


def test_a_stored_version_reads_back_whatever_it_holds() -> None:
    """Only writing is checked: a version stored before the rule (or edited in the database) still lists."""
    assert AgentVersionFields(ui={"max_running_sessions": 9}).ui["max_running_sessions"] == 9
