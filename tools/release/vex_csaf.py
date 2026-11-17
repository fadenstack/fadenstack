#!/usr/bin/env python3
"""Match OSV findings against the repository's OpenVEX statements.

Input is the JSON osv-scanner writes for one release SBOM and the repository's
OpenVEX file (``.vex/openvex.json``; a missing file means no statements).

Output, in ``--out-dir``:

* ``<product>-<version>.openvex.json``: an OpenVEX document for this release.
  Every finding gets one statement. A finding the repository's file addresses
  copies that statement's status, justification and texts; any other finding
  becomes ``under_investigation``.
* ``<tracking id>.csaf.json``: one CSAF 2.0 document (profile ``csaf_vex``) for
  every statement whose status is ``affected``.

The fail rule: a finding with CVSS >= 7.0 (or an OSV severity of HIGH or
CRITICAL) whose statement is missing or ``under_investigation`` makes the exit
status 1. With ``--dry-run`` it is reported only and the status stays 0.

Python 3.12, standard library only, no network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SEVERITY_THRESHOLD = 7.0
HIGH_LABELS = {"HIGH", "CRITICAL"}
OPENVEX_CONTEXT = "https://openvex.dev/ns/v0.2.0"
STATUSES = {"not_affected", "affected", "fixed", "under_investigation"}
CVE_RE = re.compile(r"^CVE-[0-9]{4}-[0-9]{4,}$")
PURL_ECOSYSTEM = {
    "pypi": "pypi", "npm": "npm", "go": "golang", "maven": "maven", "nuget": "nuget",
    "crates.io": "cargo", "rubygems": "gem", "packagist": "composer", "pub": "pub",
}


@dataclass
class Finding:
    """One vulnerability group on one package, as osv-scanner reports it."""

    ids: set[str]
    primary: str
    package: str
    version: str
    purl: str
    score: float | None
    label: str
    summary: str = ""
    statement: dict[str, Any] | None = field(default=None)

    @property
    def high(self) -> bool:
        """True when CVSS >= 7.0 or the OSV severity label is HIGH or CRITICAL."""
        return (self.score is not None and self.score >= SEVERITY_THRESHOLD) or self.label in HIGH_LABELS

    @property
    def status(self) -> str:
        """Status of the statement that covers this finding."""
        return str(self.statement["status"]) if self.statement else "under_investigation"

    @property
    def addressed(self) -> bool:
        """True when a statement other than under_investigation covers it."""
        return self.status != "under_investigation"


def _primary(ids: set[str]) -> str:
    """Prefer a CVE id, then GHSA, then the first id alphabetically."""
    for prefix in ("CVE-", "GHSA-"):
        hits = sorted(i for i in ids if i.startswith(prefix))
        if hits:
            return hits[0]
    return sorted(ids)[0]


def _purl(package: dict[str, Any]) -> str:
    if package.get("purl"):
        return str(package["purl"]).split("?")[0]
    eco = PURL_ECOSYSTEM.get(str(package.get("ecosystem", "")).lower().split(":")[0])
    if not eco:
        return ""
    return f"pkg:{eco}/{package.get('name', '')}@{package.get('version', '')}"


def parse_osv(data: dict[str, Any]) -> list[Finding]:
    """Turn osv-scanner's JSON into findings (one per group of aliases per package)."""
    found: dict[tuple[str, str], Finding] = {}
    for result in data.get("results") or []:
        for entry in result.get("packages") or []:
            package = entry.get("package", {})
            vulns = {v.get("id"): v for v in entry.get("vulnerabilities") or []}
            groups = entry.get("groups") or [
                {"ids": [i], "aliases": vulns[i].get("aliases", [])} for i in vulns
            ]
            for group in groups:
                ids = set(group.get("ids") or []) | set(group.get("aliases") or [])
                ids.discard("")
                if not ids:
                    continue
                label = ""
                summary = ""
                for vid in group.get("ids") or []:
                    vuln = vulns.get(vid) or {}
                    sev = str((vuln.get("database_specific") or {}).get("severity", "")).upper()
                    if sev in HIGH_LABELS:
                        label = sev
                    elif sev and not label:
                        label = sev
                    summary = summary or str(vuln.get("summary", ""))
                try:
                    score: float | None = float(group["max_severity"])
                except (KeyError, TypeError, ValueError):
                    score = None
                purl = _purl(package)
                key = (purl or str(package.get("name")), _primary(ids))
                if key in found:
                    found[key].ids |= ids
                    continue
                found[key] = Finding(
                    ids=ids, primary=_primary(ids), package=str(package.get("name", "")),
                    version=str(package.get("version", "")), purl=purl, score=score,
                    label=label, summary=summary,
                )
    return sorted(found.values(), key=lambda f: (f.package, f.primary))


