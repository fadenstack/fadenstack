"""Chat answers that would carry no mark are refused, unless an administrator allowed it for the route.

With the provenance module on, a chat request goes only to a route that marks its text: a local runtime
that writes the watermark (``node_metadata["watermark"]``, not switched off by the route's own parameters),
or a remote provider with evidence that it marks (``upstream``). A route an administrator allowed to answer
unmarked answers with a notice: the ``x-faden-unmarked`` header and a ``faden.unmarked`` event. Pictures
are marked by the gateway itself; embeddings, reranking and scores are not content.

The backend publishes what an administrator set for the route in ``node_metadata["text_marking"]``::

    {"upstream": {"vendor": "anthropic", "evidence_url": "https://...", "evidence_date": "2026-10-21"},
     "allow_unmarked": {"reason": "...", "by": "...", "at": "..."}}

Work the AI Act exempts (Art. 50(2): an assistive function for standard editing, or text that does not
substantially alter its input) is declared per request in ``faden.purpose``, by a caller whose roles give
``chat.purpose:declare``: such a request is not refused, and its receipt says ``exempt-edit``.
"""

from __future__ import annotations

from typing import Any

from faden_gateway.services.gateway import text_receipts
from faden_gateway.services.gateway.errors import GatewayError

#: What ``faden.purpose`` may name: editing a text, not writing one.
EXEMPT_PURPOSES = frozenset({"code", "translation", "correction"})
#: The permission a caller's roles give to declare a request's purpose.
DECLARE_PURPOSE = "chat.purpose:declare"
#: The response header of an answer an administrator allowed to be unmarked.
UNMARKED_HEADER = "x-faden-unmarked"


def _policy(candidate: Any) -> dict[str, Any]:
    policy = (getattr(candidate, "node_metadata", None) or {}).get("text_marking")
    return policy if isinstance(policy, dict) else {}


def marks(candidate: Any) -> bool:
    """Whether *candidate*'s answers carry a mark: its runtime's watermark, or its provider's own."""
    if text_receipts.remote(candidate):
        return text_receipts.upstream_mark(candidate) is not None
    return text_receipts.runtime_marks(candidate)


def allowed_unmarked(candidate: Any) -> bool:
    """Whether an administrator let *candidate* answer without a mark."""
    said = _policy(candidate).get("allow_unmarked")
    return isinstance(said, dict) and bool(said.get("reason"))


def purpose(payload: dict[str, Any], *, may_declare: bool) -> str | None:
    """The exempt purpose the request *payload* declares in ``faden.purpose``; ``None`` when it declares none.

    A purpose that is not one of ``EXEMPT_PURPOSES``, or from a caller who may not declare one, is a 400 or
    a 403: never silently ignored, or the caller would think its request exempt.
    """
    faden = payload.get("faden")
    said = faden.get("purpose") if isinstance(faden, dict) else None
    if said is None:
        return None
    if said not in EXEMPT_PURPOSES:
        raise GatewayError(
            status_code=400,
            message=f"faden.purpose is one of {', '.join(sorted(EXEMPT_PURPOSES))}.",
            error_type="invalid_request_error",
            code="invalid_purpose",
            param="faden.purpose",
        )
    if not may_declare:
        raise GatewayError(
            status_code=403,
            message=(
                f"Declaring a request's purpose needs the permission {DECLARE_PURPOSE}; "
                "ask your administrator."
            ),
            error_type="permission_error",
            code="permission_denied",
            param="faden.purpose",
        )
    return str(said)


def admit(candidates: list[Any]) -> list[Any]:
    """*candidates* that mark their answers or are allowed not to; a 403 ``unmarked_endpoint`` when none is.

    The routes that mark come first: an allowed unmarked route answers only when no marking route is there.
    """
    marking = [c for c in candidates if marks(c)]
    allowed = [c for c in candidates if not marks(c) and allowed_unmarked(c)]
    if marking or allowed:
        return marking + allowed
    raise GatewayError(
        status_code=403,
        message=(
            "This model's answers would carry no mark that shows them as AI-generated, and this "
            "installation marks what its models write (EU AI Act Art. 50(2)). An administrator can run "
            "the model on a runtime that writes the watermark, record that its provider marks it, or "
            "allow it to answer unmarked."
        ),
        error_type="permission_error",
        code="unmarked_endpoint",
    )


def notice(candidate: Any) -> dict[str, Any] | None:
    """The ``faden.unmarked`` event for *candidate*'s answer without a mark; ``None`` when it has one."""
    if candidate is None or marks(candidate) or not allowed_unmarked(candidate):
        return None
    return {"type": "CUSTOM", "name": "faden.unmarked", "value": {"reason": "allowed-by-administrator"}}
