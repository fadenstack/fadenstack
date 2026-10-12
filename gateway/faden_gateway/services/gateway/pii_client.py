"""HTTP client for the PII micro-service.

Used by the gateway pipeline to sanitize payloads before cloud egress
and before observability/audit recording.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from faden_gateway.services.gateway.pii_policy import PIIPolicy

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SanitizeResult:
    """Result of a /sanitize call to the PII service."""

    sanitized_payload: dict[str, Any]
    entities_found: list[dict[str, Any]] = field(default_factory=list)
    pii_detected: bool = False
    token_mapping: dict[str, str] | None = None
    #: What was found, by entity type -- counts only, never the values.
    entity_types: dict[str, int] = field(default_factory=dict)



def _entity_types(report: Any) -> dict[str, int]:
    """Entity type -> count from a PII report; the matched text is not kept."""
    counts: dict[str, int] = {}
    for entity in report or []:
        if isinstance(entity, dict) and entity.get("entity_type"):
            kind = str(entity["entity_type"])
            counts[kind] = counts.get(kind, 0) + 1
    return counts

class PIIClient:
    """Async client wrapping PII service HTTP endpoints."""

    def __init__(self, *, base_url: str, http_client: httpx.AsyncClient, service_token: str = "") -> None:
        self._base = base_url.rstrip("/")
        self._http = http_client
        # The PII service refuses a call without its token; none set, the call
        # goes out bare and fails there (which the policy's fail_action handles).
        self._headers = {"x-pii-source": "gateway"}
        if service_token:
            self._headers["Authorization"] = f"Bearer {service_token}"

    async def sanitize(
        self,
        *,
        payload: dict[str, Any],
        policy: PIIPolicy,
        mode: str | None = None,
        token_mapping: dict[str, str] | None = None,
    ) -> SanitizeResult:
        """Call ``POST /api/v1/pii/sanitize`` on the PII service.

        Parameters
        ----------
        payload:
            Full OpenAI-compatible request body (chat or embeddings).
        policy:
            Parsed ``PIIPolicy`` that determines mode, language, entities,
            and score threshold.
        mode:
            Override mode (``"redact"`` or ``"tokenize"``).
            If *None*, falls back to ``policy.egress.mode``.
        """
        effective_mode = mode or policy.egress.mode
        # Map policy mode names to PII service mode names
        if effective_mode == "tokenize_reversible":
            effective_mode = "tokenize"

        body: dict[str, Any] = {
            "payload": payload,
            "mode": effective_mode,
            "language": policy.presidio.language,
            "score_threshold": policy.presidio.threshold,
        }
        if policy.presidio.entities:
            body["entities"] = policy.presidio.entities
        if token_mapping:
            # Tokens already given in this conversation: kept, and new ones
            # numbered after them, so a tool result uses the question's tokens.
            body["token_mapping"] = token_mapping

        try:
            resp = await self._http.post(
                f"{self._base}/api/v1/pii/sanitize",
                json=body,
                timeout=10.0,
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return SanitizeResult(
                sanitized_payload=data.get("sanitized_payload", payload),
                entities_found=data.get("entities_found", []),
                pii_detected=bool(data.get("entities_found")),
                token_mapping=data.get("token_mapping"),
                entity_types=_entity_types(data.get("pii_report")),
            )
        except Exception:
            logger.exception("PII sanitize call failed")
            raise
