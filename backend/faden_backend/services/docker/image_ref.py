"""A container image reference, as Docker writes one.

``ghcr.io/org/name:tag``, ``localhost:5000/name``, ``name@sha256:...``: the
machines' agents put the image of a runtime into ``docker run`` right before
its command, so a value that starts with ``-`` is read there as an option
(``--privileged``) and the next word as the image. A reference starts with a
letter or a digit and has only the characters Docker's grammar uses.
"""

from __future__ import annotations

import re
from typing import Any

#: The same rule as the agent's (``faden_agent/runtimes/__init__.py``).
IMAGE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,511}")


def is_image_ref(value: Any) -> bool:
    """Whether *value* is an image reference."""
    return isinstance(value, str) and IMAGE_REF.fullmatch(value) is not None


def check_image_ref(value: Any, *, field: str = "image") -> str:
    """*value* if it is an image reference; ``ValueError`` saying what is wrong otherwise."""
    if not is_image_ref(value):
        msg = (
            f"{field} is not an image reference: it starts with a letter or a digit and has only letters, "
            "digits and . _ / : @ - (for example ghcr.io/org/name:tag or name@sha256:...)"
        )
        raise ValueError(msg)
    return value


def check_runtime_images(payload: dict[str, Any]) -> None:
    """The images a runtime's settings or a node command carry: ``image`` and ``provider_config.image``.

    An empty or missing one is no image (the default one is used).
    """
    if payload.get("image") not in (None, ""):
        check_image_ref(payload["image"])
    provider_config = payload.get("provider_config")
    if isinstance(provider_config, dict) and provider_config.get("image") not in (None, ""):
        check_image_ref(provider_config["image"], field="provider_config.image")
