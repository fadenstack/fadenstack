"""The closing summary names an address other machines reach the console by.

It said ``https://localhost`` for the application on .61 -- the backend's own
URL, which only a browser on the server opens -- above the real addresses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from faden_cli.commands import deploy, tls


def test_the_summary_names_an_address_other_machines_reach(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tls, "addresses", lambda _dir: ["https://llm-port", "https://192.0.2.61"])
    assert deploy.summary_base(tmp_path, "https://localhost") == "https://192.0.2.61"


def test_without_other_addresses_the_backend_url_stays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tls, "addresses", lambda _dir: [])
    assert deploy.summary_base(tmp_path, "https://localhost") == "https://localhost"