def load_statements(path: Path | None) -> list[dict[str, Any]]:
    """Read the repository's OpenVEX statements; a missing file means none."""
    if path is None or not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    statements = document.get("statements") or []
    for stmt in statements:
        if stmt.get("status") not in STATUSES:
            raise ValueError(f"statement for {stmt.get('vulnerability')} has an unknown status {stmt.get('status')!r}")
    return list(statements)


def _stmt_ids(stmt: dict[str, Any]) -> set[str]:
    vuln = stmt.get("vulnerability") or {}
    ids = {str(vuln["name"])} if vuln.get("name") else set()
    ids |= {str(a) for a in vuln.get("aliases") or []}
    return ids


def _versionless(purl: str) -> str:
    return purl.split("@", 1)[0]


def _stmt_covers_product(stmt: dict[str, Any], finding: Finding, subject_purl: str) -> bool:
    products = stmt.get("products") or []
    if not products:
        return True
    wanted = {finding.purl, _versionless(finding.purl), subject_purl} - {""}
    for product in products:
        candidates = [product.get("@id", "")] + list((product.get("identifiers") or {}).values())
        for sub in product.get("subcomponents") or []:
            candidates.append(sub.get("@id", ""))
        for cand in candidates:
            if cand in wanted or _versionless(str(cand)) in wanted:
                return True
    return False


def match(findings: list[Finding], statements: list[dict[str, Any]], subject_purl: str) -> None:
    """Attach the newest covering repository statement to each finding."""
    for finding in findings:
        covering = [
            s for s in statements
            if _stmt_ids(s) & finding.ids and _stmt_covers_product(s, finding, subject_purl)
        ]
        if covering:
            finding.statement = max(covering, key=lambda s: str(s.get("timestamp", "")))


def build_openvex(
    findings: list[Finding], *, product: str, version: str, subject_purl: str,
    author: str, id_base: str, now: datetime,
) -> dict[str, Any]:
    """The OpenVEX document for this release: one statement per finding."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    statements: list[dict[str, Any]] = []
    for f in findings:
        vuln: dict[str, Any] = {"name": f.primary}
        aliases = sorted(f.ids - {f.primary})
        if aliases:
            vuln["aliases"] = aliases
        stmt: dict[str, Any] = {"vulnerability": vuln}
        stmt["timestamp"] = str((f.statement or {}).get("timestamp") or stamp)
        stmt["products"] = [{"@id": subject_purl, "subcomponents": [{"@id": f.purl or f.package}]}]
        stmt["status"] = f.status
        for key in ("justification", "impact_statement", "action_statement"):
            if f.statement and f.statement.get(key):
                stmt[key] = f.statement[key]
        if f.status == "under_investigation":
            stmt["status_notes"] = "Reported by OSV for this release; not yet assessed."
        statements.append(stmt)
    body = json.dumps(statements, sort_keys=True).encode()
    doc_id = f"{id_base.rstrip('/')}/{product}/{version}/{hashlib.sha256(body).hexdigest()[:16]}"
    return {
        "@context": OPENVEX_CONTEXT, "@id": doc_id, "author": author, "role": "Document Creator",
        "timestamp": stamp, "version": 1, "statements": statements,
    }


def tracking_id(stem: str, vuln: str) -> str:
    """A CSAF tracking id that is also a valid file name stem."""
    return re.sub(r"[^+\-a-z0-9_]", "_", f"{stem}-{vuln}".lower())


def build_csaf(
    finding: Finding, *, product: str, version: str, subject_purl: str,
    publisher_name: str, publisher_url: str, now: datetime, stem: str = "",
) -> dict[str, Any]:
    """A CSAF 2.0 csaf_vex document for one affected finding."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    pid = "CSAFPID-0001"
    stmt = finding.statement or {}
    vuln: dict[str, Any] = {
        "product_status": {"known_affected": [pid]},
        "notes": [{"category": "details", "text": f"{finding.package} {finding.version}: {finding.summary or finding.primary}"}],
        "remediations": [{
            "category": "vendor_fix" if stmt.get("action_statement") else "none_available",
            "details": stmt.get("action_statement") or "No remediation is available at the time of this release.",
            "product_ids": [pid],
        }],
    }
    cves = sorted(i for i in finding.ids if CVE_RE.match(i))
    if cves:
        vuln["cve"] = cves[0]
    others = sorted(finding.ids - set(cves[:1]))
    vuln["ids"] = [{"system_name": "OSV", "text": i} for i in others] or [{"system_name": "OSV", "text": finding.primary}]
    if stmt.get("impact_statement"):
        vuln["threats"] = [{"category": "impact", "details": stmt["impact_statement"], "product_ids": [pid]}]
    tid = tracking_id(stem or f"{product}-{version}", finding.primary)
    return {
        "document": {
            "category": "csaf_vex", "csaf_version": "2.0", "lang": "en",
            "title": f"{product} {version}: {finding.primary} in {finding.package}",
            "publisher": {"category": "vendor", "name": publisher_name, "namespace": publisher_url},
            "tracking": {
                "id": tid, "status": "final", "version": "1",
                "initial_release_date": stamp, "current_release_date": stamp,
                "revision_history": [{"date": stamp, "number": "1", "summary": "Initial version"}],
            },
        },
        "product_tree": {"branches": [{
            "category": "vendor", "name": publisher_name,
            "branches": [{
                "category": "product_version", "name": f"{product} {version}",
                "product": {
                    "name": f"{product} {version}", "product_id": pid,
                    "product_identification_helper": {"purl": subject_purl},
                },
            }],
        }]},
        "vulnerabilities": [vuln],
    }


