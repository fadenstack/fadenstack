"""A skill in the Agent Skills format: a folder with a ``SKILL.md`` in it.

https://agentskills.io/specification -- ``SKILL.md`` is YAML frontmatter
(``name`` and ``description``, optionally ``license``, ``compatibility``,
``metadata``, ``allowed-tools``) over a Markdown body, and the folder may hold
``references/``, ``assets/`` and ``scripts/``. Claude Code, Codex and Open
WebUI read the same folders.

What Fadenstack adds -- the title, tags, priority, when the skill comes into a
chat, the tool lists, knowledge sources and triggers -- lives in
``faden.yaml`` beside ``SKILL.md`` rather than in its frontmatter, so a
skill exported from here is a plain Agent Skill anywhere else, and one
written elsewhere imports as is. In particular the spec's ``allowed-tools``
(``Bash(git:*) Read``) is kept but not applied: read as Fadenstack's
``allowed_tools`` it would strip every MCP tool from a chat.

Older Fadenstack documents put those fields in the frontmatter itself; they are
still read, and moved where they belong.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

import yaml

from faden_skills.settings import settings

SKILL_FILE = "SKILL.md"
EXTENSION_FILE = "faden.yaml"

NAME_MAX = 64
DESCRIPTION_MAX = 1024
TITLE_MAX = 255

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: ``faden.yaml`` fields that are part of a version (a rollback restores them).
MANIFEST_KEYS = (
    "allowed_tools",
    "preferred_tools",
    "forbidden_tools",
    "knowledge_sources",
    "trigger_rules",
)
#: ``faden.yaml`` fields that describe the skill in the catalog. They seed a
#: new skill on import; afterwards they are edited on the skill itself.
CATALOG_KEYS = ("title", "tags", "priority", "activation")

#: Keys older Fadenstack documents put in the frontmatter that only this
#: service ever used; dropped on import, never exported.
_LEGACY_ONLY_KEYS = ("scope", "status", "enabled", "visibility")

_TRIGGER_KEYS = ("keywords", "file_types", "intents")

#: Paths a folder may carry that are never taken from an upload.
_JUNK = re.compile(r"(^|/)(__MACOSX|\.DS_Store|Thumbs\.db|\.git)(/|$)")

#: Files that could be run. Nothing here runs a skill's scripts -- there is no
#: sandbox to run them in -- so they are kept for export and shown as such.
_EXECUTABLE_SUFFIXES = (
    ".sh",
    ".bash",
    ".py",
    ".js",
    ".mjs",
    ".ts",
    ".ps1",
    ".bat",
    ".cmd",
    ".exe",
    ".rb",
    ".pl",
)


class BundleError(ValueError):
    """The content is not a skill this service can store."""


@dataclass
class SkillBundle:
    """One version of a skill, as the files would hold it."""

    name: str
    description: str
    body: str
    #: SKILL.md's other frontmatter fields, as written.
    frontmatter: dict[str, Any] = field(default_factory=dict)
    #: ``faden.yaml``'s versioned fields (``MANIFEST_KEYS``).
    manifest: dict[str, Any] = field(default_factory=dict)
    #: ``faden.yaml``'s catalog fields (``CATALOG_KEYS``).
    catalog: dict[str, Any] = field(default_factory=dict)
    #: ``{path: {"text": ...} | {"base64": ...}}``, paths relative to the folder.
    files: dict[str, dict[str, str]] = field(default_factory=dict)


# ── Names ─────────────────────────────────────────────────────────────


def is_valid_name(name: str) -> bool:
    """Whether *name* is an Agent Skills ``name``: a-z, 0-9, single hyphens."""
    return len(name) <= NAME_MAX and bool(_NAME_RE.match(name))


def validate_name(name: str) -> str:
    """*name*, or ``BundleError`` saying what is wrong with it."""
    if not name:
        msg = "A skill needs a name."
        raise BundleError(msg)
    if len(name) > NAME_MAX:
        msg = f"A skill's name is at most {NAME_MAX} characters."
        raise BundleError(msg)
    if not _NAME_RE.match(name):
        msg = (
            f"'{name}' is not a skill name: lowercase letters, digits and "
            "single hyphens, not starting or ending with a hyphen."
        )
        raise BundleError(msg)
    return name


def slugify(text: str) -> str:
    """A valid skill name made from a title ("Code Review!" -> "code-review")."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:NAME_MAX].rstrip("-")
    return slug or "skill"


