"""The client-event models: time approvals, the trigger, and what stays refused."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from faden_gateway.services.extensions import ExtensionRegistry
from faden_gateway.web.api.v1.agents import ClientEvents


def _approval(**extra: object) -> dict[str, object]:
    return {"type": "approval.decided", "tool": "write_page", "decision": "approved", **extra}


def _parse(*events: dict[str, object]) -> ClientEvents:
    return ClientEvents.model_validate({"events": list(events)})


def test_time_scope_needs_expires_at() -> None:
    ok = _parse(_approval(scope="time", expires_at="2026-10-30T12:00:00Z")).events[0]
    assert ok.scope == "time" and ok.expires_at is not None
    with pytest.raises(ValidationError, match="expires_at is required"):
        _parse(_approval(scope="time"))


@pytest.mark.parametrize("scope", ["once", "chat", "always", "bypass", None])
def test_expires_at_only_with_time_scope(scope: str | None) -> None:
    extra = {"scope": scope} if scope else {}
    with pytest.raises(ValidationError, match="only when scope is time"):
        _parse(_approval(expires_at="2026-10-30T12:00:00Z", **extra))
    assert _parse(_approval(**extra)).events[0].expires_at is None


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
    event = _parse(_approval(scope="time", expires_at="2026-10-30T12:00:00Z", trigger="module:mail")).events[0]
    data = event.model_dump(mode="json", exclude={"type", "occurred_at", "conversation_id"})
    assert data["scope"] == "time" and data["trigger"] == "module:mail"
    assert data["expires_at"].startswith("2026-10-30T12:00:00")


def test_core_features_list_the_new_capabilities() -> None:
    features = ExtensionRegistry().features
    assert {"approval_scopes.time", "client_events.trigger"} <= features
