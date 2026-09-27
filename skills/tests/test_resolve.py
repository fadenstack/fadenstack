"""Which skills a chat gets, from the ones its user can see (no database)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from llm_port_skills.services.resolve import (
    Candidate,
    RequestContext,
    choose,
    mentions,
)


@dataclass
class _Assignment:
    target_type: str
    target_id: str | None = None
    enabled: bool = True
    priority_override: int | None = None


def _skill(
    slug: str,
    *,
    owner: str | None = None,
    visibility: str = "tenant",
    activation: str = "auto",
    priority: int = 50,
    keywords: list[str] | None = None,
    assignments: list[Any] | None = None,
) -> Candidate:
    manifest = {"trigger_rules": {"keywords": keywords}} if keywords else {}
    return Candidate(
        skill_id=uuid.uuid4(),
        slug=slug,
        owner_id=owner,
        visibility=visibility,
        activation=activation,
        priority=priority,
        tags=[],
        version=1,
        manifest=manifest,
        assignments=assignments or [],
    )


def _slugs(matches: list[Any]) -> list[str]:
    return [m.candidate.slug for m in matches]


def test_mentions_are_skills_not_prices() -> None:
    text = (
        "Use $code-review and $SQL-Tuning, not $100, US$5 or $$x; again $code-review."
    )
    assert mentions(text) == ["code-review", "sql-tuning"]


def test_an_auto_skill_needs_its_trigger_as_a_word() -> None:
    art = _skill("art-critic", keywords=["art"])
    ctx = RequestContext(user_id="u", user_query="Let us start the report")
    assert choose([art], ctx, max_results=3, max_explicit=5) == []
    ctx = RequestContext(user_id="u", user_query="What makes art good?")
    assert _slugs(choose([art], ctx, max_results=3, max_explicit=5)) == ["art-critic"]


def test_a_manual_skill_comes_only_when_asked_for() -> None:
    manual = _skill("tone", activation="manual", keywords=["tone"])
    ctx = RequestContext(user_id="u", user_query="fix the tone")
    assert choose([manual], ctx, max_results=3, max_explicit=5) == []
    ctx = RequestContext(user_id="u", user_query="fix the tone with $tone")
    [match] = choose([manual], ctx, max_results=3, max_explicit=5)
    assert match.matched_by == "explicit"


def test_a_private_always_skill_is_always_on_for_its_owner_only() -> None:
    """Sharing a skill must not put it into the other person's every chat."""
    mine = _skill("my-style", owner="alice", visibility="private", activation="always")
    alice = RequestContext(user_id="alice", user_query="hello")
    bob = RequestContext(user_id="bob", user_query="hello")
    assert [
        m.matched_by for m in choose([mine], alice, max_results=3, max_explicit=5)
    ] == [
        "always",
    ]
    assert choose([mine], bob, max_results=3, max_explicit=5) == []


def test_a_tenant_always_skill_is_on_for_everyone() -> None:
    policy = _skill("policy", activation="always")
    ctx = RequestContext(user_id="anyone", user_query="hello")
    assert _slugs(choose([policy], ctx, max_results=3, max_explicit=5)) == ["policy"]


def test_asking_by_name_prefers_your_own_skill() -> None:
    theirs = _skill("review", visibility="tenant")
    shared = _skill("review", owner="carol", visibility="private")
    mine = _skill("review", owner="alice", visibility="private")
    ctx = RequestContext(user_id="alice", requested=["review"])
    [match] = choose([theirs, shared, mine], ctx, max_results=3, max_explicit=5)
    assert match.candidate is mine
    ctx = RequestContext(user_id="bob", requested=["review"])
    [match] = choose([theirs, shared], ctx, max_results=3, max_explicit=5)
    assert match.candidate is shared


def test_named_skills_come_first_and_on_top_of_the_limit() -> None:
    always = [_skill(f"a{i}", activation="always", priority=90 - i) for i in range(4)]
    named = _skill("named", activation="manual", priority=1)
    ctx = RequestContext(user_id="u", requested=[str(named.skill_id)])
    chosen = choose([*always, named], ctx, max_results=2, max_explicit=5)
    assert _slugs(chosen) == ["named", "a0", "a1"]


def test_explicit_requests_are_capped() -> None:
    skills = [_skill(f"s{i}", activation="manual") for i in range(4)]
    ctx = RequestContext(user_id="u", requested=[s.slug for s in skills])
    assert _slugs(choose(skills, ctx, max_results=3, max_explicit=2)) == ["s0", "s1"]


def test_an_assignment_for_another_project_does_not_match() -> None:
    assigned = _skill(
        "proj",
        assignments=[_Assignment("project", "p1")],
    )
    other = RequestContext(user_id="u", project_id="p2", user_query="x")
    here = RequestContext(user_id="u", project_id="p1", user_query="x")
    assert choose([assigned], other, max_results=3, max_explicit=5) == []
    [match] = choose([assigned], here, max_results=3, max_explicit=5)
    assert match.matched_by == "assignment"


# ── Ranking (from ray's test_rank_skills, c80e68f) ────────────────────
#
# The old resolver cut to the top three before it dropped the skills that did
# not apply, so three unmatched workspace skills of higher priority took every
# place and the global "QA answer style" skill never reached a chat (dev stack,
# 2026-09-26). A workspace skill is an auto one now, a global one always on.

PLAN = _skill("test-plan", activation="auto", priority=40, keywords=["test plan"])
CASES = _skill("test-cases", activation="auto", priority=40, keywords=["pytest"])
BUGS = _skill("bug-triage", activation="auto", priority=40, keywords=["bug"])
STYLE = _skill("qa-style", activation="always", priority=10)


def test_an_always_on_skill_is_not_crowded_out_by_ones_that_do_not_apply() -> None:
    ctx = RequestContext(user_id="u", user_query="hello there")
    chosen = choose([PLAN, CASES, BUGS, STYLE], ctx, max_results=3, max_explicit=5)
    assert _slugs(chosen) == ["qa-style"]


def test_matched_skills_rank_by_score_and_are_cut_at_the_limit() -> None:
    ctx = RequestContext(user_id="u", user_query="a test plan for the pytest bug")
    chosen = choose([STYLE, PLAN, CASES, BUGS], ctx, max_results=3, max_explicit=5)
    assert _slugs(chosen) == ["bug-triage", "test-cases", "test-plan"]
    assert [m.score for m in chosen] == [70, 70, 70]


def test_a_workspace_assignment_applies_only_there() -> None:
    assigned = _skill(
        "assigned",
        priority=20,
        assignments=[_Assignment("workspace", "w1")],
    )
    here = RequestContext(user_id="u", workspace_id="w1")
    elsewhere = RequestContext(user_id="u", workspace_id="w2")
    assert _slugs(choose([assigned], here, max_results=3, max_explicit=5)) == [
        "assigned",
    ]
    assert choose([assigned], elsewhere, max_results=3, max_explicit=5) == []
