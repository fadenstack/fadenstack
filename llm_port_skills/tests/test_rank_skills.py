"""Which skills a chat turn gets: those that apply, best first.

The ranking cut to the top three before it dropped the skills that did not
apply, so three unmatched workspace skills of higher priority took every
place and the global "QA answer style" skill never reached a chat (dev
stack, 2026-09-26).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from llm_port_skills.services.dao import rank_skills


def _skill(name: str, scope: str, priority: int, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        scope=scope,
        priority=priority,
        assignments=extra.get("assignments", []),
        trigger_rules=extra.get("trigger_rules", {}),
        tags=extra.get("tags", []),
    )


PLAN = _skill("Test plan", "workspace", 40, trigger_rules={"keywords": ["test plan"]})
CASES = _skill("Test cases", "workspace", 40, trigger_rules={"keywords": ["pytest"]})
BUGS = _skill("Bug triage", "workspace", 40, trigger_rules={"keywords": ["bug"]})
STYLE = _skill("QA style", "global", 10)


def _names(ranked: list[tuple[Any, int]]) -> list[str]:
    return [skill.name for skill, _ in ranked]


def test_a_global_skill_is_not_crowded_out_by_ones_that_do_not_apply() -> None:
    """Unmatched workspace skills rank above it by priority, but do not apply."""
    ranked = rank_skills([PLAN, CASES, BUGS, STYLE], user_query="hello there")
    assert _names(ranked) == ["QA style"]


def test_matched_skills_rank_by_score_and_are_cut_at_the_limit() -> None:
    """Keyword matches first; the global skill still applies within the limit."""
    ranked = rank_skills(
        [STYLE, PLAN, CASES, BUGS],
        user_query="a test plan for the pytest bug",
        max_results=3,
    )
    assert _names(ranked) == ["Test plan", "Test cases", "Bug triage"]
    assert [score for _, score in ranked] == [70, 70, 70]


def test_an_assignment_applies_a_skill_to_its_target() -> None:
    """A workspace assignment matches only that workspace."""
    assigned = _skill(
        "Assigned",
        "workspace",
        20,
        assignments=[
            SimpleNamespace(
                enabled=True,
                target_type="workspace",
                target_id="w1",
                priority_override=None,
            ),
        ],
    )
    assert _names(rank_skills([assigned], workspace_id="w1")) == ["Assigned"]
    assert _names(rank_skills([assigned], workspace_id="w2")) == []
