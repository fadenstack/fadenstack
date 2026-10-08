"""Drafting a skill from a chat: the model proposes, the user saves.

``/skill`` in the chat (or "Create skill from this chat") asks the chat's
model to write a SKILL.md from the conversation. The draft comes back to the
browser as a proposal -- nothing is saved until the user has read it and
pressed Save. A model that could save skills on its own could be talked into
it by text in a document or web page, and such a skill would come back into
every later chat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

NAME_MAX = 64
DESCRIPTION_MAX = 1024
#: How much of the conversation the model is shown, newest first.
TRANSCRIPT_MAX_CHARS = 24_000
MESSAGE_MAX_CHARS = 4_000

SKILL_WRITER_PROMPT = """\
You write Agent Skills: reusable instructions that teach an AI assistant how \
to do one kind of task well.

From the conversation you are given, write ONE skill that captures the \
reusable way of working in it -- the procedure, the checks, the output \
format -- not the particulars of this one conversation.

Answer with the SKILL.md file only, in exactly this form, with no code \
fence around it:

---
name: <lowercase-words-with-hyphens, at most 64 characters>
description: <one or two sentences: what the skill does, and when to use \
it -- the situations and words that should bring it in>
---

## Goal
<what the skill achieves>

## Procedure
1. <step>
2. <step>

## Output format
<what the answer looks like>

## Do not
- <what to avoid>

Rules:
- Write instructions to an assistant ("Do X", "Check Y"), not a summary of \
the conversation.
- Keep it general: no names, e-mail addresses, credentials, customer data \
or other specifics from the conversation.
- Keep it under 500 lines.
"""

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^```[\w-]*\s*\n(.*?)\n```\s*$", re.DOTALL)
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Draft:
    """A proposed skill."""

    slug: str
    name: str
    description: str
    body_markdown: str

    @property
    def content(self) -> str:
        """The draft as a SKILL.md."""
        head = yaml.safe_dump(
            {"name": self.slug, "description": self.description},
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        return f"---\n{head}\n---\n\n{self.body_markdown.strip()}\n"

    def to_json(self) -> dict[str, Any]:
        """The draft as the API returns it."""
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "body_markdown": self.body_markdown,
            "content": self.content,
        }


def transcript(messages: list[dict[str, Any]], *, max_chars: int = TRANSCRIPT_MAX_CHARS) -> str:
    """The conversation as text, keeping the newest turns that fit."""
    lines: list[str] = []
    used = 0
    for message in reversed(messages):
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = _text_of(message.get("content"))
        if not text.strip():
            continue
        if len(text) > MESSAGE_MAX_CHARS:
            text = text[:MESSAGE_MAX_CHARS] + " […]"
        line = f"{'User' if role == 'user' else 'Assistant'}: {text.strip()}"
        if used + len(line) > max_chars and lines:
            break
        lines.append(line)
        used += len(line) + 2
    return "\n\n".join(reversed(lines))


def writer_messages(
    conversation: str,
    *,
    guidance: str | None = None,
    current: str | None = None,
) -> list[dict[str, str]]:
    """The messages that ask the model for a SKILL.md."""
    parts = [f"<conversation>\n{conversation}\n</conversation>"]
    if current:
        parts.append(
            "The skill as it is now -- revise it with what the conversation "
            f"adds, and keep its name:\n<skill>\n{current}\n</skill>",
        )
    if guidance and guidance.strip():
        parts.append(f"What the user wants from the skill: {guidance.strip()}")
    parts.append("Write the SKILL.md now.")
    return [
        {"role": "system", "content": SKILL_WRITER_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def parse_draft(text: str, *, fallback_name: str = "new-skill") -> Draft:
    """The model's answer as a draft, however loosely it kept to the form."""
    text = _THINK_RE.sub("", text or "")
    if "</think>" in text.lower():  # reasoning that began before the answer
        text = text[text.lower().rindex("</think>") + len("</think>") :]
    text = text.strip()
    fenced = _FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    front: dict[str, Any] = {}
    body = text
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "---"), None)
    if start is not None and start <= 2:  # a stray line before it is tolerated
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].strip() == "---"),
            None,
        )
        if end is not None:
            block = lines[start + 1 : end]
            try:
                loaded = yaml.safe_load("\n".join(block))
            except yaml.YAMLError:
                loaded = None
            if not isinstance(loaded, dict):
                # A description with a colon in it ("... error code: explain ...") is not YAML, and models
                # write one often: read the block a line at a time instead of losing the name and description.
                loaded = _loose_front(block)
            if loaded:
                front = loaded
                body = "\n".join(lines[end + 1 :]).strip()

    raw_name = " ".join(str(front.get("name") or "").split())
    slug = slugify(raw_name) if raw_name else slugify(fallback_name)
    # A name written as a title ("SQL Review") is kept as the title.
    title = raw_name if raw_name and not _NAME_RE.match(raw_name) else title_from_slug(slug)
    if slug == "new-skill" and raw_name:
        title = title_from_slug(slug)
    description = " ".join(str(front.get("description") or "").split())[:DESCRIPTION_MAX]
    return Draft(
        slug=slug,
        name=title[:255],
        description=description,
        body_markdown=body,
    )


_KEY_RE = re.compile(r"^([A-Za-z][\w-]*):\s?(.*)$")


def _loose_front(block: list[str]) -> dict[str, str]:
    """``key: value`` lines, the value running to the end of the line and on over indented lines."""
    front: dict[str, str] = {}
    key = None
    for line in block:
        match = _KEY_RE.match(line)
        if match:
            key = match.group(1)
            front[key] = match.group(2).strip().strip("\"'")
        elif key is not None and line[:1].isspace() and line.strip():
            front[key] = f"{front[key]} {line.strip()}".strip()
    return front


def slugify(text: str) -> str:
    """A valid skill name made from any text."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:NAME_MAX].rstrip("-")
    return slug if _NAME_RE.match(slug) else "new-skill"


def title_from_slug(slug: str) -> str:
    """"code-review" -> "Code Review"."""
    return " ".join(part.capitalize() for part in slug.split("-"))


def skill_markdown(skill: dict[str, Any]) -> str:
    """A skill from the skills API as a SKILL.md, for the model to revise."""
    return Draft(
        slug=str(skill.get("slug") or "skill"),
        name=str(skill.get("name") or ""),
        description=str(skill.get("description") or ""),
        body_markdown=str(skill.get("body_markdown") or ""),
    ).content


def _text_of(content: Any) -> str:
    """A message's text: a string, or the text parts of a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""
