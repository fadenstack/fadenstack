"""Image generation is off in this version: no picture is made, and none a model makes leaves the gateway.

From 2 December 2026 the EU AI Act prohibits placing on the market a system that can foreseeably generate
non-consensual intimate imagery or child sexual abuse material without reasonable and adequate safeguards
(Art. 5(1)(ba), (bb) and 5(1a), as Regulation 2026/1744 adds them). This version has no such safeguard, so
image generation is off for every install, upgrades included.

``ENABLED`` is the one switch. There is no setting, environment variable or administrator's switch that turns
it on: a later version flips it here, together with its safeguard (a prompt check and an output check).
The backend's console side mirrors it (``faden_backend.services.llm.image_generation``); a test there fails
when the two differ.

While it is off:

- ``/v1/images/generations`` and an agent's ``/v1/agents/{agent}/images/generations`` refuse every request
  before anything runs (:func:`refusal`): no model is asked and nothing is charged;
- a chat request that asks for pictures (``modalities`` with ``image``) is refused the same way;
- a chat answer that carries a picture anyway (a model that answers with pictures: LiteLLM's
  ``message.images`` or ``image_url`` parts, whole or streamed) is refused in its place: a whole answer
  fails, a stream ends with the refusal before the picture's event (:func:`refuse_pictures`);
- an agent's profile says ``images: false``, so the SDKs offer no picture tools.

Image deployments a server already runs are not touched; they are simply never asked.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, aclosing, nullcontext
from typing import Any, Final

from faden_gateway.services.gateway.errors import GatewayError

#: Whether this version makes pictures. Off: see the module's docstring. Tests may patch it.
ENABLED: Final[bool] = False

#: The refusal's stable code, for clients to tell it from every other error.
CODE: Final = "images_disabled"

#: What every refusal says (the owner's text, 16 Nov 2026); the console shows it in each of its languages.
NOTE: Final = (
    "Image generation is turned off in this version. From 2 December 2026 the EU AI Act (Article 5) requires "
    "safeguards against generating harmful images, and this version does not have them yet. Image generation "
    "will return in a later version, with those safeguards."
)


def refusal() -> GatewayError:
    """The error every refused request and answer gets: 403, ``invalid_request_error``, ``images_disabled``.

    One instance per refusal: the error is raised, and an exception keeps its traceback.
    """
    return GatewayError(status_code=403, message=NOTE, error_type="invalid_request_error", code=CODE)


def asks_for_pictures(payload: dict[str, Any]) -> bool:
    """Whether a chat request asks for pictures in its answer (``"modalities": ["text", "image"]``)."""
    modalities = payload.get("modalities")
    if isinstance(modalities, str):
        modalities = [modalities]
    return isinstance(modalities, list) and any(str(m).strip().lower() == "image" for m in modalities)


def _carries(message: Any) -> bool:
    """Whether a chat answer's message or delta holds a picture: ``images``, or an ``image_url`` part."""
    if not isinstance(message, dict):
        return False
    images = message.get("images")
    if isinstance(images, list) and images:
        return True
    content = message.get("content")
    return isinstance(content, list) and any(
        isinstance(part, dict) and "image_url" in part for part in content
    )


def answer_carries_pictures(payload: Any) -> bool:
    """Whether a whole chat answer carries a picture in any of its choices."""
    choices = payload.get("choices") if isinstance(payload, dict) else None
    return isinstance(choices, list) and any(
        isinstance(choice, dict) and _carries(choice.get("message")) for choice in choices
    )


#: What an event that carries a picture contains; any other event passes as it came, never parsed.
_PICTURE_SIGNS = (b"image_url", b'"images"')


def _event_carries_pictures(event: bytes) -> bool:
    """Whether one SSE event (without its blank line) carries a picture in a delta."""
    if not any(sign in event for sign in _PICTURE_SIGNS):
        return False
    for line in event.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            body = json.loads(line[len("data:"):].strip())
        except ValueError:
            return False
        choices = body.get("choices") if isinstance(body, dict) else None
        return isinstance(choices, list) and any(
            isinstance(choice, dict) and _carries(choice.get("delta")) for choice in choices
        )
    return False


def _refused_event() -> bytes:
    error = refusal()
    body = {"error": {"message": error.message, "type": error.error_type, "code": error.code}}
    return f"data: {json.dumps(body)}\n\n".encode() + b"data: [DONE]\n\n"


async def refuse_pictures(stream: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """A streamed chat answer as it came, up to an event that carries a picture: it then ends refused.

    Events are passed on whole (the stream is cut at event boundaries); only an event that names a picture is
    read. Neither the picture's event nor anything after it leaves, and the model's stream is closed.
    """
    pending = b""
    source: AbstractAsyncContextManager[AsyncIterator[bytes]] = (
        aclosing(stream) if hasattr(stream, "aclose") else nullcontext(stream)  # type: ignore[type-var]
    )
    async with source as chunks:
        async for chunk in chunks:
            pending += chunk
            end = pending.rfind(b"\n\n")
            if end < 0:
                continue
            whole, pending = pending[:end + 2], pending[end + 2:]
            passed, refused = _until_a_picture(whole)
            if passed:
                yield passed
            if refused:
                yield _refused_event()
                return
        if pending:
            passed, refused = _until_a_picture(pending)
            if passed:
                yield passed
            if refused:
                yield _refused_event()


def _until_a_picture(whole: bytes) -> tuple[bytes, bool]:
    """The events of *whole* before the first that carries a picture, and whether one did."""
    if not any(sign in whole for sign in _PICTURE_SIGNS):
        return whole, False
    events = whole.split(b"\n\n")
    kept: list[bytes] = []
    for event in events:
        if event.strip() and _event_carries_pictures(event):
            return b"".join(e + b"\n\n" for e in kept), True
        kept.append(event)
    return whole, False
