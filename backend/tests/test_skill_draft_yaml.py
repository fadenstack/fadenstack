"""A model's draft of a skill is read without expanding YAML anchors, and within limits."""

from __future__ import annotations

import time

import pytest

from faden_backend.services.skills import drafting, safe_yaml


def _billion_laughs() -> str:
    """Nine anchors, each a list of ten of the last: a billion strings once expanded."""
    lines = ['a: &a ["lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol", "lol"]']
    for prev, name in zip("abcdefgh", "bcdefghi", strict=True):
        lines.append(f"{name}: &{name} [" + ", ".join([f"*{prev}"] * 10) + "]")
    return "\n".join(lines)


def test_the_loader_refuses_anchors_aliases_depth_and_size() -> None:
    with pytest.raises(safe_yaml.YamlRefused, match="anchors"):
        safe_yaml.load(_billion_laughs())
    with pytest.raises(safe_yaml.YamlRefused, match="aliases"):
        safe_yaml.load("a: *missing")
    with pytest.raises(safe_yaml.YamlRefused, match="nests more than"):
        safe_yaml.load("a: " + "[" * 5000 + "]" * 5000)
    with pytest.raises(safe_yaml.YamlRefused, match="larger than 32 KiB"):
        safe_yaml.load("\n".join(f"k{i}: {'x' * 60}" for i in range(600)))
    assert safe_yaml.load("name: a\nmetadata:\n  tools: [{n: 1}]") == {"name": "a", "metadata": {"tools": [{"n": 1}]}}


def test_a_billion_laughs_draft_is_read_a_line_at_a_time_and_expands_nothing() -> None:
    answer = f"---\nname: my-skill\ndescription: Does a thing.\n{_billion_laughs()}\n---\n\n## Goal\nDo it."
    started = time.perf_counter()
    draft = drafting.parse_draft(answer)
    assert time.perf_counter() - started < 1
    assert draft.slug == "my-skill"
    assert draft.description == "Does a thing."
    assert draft.body_markdown == "## Goal\nDo it."


def test_a_deep_draft_is_read_a_line_at_a_time_too() -> None:
    draft = drafting.parse_draft(f"---\nname: deep-one\ndescription: x\nmeta: {'[' * 5000}{']' * 5000}\n---\nBody")
    assert draft.slug == "deep-one" and draft.body_markdown == "Body"
