"""User preferences merge: nested keys merge, and ``None`` removes a key (how guides are reset)."""

from __future__ import annotations

from faden_backend.web.api.admin.users.preferences import _deep_merge


def test_nested_keys_merge() -> None:
    base = {"tour_progress": {"a": {"completed": True}}, "profile": "team"}
    merged = _deep_merge(base, {"tour_progress": {"b": {"completed": False}}})
    assert merged == {"tour_progress": {"a": {"completed": True}, "b": {"completed": False}}, "profile": "team"}
    assert base == {"tour_progress": {"a": {"completed": True}}, "profile": "team"}


def test_none_removes_a_key_at_any_depth() -> None:
    base = {"tour_progress": {"a": {"completed": True}, "b": {"completed": True}}, "profile": "team"}
    merged = _deep_merge(base, {"tour_progress": {"a": None}, "profile": None})
    assert merged == {"tour_progress": {"b": {"completed": True}}}
    assert _deep_merge(base, {"tour_progress": None}) == {"profile": "team"}


def test_removing_a_missing_key_is_harmless() -> None:
    assert _deep_merge({}, {"getting_started": None}) == {}
