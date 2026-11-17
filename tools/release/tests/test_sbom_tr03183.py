"""Tests for sbom_tr03183.py."""

import copy
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sbom_tr03183 as sbom  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)
DIGEST_OF_MANIFEST = None


def raw() -> dict:
    return json.loads((FIXTURES / "syft_raw.cdx.json").read_text(encoding="utf-8"))


def enrich_file(tmp_path: Path) -> tuple[dict, Path]:
    deliverable = tmp_path / "fadenstack-0.7.0-py3-none-any.whl"
    deliverable.write_bytes(b"wheel bytes \x00\x01\x02" * 1000)
    out = sbom.enrich(
        raw(), kind="file", product="fadenstack-cli", version="0.7.0",
        supplier_name="Example GmbH", supplier_url="https://example.invalid",
        subject_file=deliverable, now=NOW,
    )
    return out, deliverable


def prop(component: dict, name: str) -> list[str]:
    return [p["value"] for p in component.get("properties", []) if p["name"] == name]


def test_file_hashes_come_from_the_real_bytes(tmp_path: Path) -> None:
    doc, deliverable = enrich_file(tmp_path)
    data = deliverable.read_bytes()
    hashes = {h["alg"]: h["content"] for h in doc["metadata"]["component"]["hashes"]}
    assert hashes["SHA-512"] == hashlib.sha512(data).hexdigest()
    assert hashes["SHA-256"] == hashlib.sha256(data).hexdigest()


def test_file_purl_and_fields(tmp_path: Path) -> None:
    doc, _ = enrich_file(tmp_path)
    main = doc["metadata"]["component"]
    assert main["purl"] == "pkg:generic/fadenstack-cli/fadenstack-0.7.0-py3-none-any.whl@0.7.0"
    assert main["version"] == "0.7.0" and main["type"] == "file"
    assert doc["metadata"]["timestamp"] == "2026-11-17T12:00:00Z"
    assert doc["specVersion"] == "1.6"
    assert doc["metadata"]["manufacturer"]["name"] == "Example GmbH"
    assert doc["metadata"]["authors"] == [{"name": "Example GmbH"}]
    assert prop(main, "bsi:component:filename") == ["fadenstack-0.7.0-py3-none-any.whl"]
    assert prop(main, "bsi:component:archive") == ["archive"]
    assert prop(main, "bsi:component:executable") == ["non-executable"]
    assert prop(main, "bsi:component:structured") == ["structured"]


def test_root_dependency_lists_the_components(tmp_path: Path) -> None:
    doc, _ = enrich_file(tmp_path)
    root = doc["dependencies"][0]
    assert root["ref"] == doc["metadata"]["component"]["bom-ref"]
    assert "pkg:pypi/click@8.1.7" in root["dependsOn"]


def test_component_licence_property_added(tmp_path: Path) -> None:
    doc, _ = enrich_file(tmp_path)
    click = next(c for c in doc["components"] if c["name"] == "click")
    assert prop(click, "bsi:component:effectiveLicence") == ["BSD-3-Clause"]


def test_image_purl_and_hashes(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b'{"schemaVersion": 2, "layers": []}')
    digest = "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest()
    doc = sbom.enrich(
        raw(), kind="image", product="backend", version="0.7.0",
        supplier_name="Example GmbH", supplier_url="https://example.invalid",
        manifest_file=manifest, repository="ghcr.io/fadenstack/backend", digest=digest, now=NOW,
    )
    main = doc["metadata"]["component"]
    assert main["purl"] == f"pkg:oci/backend@{digest}?repository_url=ghcr.io/fadenstack/backend"
    assert main["type"] == "container"
    sha512 = next(h["content"] for h in main["hashes"] if h["alg"] == "SHA-512")
    assert sha512 == hashlib.sha512(manifest.read_bytes()).hexdigest()
    assert prop(main, "bsi:component:filename") == []


def test_image_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(b"other bytes")
    with pytest.raises(sbom.SbomError):
        sbom.enrich(
            raw(), kind="image", product="p", version="1", supplier_name="x", supplier_url="https://x.invalid",
            manifest_file=manifest, repository="ghcr.io/a/b", digest="sha256:" + "0" * 64,
        )


def test_enriched_document_validates(tmp_path: Path) -> None:
    doc, _ = enrich_file(tmp_path)
    errors, _ = sbom.validate(doc)
    assert errors == []


def test_validation_fails_without_timestamp(tmp_path: Path) -> None:
    doc, _ = enrich_file(tmp_path)
    del doc["metadata"]["timestamp"]
    errors, _ = sbom.validate(doc)
    assert any("timestamp" in e for e in errors)


def test_validation_fails_on_wrong_spec_version(tmp_path: Path) -> None:
    doc, _ = enrich_file(tmp_path)
    doc["specVersion"] = "1.5"
    errors, _ = sbom.validate(doc)
    assert any("specVersion" in e for e in errors)


def test_validation_fails_without_top_level_hash(tmp_path: Path) -> None:
    doc, _ = enrich_file(tmp_path)
    doc["metadata"]["component"]["hashes"] = [h for h in doc["metadata"]["component"]["hashes"] if h["alg"] != "SHA-512"]
    errors, _ = sbom.validate(doc)
    assert any("SHA-512" in e for e in errors)


def test_raw_syft_document_fails_validation() -> None:
    errors, _ = sbom.validate(raw())
    assert errors


def test_coverage_numbers(tmp_path: Path) -> None:
    doc, _ = enrich_file(tmp_path)
    _, cov = sbom.validate(doc)
    assert cov["purl"] == {"count": 2, "total": 3, "percent": 66.7}
    assert cov["licence"]["count"] == 1
    assert cov["hash"]["count"] == 1
    assert cov["sha512"]["count"] == 0


def test_coverage_of_empty_component_list(tmp_path: Path) -> None:
    doc, _ = enrich_file(tmp_path)
    broken = copy.deepcopy(doc)
    broken["components"] = []
    _, cov = sbom.validate(broken)
    assert cov["purl"]["total"] == 0 and cov["purl"]["percent"] == 0.0


def test_cli_roundtrip_and_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    deliverable = tmp_path / "tool.exe"
    deliverable.write_bytes(b"MZ" * 100)
    out = tmp_path / "out.cdx.json"
    summary = tmp_path / "summary.md"
    code = sbom.main([
        "enrich", "--input", str(FIXTURES / "syft_raw.cdx.json"), "--output", str(out), "--kind", "file",
        "--product", "p", "--version", "1.0.0", "--supplier-name", "Example GmbH",
        "--supplier-url", "https://example.invalid", "--subject-file", str(deliverable),
    ])
    assert code == 0
    assert sbom.main(["validate", "--input", str(out), "--summary-file", str(summary)]) == 0
    assert "package URL" in summary.read_text(encoding="utf-8")
    assert prop(json.loads(out.read_text(encoding="utf-8"))["metadata"]["component"], "bsi:component:executable") == ["executable"]
    assert sbom.main(["validate", "--input", str(FIXTURES / "syft_raw.cdx.json"), "--summary-file", ""]) == 1
