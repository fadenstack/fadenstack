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


class PrivacyProxy:
    """Enforces PII scanning before outbound MCP tool calls.

    When the PII service is unavailable, the proxy falls back to
    ``allow`` mode — it will not block tool calls but will log a
    warning.
    """

    def __init__(
        self,
        *,
        pii_base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._base = pii_base_url.rstrip("/")
        self._http = http_client

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
        if pii_mode == "allow" or not self._base:
            return PrivacyDecision(allowed=True, sanitized_args=arguments)

        try:
            return await self._call_pii_service(
                arguments=arguments,
                pii_mode=pii_mode,
            )
        except Exception:
            logger.warning(
                "PII service unreachable — falling back to allow",
                exc_info=True,
            )
            return PrivacyDecision(allowed=True, sanitized_args=arguments)

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
            headers={"x-pii-source": "mcp-proxy"},
        )
        resp.raise_for_status()
        data = resp.json()

        raw_entities = data.get("entities_found", [])
        entities_found: list[dict[str, Any]]
        entities_count: int

        if isinstance(raw_entities, list):
            entities_found = [e for e in raw_entities if isinstance(e, dict)]
            entities_count = len(raw_entities)
        elif isinstance(raw_entities, int):
            entities_found = []
            entities_count = max(raw_entities, 0)
        else:
            entities_found = []
            entities_count = 0

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
