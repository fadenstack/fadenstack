"""Secrets in a command's payload: sealed where the command is stored, opened only for its machine.

A model's engine settings travel to the machine in its command (``RUN_SERVE_APP``): with the provenance module
on, they hold the installation's text watermark key (``engine_kwargs.watermark_config.key``,
``services/provenance/watermark.py``). Stored as it came, it sat in plain text in the command's row, and the
console's command pages -- readable by the viewer and operator roles, which see no secret anywhere else --
returned it. Now:

- :func:`seal` replaces the key with ``key_sealed`` (the settings key's encryption, as a takeover's cluster
  token is sealed) before the command is stored;
- :func:`for_machine` opens it again only for the payload sent to the machine (the dispatch, signed there);
- :func:`for_people` shows ``key_sealed`` (and a key stored in plain text before this) as ``"sealed"``.
"""

from __future__ import annotations

from typing import Any

#: The engine setting that holds a key.
WATERMARK = "watermark_config"
SEALED = "key_sealed"
SHOWN = "sealed"


def _walk(value: Any, change: Any) -> Any:
    if isinstance(value, dict):
        out = {k: _walk(v, change) for k, v in value.items()}
        config = out.get(WATERMARK)
        if isinstance(config, dict):
            out[WATERMARK] = change(dict(config))
        return out
    if isinstance(value, list):
        return [_walk(v, change) for v in value]
    return value


def seal(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """*payload* with every watermark key sealed; unchanged when it holds none."""
    from faden_backend.services.inference.drivers.ray.secrets import seal_token  # noqa: PLC0415

    def sealed(config: dict[str, Any]) -> dict[str, Any]:
        if "key" in config:
            config[SEALED] = seal_token(str(config.pop("key")))
        return config

    return _walk(payload, sealed) if payload else payload


def for_machine(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """*payload* as the machine needs it: every sealed watermark key opened (the integer vLLM takes)."""
    from faden_backend.services.inference.drivers.ray.secrets import unseal_token  # noqa: PLC0415

    def opened(config: dict[str, Any]) -> dict[str, Any]:
        if SEALED in config:
            plain = unseal_token(str(config.pop(SEALED)))
            if plain is not None and plain.isdigit():
                config["key"] = int(plain)
        return config

    return _walk(payload, opened) if payload else payload


def for_people(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """*payload* as the console and the API show it: no watermark key, sealed or plain."""

    def shown(config: dict[str, Any]) -> dict[str, Any]:
        for name in ("key", SEALED):
            if name in config:
                config[name] = SHOWN
        return config

    return _walk(payload, shown) if payload else payload
