"""Privacy proxy for MCP tool call arguments.

Applies Presidio-based PII detection/redaction before arguments
are sent to upstream MCP servers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrivacyDecision:
    """Result of a privacy check on tool call arguments."""

    allowed: bool
    sanitized_args: dict[str, Any]
    entities_found: list[dict[str, Any]] = field(default_factory=list)
    redaction_summary: dict[str, Any] = field(default_factory=dict)


#: Why the check could not run (``redaction_summary["reason"]``).
CHECK_UNAVAILABLE = "check_unavailable"

#: What a refused tool call says, for the operator and the model.
CHECK_UNAVAILABLE_MESSAGE = (
    "The personal-data check for this tool could not run, so the call was "
    "not made and nothing was sent. An administrator can start the PII "
    "service (the 'pii' module), or, if the check is not needed, set this "
    "MCP server's PII mode to Allow."
)


def _refused_unavailable() -> PrivacyDecision:
    return PrivacyDecision(
        allowed=False,
        sanitized_args={},
        redaction_summary={"action": "blocked", "reason": CHECK_UNAVAILABLE},
    )


class PrivacyProxy:
    """Enforces PII scanning before outbound MCP tool calls.

    Fails closed: in ``redact`` and ``block`` mode a call is refused when
    the PII service is not configured, cannot be reached, errors or sends
    an answer that cannot be read.  Passing the original arguments on
    would send exactly the personal data the mode promises to keep back.
    """

    def __init__(
        self,
        *,
        pii_base_url: str,
        http_client: httpx.AsyncClient,
        service_token: str = "",
    ) -> None:
        self._base = pii_base_url.rstrip("/")
        self._http = http_client
        # The PII service refuses a call without its token. None set, the call
        # fails there and is refused here like any other failed check.
        self._headers = {"x-pii-source": "mcp-proxy"}
        if service_token:
            self._headers["Authorization"] = f"Bearer {service_token}"

    async def check(
        self,
        *,
        arguments: dict[str, Any],
        pii_mode: str,
    ) -> PrivacyDecision:
        """Run a PII check on *arguments* according to *pii_mode*.

        Modes:
            ``allow``  — passthrough, no scanning
            ``redact`` — scan and replace PII with redaction markers
            ``block``  — scan and reject if PII is detected
        """
        if pii_mode == "allow":
            return PrivacyDecision(allowed=True, sanitized_args=arguments)

        if not self._base:
            logger.warning("PII service not configured — refusing tool call")
            return _refused_unavailable()

        try:
            return await self._call_pii_service(
                arguments=arguments,
                pii_mode=pii_mode,
            )
        except Exception:
            logger.warning(
                "PII check could not run — refusing tool call",
                exc_info=True,
            )
            return _refused_unavailable()

    async def _call_pii_service(
        self,
        *,
        arguments: dict[str, Any],
        pii_mode: str,
    ) -> PrivacyDecision:
        """Call the PII service to scan/redact arguments."""
        # Build a synthetic payload wrapping the arguments as a
        # user message so Presidio can inspect the text content.
        payload = {
            "payload": {
                "messages": [
                    {
                        "role": "user",
                        "content": _args_to_text(arguments),
                    },
                ],
            },
            "mode": "redact" if pii_mode == "redact" else "detect",
            "language": "en",
        }

        resp = await self._http.post(
            f"{self._base}/api/v1/pii/sanitize",
            json=payload,
            timeout=10.0,
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or "entities_found" not in data:
            raise ValueError("PII service answer has no entities_found")

        raw_entities = data["entities_found"]
        entities_found: list[dict[str, Any]]
        entities_count: int

        if isinstance(raw_entities, list):
            entities_found = [e for e in raw_entities if isinstance(e, dict)]
            entities_count = len(raw_entities)
        elif isinstance(raw_entities, int):
            entities_found = []
            entities_count = max(raw_entities, 0)
        else:
            # Neither a list nor a count: no way to tell whether the
            # arguments hold personal data.
            raise ValueError("PII service answer has an unusable entities_found")

        pii_detected = entities_count > 0

        if pii_mode == "block" and pii_detected:
            return PrivacyDecision(
                allowed=False,
                sanitized_args=arguments,
                entities_found=entities_found,
                redaction_summary={"action": "blocked", "count": entities_count},
            )

        if pii_mode == "redact" and pii_detected:
            sanitized_payload = data.get("sanitized_payload", {})
            sanitized_text = _extract_sanitized_text(sanitized_payload)
            sanitized_args = _text_to_args(sanitized_text)
            if sanitized_args is None:
                # Fail closed: the redacted text is no longer a JSON object,
                # so the original (unredacted) arguments must not be sent.
                return PrivacyDecision(
                    allowed=False,
                    sanitized_args={},
                    entities_found=entities_found,
                    redaction_summary={
                        "action": "blocked",
                        "reason": "unredactable",
                        "count": entities_count,
                    },
                )
            return PrivacyDecision(
                allowed=True,
                sanitized_args=sanitized_args,
                entities_found=entities_found,
                redaction_summary={"action": "redacted", "count": entities_count},
            )

        return PrivacyDecision(allowed=True, sanitized_args=arguments)


async def default_pii_mode(
    *,
    configured: str,
    pii_base_url: str,
    http_client: httpx.AsyncClient,
) -> str:
    """The PII mode a new server gets when the request names none.

    ``configured`` (``FADEN_MCP_DEFAULT_PII_MODE``) wins when set.  Otherwise
    ``redact`` if the PII service answers its health check, ``allow`` if it
    does not: without the service every ``redact`` call would be refused, and
    an install that never started the optional ``pii`` module would get a
    server that cannot be used.  Saved servers keep their mode.
    """
    if configured in ("allow", "redact", "block"):
        return configured
    base = pii_base_url.rstrip("/")
    if not base:
        return "allow"
    try:
        resp = await http_client.get(f"{base}/api/health", timeout=3.0)
        resp.raise_for_status()
    except Exception:
        logger.info("PII service not reachable — new MCP server gets PII mode allow")
        return "allow"
    return "redact"


def _args_to_text(arguments: dict[str, Any]) -> str:
    """Flatten tool arguments into a single text string for PII scanning."""
    import json

    return json.dumps(arguments, ensure_ascii=False)


def _extract_sanitized_text(sanitized_payload: dict[str, Any]) -> str:
    """Extract the sanitized text from the PII service response payload."""
    messages = sanitized_payload.get("messages", [])
    if messages:
        return messages[0].get("content", "")
    return ""


def _text_to_args(sanitized_text: str) -> dict[str, Any] | None:
    """Reconstruct arguments from sanitized text.

    Returns ``None`` when the redacted text no longer parses back into a
    JSON object.  The caller must then refuse the call: the original
    arguments still contain the personal data.
    """
    import json

    try:
        parsed = json.loads(sanitized_text)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
