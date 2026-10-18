"""Generated pictures leave the gateway marked as made by AI, or not at all.

With the provenance module on, each picture an answer carries goes to the
provenance engine (``ProvenanceClient.mark_image``), which embeds a watermark
and signed metadata and keeps a receipt; the answer goes out in its own shape
with the marked bytes in place of the made ones, and the receipts in
``X-Faden-Provenance``. A picture the engine cannot mark fails the whole
request: 503, ``provenance_unavailable``, and none of its pictures is returned.
So does a picture given as a link, which the gateway has no bytes of to mark.

The answers that carry pictures: an images answer (``data[].b64_json``), and
a chat answer whose model answers with pictures (LiteLLM's ``message.images``,
or ``image_url`` parts of a list ``content``).
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass, field
from typing import Any

from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.services.gateway.provenance_client import EditKind, ProvenanceClient, ProvenanceMarkError

logger = logging.getLogger(__name__)

#: An answer's receipts, comma-separated: its text's first, when it has text (``text_receipts``), then
#: those of the pictures it carries, in their order.
HEADER = "x-faden-provenance"

#: What the caller reads when a picture could not be marked; the gateway's log has the reason.
UNMARKED_MESSAGE = (
    "The picture could not be marked as AI-generated, so it was not returned. "
    "Try again later, or ask the administrator to check the provenance service."
)


def media_type_of(data: bytes) -> str | None:
    """The picture's type by its first bytes: PNG, JPEG or WebP, the ones the engine marks; else ``None``."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass(slots=True)
class _Marker:
    """Marks one answer's pictures in turn and keeps their receipts; the first failure fails them all."""

    client: ProvenanceClient
    model: str
    agent: str | None
    edit_kind: EditKind
    request_id: str
    receipts: list[str] = field(default_factory=list)

    def failed(self, code: str, reason: str) -> GatewayError:
        """The request's error, with one log line for why (never the picture)."""
        logger.warning("Request %s: a picture could not be marked (%s): %s", self.request_id, code, reason)
        return GatewayError(
            status_code=503, message=UNMARKED_MESSAGE, error_type="provenance_unavailable", code=code,
        )

    async def mark(self, encoded: str) -> tuple[str, str]:
        """The picture in base64 *encoded*, marked: as base64, and its media type."""
        try:
            data = base64.b64decode(encoded)
        except (binascii.Error, ValueError):
            raise self.failed("not_a_picture", "the answer's picture is not base64") from None
        media_type = media_type_of(data)
        if media_type is None:
            raise self.failed("unsupported_media_type", "the picture is not PNG, JPEG or WebP")
        try:
            marked = await self.client.mark_image(
                data, media_type=media_type, model=self.model, agent=self.agent, edit_kind=self.edit_kind,
            )
        except ProvenanceMarkError as exc:
            raise self.failed(exc.code, str(exc)) from exc
        self.receipts.append(marked.receipt_id)
        return base64.b64encode(marked.image).decode("ascii"), marked.media_type

    async def part(self, part: Any) -> Any:
        """A chat answer's picture (``{"image_url": {"url": "data:..."}}``) marked; else as it came."""
        if not isinstance(part, dict) or "image_url" not in part:
            return part
        image_url = part["image_url"]
        url = image_url.get("url") if isinstance(image_url, dict) else image_url
        if not isinstance(url, str) or not url:
            return part
        if not url.startswith("data:"):
            raise self.failed("picture_as_link", "the model answered with a link, not the picture")
        head, _, encoded = url.partition(",")
        if not head.endswith(";base64"):
            raise self.failed("unsupported_media_type", "the picture's data URL is not base64")
        marked, media_type = await self.mark(encoded)
        marked_url = f"data:{media_type};base64,{marked}"
        if isinstance(image_url, dict):
            return {**part, "image_url": {**image_url, "url": marked_url}}
        return {**part, "image_url": marked_url}


async def mark_images(
    client: ProvenanceClient,
    payload: dict[str, Any],
    *,
    model: str,
    agent: str | None,
    request_id: str,
    edit_kind: EditKind = "generated",
) -> tuple[dict[str, Any], list[str]]:
    """An images answer with each ``b64_json`` picture marked, and the receipts in order.

    A picture given as ``url`` fails the request: there are no bytes to mark,
    and the gateway has nowhere to put marked ones for a link to point to.
    """
    data = payload.get("data")
    if not isinstance(data, list):
        return payload, []
    marker = _Marker(client=client, model=model, agent=agent, edit_kind=edit_kind, request_id=request_id)
    marked: list[Any] = []
    for item in data:
        encoded = item.get("b64_json") if isinstance(item, dict) else None
        if encoded:
            picture, _ = await marker.mark(str(encoded))
            marked.append({**item, "b64_json": picture})
        elif isinstance(item, dict) and item.get("url"):
            raise marker.failed("picture_as_link", "the image server answered with a link, not the picture")
        else:
            marked.append(item)
    return {**payload, "data": marked}, marker.receipts


async def mark_chat(
    client: ProvenanceClient,
    payload: dict[str, Any],
    *,
    model: str,
    agent: str | None,
    request_id: str,
) -> tuple[dict[str, Any], list[str]]:
    """A chat answer with each picture its messages carry marked, and the receipts in order.

    An answer without pictures -- nearly every one -- comes back as it is.
    """
    choices = payload.get("choices")
    if not isinstance(choices, list):
        return payload, []
    marker = _Marker(client=client, model=model, agent=agent, edit_kind="generated", request_id=request_id)
    marked: list[Any] = []
    for choice in choices:
        message = choice.get("message") if isinstance(choice, dict) else None
        if not isinstance(message, dict):
            marked.append(choice)
            continue
        shaped = dict(message)
        if isinstance(message.get("images"), list):
            shaped["images"] = [await marker.part(p) for p in message["images"]]
        if isinstance(message.get("content"), list):
            shaped["content"] = [await marker.part(p) for p in message["content"]]
        marked.append({**choice, "message": shaped})
    if not marker.receipts:
        return payload, []
    return {**payload, "choices": marked}, marker.receipts
