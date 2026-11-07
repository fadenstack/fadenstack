"""The client-event models: the trigger, and what stays refused (a time held by the app among it)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from faden_gateway.services.extensions import ExtensionRegistry
from faden_gateway.web.api.v1.agents import ClientEvents


def _approval(**extra: object) -> dict[str, object]:
    return {"type": "approval.decided", "tool": "write_page", "decision": "approved", **extra}


def _parse(*events: dict[str, object]) -> ClientEvents:
    return ClientEvents.model_validate({"events": list(events)})


def test_no_approval_for_a_time_held_by_the_app() -> None:
    """The app-held "allow for 1 hour" went (sdk/38 D6): neither the scope ``time`` nor an ``expires_at``."""
    with pytest.raises(ValidationError):
        _parse(_approval(scope="time"))
    with pytest.raises(ValidationError):
        _parse(_approval(scope="time", expires_at="2026-10-30T12:00:00Z"))
    with pytest.raises(ValidationError):
        _parse(_approval(scope="once", expires_at="2026-10-30T12:00:00Z"))


@pytest.mark.parametrize("scope", ["once", "chat", "always", "bypass", None])
def test_the_other_scopes_stay(scope: str | None) -> None:
    extra = {"scope": scope} if scope else {}
    assert _parse(_approval(**extra)).events[0].scope == scope


@pytest.mark.parametrize("trigger", ["model", "user", "module:calendar", "module:a", "module:" + "a" * 63])
def test_trigger_accepts_the_pattern(trigger: str) -> None:
    tool = {"type": "tool.executed", "tool": "t", "duration_ms": 1, "outcome": "ok", "trigger": trigger}
    assert _parse(tool, _approval(trigger=trigger)).events[0].trigger == trigger


@pytest.mark.parametrize(
    "trigger", ["", "Model", "module:", "module:-x", "module:Up", "module:" + "a" * 64, "plugin:x", "user ", "module:a_b"],
)
def test_trigger_refuses_other_values(trigger: str) -> None:
    tool = {"type": "tool.executed", "tool": "t", "duration_ms": 1, "outcome": "ok", "trigger": trigger}
    with pytest.raises(ValidationError):
        _parse(tool)
    with pytest.raises(ValidationError):
        _parse(_approval(trigger=trigger))


def test_unknown_fields_stay_refused() -> None:
    with pytest.raises(ValidationError):
        _parse(_approval(scope="once", arguments={"a": 1}))
    with pytest.raises(ValidationError):
        _parse({"type": "tool.executed", "tool": "t", "duration_ms": 1, "outcome": "ok", "expires_at": "2026-10-30T12:00:00Z"})


def test_new_values_are_dumped_into_the_stored_data() -> None:
    event = _parse(_approval(scope="chat", trigger="module:mail")).events[0]
    data = event.model_dump(mode="json", exclude={"type", "occurred_at", "conversation_id"})
    assert data["scope"] == "chat" and data["trigger"] == "module:mail"
    assert "expires_at" not in data


def test_core_features_list_the_trigger_and_no_time_scope() -> None:
    features = ExtensionRegistry().features
    assert "client_events.trigger" in features
    assert "approval_scopes.time" not in features
