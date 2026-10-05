"""PII policy helpers for the gateway pipeline.

Parses the ``pii_config`` JSON from ``TenantLLMPolicy`` into typed
dataclasses and provides convenience predicates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Validation catalogues ────────────────────────────────────────
VALID_EGRESS_MODES: frozenset[str] = frozenset({"redact", "tokenize_reversible"})
VALID_FAIL_ACTIONS: frozenset[str] = frozenset({"block", "allow", "fallback_to_local"})
VALID_ENTITIES: frozenset[str] = frozenset({
    "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE",
    "PERSON", "LOCATION", "IP_ADDRESS", "DATE_TIME", "NRP",
    "MEDICAL_LICENSE", "URL", "US_SSN", "US_BANK_NUMBER",
    "US_DRIVER_LICENSE", "US_ITIN", "US_PASSPORT",
    "UK_NHS", "SG_NRIC_FIN", "AU_ABN", "AU_ACN", "AU_TFN",
    "AU_MEDICARE", "IN_PAN", "IN_AADHAAR", "IN_VEHICLE_REGISTRATION",
})


@dataclass(frozen=True, slots=True)
class PIITelemetryPolicy:
    """Controls what goes into Langfuse / audit DB."""

    enabled: bool = False
    mode: str = "sanitized"  # sanitized | metrics_only
    store_raw: bool = False


@dataclass(frozen=True, slots=True)
class PIIEgressPolicy:
    """Controls sanitization before cloud provider egress."""

    enabled_for_cloud: bool = False
    enabled_for_local: bool = False
    mode: str = "redact"  # redact | tokenize_reversible
    fail_action: str = "block"  # block | allow | fallback_to_local


@dataclass(frozen=True, slots=True)
class PIIPresidioConfig:
    """Presidio engine overrides from the policy."""

    language: str = "en"
    threshold: float = 0.6
    entities: list[str] = field(default_factory=lambda: [
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "CREDIT_CARD",
        "IBAN_CODE",
        "PERSON",
        "LOCATION",
    ])


@dataclass(frozen=True, slots=True)
class PIIPolicy:
    """Full parsed PII policy for a tenant/workspace."""

    telemetry: PIITelemetryPolicy = field(default_factory=PIITelemetryPolicy)
    egress: PIIEgressPolicy = field(default_factory=PIIEgressPolicy)
    presidio: PIIPresidioConfig = field(default_factory=PIIPresidioConfig)

    @property
    def any_enabled(self) -> bool:
        """True if any PII protection is active."""
        return self.telemetry.enabled or self.egress.enabled_for_cloud or self.egress.enabled_for_local

    @property
    def egress_enabled_for(self) -> tuple[bool, bool]:
        """Return (cloud, local) enablement flags."""
        return self.egress.enabled_for_cloud, self.egress.enabled_for_local


def parse_pii_policy(raw: dict[str, Any] | None) -> PIIPolicy | None:
    """Parse ``pii_config`` JSON into a typed ``PIIPolicy``.

    Returns ``None`` when PII is not configured (column is NULL or empty).
    """
    if not raw:
        return None

    default_entities = PIIPresidioConfig().entities
    tel_raw = raw.get("telemetry", {})
    egr_raw = raw.get("egress", {})
    pre_raw = raw.get("presidio", {})

    telemetry = PIITelemetryPolicy(
        enabled=bool(tel_raw.get("enabled", False)),
        mode=str(tel_raw.get("mode", "sanitized")),
        store_raw=bool(tel_raw.get("store_raw", False)),
    )
    raw_mode = str(egr_raw.get("mode", "redact"))
    if raw_mode not in VALID_EGRESS_MODES:
        logger.warning("Unknown PII egress mode %r, defaulting to 'redact'", raw_mode)
        raw_mode = "redact"

    raw_fail = str(egr_raw.get("fail_action", "block"))
    if raw_fail not in VALID_FAIL_ACTIONS:
        logger.warning("Unknown PII fail_action %r, defaulting to 'block'", raw_fail)
        raw_fail = "block"

    egress = PIIEgressPolicy(
        enabled_for_cloud=bool(egr_raw.get("enabled_for_cloud", False)),
        enabled_for_local=bool(egr_raw.get("enabled_for_local", False)),
        mode=raw_mode,
        fail_action=raw_fail,
    )

    raw_threshold = float(pre_raw.get("threshold", 0.6))
    raw_threshold = max(0.0, min(1.0, raw_threshold))

    raw_entities = list(pre_raw.get("entities", default_entities))
    validated_entities = [e for e in raw_entities if e in VALID_ENTITIES]
    if len(validated_entities) != len(raw_entities):
        dropped = set(raw_entities) - set(validated_entities)
        logger.warning("Stripped unknown PII entities: %s", dropped)

    presidio = PIIPresidioConfig(
        language=str(pre_raw.get("language", "en")),
        threshold=raw_threshold,
        entities=validated_entities,
    )

    policy = PIIPolicy(telemetry=telemetry, egress=egress, presidio=presidio)

    if not policy.any_enabled:
        return None

    return policy


# ── Session override model ───────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SessionPIIOverride:
    """Per-session PII overrides.  ``None`` means inherit from floor."""

    pii_enabled: bool | None = None
    egress_enabled_for_cloud: bool | None = None
    egress_enabled_for_local: bool | None = None
    egress_mode: str | None = None
    egress_fail_action: str | None = None
    telemetry_enabled: bool | None = None
    telemetry_mode: str | None = None
    presidio_threshold: float | None = None
    presidio_entities_add: list[str] | None = None


# ── Clamping helpers ─────────────────────────────────────────────

# Higher rank = more protective
FAIL_ACTION_ORDER: dict[str, int] = {
    "allow": 1,
    "fallback_to_local": 2,
    "block": 3,
}


def max_protective_action(
    floor_action: str,
    override_action: str | None,
) -> str:
    """Return the more protective fail_action. Unknown values → block."""
    if override_action is None:
        # No override — keep floor (validated elsewhere)
        return floor_action if floor_action in FAIL_ACTION_ORDER else "block"

    floor_rank = FAIL_ACTION_ORDER.get(floor_action, 3)  # unknown = block
    override_rank = FAIL_ACTION_ORDER.get(override_action, 3)  # unknown = block

    if override_rank >= floor_rank:
        return override_action if override_action in FAIL_ACTION_ORDER else "block"
    return floor_action if floor_action in FAIL_ACTION_ORDER else "block"


def clamp_and_merge(
    floor: PIIPolicy,
    override: SessionPIIOverride,
    *,
    allow_mode_override: bool = False,
    allow_weaken: bool = False,
) -> PIIPolicy:
    """Merge session override onto floor, clamped so it never weakens.

    When *allow_weaken* is ``True`` (admin privilege), the override may
    go below the tenant floor — booleans can be turned off, threshold
    lowered, and entities removed.  ``store_raw`` remains non-overridable.
    """
    # Mode: only switch if admin allows and value is valid
    effective_mode = floor.egress.mode
    if (allow_mode_override or allow_weaken) and override.egress_mode in VALID_EGRESS_MODES:
        effective_mode = override.egress_mode  # type: ignore[assignment]

    if allow_weaken:
        egress = PIIEgressPolicy(
            enabled_for_cloud=(
                override.egress_enabled_for_cloud
                if override.egress_enabled_for_cloud is not None
                else floor.egress.enabled_for_cloud
            ),
            enabled_for_local=(
                override.egress_enabled_for_local
                if override.egress_enabled_for_local is not None
                else floor.egress.enabled_for_local
            ),
            mode=effective_mode,
            fail_action=(
                override.egress_fail_action
                if override.egress_fail_action in VALID_FAIL_ACTIONS
                else floor.egress.fail_action
            ),
        )

        telemetry = PIITelemetryPolicy(
            enabled=(
                override.telemetry_enabled
                if override.telemetry_enabled is not None
                else floor.telemetry.enabled
            ),
            mode=override.telemetry_mode if override.telemetry_mode is not None else floor.telemetry.mode,
            store_raw=floor.telemetry.store_raw,  # NOT overridable
        )

        # Entities: use override additions as the full set (may be subset of floor)
        valid_add = [
            e for e in (override.presidio_entities_add or [])
            if e in VALID_ENTITIES
        ]
        # If override provides entities, use them; otherwise keep floor
        merged_entities = valid_add if valid_add else list(floor.presidio.entities)

        presidio = PIIPresidioConfig(
            language=floor.presidio.language,
            threshold=override.presidio_threshold if override.presidio_threshold is not None else floor.presidio.threshold,
            entities=merged_entities,
        )
    else:
        egress = PIIEgressPolicy(
            enabled_for_cloud=floor.egress.enabled_for_cloud or bool(override.egress_enabled_for_cloud),
            enabled_for_local=floor.egress.enabled_for_local or bool(override.egress_enabled_for_local),
            mode=effective_mode,
            fail_action=max_protective_action(floor.egress.fail_action, override.egress_fail_action),
        )

        telemetry = PIITelemetryPolicy(
            enabled=floor.telemetry.enabled or bool(override.telemetry_enabled),
            mode=override.telemetry_mode if override.telemetry_mode is not None else floor.telemetry.mode,
            store_raw=floor.telemetry.store_raw,  # NOT overridable
        )

        # Entities: union of floor + valid additions
        valid_add = [
            e for e in (override.presidio_entities_add or [])
            if e in VALID_ENTITIES
        ]
        merged_entities = list(dict.fromkeys(floor.presidio.entities + valid_add))

        presidio = PIIPresidioConfig(
            language=floor.presidio.language,
            threshold=max(floor.presidio.threshold, override.presidio_threshold or 0.0),
            entities=merged_entities,
        )

    return PIIPolicy(egress=egress, telemetry=telemetry, presidio=presidio)


# ── A session's effective policy: the one place it is computed ───


def mode_switch_allowed(
    *,
    tenant_default: bool,
    agent_setting: bool | None = None,
    weaken: bool = False,
) -> bool:
    """Whether a session's own choice of egress mode (redact, tokenize) is applied.

    It is when the override was saved by someone who may weaken the tenant's
    policy (*weaken*: weakening implies switching the mode), or the agent the
    session runs under allows it (*agent_setting* true), or the agent leaves it
    to the tenant (``None``, as every console chat does) and the tenant allows
    it (*tenant_default*: ``tenant_llm_policy.allow_mode_override``). An agent
    set to false refuses it whatever the tenant says.
    """
    if weaken:
        return True
    if agent_setting is not None:
        return agent_setting
    return tenant_default


@dataclass(frozen=True, slots=True)
class SessionPIIResolution:
    """A session's PII policy as its requests get it, and what decided its mode."""

    #: The tenant's policy, or the system default: what an override is clamped to.
    floor: PIIPolicy | None
    #: What the session's requests get; ``None`` when no PII policy applies.
    effective: PIIPolicy | None
    #: A mode the session's override asks for is applied (``mode_switch_allowed``).
    mode_switch_allowed: bool
    #: The valid egress mode the override asks for, if any.
    requested_mode: str | None = None
    #: The override was saved with the right to weaken.
    weaken: bool = False

    @property
    def mode_applied(self) -> bool:
        """False when the override asks for a mode its requests do not get."""
        if self.requested_mode is None or self.effective is None:
            return True
        return self.effective.egress.mode == self.requested_mode


