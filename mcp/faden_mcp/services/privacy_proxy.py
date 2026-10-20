"""Privacy proxy for MCP tool call arguments.

Applies Presidio-based PII detection/redaction before arguments
are sent to upstream MCP servers.
"""

from __future__ import annotations

import logging
import math
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
        self._base = pii_base_url.rstrip("/").removesuffix("/api")
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
            )
            return _refused_unavailable()

    async def _call_pii_service(
        self,
        *,
        arguments: dict[str, Any],
        pii_mode: str,
    ) -> PrivacyDecision:
        """Call the PII service to scan/redact arguments."""
        import json
        import asyncio

        if pii_mode not in ("redact", "block"):
            raise ValueError("Unsupported PII mode")
        pending = [(arguments, 0)]
        nodes = 0
        while pending:
            value, depth = pending.pop()
            nodes += 1
            if nodes > 4096 or depth > 64:
                raise ValueError("PII arguments exceed structural limits")
            children = value.values() if isinstance(value, dict) else value
            pending.extend(
                (child, depth + 1)
                for child in children
                if isinstance(child, (dict, list))
            )
        leaves: list[tuple[Any, Any]] = []

        def walk(value: Any, depth: int = 0) -> None:
            if depth > 64:
                raise ValueError("PII arguments too deeply nested")
            items = value.items() if isinstance(value, dict) else enumerate(value)
            for key, child in items:
                if isinstance(child, str):
                    leaves.append((value, key))
                elif isinstance(child, (dict, list)):
                    walk(child, depth + 1)

        walk(arguments)
        if (
            len(leaves) > 256
            or sum(len(parent[key].encode("utf-8")) for parent, key in leaves) > 524_288
        ):
            raise ValueError("PII arguments exceed text limits")
        encoded = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 1_048_576:
            raise ValueError("PII arguments exceed body limits")
        payload = {
            "payload": {
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "pii_arguments",
                                    "arguments": encoded,
                                },
                            }
                        ],
                    }
                ]
            },
            "mode": "redact",
        }
        async with asyncio.timeout(10):
            resp = await self._http.post(
                f"{self._base}/api/v1/pii/sanitize",
                json=payload,
                timeout=10.0,
                headers={**self._headers, "x-pii-budget-ms": "8000"},
            )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid PII response")
        count, report = data.get("entities_found"), data.get("pii_report")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(report, list)
            or len(report) != count
        ):
            raise ValueError("Invalid PII count/report")
        if any(
            not isinstance(row, dict)
            or not isinstance(row.get("entity_type"), str)
            or type(row.get("start")) is not int
            or type(row.get("end")) is not int
            or not 0 <= row["start"] < row["end"]
            or not isinstance(row.get("text"), str)
            or type(row.get("score")) not in (int, float)
            or not math.isfinite(row["score"])
            or not 0 <= row["score"] <= 1
            for row in report
        ):
            raise ValueError("Invalid PII report")
        version = data.get("contract_version", 1)
        if type(version) is not int or version != 2:
            raise ValueError("Unsupported PII contract")
        if version == 2 and (
            data.get("coverage_status") != "complete"
            or not isinstance(data.get("profile_fingerprint"), str)
            or not data["profile_fingerprint"]
        ):
            raise ValueError("Incomplete PII scan")
        try:
            encoded_result = data["sanitized_payload"]["messages"][0]["tool_calls"][0][
                "function"
            ]["arguments"]
            rebuilt = json.loads(encoded_result)
        except (KeyError, IndexError, TypeError, ValueError, RecursionError) as error:
            raise ValueError("Invalid PII arguments response") from error
        _validate_argument_shape(arguments, rebuilt)
        if pii_mode == "block" and count:
            return PrivacyDecision(
                allowed=False,
                sanitized_args={},
                entities_found=report,
                redaction_summary={"action": "blocked", "count": count},
            )
        return PrivacyDecision(
            allowed=True,
            sanitized_args=arguments if pii_mode == "block" else rebuilt,
            entities_found=report,
            redaction_summary={"action": "redacted", "count": count} if count else {},
        )


def _validate_argument_shape(before: Any, after: Any, depth: int = 0) -> None:
    if depth > 64 or type(before) is not type(after):
        raise ValueError("Invalid PII argument type")
    if isinstance(before, dict):
        if before.keys() != after.keys():
            raise ValueError("Invalid PII argument fields")
        for key, value in before.items():
            _validate_argument_shape(value, after[key], depth + 1)
    elif isinstance(before, list):
        if len(before) != len(after):
            raise ValueError("Invalid PII argument units")
        for left, right in zip(before, after, strict=True):
            _validate_argument_shape(left, right, depth + 1)
    elif not isinstance(before, str) and before != after:
        raise ValueError("Invalid PII argument value")


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
    base = pii_base_url.rstrip("/").removesuffix("/api")
    if not base:
        return "allow"
    try:
        resp = await http_client.get(f"{base}/api/ready", timeout=3.0)
        resp.raise_for_status()
    except Exception:
        logger.info("PII service not reachable — new MCP server gets PII mode allow")
        return "allow"
    return "redact"
