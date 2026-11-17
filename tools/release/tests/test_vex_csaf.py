"""Tests for vex_csaf.py."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vex_csaf as vc  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
SUBJECT = "pkg:generic/p/p-1.0.0.whl@1.0.0"
NOW = datetime(2026, 11, 17, 12, 0, 0, tzinfo=UTC)


def findings(vex: str = "openvex_repo.json") -> list[vc.Finding]:
    items = vc.parse_osv(json.loads((FIXTURES / "osv_findings.json").read_text(encoding="utf-8")))
    vc.match(items, vc.load_statements(FIXTURES / vex), SUBJECT)
    return items


def by_id(items: list[vc.Finding], ident: str) -> vc.Finding:
    return next(f for f in items if ident in f.ids)


def test_findings_are_grouped_by_alias() -> None:
    items = findings()
    assert len(items) == 3
    assert by_id(items, "GHSA-8r7q-cvjq-x353").primary == "CVE-2014-1402"
    assert by_id(items, "CVE-2014-1402").high


def test_addressed_by_alias_not_affected() -> None:
    f = by_id(findings(), "PYSEC-2014-8")  # statement names the CVE, finding came as PYSEC/GHSA
    assert f.status == "not_affected" and f.addressed


def test_ghsa_statement_matches_cve_finding() -> None:
    f = by_id(findings(), "CVE-2023-25577")
    assert f.status == "affected"


def test_unaddressed_becomes_under_investigation() -> None:
    f = by_id(findings(), "CVE-2014-0012")
    assert f.status == "under_investigation" and not f.addressed and not f.high


def test_statement_for_another_product_does_not_match() -> None:
    items = vc.parse_osv(json.loads((FIXTURES / "osv_findings.json").read_text(encoding="utf-8")))
    stmts = [{"vulnerability": {"name": "CVE-2014-1402"}, "products": [{"@id": "pkg:pypi/other"}], "status": "not_affected"}]
    vc.match(items, stmts, SUBJECT)
    assert by_id(items, "CVE-2014-1402").status == "under_investigation"


def test_openvex_document_shape() -> None:
    items = findings()
    doc = vc.build_openvex(items, product="p", version="1.0.0", subject_purl=SUBJECT, author="Test", id_base="https://example.invalid/vex", now=NOW)
    assert doc["@context"] == "https://openvex.dev/ns/v0.2.0"
    assert doc["author"] == "Test" and doc["version"] == 1
    assert len(doc["statements"]) == 3
    statuses = {s["vulnerability"]["name"]: s["status"] for s in doc["statements"]}
    assert statuses == {"CVE-2014-1402": "not_affected", "CVE-2014-0012": "under_investigation", "CVE-2023-25577": "affected"}
    first = next(s for s in doc["statements"] if s["vulnerability"]["name"] == "CVE-2014-1402")
    assert first["justification"] == "vulnerable_code_not_in_execute_path"
    assert first["products"][0]["@id"] == SUBJECT
    assert "GHSA-8r7q-cvjq-x353" in first["vulnerability"]["aliases"]


def test_csaf_only_for_affected(tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = vc.main([
        "--osv", str(FIXTURES / "osv_findings.json"), "--vex", str(FIXTURES / "openvex_repo.json"),
        "--out-dir", str(out), "--product", "p", "--version", "1.0.0", "--subject-purl", SUBJECT,
        "--author", "Test", "--author-url", "https://example.invalid", "--summary-file", "",
    ])
    assert code == 0  # the one high finding without a statement does not exist here
    names = sorted(p.name for p in out.iterdir())
    assert names == ["p-1.0.0.openvex.json", "p-1_0_0-cve-2023-25577.csaf.json"]
    csaf = json.loads((out / names[1]).read_text(encoding="utf-8"))
    assert csaf["document"]["category"] == "csaf_vex" and csaf["document"]["csaf_version"] == "2.0"
    vuln = csaf["vulnerabilities"][0]
    assert vuln["cve"] == "CVE-2023-25577"
    assert vuln["product_status"]["known_affected"] == ["CSAFPID-0001"]
    assert vuln["remediations"][0]["details"].startswith("Upgrade werkzeug")


def run(vex: str, tmp_path: Path, *extra: str) -> int:
    return vc.main([
        "--osv", str(FIXTURES / "osv_findings.json"), "--vex", str(FIXTURES / vex), "--out-dir", str(tmp_path / "o"),
        "--product", "p", "--version", "1.0.0", "--subject-purl", SUBJECT, "--author", "Test",
        "--author-url", "https://example.invalid", "--summary-file", "", *extra,
    ])


def test_fail_rule_fails_on_unaddressed_high(tmp_path: Path) -> None:
    assert run("openvex_empty.json", tmp_path) == 1


def test_fail_rule_is_off_in_a_dry_run(tmp_path: Path) -> None:
    assert run("openvex_empty.json", tmp_path, "--dry-run") == 0


def test_missing_vex_file_means_no_statements(tmp_path: Path) -> None:
    assert run("does-not-exist.json", tmp_path) == 1
    doc = json.loads(next((tmp_path / "o").glob("*.openvex.json")).read_text(encoding="utf-8"))
    assert {s["status"] for s in doc["statements"]} == {"under_investigation"}


def test_under_investigation_statement_still_fails(tmp_path: Path) -> None:
    items = findings("openvex_empty.json")
    vc.match(items, [{"vulnerability": {"name": "CVE-2023-25577"}, "status": "under_investigation"}], SUBJECT)
    assert by_id(items, "CVE-2023-25577") in vc.evaluate(items)


def test_label_alone_makes_a_finding_high() -> None:
    f = vc.Finding(ids={"X"}, primary="X", package="p", version="1", purl="", score=None, label="CRITICAL")
    assert f.high
    assert not vc.Finding(ids={"X"}, primary="X", package="p", version="1", purl="", score=6.9, label="MODERATE").high
    assert vc.Finding(ids={"X"}, primary="X", package="p", version="1", purl="", score=7.0, label="").high


def test_empty_osv_result(tmp_path: Path) -> None:
    osv = tmp_path / "osv.json"
    osv.write_text('{"results": []}', encoding="utf-8")
    code = vc.main([
        "--osv", str(osv), "--vex", str(tmp_path / "none.json"), "--out-dir", str(tmp_path / "o"), "--product", "p",
        "--version", "1", "--subject-purl", SUBJECT, "--author", "T", "--author-url", "https://example.invalid", "--summary-file", "",
    ])
    assert code == 0
    doc = json.loads((tmp_path / "o" / "p-1.openvex.json").read_text(encoding="utf-8"))
    assert doc["statements"] == []