def title_from_name(name: str) -> str:
    """A readable title for a name ("code-review" -> "Code Review")."""
    return " ".join(part.capitalize() for part in name.split("-")) or name


# ── SKILL.md ──────────────────────────────────────────────────────────


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """``(frontmatter, body)`` of a Markdown document.

    The frontmatter ends at the first line that is only ``---`` (or ``...``).
    The old parser ended it at the first ``---`` anywhere, so a ``---`` in a
    YAML value, or a ``----`` rule, cut the frontmatter short and pushed its
    remainder into the body the model was given.
    """
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    for i in range(1, len(lines)):
        if lines[i].strip() in {"---", "..."}:
            raw = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :]).strip()
            try:
                data = yaml.safe_load(raw) if raw.strip() else {}
            except yaml.YAMLError as exc:
                msg = f"The frontmatter is not valid YAML: {exc}"
                raise BundleError(msg) from exc
            if data is None:
                data = {}
            if not isinstance(data, dict):
                msg = "The frontmatter must be a mapping of fields."
                raise BundleError(msg)
            return data, body
    # An opening ``---`` with no end: there is no frontmatter.
    return {}, text.strip()


def from_markdown(text: str, *, default_name: str | None = None) -> SkillBundle:
    """A bundle from one ``SKILL.md`` (or an older Fadenstack skill document)."""
    fm, body = split_frontmatter(text)
    fm = dict(fm)
    extension = {
        key: fm.pop(key) for key in (*MANIFEST_KEYS, *CATALOG_KEYS) if key in fm
    }
    for key in _LEGACY_ONLY_KEYS:
        fm.pop(key, None)

    raw_name = fm.pop("name", None)
    description = fm.pop("description", None)
    name, title = _name_and_title(raw_name, default_name)
    if title and "title" not in extension:
        extension["title"] = title

    manifest, catalog = split_extension(extension)
    return SkillBundle(
        name=name,
        description=_text(description),
        body=body,
        frontmatter=_json_safe(fm),
        manifest=manifest,
        catalog=catalog,
    )


