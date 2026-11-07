"""Database model package for gateway service."""

from faden_gateway.db.models.gateway import (
    LLMGatewayRequestLog,
    LLMModelAlias,
    LLMPoolMembership,
    LLMProviderInstance,
    PriceCatalog,
    PrivacyMode,
    ProviderHealthStatus,
    ProviderType,
    TenantLLMPolicy,
)
from faden_gateway.db.models.usage import (
    UsageDaily,
    UsageDailyPerson,
    UsageErased,
    UsageHourly,
    UsageRollupState,
)

__all__ = [
    "LLMGatewayRequestLog",
    "LLMModelAlias",
    "LLMPoolMembership",
    "LLMProviderInstance",
    "PriceCatalog",
    "PrivacyMode",
    "ProviderHealthStatus",
    "ProviderType",
    "TenantLLMPolicy",
    "UsageDaily",
    "UsageDailyPerson",
    "UsageErased",
    "UsageHourly",
    "UsageRollupState",
]


def load_all_models() -> None:
    """Import all ORM models for migration discovery."""