def resolve_floor(
    tenant_policy: Any | None, default_policy: dict[str, Any] | None = None,
) -> PIIPolicy | None:
    """The PII floor: the tenant's ``pii_config``, else the system default, else none."""
    raw = getattr(tenant_policy, "pii_config", None) if tenant_policy is not None else None
    floor = parse_pii_policy(raw) if raw else None
    if floor is None and default_policy:
        floor = parse_pii_policy(default_policy)
    return floor


def _override_of(row: Any) -> SessionPIIOverride:
    if isinstance(row, SessionPIIOverride):
        return row
    return SessionPIIOverride(
        pii_enabled=row.pii_enabled,
        egress_enabled_for_cloud=row.egress_enabled_for_cloud,
        egress_enabled_for_local=row.egress_enabled_for_local,
        egress_mode=row.egress_mode,
        egress_fail_action=row.egress_fail_action,
        telemetry_enabled=row.telemetry_enabled,
        telemetry_mode=row.telemetry_mode,
        presidio_threshold=row.presidio_threshold,
        presidio_entities_add=row.presidio_entities_add,
    )


#: ``resolve_session_pii``'s default: the system default policy from the settings.
_SYSTEM_DEFAULT: Any = object()


def resolve_session_pii(
    tenant_policy: Any | None,
    override: Any | None = None,
    *,
    agent_allow_mode_override: bool | None = None,
    default_policy: dict[str, Any] | None = _SYSTEM_DEFAULT,
) -> SessionPIIResolution:
    """A session's effective PII policy: the panel shows it, and its requests get it.

    *tenant_policy* is the ``TenantLLMPolicy`` row (its ``pii_config`` and its
    ``allow_mode_override``, the tenant default); *override* the session's
    ``SessionPIIOverrideRow`` -- with ``allow_weaken``, the right it was saved
    with -- or a ``SessionPIIOverride`` (an agent's conversation, which has no
    stored override and no weaken right); *agent_allow_mode_override* the
    setting of the agent the session runs under (``None`` for a console chat).
    *default_policy* is the system default, used when the tenant has none
    (``settings.pii_default_policy`` unless given).
    """
    if default_policy is _SYSTEM_DEFAULT:
        from faden_gateway.settings import settings  # noqa: PLC0415

        default_policy = getattr(settings, "pii_default_policy", None)
    floor = resolve_floor(tenant_policy, default_policy)
    # Only a real ``true`` counts: a missing column or a stand-in object never switches the mode.
    tenant_default = getattr(tenant_policy, "allow_mode_override", False) is True
    weaken = getattr(override, "allow_weaken", False) is True
    allowed = mode_switch_allowed(
        tenant_default=tenant_default, agent_setting=agent_allow_mode_override, weaken=weaken,
    )
    if override is None:
        return SessionPIIResolution(floor=floor, effective=floor, mode_switch_allowed=allowed)
    merged = _override_of(override)
    requested = merged.egress_mode if merged.egress_mode in VALID_EGRESS_MODES else None
    effective = (
        clamp_and_merge(floor, merged, allow_mode_override=allowed, allow_weaken=weaken)
        if floor is not None else None
    )
    return SessionPIIResolution(
        floor=floor,
        effective=effective,
        mode_switch_allowed=allowed,
        requested_mode=requested,
        weaken=weaken,
    )
