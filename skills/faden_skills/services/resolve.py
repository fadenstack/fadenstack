"""Which of the skills someone can see a chat request gets.

Three ways in, in this order:

1. **Asked for by name** -- the chat's skill picker sends slugs, and a
   ``$slug`` in the message asks for one. Always included (up to
   ``max_explicit``), whatever the skill's activation.
2. **Always on** -- ``activation: always`` skills, for everyone when the whole
   tenant sees them and for the owner alone when private. People a private
   skill is shared with get it only when it matches (see 3), so sharing a
   skill never puts it into someone's every chat.
3. **Matched** -- ``activation: auto`` skills whose triggers (keywords, file
   types, tags) or assignments match the request.

2 and 3 compete for ``max_results`` places by score; ``manual`` skills only
ever come in by 1.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from faden_skills.db.models.skill import SkillActivation, SkillVisibility

#: ``$code-review`` in a message. Not after a word character or another
#: ``$`` (``US$5``, ``$$``), and the slug must hold a letter (``$100`` is
#: a price, not a skill).
_MENTION_RE = re.compile(r"(?<![\w$])\$([a-z0-9]+(?:-[a-z0-9]+)*)(?![\w-])", re.I)
_LETTER_RE = re.compile(r"[a-z]")

MATCHED_EXPLICIT = "explicit"
MATCHED_ALWAYS = "always"
MATCHED_TRIGGER = "trigger"
MATCHED_ASSIGNMENT = "assignment"

_ASSIGNMENT_BOOST = 50
_TRIGGER_BOOST = 30
_TAG_BOOST = 20


class AssignmentLike(Protocol):
    """What resolution reads from an assignment."""

    target_type: str
    target_id: str | None
    enabled: bool
    priority_override: int | None


@dataclass
class Candidate:
    """A skill the caller can see, with its published version's manifest."""

    skill_id: uuid.UUID
    slug: str
    owner_id: str | None
    visibility: str
    activation: str
    priority: int
    tags: list[str]
    version: int
    manifest: dict[str, Any]
    assignments: Sequence[AssignmentLike] = ()


@dataclass
class RequestContext:
    """What the request says about itself."""

    user_id: str | None = None
    requested: Sequence[str] = ()
    user_query: str | None = None
    file_types: Sequence[str] = ()
    tags: Sequence[str] = ()
    assistant_id: str | None = None
    workspace_id: str | None = None
    project_id: str | None = None


@dataclass
class Match:
    """A skill the request gets, and why."""

    candidate: Candidate
    score: int
    matched_by: str
    extra: dict[str, Any] = field(default_factory=dict)


def mentions(text: str | None) -> list[str]:
    """The skills a message asks for with ``$slug``, in order, once each."""
    if not text:
        return []
    found: list[str] = []
    for match in _MENTION_RE.finditer(text):
        slug = match.group(1).lower()
        if _LETTER_RE.search(slug) and slug not in found:
            found.append(slug)
    return found


def choose(
    candidates: Iterable[Candidate],
    ctx: RequestContext,
    *,
    max_results: int,
    max_explicit: int,
) -> list[Match]:
    """The skills *ctx* gets from *candidates*: named ones first, then by score."""
    candidates = list(candidates)
    asked = [a.strip().lower() for a in ctx.requested if a and a.strip()]
    asked += [m for m in mentions(ctx.user_query) if m not in asked]

    explicit = _explicit(candidates, asked, ctx.user_id)[: max(max_explicit, 0)]
    taken = {m.candidate.skill_id for m in explicit}

    scored: list[Match] = []
    for candidate in candidates:
        if candidate.skill_id in taken:
            continue
        match = _implicit(candidate, ctx)
        if match is not None:
            scored.append(match)
    scored.sort(key=lambda m: (-m.score, -m.candidate.priority, m.candidate.slug))
    return explicit + scored[: max(max_results, 0)]


def _explicit(
    candidates: list[Candidate],
    asked: list[str],
    user_id: str | None,
) -> list[Match]:
    """The candidates *asked* names, by slug or id, in the order asked.

    Two skills can share a slug (a private one and the tenant's); the one
    closest to the asker wins -- their own, then one shared with them, then
    the tenant's, then a global one.
    """
    by_id = {str(c.skill_id): c for c in candidates}
    by_slug: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_slug.setdefault(c.slug, []).append(c)

    def closeness(c: Candidate) -> int:
        if user_id and c.owner_id == user_id:
            return 0
        if c.visibility == SkillVisibility.PRIVATE.value:
            return 1
        if c.visibility == SkillVisibility.TENANT.value:
            return 2
        return 3

    out: list[Match] = []
    seen: set[uuid.UUID] = set()
    for name in asked:
        pick = by_id.get(name)
        if pick is None and name in by_slug:
            pick = min(by_slug[name], key=closeness)
        if pick is None or pick.skill_id in seen:
            continue
        seen.add(pick.skill_id)
        out.append(Match(pick, 1000 + pick.priority, MATCHED_EXPLICIT))
    return out


def _implicit(candidate: Candidate, ctx: RequestContext) -> Match | None:
    """The candidate's match without being named, or ``None``."""
    if candidate.activation == SkillActivation.MANUAL.value:
        return None
    matched = _score(candidate, ctx)
    always_for_them = candidate.activation == SkillActivation.ALWAYS.value and (
        candidate.visibility != SkillVisibility.PRIVATE.value
        or (ctx.user_id is not None and candidate.owner_id == ctx.user_id)
    )
    if always_for_them:
        score = matched[0] if matched else candidate.priority
        return Match(candidate, score, MATCHED_ALWAYS)
    if matched is None:
        return None
    return Match(candidate, matched[0], matched[1])


def _score(candidate: Candidate, ctx: RequestContext) -> tuple[int, str] | None:
    """``(score, matched_by)`` from assignments and triggers, if any matched."""
    score = candidate.priority
    matched_by: str | None = None

    for assignment in candidate.assignments:
        if not assignment.enabled or not _assignment_matches(assignment, ctx):
            continue
        matched_by = matched_by or MATCHED_ASSIGNMENT
        score += _ASSIGNMENT_BOOST
        if assignment.priority_override is not None:
            score = assignment.priority_override

    rules = candidate.manifest.get("trigger_rules") or {}
    query = ctx.user_query or ""
    for keyword in rules.get("keywords") or []:
        if query and _has_word(query, keyword):
            matched_by = matched_by or MATCHED_TRIGGER
            score += _TRIGGER_BOOST
    wanted_types = {ft.lower().lstrip(".") for ft in ctx.file_types}
    for file_type in rules.get("file_types") or []:
        if file_type.lower().lstrip(".") in wanted_types:
            matched_by = matched_by or MATCHED_TRIGGER
            score += _TRIGGER_BOOST
    skill_tags = set(candidate.tags or [])
    for tag in ctx.tags:
        if tag in skill_tags:
            matched_by = matched_by or MATCHED_TRIGGER
            score += _TAG_BOOST

    return (score, matched_by) if matched_by else None


def _assignment_matches(assignment: AssignmentLike, ctx: RequestContext) -> bool:
    kind = assignment.target_type
    if kind in {"global", "tenant"}:
        return True
    target = {
        "assistant": ctx.assistant_id,
        "workspace": ctx.workspace_id,
        "project": ctx.project_id,
    }.get(kind)
    return target is not None and assignment.target_id == target


def _has_word(text: str, keyword: str) -> bool:
    """Whether *keyword* is in *text* as a whole word or phrase.

    A plain substring test fired "art" on "start" and "sql" on "nosql".
    """
    keyword = keyword.strip()
    if not keyword:
        return False
    pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None