def to_markdown(bundle: SkillBundle) -> str:
    """The bundle's ``SKILL.md``."""
    fm: dict[str, Any] = {"name": bundle.name, "description": bundle.description}
    fm.update(bundle.frontmatter)
    head = yaml.safe_dump(
        fm,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{head}\n---\n\n{bundle.body.strip()}\n"


def _name_and_title(
    raw_name: Any,
    default_name: str | None,
) -> tuple[str, str | None]:
    """The skill name, and a title when the document's name was one.

    Older Fadenstack documents used ``name`` for the display title ("Financial
    Analysis"); a name that is not a valid skill name is read as that.
    """
    candidate = _text(raw_name).strip()
    if candidate and is_valid_name(candidate):
        return candidate, None
    if candidate:
        return slugify(candidate), candidate[:TITLE_MAX]
    if default_name:
        return slugify(default_name), None
    msg = "SKILL.md has no name."
    raise BundleError(msg)


# ── faden.yaml ──────────────────────────────────────────────────────


def split_extension(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(manifest, catalog)`` from ``faden.yaml``'s fields, checked."""
    manifest = normalize_manifest(
        {key: data[key] for key in MANIFEST_KEYS if key in data},
    )
    catalog: dict[str, Any] = {}
    if data.get("title") is not None:
        catalog["title"] = _text(data["title"]).strip()[:TITLE_MAX]
    if data.get("tags") is not None:
        catalog["tags"] = _string_list(data["tags"], "tags")
    if data.get("priority") is not None:
        catalog["priority"] = _priority(data["priority"])
    if data.get("activation") is not None:
        activation = _text(data["activation"]).strip()
        if activation not in {"always", "auto", "manual"}:
            msg = "activation is one of always, auto, manual."
            raise BundleError(msg)
        catalog["activation"] = activation
    return manifest, catalog


def extension_yaml(bundle: SkillBundle) -> str | None:
    """The bundle's ``faden.yaml``, or ``None`` when there is nothing in it."""
    data = {**bundle.catalog, **bundle.manifest}
    if not data:
        return None
    return yaml.safe_dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )


def normalize_manifest(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """The manifest with its types checked and empty fields left out."""
    out: dict[str, Any] = {}
    for key in ("allowed_tools", "preferred_tools", "forbidden_tools"):
        if manifest and manifest.get(key):
            out[key] = _string_list(manifest[key], key)
    if manifest and manifest.get("knowledge_sources"):
        out["knowledge_sources"] = _string_list(
            manifest["knowledge_sources"],
            "knowledge_sources",
        )
    rules = (manifest or {}).get("trigger_rules")
    if rules:
        if not isinstance(rules, dict):
            msg = "trigger_rules is a mapping (keywords, file_types)."
            raise BundleError(msg)
        clean = {
            key: _string_list(rules[key], f"trigger_rules.{key}")
            for key in _TRIGGER_KEYS
            if rules.get(key)
        }
        if clean:
            out["trigger_rules"] = clean
    return out


# ── Folders (.zip) ────────────────────────────────────────────────────


def from_zip(data: bytes) -> SkillBundle:
    """A bundle from a zipped skill folder.

    ``SKILL.md`` may be at the top of the archive or in one folder there
    (``my-skill/SKILL.md``), as a zipped folder usually is.
    """
    if len(data) > settings.max_upload_bytes:
        msg = f"The archive is larger than {settings.max_upload_bytes} bytes."
        raise BundleError(msg)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        msg = "The file is not a zip archive."
        raise BundleError(msg) from exc

    with archive:
        entries = [
            info
            for info in archive.infolist()
            if not info.is_dir() and not _JUNK.search(_clean_member(info.filename))
        ]
        if len(entries) > settings.max_files_per_skill + 2:
            msg = f"A skill holds at most {settings.max_files_per_skill} files."
            raise BundleError(msg)
        # Sizes from the directory, before anything is inflated.
        if sum(info.file_size for info in entries) > settings.max_bundle_bytes:
            msg = f"A skill is at most {settings.max_bundle_bytes} bytes unpacked."
            raise BundleError(msg)

        root = _find_root([_clean_member(info.filename) for info in entries])
        skill_md: str | None = None
        extension: dict[str, Any] = {}
        files: dict[str, dict[str, str]] = {}
        for info in entries:
            member = _clean_member(info.filename)
            if not member.startswith(root):
                continue
            path = safe_path(member[len(root) :])
            raw = archive.read(info)
            if path == SKILL_FILE:
                skill_md = _decode_text(raw, SKILL_FILE)
            elif path == EXTENSION_FILE:
                extension = _load_extension(_decode_text(raw, EXTENSION_FILE))
            else:
                files[path] = file_entry(raw)

    if skill_md is None:
        msg = "The archive has no SKILL.md."
        raise BundleError(msg)
    folder = root.rstrip("/").rsplit("/", 1)[-1] or None
    bundle = from_markdown(skill_md, default_name=folder)
    manifest, catalog = split_extension(extension)
    bundle.manifest = {**bundle.manifest, **manifest}
    bundle.catalog = {**bundle.catalog, **catalog}
    bundle.files = validate_files(files)
    return bundle


def to_zip(bundle: SkillBundle) -> bytes:
    """The bundle as a zipped folder named after the skill."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{bundle.name}/{SKILL_FILE}", to_markdown(bundle))
        ext = extension_yaml(bundle)
        if ext is not None:
            archive.writestr(f"{bundle.name}/{EXTENSION_FILE}", ext)
        for path in sorted(bundle.files):
            archive.writestr(f"{bundle.name}/{path}", file_bytes(bundle.files[path]))
    return buf.getvalue()


def _find_root(members: list[str]) -> str:
    """The folder prefix ``SKILL.md`` is in: ``""`` or ``"name/"``."""
    candidates = sorted(
        (m for m in members if posixpath.basename(m) == SKILL_FILE),
        key=lambda m: m.count("/"),
    )
    if not candidates:
        msg = "The archive has no SKILL.md."
        raise BundleError(msg)
    top = candidates[0]
    if top.count("/") > 1:
        msg = "SKILL.md must be at the top of the archive or in one folder there."
        raise BundleError(msg)
    return top[: -len(SKILL_FILE)]


def _clean_member(name: str) -> str:
    return name.replace("\\", "/")


def _load_extension(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"{EXTENSION_FILE} is not valid YAML: {exc}"
        raise BundleError(msg) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        msg = f"{EXTENSION_FILE} must be a mapping of fields."
        raise BundleError(msg)
    return data


# ── Files ─────────────────────────────────────────────────────────────


def safe_path(path: str) -> str:
    """*path* as a relative path inside the folder, or ``BundleError``."""
    path = path.replace("\\", "/")
    if not path or path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        msg = f"'{path}' is not a path inside the skill."
        raise BundleError(msg)
    normalized = posixpath.normpath(path)
    if normalized.startswith("..") or normalized in {".", ""}:
        msg = f"'{path}' is not a path inside the skill."
        raise BundleError(msg)
    return normalized


def file_entry(raw: bytes) -> dict[str, str]:
    """How a file is stored: as text when it is UTF-8, else as base64."""
    if b"\x00" not in raw:
        try:
            return {"text": raw.decode("utf-8")}
        except UnicodeDecodeError:
            pass
    return {"base64": base64.b64encode(raw).decode("ascii")}


def file_bytes(entry: dict[str, str]) -> bytes:
    """A stored file's bytes."""
    if "text" in entry:
        return entry["text"].encode("utf-8")
    try:
        return base64.b64decode(entry.get("base64", ""), validate=True)
    except binascii.Error as exc:
        msg = "A file's base64 content is not valid."
        raise BundleError(msg) from exc


def is_executable(path: str) -> bool:
    """Whether *path* is a script: nothing runs it, and the editor says so."""
    return path.startswith("scripts/") or path.lower().endswith(_EXECUTABLE_SUFFIXES)


def validate_files(files: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """The folder's files, with their paths and sizes checked."""
    if not files:
        return {}
    if not isinstance(files, dict):
        msg = "files is a mapping of path to content."
        raise BundleError(msg)
    if len(files) > settings.max_files_per_skill:
        msg = f"A skill holds at most {settings.max_files_per_skill} files."
        raise BundleError(msg)
    out: dict[str, dict[str, str]] = {}
    total = 0
    for raw_path, entry in files.items():
        path = safe_path(str(raw_path))
        if path in {SKILL_FILE, EXTENSION_FILE}:
            msg = f"{path} is written from the skill itself, not given as a file."
            raise BundleError(msg)
        if not isinstance(entry, dict) or not (
            isinstance(entry.get("text"), str) or isinstance(entry.get("base64"), str)
        ):
            msg = f"{path}: a file is {{'text': ...}} or {{'base64': ...}}."
            raise BundleError(msg)
        clean = (
            {"text": entry["text"]}
            if isinstance(entry.get("text"), str)
            else {"base64": entry["base64"]}
        )
        size = len(file_bytes(clean))
        if size > settings.max_file_bytes:
            msg = f"{path} is larger than {settings.max_file_bytes} bytes."
            raise BundleError(msg)
        total += size
        out[path] = clean
    if total > settings.max_bundle_bytes:
        msg = f"A skill's files are at most {settings.max_bundle_bytes} bytes."
        raise BundleError(msg)
    return out


def describe_files(files: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """What the editor lists for a version's files."""
    return [
        {
            "path": path,
            "size": len(file_bytes(entry)),
            "kind": "text" if "text" in entry else "binary",
            "executable": is_executable(path),
        }
        for path, entry in sorted(files.items())
    ]


# ── Content hash ──────────────────────────────────────────────────────


def content_sha256(
    *,
    name: str,
    description: str,
    body: str,
    frontmatter: dict[str, Any],
    manifest: dict[str, Any],
    files: dict[str, dict[str, str]],
) -> str:
    """The hash of everything a version holds.

    The audit log records it with each skill a request used, so what the
    model was told can be shown to be exactly a stored version.
    """
    doc = {
        "name": name,
        "description": description,
        "body": body,
        "frontmatter": frontmatter,
        "manifest": manifest,
        "files": files,
    }
    canonical = json.dumps(
        doc,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Checks ────────────────────────────────────────────────────────────


def check_content(*, description: str, body: str) -> None:
    """``BundleError`` when a version's text is outside the limits."""
    if len(description) > DESCRIPTION_MAX:
        msg = f"A description is at most {DESCRIPTION_MAX} characters."
        raise BundleError(msg)
    if not body.strip():
        msg = "A skill needs instructions (the SKILL.md body is empty)."
        raise BundleError(msg)
    if len(body) > settings.max_body_chars:
        msg = (
            f"A skill's instructions are at most {settings.max_body_chars} characters."
        )
        raise BundleError(msg)


def _string_list(value: Any, what: str) -> list[str]:
    if isinstance(value, str):
        value = [part for part in re.split(r"[,\s]+", value) if part]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        msg = f"{what} is a list of strings."
        raise BundleError(msg)
    return [v.strip() for v in value if v.strip()]


def _priority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        msg = "priority is a whole number from 0 to 100."
        raise BundleError(msg)
    return value


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _decode_text(raw: bytes, what: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"{what} is not UTF-8 text."
        raise BundleError(msg) from exc


def _json_safe(value: Any) -> Any:
    """*value* with what YAML reads but JSON cannot hold (dates) made text."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
