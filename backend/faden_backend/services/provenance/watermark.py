"""The text watermark the model runtimes put into answers (vLLM's ``watermark_config``).

vLLM 0.30 and later watermark the text a model generates when the engine is started with a key: each token
is chosen with a pseudorandom function of the key and the tokens before it (Gumbel-max), which leaves the
output's distribution as it was and lets whoever holds the key find the mark in a long enough text. The CLI
makes the keys (``FADEN_PROVENANCE_WATERMARK_KEYS``, ``<version>:<whole number>``, every version kept); with
the provenance module on, the backend gives the current one to the chat deployments it starts on a runtime
that has the watermark, and publishes which version and how (``published``), never the key, so the gateway's
receipt for an answer names them and the provenance engine checks a text with the same key later.

- Only chat models: an embedding, scoring or picture model generates no text.
- With speculative decoding (MTP, EAGLE) vLLM needs ``dual_key_gumbel`` and probabilistic draft sampling,
  which this sets; parallel drafting or another method has no watermark, and the deployment starts without.
- A deployment keeps the key version it was started with until it is applied again for another reason
  (``RayDeploymentManager``): a new version from ``faden secrets rotate`` restarts no model by itself.
- ``engine.config`` may switch the watermark off for one deployment (``watermark_config: false``), a
  superuser setting (``engine_guard``); the receipts of its answers say so. A key in the spec is never used.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: The first vLLM with the watermark.
MIN_VLLM = (0, 30)
#: Tokens before each one the pseudorandom function reads (vLLM's default).
CONTEXT_WIDTH = 4
#: The speculative methods vLLM watermarks: autoregressive, model-based drafts.
_SPECULATIVE_METHODS = frozenset({"mtp", "eagle", "eagle3", "dspark"})
#: What an operator may set of it besides switching it off: how it reads the context, never its key.
_OPERATOR_FIELDS = ("context_width", "deduplicate_contexts", "deduplicate_contexts_max_history")
_OFF = frozenset({"false", "off", "none", "no", "0"})
_VERSION = re.compile(r"\s*v?(\d+)\.(\d+)")


class WatermarkKeyError(ValueError):
    """``FADEN_BACKEND_PROVENANCE_WATERMARK_KEYS`` does not read; the message never holds a key."""


@dataclass(frozen=True)
class WatermarkKeys:
    """The installation's watermark keys, by version."""

    versions: dict[int, int]

    @property
    def current(self) -> int | None:
        """The version new deployments are started with: the highest."""
        return max(self.versions) if self.versions else None

    def pick(self, pinned: int | None) -> tuple[int, int] | None:
        """(version, key): *pinned* while it is still kept, else the current one; ``None`` without keys."""
        version = pinned if pinned in self.versions else self.current
        return (version, self.versions[version]) if version is not None else None


def parse_keys(value: str) -> WatermarkKeys:
    """``"1:<whole number>,2:<whole number>"``, as the CLI writes it."""
    versions: dict[int, int] = {}
    for part in (p.strip() for p in (value or "").split(",") if p.strip()):
        number, _, key = part.partition(":")
        number, key = number.strip(), key.strip()
        if not number.isdigit() or not key.isdigit() or not 0 < int(key) < 2**64:
            raise WatermarkKeyError(
                "FADEN_BACKEND_PROVENANCE_WATERMARK_KEYS is not of the form <version>:<whole number>,..."
            )
        versions[int(number)] = int(key)
    return WatermarkKeys(versions)


def installed_keys() -> WatermarkKeys | None:
    """The keys the backend was given, with the provenance module on; else, or unreadable, ``None``."""
    from faden_backend.settings import settings  # noqa: PLC0415

    if not settings.provenance_enabled or not settings.provenance_watermark_keys.strip():
        return None
    try:
        keys = parse_keys(settings.provenance_watermark_keys)
    except WatermarkKeyError as exc:
        log.warning("%s Models start without the text watermark.", exc)
        return None
    return keys if keys.versions else None


def runtime_has_it(vllm_version: str | None) -> bool:
    """Whether a runtime of this vLLM version can watermark (0.30 and later; ``None``: not known, no)."""
    found = _VERSION.match(vllm_version or "")
    if found is None:
        return False
    return (int(found.group(1)), int(found.group(2))) >= MIN_VLLM


def switched_off(value: Any) -> bool:
    """Whether a spec's ``watermark_config`` switches the watermark off for its deployment."""
    return value is False or (isinstance(value, str) and value.strip().lower() in _OFF)


def _speculative(kwargs: dict[str, Any]) -> dict[str, Any] | None:
    value = kwargs.get("speculative_config")
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else None
        except ValueError:
            return None
    return dict(value) if isinstance(value, dict) and value else None


def apply(kwargs: dict[str, Any], key: tuple[int, int]) -> dict[str, Any] | None:
    """Start the engine of *kwargs* with the watermark key *key* (version, key), in place; what to publish.

    Returns ``{"algorithm", "key_version", "context_width"}``, or ``None`` when the deployment starts
    without the watermark: switched off in its spec, or a speculative decoding vLLM does not watermark.
    """
    asked = kwargs.pop("watermark_config", None)
    if switched_off(asked):
        return None
    if isinstance(asked, str):
        try:
            asked = json.loads(asked) if asked.strip() else None
        except ValueError:
            asked = None
    config: dict[str, Any] = {"algorithm": "gumbel", "context_width": CONTEXT_WIDTH}
    if isinstance(asked, dict):
        config.update({field: asked[field] for field in _OPERATOR_FIELDS if field in asked})
    speculative = _speculative(kwargs)
    if speculative is not None:
        method = str(speculative.get("method") or "").lower()
        if method not in _SPECULATIVE_METHODS or speculative.get("parallel_drafting"):
            log.info("The text watermark is left out: vLLM does not watermark this speculative decoding.")
            return None
        # Draft tokens carry the watermark of one key, the rest of the other; vLLM accepts no other way.
        config["algorithm"] = "dual_key_gumbel"
        speculative["draft_sample_method"] = "probabilistic"
        speculative["rejection_sample_method"] = "standard"
        kwargs["speculative_config"] = speculative
    version, secret = key
    kwargs["watermark_config"] = {**config, "key": secret}
    return {
        "algorithm": config["algorithm"],
        "key_version": version,
        "context_width": int(config["context_width"]),
    }


def leave_out(kwargs: dict[str, Any]) -> None:
    """Without the watermark: a spec's own ``watermark_config`` never reaches the engine (it holds a key)."""
    kwargs.pop("watermark_config", None)


def published(
    llm_serving_args: dict[str, Any], keys: WatermarkKeys | None, tokenizer: str | None = None,
) -> dict[str, Any] | None:
    """How a compiled deployment watermarks (algorithm, key version, context width; never the key).

    *tokenizer* is the model repository whose tokenizer it generates with: the provenance engine checks a
    text with it (the name the gateway routes by can be an alias).
    """
    for config in llm_serving_args.get("llm_configs") or []:
        engine = (config.get("engine_kwargs") if isinstance(config, dict) else None) or {}
        mark = engine.get("watermark_config") if isinstance(engine, dict) else None
        if not isinstance(mark, dict) or "key" not in mark:
            continue
        version = next((v for v, k in (keys.versions.items() if keys else ()) if k == mark["key"]), None)
        found: dict[str, Any] = {
            "algorithm": str(mark.get("algorithm") or "gumbel"),
            "key_version": version,
            "context_width": int(mark.get("context_width") or CONTEXT_WIDTH),
        }
        if tokenizer:
            found["tokenizer"] = tokenizer
        return found
    return None
