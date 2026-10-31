"""The PII policy every install starts with (``faden_gateway.pii_default_policy``), and earlier ones.

The default names no entity types and takes the installed detector's thresholds: the PII service then looks
for its standard types, as far as the detector finds them (``DEFAULT_ENTITIES`` there, without DATE_TIME). It
fits whichever detector a site installs -- the older Presidio engine or a local model -- and a change of it.

The earlier defaults named eleven or twelve types (NRP, which only spaCy's model finds, among them) with a
threshold on Presidio's scale; a local detector refuses both, and every request to a remote model was refused.
An install still holding one of them, as it was seeded, is given this one at start (``is_earlier_default``).
A default an administrator changed is theirs and stays.

No imports: the PII service's contract test loads this file on its own.
"""

from __future__ import annotations

import copy
from typing import Any

#: Redact what leaves for remote (cloud) models; local models are not scanned; a failed scan blocks.
CORE_DEFAULT_PII_POLICY: dict[str, Any] = {
    "telemetry": {
        "enabled": False,
        "mode": "sanitized",
        "store_raw": False,
    },
    "egress": {
        "enabled_for_cloud": True,
        "enabled_for_local": False,
        "mode": "redact",
        "fail_action": "block",
    },
    "presidio": {
        "language": "en",
        # Shown in the console; in profile mode the detector's own thresholds apply.
        "threshold": 0.35,
        "threshold_mode": "profile",
        # None named: the PII service's standard types that the installed detector finds.
        "entities": [],
    },
}

_ELEVEN = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "US_SSN",
    "LOCATION",
    "NRP",
    "MEDICAL_LICENSE",
    "URL",
]


def _earlier(entities: list[str]) -> dict[str, Any]:
    policy = copy.deepcopy(CORE_DEFAULT_PII_POLICY)
    policy["presidio"] = {"language": "en", "threshold": 0.35, "entities": entities}
    return policy


#: The defaults installs were seeded with before: twelve types until 26 September 2026, then eleven (no
#: DATE_TIME).
EARLIER_DEFAULTS: tuple[dict[str, Any], ...] = (
    _earlier([*_ELEVEN[:8], "DATE_TIME", *_ELEVEN[8:]]),
    _earlier(_ELEVEN),
)


def _canonical(policy: Any) -> Any:
    """*policy* as the gateway reads it: absent keys at their defaults, the entity types as a set."""
    if not isinstance(policy, dict):
        return None
    telemetry = policy.get("telemetry") or {}
    egress = policy.get("egress") or {}
    presidio = policy.get("presidio") or {}
    if not all(isinstance(part, dict) for part in (telemetry, egress, presidio)):
        return None
    entities = presidio.get("entities")
    return (
        bool(telemetry.get("enabled", False)),
        telemetry.get("mode", "sanitized"),
        bool(telemetry.get("store_raw", False)),
        bool(egress.get("enabled_for_cloud", False)),
        bool(egress.get("enabled_for_local", False)),
        egress.get("mode", "redact"),
        egress.get("fail_action", "block"),
        presidio.get("language", "en"),
        presidio.get("threshold", 0.6),
        presidio.get("threshold_mode", "legacy"),
        presidio.get("profile_id"),
        frozenset(entities) if isinstance(entities, list) else entities,
    )


def is_earlier_default(policy: Any) -> bool:
    """Whether *policy* is one of the earlier defaults as seeded (also saved unchanged from the console)."""
    found = _canonical(policy)
    return found is not None and any(found == _canonical(earlier) for earlier in EARLIER_DEFAULTS)


def default_policy() -> dict[str, Any]:
    """A copy of the default, to store."""
    return copy.deepcopy(CORE_DEFAULT_PII_POLICY)
