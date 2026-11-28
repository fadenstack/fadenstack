"""A node's advertised address when the Fadenstack server is on another machine.

The server (backend, gateway, Prometheus) reaches a Mac through the address the
agent advertises, so it has to be the IP the route toward the server leaves
from -- never the machine's ``.local`` name (a Linux server and its containers
do not resolve mDNS) and never loopback.
"""

from __future__ import annotations

import socket

import pytest

from faden_agent import config as config_module
from faden_agent.config import AgentConfig

_ENV = (
    "FADEN_NODE_AGENT_BACKEND_URL", "FADEN_NODE_AGENT_HOST", "FADEN_NODE_AGENT_ADVERTISE_HOST",
    "FADEN_NODE_AGENT_AGENT_ID",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "some-mac.local")


def test_a_remote_server_gets_the_routable_ip_not_the_mdns_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FADEN_NODE_AGENT_BACKEND_URL", "https://server.example:8443")
    # What `join` saves: HOST is the machine's name, no ADVERTISE_HOST.
    monkeypatch.setenv("FADEN_NODE_AGENT_HOST", "some-mac.local")
    monkeypatch.setattr(config_module, "_detect_routable_ip", lambda url: "192.0.2.50")
    cfg = AgentConfig.from_env()
    assert cfg.advertise_host == "192.0.2.50"


def test_an_explicit_advertise_host_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FADEN_NODE_AGENT_BACKEND_URL", "https://server.example:8443")
    monkeypatch.setenv("FADEN_NODE_AGENT_ADVERTISE_HOST", "198.51.100.7")
    monkeypatch.setattr(config_module, "_detect_routable_ip", lambda url: "192.0.2.50")
    assert AgentConfig.from_env().advertise_host == "198.51.100.7"


def test_the_route_probe_sends_nothing_and_follows_the_backend_address() -> None:
    # A UDP connect() only selects a route; an address in a documentation range may have none (None, not an error).
    assert config_module._detect_routable_ip("http://127.0.0.1:8000") == "127.0.0.1"