def evaluate(findings: list[Finding]) -> list[Finding]:
    """High findings that no statement other than under_investigation covers."""
    return [f for f in findings if f.high and not f.addressed]


def summary_markdown(label: str, findings: list[Finding], blocking: list[Finding], dry_run: bool) -> str:
    """Render what was found and what it means as Markdown."""
    lines = [f"#### OSV findings and VEX: {label}", ""]
    if not findings:
        lines += ["No findings.", ""]
        return "\n".join(lines)
    lines += ["| Finding | Package | Severity | VEX status |", "| --- | --- | --- | --- |"]
    for f in findings:
        sev = f"{f.score}" if f.score is not None else (f.label or "unknown")
        mark = " (high)" if f.high else ""
        lines.append(f"| {f.primary} | {f.package} {f.version} | {sev}{mark} | {f.status} |")
    lines.append("")
    if blocking:
        verb = "reported only (dry run)" if dry_run else "FAIL"
        lines.append(f"High findings without a statement: {len(blocking)}, {verb}: " + ", ".join(f.primary for f in blocking))
    else:
        lines.append("Every high finding has a statement.")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--osv", required=True, help="osv-scanner JSON for one SBOM")
    p.add_argument("--vex", default=".vex/openvex.json", help="the repository's OpenVEX file (may be missing)")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--product", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--subject-purl", required=True, help="package URL of the deliverable")
    p.add_argument("--author", required=True, help="author of the VEX document and CSAF publisher name")
    p.add_argument("--author-url", required=True, help="CSAF publisher namespace")
    p.add_argument("--id-base", default="", help="URL prefix for the OpenVEX @id; default author-url/vex")
    p.add_argument("--stem", default="", help="file name stem for the outputs; default <product>-<version>")
    p.add_argument("--label", default="")
    p.add_argument("--dry-run", action="store_true", help="report high findings without failing")
    p.add_argument("--summary-file", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    now = datetime.now(UTC)
    try:
        findings = parse_osv(json.loads(Path(args.osv).read_text(encoding="utf-8")))
        statements = load_statements(Path(args.vex))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    match(findings, statements, args.subject_purl)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    vex = build_openvex(
        findings, product=args.product, version=args.version, subject_purl=args.subject_purl,
        author=args.author, id_base=args.id_base or f"{args.author_url.rstrip('/')}/vex", now=now,
    )
    stem = args.stem or f"{args.product}-{args.version}"
    vex_path = out / f"{stem}.openvex.json"
    vex_path.write_text(json.dumps(vex, indent=2) + "\n", encoding="utf-8", newline="\n")
    csaf_paths: list[Path] = []
    for f in findings:
        if f.status == "affected":
            doc = build_csaf(
                f, product=args.product, version=args.version, subject_purl=args.subject_purl,
                publisher_name=args.author, publisher_url=args.author_url, now=now, stem=stem,
            )
            path = out / f"{doc['document']['tracking']['id']}.csaf.json"
            path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
            csaf_paths.append(path)
    blocking = evaluate(findings)
    text = summary_markdown(args.label or args.product, findings, blocking, args.dry_run)
    text += f"Wrote {vex_path.name}" + "".join(f", {p.name}" for p in csaf_paths) + "\n"
    print(text)
    if args.summary_file:
        with open(args.summary_file, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
    return 1 if blocking and not args.dry_run else 0


if __name__ == "__main__":
    sys.exit(main())
