"""Cache adapter layer — abstracts Redis behind a protocol.

Core deployments without Redis get ``NoOpCache`` (fail-open).
Enterprise / performance tiers get ``RedisCache``.
"""

from faden_gateway.services.cache.noop import NoOpCache
from faden_gateway.services.cache.protocol import CacheBackend
from faden_gateway.services.cache.redis import RedisCache

__all__ = ["CacheBackend", "NoOpCache", "RedisCache"]
