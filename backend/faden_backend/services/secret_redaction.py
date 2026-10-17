"""Secret values never leave the backend: their names do, their values become :data:`MASK`.

The console shows what a container, a stack or a provider was set up with, and its edit forms send all of it
back. So a value is hidden in what the backend answers and, when the form sends the mask back unchanged, the
stored value is put in again (:func:`restore_env` and friends). A value the user typed anew replaces it.

What counts as a secret:

- by name: an environment variable, header or parameter whose name has a part such as ``PASSWORD``, ``TOKEN``,
  ``SECRET``, ``KEY`` or ``AUTH`` (``FADEN_GATEWAY_ENCRYPTION_KEY``, ``REDISCLI_AUTH``, ``Authorization``);
- by value: the password in a URL (``postgresql://user:password@host/db``);
- on a command line: the value after such a flag (``--requirepass x``, ``--password=x``).

A switch (``true``, ``off``) or an empty value is not hidden: there is nothing to hide, and the form needs it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

#: What a hidden value reads as. Bullets, so a password field shows dots and nobody types it by chance.
MASK = "•" * 8

#: Name parts that make a value secret (names are split at ``_``, ``-`` and ``.``, compared upper case).
_SECRET_PARTS = frozenset({
    "PASS", "PASSWD", "PASSWORD", "PWD", "PASSPHRASE",
    "SECRET", "SECRETS", "TOKEN", "TOKENS", "KEY", "APIKEY", "KEYS",
    "AUTH", "AUTHORIZATION", "COOKIE", "PEPPER", "SALT", "CREDENTIAL", "CREDENTIALS",
    "PRIVATE", "DSN", "REQUIREPASS", "SIGNATURE",
})
#: Values that only switch something: never hidden.
_SWITCHES = frozenset({"true", "false", "yes", "no", "on", "off", "enabled", "disabled", "none", "null"})
#: ``scheme://user:password@`` -- the password group is hidden.
_URL_PASSWORD = re.compile(r"^(?P<head>[A-Za-z][A-Za-z0-9+.\-]*://[^/\s:@]*:)(?P<password>[^@\s/]+)(?P<tail>@.*)$")
_SPLIT = re.compile(r"[_\-.\s]+")


def secret_name(name: str) -> bool:
    """Whether a value under *name* is a secret by its name."""
    upper = name.strip().upper()
    parts = [part for part in _SPLIT.split(upper) if part]
    return any(part in _SECRET_PARTS for part in parts) or upper.endswith(("APIKEY", "PASSWORD"))


def _hideable(value: str) -> bool:
    return bool(value) and value.strip().lower() not in _SWITCHES


def mask_value(name: str, value: str) -> str:
    """*value* as the console may see it: hidden when the name says secret, a URL's password hidden."""
    if secret_name(name) and _hideable(value):
        return MASK
    match = _URL_PASSWORD.match(value)
    if match:
        return f"{match['head']}{MASK}{match['tail']}"
    return value


def is_masked(value: str) -> bool:
    """Whether *value* still carries the mask somewhere."""
    return MASK in value


# ── KEY=VALUE lists (container environment, .env text) ────────────────────────────


def _split(item: str) -> tuple[str, str | None]:
    if "=" not in item:
        return item, None
    key, value = item.split("=", 1)
    return key, value


def mask_env(env: Iterable[str]) -> list[str]:
    """``KEY=VALUE`` items with secret values hidden."""
    out = []
    for item in env:
        key, value = _split(item)
        out.append(item if value is None else f"{key}={mask_value(key, value)}")
    return out


def hidden_names(env: Iterable[str]) -> list[str]:
    """The names in ``KEY=VALUE`` items whose value :func:`mask_env` hides (all of it or a URL's password)."""
    names = []
    for item in env:
        key, value = _split(item)
        if value is not None and mask_value(key, value) != value:
            names.append(key)
    return names


class HiddenValueError(ValueError):
    """A hidden value came back for something that has no stored value to put in."""


def restore_value(name: str, submitted: str, stored: str | None) -> str:
    """The value to keep: *stored* when *submitted* is its masked form, otherwise *submitted*."""
    if not is_masked(submitted):
        return submitted
    if stored is not None and mask_value(name, stored) == submitted:
        return stored
    raise HiddenValueError(
        f"The value of {name} is hidden and was changed only in part, or it is new: type the whole value.",
    )


