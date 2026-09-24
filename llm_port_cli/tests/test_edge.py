"""The CLI reaches its own console over HTTPS once edge material is in place."""

from __future__ import annotations

from pathlib import Path

from llmport.core.edge import console_url, edge_material_present, verify_for


def test_the_console_url_follows_the_edge_material(tmp_path: Path) -> None:
    assert not edge_material_present(tmp_path)
    assert console_url(tmp_path, {}) == "http://localhost"
    assert console_url(tmp_path, {"LLM_PORT_HTTP_PORT": "8080"}) == "http://localhost:8080"
    assert console_url(tmp_path, {"LLM_PORT_HTTP_PORT": '"80"'}) == "http://localhost"

    (tmp_path / "tls").mkdir()
    (tmp_path / "tls" / "edge.crt").write_text("cert", encoding="utf-8")
    assert not edge_material_present(tmp_path), "a certificate without its key serves nothing"
    (tmp_path / "tls" / "edge.key").write_text("key", encoding="utf-8")
    assert edge_material_present(tmp_path)
    assert console_url(tmp_path, {"LLM_PORT_HTTP_PORT": "8080"}) == "https://localhost"
    assert console_url(tmp_path, {"LLM_PORT_HTTPS_PORT": "8443"}) == "https://localhost:8443"


def test_this_machine_is_not_asked_to_trust_its_own_certificate() -> None:
    assert verify_for("https://localhost") is False
    assert verify_for("https://localhost:8443") is False
    assert verify_for("http://localhost") is True
