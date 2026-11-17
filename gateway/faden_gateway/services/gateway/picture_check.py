"""Pictures by what they are, and no multimodal handles: what a chat's pictures may be when they leave.

vLLM 0.31.0 opens a picture with Pillow's format detection, which goes by the file's own first bytes, not by
the label of its ``data:`` URL: an EPS file labelled ``image/png`` is opened as EPS, which Pillow hands to
Ghostscript where the image has it (GHSA-h3rc-j82p-2c3h, fixed in vLLM 0.32.0). The runtime images hold no
Ghostscript. The gateway still lets through only the four picture formats every model server and provider
takes, by their first bytes: PNG, JPEG, WebP and GIF. Anything else in a picture part is refused with a 400
before the model is asked (release plan §12.3).

vLLM also lets a request name a multimodal item instead of sending it: a media part's ``uuid`` names an
entry of its processor cache, and the routes of its token-in mode take items by shared-memory handle
(``features``, ``kwargs_data``, ``mm_hashes``; GHSA-6757-hggm-hp2w, a forged handle reaches ``pickle.loads``
on a worker). The gateway serves none of those routes; what a client sends it names no cache entry either:
a part's ``uuid`` and those fields are dropped, and a picture part without data is refused. Parts that carry
serialized embeddings (``image_embeds``, ``audio_embeds``) are refused: they are tensors, not pictures.

Runs on what leaves, after ``media_fetch.inline``: then every picture the gateway fetched, and every one the
chat adds, is a ``data:`` URL. With ``media_url_check`` off, a picture URL goes on as it is and the model
server fetches it; that one cannot be checked here.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterator
from typing import Any
from urllib.parse import unquote_to_bytes

from faden_gateway.services.gateway import media_urls
from faden_gateway.services.gateway.errors import GatewayError

#: The formats a picture may be, by their first bytes.
ALLOWED = ("PNG", "JPEG", "WebP", "GIF")
#: Top-level request fields that name multimodal items held elsewhere (vLLM's token-in routes).
HANDLE_FIELDS = frozenset({"features", "kwargs_data", "mm_hashes", "multi_modal_uuids", "multi_modal_data"})
#: Part keys that name a cached multimodal item instead of carrying it.
_PART_HANDLES = frozenset({"uuid"})
#: Part types that carry serialized tensors.
_EMBEDS = frozenset({"image_embeds", "audio_embeds"})
#: How much of a picture is read: enough for every signature below.
_HEAD = 16


def kind_of(head: bytes) -> str | None:
    """The allowed format *head* starts with, or ``None``."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WebP"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    return None


def _named(head: bytes) -> str:
    """What a refused picture looks like, for the message."""
    known = (
        ((b"%!PS", b"\xc5\xd0\xd3\xc6"), "PostScript (EPS)"), ((b"%PDF",), "PDF"), ((b"BM",), "BMP"),
        ((b"II*\x00", b"MM\x00*"), "TIFF"), ((b"<svg", b"<?xml"), "SVG or XML"),
    )
    stripped = head.lstrip()
    for signatures, name in known:
        if any(stripped.startswith(s) for s in signatures):
            return name
    return "not a picture of these formats"


def _head(text: str) -> bytes | None:
    """The first bytes of a picture given as a ``data:`` URL or bare base64; ``None`` for a URL to fetch."""
    value = text.strip()
    if value[:5].lower() == "data:":
        meta, sep, data = value[5:].partition(",")
        if not sep:
            return b""
        if meta.lower().endswith(";base64"):
            return _b64_head(data)
        return unquote_to_bytes(data[: _HEAD * 3])[:_HEAD]
    if media_urls._is_source(value):  # noqa: SLF001 -- the same test media_urls uses for a URL
        return None
    return _b64_head(value)


def _b64_head(data: str) -> bytes:
    chunk = "".join(data[: _HEAD * 2].split())
    chunk = chunk[: len(chunk) - len(chunk) % 4]
    try:
        return base64.b64decode(chunk, validate=True)[:_HEAD]
    except (binascii.Error, ValueError):
        return b""


def _parts(messages: Any) -> Iterator[tuple[int, int, dict[str, Any]]]:
    if not isinstance(messages, list):
        return
    for m, message in enumerate(messages):
        content = message.get("content") if isinstance(message, dict) else None
        parts = [content] if isinstance(content, dict) else content
        if isinstance(parts, list):
            for p, part in enumerate(parts):
                if isinstance(part, dict):
                    yield m, p, part


def _refused(where: str, message: str, code: str) -> GatewayError:
    return GatewayError(
        status_code=400, message=f"{where}: {message}", error_type="invalid_request_error", code=code,
        param=where,
    )


def _check_picture(where: str, part: dict[str, Any]) -> None:
    # The walk media_urls makes through a part, without what names a cached item (a uuid is not data).
    sources = [text for _path, text in media_urls._strings(_without_handles(part), where)]  # noqa: SLF001
    heads = [_head(text) for text in sources if text.strip()]
    if not heads:
        raise _refused(where, "a picture part carries no picture.", "picture_missing")
    for head in heads:
        if head is None:
            continue  # a URL: only with media_url_check off (see the module's text)
        if kind_of(head) is None:
            formats = f"{', '.join(ALLOWED[:-1])} and {ALLOWED[-1]}"
            why = f"only {formats} pictures are taken; this one is {_named(head)}."
            raise _refused(where, why, "picture_type_not_allowed")


def _without_handles(part: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in part.items() if key not in _PART_HANDLES}


def checked(payload: dict[str, Any]) -> dict[str, Any]:
    """*payload* with no multimodal handle in it; :class:`GatewayError` (400) for a picture not allowed.

    Copied where something is dropped, never changed in place; *payload* itself when nothing is.
    """
    messages = payload.get("messages")
    out_messages: list[Any] | None = None
    for m, p, part in _parts(messages):
        where = f"messages[{m}].content[{p}]"
        if part.get("type") in _EMBEDS or any(key in part for key in _EMBEDS):
            why = "embeddings are not taken as input; send the picture itself."
            raise _refused(where, why, "embeds_not_allowed")
        if not (media_urls._is_media_part(part)):  # noqa: SLF001
            continue
        if media_urls.part_kind(part) == "image":
            _check_picture(where, part)
        if _PART_HANDLES.isdisjoint(part):
            continue
        out_messages = list(messages) if out_messages is None else out_messages
        message = dict(out_messages[m])
        content = message["content"]
        if isinstance(content, dict):
            message["content"] = _without_handles(content)
        else:
            content = list(content)
            content[p] = _without_handles(part)
            message["content"] = content
        out_messages[m] = message
    dropped = HANDLE_FIELDS.intersection(payload)
    if out_messages is None and not dropped:
        return payload
    out = {key: value for key, value in payload.items() if key not in HANDLE_FIELDS}
    if out_messages is not None:
        out["messages"] = out_messages
    return out
