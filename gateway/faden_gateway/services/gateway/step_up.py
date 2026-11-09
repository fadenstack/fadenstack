"""The proof of a fresh sign-in that a Bypass grant needs (sdk/41; OWASP ASVS 5.0 7.5.3).

Switching Bypass on is a highly sensitive operation: the person signs in again in the browser, and the backend
hands the app a short proof of that sign-in once (``backend/faden_backend/services/agents/step_up.py``). The app
sends it with the grant request in ``x-faden-step-up``; the gateway makes a Bypass grant only with a proof that

- the backend signed for this purpose: an HS256 JWT under a key derived from the shared secret by a fixed label,
  so it is never valid as a sign-in, and no sign-in or other grant is valid as it;
- names the tenant, the person, the agent and the device of the caller's agent token;
- is at most :data:`MAX_AGE_S` seconds old by this server's clock, counted from the sign-in (``auth_time``).

One proof covers that person's switch-ons on that device and agent while it is fresh: parallel chats need one
sign-in. A Bypass that resumes after the app's pause needs none (it was confirmed when it began). Without a good
proof the answer is a challenge in the manner of RFC 9470: 401, ``WWW-Authenticate: Bearer
error="insufficient_user_authentication", max_age="300"``, and the code ``bypass_step_up_required`` -- and no
grant (ASVS 5.0 16.5.3: no fail-open). The proof is never logged or stored; the grant keeps how and when the
person signed in (sdk/09 L-59).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import jwt

from faden_gateway.services.gateway.errors import GatewayError
from faden_gateway.settings import settings

logger = logging.getLogger(__name__)

#: The request header the proof comes in. Never a body field: a refused body may be echoed back in full.
HEADER = "x-faden-step-up"
#: How old the sign-in may be (owner, 2026-11-09: five minutes), by this server's clock.
MAX_AGE_S = 300
#: A sign-in dated this far ahead of this clock still counts (the backend's and this clock may differ a little).
SKEW_S = 30
#: The proof's ``typ`` and ``aud``; the label its key is derived with.
TOKEN_TYPE = "bypass_step_up"  # noqa: S105 - a claim value, not a secret
AUDIENCE = "faden-gateway:bypass"
KEY_LABEL = b"fadenstack bypass step-up proof v1"
#: How the person signed in again.
METHODS = frozenset({"password", "oidc", "saml", "ldap"})
#: Where an app starts a step-up (the backend; sdk/41).
START = "/api/agent-tokens/step-ups"


def key(secret: str) -> bytes:
    """The proof's signing key: an HMAC of :data:`KEY_LABEL` under the shared secret, never the secret itself."""
    return hmac.new(secret.encode(), KEY_LABEL, hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class Proof:
    """What a good proof says: which step-up, how and when the person signed in, the longest time they saw."""

    id: uuid.UUID
    method: str
    provider: str | None
    signed_in_at: datetime
    idp_auth_time: datetime | None
    max_minutes: int | None


class StepUpRequiredError(GatewayError):
    """No good proof: 401 with the RFC 9470 challenge; *reason* (``missing``, ``stale``, ``mismatch``,
    ``invalid``) is said in the body and logged, the proof never.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(
            status_code=401,
            error_type="authentication_error",
            code="bypass_step_up_required",
            message=(
                "Switching Bypass on needs a fresh sign-in: sign in again in the browser, within the last 5 minutes."
            ),
            headers={
                "WWW-Authenticate": (
                    'Bearer error="insufficient_user_authentication", '
                    'error_description="A fresh sign-in is needed to switch Bypass on", '
                    f'max_age="{MAX_AGE_S}"'
                ),
            },
            extra={"step_up": {"max_age": MAX_AGE_S, "reason": reason, "start": START}},
        )
        self.reason = reason


def _moment(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, UTC)


def verify(
    token: str | None, *, tenant_id: str, user_id: str, agent_id: str, device_id: str, now: float | None = None,
) -> Proof:
    """The proof in *token* if it is good for this caller now; else :class:`StepUpRequiredError`."""
    if not token:
        raise StepUpRequiredError("missing")
    if not settings.jwt_secret:
        # No secret, nothing can be checked: refuse (ASVS 5.0 16.5.3).
        raise StepUpRequiredError("invalid")
    moment = time.time() if now is None else now
    try:
        claims = jwt.decode(
            token,
            key(settings.jwt_secret),
            algorithms=["HS256"],
            audience=AUDIENCE,
            leeway=0,
            options={
                "require": ["exp", "iat", "typ", "jti", "ten", "usr", "agt", "did", "auth_time", "method"],
                # The times are checked below by *moment*, with the backend's clock allowed SKEW_S ahead.
                "verify_exp": False,
                "verify_iat": False,
            },
        )
    except jwt.PyJWTError as exc:
        logger.warning("A Bypass step-up proof was refused: %s", type(exc).__name__)
        raise StepUpRequiredError("invalid") from None
    if claims.get("typ") != TOKEN_TYPE or claims.get("method") not in METHODS:
        raise StepUpRequiredError("invalid")
    owner = (str(claims["ten"]), str(claims["usr"]), str(claims["agt"]), str(claims["did"]))
    if owner != (tenant_id, user_id, agent_id, device_id):
        logger.warning("A Bypass step-up proof for another person, agent or device was refused")
        raise StepUpRequiredError("mismatch")
    signed_in = claims.get("auth_time")
    if isinstance(signed_in, bool) or not isinstance(signed_in, (int, float)):
        raise StepUpRequiredError("invalid")
    expires = claims.get("exp")
    if (
        moment - signed_in > MAX_AGE_S
        or signed_in - moment > SKEW_S
        or not isinstance(expires, (int, float))
        or moment >= expires
    ):
        raise StepUpRequiredError("stale")
    try:
        proof_id = uuid.UUID(str(claims["jti"]))
    except ValueError:
        raise StepUpRequiredError("invalid") from None
    longest = claims.get("max_minutes")
    return Proof(
        id=proof_id,
        method=str(claims["method"]),
        provider=str(claims["prv"]) if claims.get("prv") else None,
        signed_in_at=datetime.fromtimestamp(signed_in, UTC),
        idp_auth_time=_moment(claims.get("idp_auth_time")),
        max_minutes=longest if isinstance(longest, int) and not isinstance(longest, bool) and longest > 0 else None,
    )
