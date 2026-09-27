"""A skill's other files -- references/, assets/ -- read by the model.

A skill is a folder: SKILL.md, whose instructions go into the chat, and files
the instructions point to ("see references/forms.md"). Those files never
reached the model. Now the skill's block names them, and the model reads one
when it needs it -- the Agent Skills way of loading a skill's detail only
when a task calls for it:

- A route whose model calls tools gets ``read_skill_file``, which the gateway
  runs against the skills service, for the skills resolved for this request
  only.
- Any other route gets the small text files inline, as many as fit a budget,
  and the rest named as not included.
"""

from __future__ import annotations

import json
from typing import Any

from faden_gateway.services.gateway.skills_client import ResolvedSkill, SkillsClient

NAME = "read_skill_file"

#: Characters a file is sent in at a time; the model asks for the rest.
CHUNK_CHARS = 20_000
#: Characters of skill files inlined in one request when the model cannot
#: call the tool (about 3000 tokens).
INLINE_CHARS = 12_000

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": NAME,
            "description": (
                "Read a file of one of the active skills -- a reference, "
                "template or example its instructions point to. Only the files "
                "listed with an active skill can be read."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "The skill's name, as listed (e.g. pdf-tools).",
                    },
                    "path": {"type": "string", "description": "The file's path in the skill, as listed."},
                    "offset": {
                        "type": "integer",
                        "description": (
                            f"Where to start reading, in characters (default 0); a file longer "
                            f"than {CHUNK_CHARS} characters is read in parts."
                        ),
                    },
                },
                "required": ["skill", "path"],
            },
        },
    },
]


def has_files(skills: list[ResolvedSkill]) -> bool:
    """Whether any resolved skill has files beside its SKILL.md."""
    return any(skill.files for skill in skills)


def skill_block(skill: ResolvedSkill, *, as_tool: bool) -> str:
    """The system message that gives the model *skill*."""
    parts = [f"=== ACTIVE SKILL: {skill.name} (v{skill.version}) ===", skill.body_markdown]
    if skill.files:
        parts.append(_files_section(skill, as_tool=as_tool))
    parts.append("=== END SKILL ===")
    return "\n".join(parts)


def _files_section(skill: ResolvedSkill, *, as_tool: bool) -> str:
    files = skill.files or []
    if as_tool:
        lines = [
            f"Files of this skill (read one with {NAME}, skill={skill.slug!r}, "
            "when the instructions call for it):",
        ]
        lines += [f"- {f['path']} ({_size(f)})" for f in files]
        return "\n".join(lines)
    lines = ["Files of this skill:"]
    inlined = [f for f in files if f.get("text") is not None]
    lines += [
        f"- {f['path']} (not included: {'binary' if f.get('kind') == 'binary' else 'too long'})"
        for f in files
        if f.get("text") is None
    ]
    for f in inlined:
        lines.append(f"--- {f['path']} ---\n{f['text']}\n--- end of {f['path']} ---")
    return "\n".join(lines)


def _size(ref: dict[str, Any]) -> str:
    size = int(ref.get("size") or 0)
    kind = ref.get("kind") or "text"
    shown = f"{size:,} bytes" if size < 10_240 else f"{size // 1024:,} KB"
    return shown if kind == "text" else f"{shown}, binary"


class SkillFiles:
    """Runs ``read_skill_file`` over the skills resolved for one request."""

    def __init__(self, client: SkillsClient, skills: list[ResolvedSkill], *, tenant_id: str) -> None:
        self._client = client
        self._skills = {skill.slug: skill for skill in skills}
        self._tenant_id = tenant_id

    async def run(self, arguments: dict[str, Any]) -> tuple[str, bool]:
        """The tool's answer for the model, and whether it is an error."""
        try:
            return json.dumps(await self._read(arguments)), False
        except Exception as exc:  # the model is told, and can go on
            return json.dumps({"error": str(exc)}), True

    async def _read(self, arguments: dict[str, Any]) -> dict[str, Any]:
        slug = str(arguments.get("skill") or "").strip().lstrip("$")
        path = str(arguments.get("path") or "").strip().lstrip("/")
        skill = self._skills.get(slug)
        if skill is None:
            names = ", ".join(sorted(s for s, v in self._skills.items() if v.files)) or "none"
            raise ValueError(f"No active skill named {slug!r}; skills with files: {names}.")
        if path not in {f["path"] for f in skill.files or []}:
            listed = ", ".join(f["path"] for f in skill.files or [])
            raise ValueError(f"{slug} has no file {path!r}; its files: {listed}.")
        found = await self._client.read_file(
            skill_id=skill.skill_id,
            version=skill.version,
            path=path,
            tenant_id=self._tenant_id,
        )
        text = found.get("text")
        if text is None:
            raise ValueError(f"{path} is a binary file; it cannot be read as text.")
        offset = max(0, int(arguments.get("offset") or 0))
        part = text[offset : offset + CHUNK_CHARS]
        more = offset + len(part) < len(text)
        answer: dict[str, Any] = {
            "skill": slug,
            "path": path,
            "offset": offset,
            "total_chars": len(text),
            "text": part,
        }
        if more:
            answer["next_offset"] = offset + len(part)
        return answer