def restore_env(submitted: Iterable[str], stored: Iterable[str]) -> list[str]:
    """``KEY=VALUE`` items as the form sent them, with each hidden value put back from *stored*."""
    old = dict(_split(item) for item in stored)
    out = []
    for item in submitted:
        key, value = _split(item)
        out.append(item if value is None else f"{key}={restore_value(key, value, old.get(key))}")
    return out


def mask_env_text(text: str | None) -> str | None:
    """A ``.env`` file's text with secret values hidden; comments and blank lines as they are."""
    if text is None:
        return None
    lines = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        stripped = body.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(line)
            continue
        indent = body[: len(body) - len(stripped)]
        key, value = stripped.split("=", 1)
        name = key.strip().removeprefix("export ").strip()
        lines.append(f"{indent}{key}={mask_value(name, value)}{ending}")
    return "".join(lines)


def restore_env_text(submitted: str | None, stored: str | None) -> str | None:
    """A ``.env`` text as sent, with each hidden value put back from *stored* (the previous revision)."""
    if submitted is None or not is_masked(submitted):
        return submitted
    old: dict[str, str] = {}
    for line in (stored or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            old[key.strip().removeprefix("export ").strip()] = value
    lines = []
    for line in submitted.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        stripped = body.lstrip()
        if not is_masked(body) or "=" not in stripped:
            lines.append(line)
            continue
        indent = body[: len(body) - len(stripped)]
        key, value = stripped.split("=", 1)
        name = key.strip().removeprefix("export ").strip()
        lines.append(f"{indent}{key}={restore_value(name, value, old.get(name))}{ending}")
    return "".join(lines)


# ── Command lines ─────────────────────────────────────────────────────────────────


def _flag_name(arg: str) -> str | None:
    """``--requirepass`` -> ``requirepass``; not a flag -> ``None``."""
    if not arg.startswith("-") or arg in ("-", "--"):
        return None
    return arg.lstrip("-").split("=", 1)[0]


def mask_cmd(cmd: list[str] | None) -> list[str] | None:
    """A command line with the values of secret flags hidden (``--requirepass x``, ``--password=x``)."""
    if not cmd:
        return cmd
    out: list[str] = []
    hide_next = False
    for arg in cmd:
        flag = _flag_name(arg)
        if hide_next and flag is None:
            # The value after a secret flag.
            out.append(MASK if _hideable(arg) else arg)
            hide_next = False
            continue
        hide_next = False
        if flag is None:
            # A positional KEY=VALUE (some images take settings so).
            key, value = _split(arg)
            out.append(arg if value is None else f"{key}={mask_value(key, value)}")
        elif "=" in arg:
            head, value = arg.split("=", 1)
            if secret_name(flag):
                out.append(f"{head}={MASK}" if _hideable(value) else arg)
            else:
                # --env=DB_PASSWORD=x: the value is itself KEY=VALUE.
                key, inner = _split(value)
                out.append(arg if inner is None else f"{head}={key}={mask_value(key, inner)}")
        else:
            out.append(arg)
            hide_next = secret_name(flag)
    return out


def restore_cmd(submitted: list[str] | None, stored: list[str] | None) -> list[str] | None:
    """A command line as sent, each hidden argument put back from *stored* at the same place."""
    if not submitted or not any(is_masked(arg) for arg in submitted):
        return submitted
    old = stored or []
    old_masked = mask_cmd(old) or []
    out = []
    for index, arg in enumerate(submitted):
        if not is_masked(arg):
            out.append(arg)
        elif index < len(old) and old_masked[index] == arg:
            out.append(old[index])
        else:
            raise HiddenValueError(
                "A hidden argument of the command was changed or moved: type it again in full.",
            )
    return out


# ── Mappings (provider headers and parameters, node environment) ──────────────────


def mask_mapping(values: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A mapping with secret values hidden, nested mappings (``extra_headers``) included."""
    if values is None:
        return None
    out: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, Mapping):
            out[key] = mask_mapping(value)
        elif isinstance(value, str):
            out[key] = mask_value(str(key), value)
        else:
            out[key] = value
    return out


def restore_mapping(
    submitted: Mapping[str, Any] | None, stored: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """A mapping as sent, each hidden value put back from *stored* under the same key."""
    if submitted is None:
        return None
    old = stored or {}
    out: dict[str, Any] = {}
    for key, value in submitted.items():
        before = old.get(key)
        if isinstance(value, Mapping):
            out[key] = restore_mapping(value, before if isinstance(before, Mapping) else None)
        elif isinstance(value, str):
            out[key] = restore_value(str(key), value, before if isinstance(before, str) else None)
        else:
            out[key] = value
    return out
