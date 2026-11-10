"""The gateway keeps a model server's address 30 seconds, not LiteLLM's 300.

A provider's container that restarts can come back at another address. Each gateway worker's LiteLLM resolved
the name through aiohttp's DNS cache, which LiteLLM 1.100.4 sets from ``AIOHTTP_TTL_DNS_CACHE`` (``constants.py``:
``int(os.getenv("AIOHTTP_TTL_DNS_CACHE", 300))``, handed to ``TCPConnector(ttl_dns_cache=...)`` of its default
aiohttp transport). On a test server every chat to that model then failed with 502 "The model failed to answer"
for up to five minutes, while the gateway itself already resolved the new address (10 November 2026).
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE = Path(__file__).parents[2] / "shared" / "docker-compose.yaml"


def test_the_gateway_caches_a_model_servers_address_for_30_seconds() -> None:
    gateway = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["faden-gateway"]

    value = gateway["environment"]["AIOHTTP_TTL_DNS_CACHE"]

    # A string compose passes on as it is; LiteLLM's int() must read it (a "30s" would stop the gateway).
    assert value == "30" and int(value) == 30
