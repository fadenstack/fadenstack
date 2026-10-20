"""HTTP client for the PII micro-service.

Used by the gateway pipeline to sanitize payloads before cloud egress
and before observability/audit recording.
"""

from __future__ import annotations

import logging
import asyncio
import json
import math
from dataclasses import dataclass, field
from typing import Any

import httpx

from faden_gateway.services.gateway.pii_policy import PIIPolicy

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SanitizeResult:
    """Result of a /sanitize call to the PII service."""

    sanitized_payload: dict[str, Any]
    entities_found: int = 0
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
        self._base = base_url.rstrip("/").removesuffix("/api")
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
        request_id: str | None = None,
        cache_scope: str = "",
        budget_ms: int = 8_000,
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
            "profile_id": policy.presidio.profile_id,
            "threshold_mode": policy.presidio.threshold_mode,
        }
        if policy.presidio.threshold_mode == "profile":
            body.pop("score_threshold")
        if policy.presidio.entities:
            body["entities"] = policy.presidio.entities
        if token_mapping:
            # Tokens already given in this conversation: kept, and new ones
            # numbered after them, so a tool result uses the question's tokens.
            body["token_mapping"] = token_mapping

        try:
            headers = {**self._headers, "x-pii-budget-ms": str(min(max(budget_ms, 1), 8_000))}
            if request_id:
                headers["x-request-id"] = request_id
            if cache_scope:
                headers["x-pii-scope"] = cache_scope[:128]
            async with asyncio.timeout(10):
                resp = await self._http.post(
                    f"{self._base}/api/v1/pii/sanitize",
                    json=body,
                    timeout=10.0,
                    headers=headers,
                )
            resp.raise_for_status()
            data = resp.json()
            _validate_response(data, effective_mode)
            if data.get("contract_version", 1) < 2 and any(
                isinstance(message, dict) and (message.get("tool_calls") or message.get("function_call"))
                for message in payload.get("messages", [])
            ):
                raise ValueError("PII contract lacks tool argument coverage")
            _validate_shape(payload, data["sanitized_payload"])
            _validate_tool_arguments(payload, data["sanitized_payload"])
            return SanitizeResult(
                sanitized_payload=data["sanitized_payload"],
                entities_found=data["entities_found"],
                pii_detected=data["entities_found"] > 0,
                token_mapping=data.get("token_mapping"),
                entity_types=_entity_types(data.get("pii_report")),
            )
        except Exception:
            # HTTP/validation exceptions can contain response bodies. Never log PII.
            logger.warning("PII sanitize call failed")
            raise


def _validate_shape(original: Any, sanitized: Any, depth: int = 0) -> None:
    """A successful sanitizer cannot drop fields or change argument types."""
    if depth > 64 or type(original) is not type(sanitized):
        raise ValueError("Invalid PII response structure")
    if isinstance(original, dict):
        if original.keys() != sanitized.keys():
            raise ValueError("Invalid PII response fields")
        for key, value in original.items():
            _validate_shape(value, sanitized[key], depth + 1)
    elif isinstance(original, list):
        if len(original) != len(sanitized):
            raise ValueError("Invalid PII response units")
        for before, after in zip(original, sanitized, strict=True):
            _validate_shape(before, after, depth + 1)
    elif not isinstance(original, str) and original != sanitized:
        raise ValueError("Invalid PII response value")


def _validate_tool_arguments(original: dict, sanitized: dict) -> None:
    for before, after in zip(original.get("messages", []), sanitized.get("messages", []), strict=True):
        functions = [
            (left["function"], right["function"])
            for left, right in zip(before.get("tool_calls", []), after.get("tool_calls", []), strict=True)
        ]
        if "function_call" in before:
            functions.append((before["function_call"], after["function_call"]))
        for left, right in functions:
            _validate_shape(json.loads(left["arguments"]), json.loads(right["arguments"]))


def _validate_response(data: Any, mode: str) -> None:
    """Accept the real v1 contract and explicitly complete v2 responses only."""
    if not isinstance(data, dict) or not isinstance(data.get("sanitized_payload"), dict):
        raise ValueError("Invalid PII response payload")
    count, report = data.get("entities_found"), data.get("pii_report")
    if type(count) is not int or count < 0 or not isinstance(report, list) or count != len(report):
        raise ValueError("Invalid PII response count/report")
    for entity in report:
        if (
            not isinstance(entity, dict)
            or not isinstance(entity.get("entity_type"), str)
            or type(entity.get("start")) is not int
            or type(entity.get("end")) is not int
            or not 0 <= entity["start"] < entity["end"]
            or not isinstance(entity.get("text"), str)
            or type(entity.get("score")) not in (int, float)
            or not math.isfinite(entity["score"])
            or not 0 <= entity["score"] <= 1
        ):
            raise ValueError("Invalid PII entity")
    mapping = data.get("token_mapping")
    if mapping is not None and (
        not isinstance(mapping, dict)
        or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items())
    ):
        raise ValueError("Invalid PII token mapping")
    if mode == "tokenize" and count and not mapping:
        raise ValueError("Missing reversible PII mapping")
    version = data.get("contract_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported PII contract")
    if version == 2 and (
        data.get("coverage_status") != "complete" or not isinstance(data.get("profile_fingerprint"), str)
        or not data["profile_fingerprint"]
    ):
        raise ValueError("Incomplete PII coverage")
