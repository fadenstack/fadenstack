#!/usr/bin/env python3
"""Enrich and validate a CycloneDX 1.6 SBOM against BSI TR-03183-2.

Two sub-commands:

``enrich``    takes the SBOM syft wrote for one release deliverable (a file or
              a container image) and makes the deliverable itself the
              ``metadata.component``: name, version, package URL, SHA-512 and
              SHA-256 hashes computed from the real bytes, the supplier and
              manufacturer, the SBOM author and timestamp, and the BSI
              properties. A root ``dependencies`` entry is added when missing.
``validate``  fails (exit status 1) when a required field is missing or the
              spec version is not 1.6, and reports how many components carry a
              package URL, a licence and a hash (a Markdown table, printed and
              optionally appended to ``$GITHUB_STEP_SUMMARY``).

Field mapping (BSI TR-03183-2 v2.1.0, section 5.2 -> CycloneDX 1.6):

  SBOM creator (email or URL)      metadata.manufacturer (name, url) and
                                   metadata.authors (name)
  SBOM timestamp                   metadata.timestamp (UTC, RFC 3339)
  Component creator                component.manufacturer / component.supplier
  Component name, version          component.name, component.version
  Component filename               property bsi:component:filename
  Dependencies                     top-level dependencies[]
  Licence                          component.licenses[]; property
                                   bsi:component:effectiveLicence
  SHA-512 hash of the deliverable  component.hashes[] with alg SHA-512
  Executable / archive / structured  properties bsi:component:executable,
                                   bsi:component:archive,
                                   bsi:component:structured

The property names come from BSI's CycloneDX property taxonomy:
https://github.com/BSI-Bund/tr-03183-cyclonedx-property-taxonomy (v0.1.2,
based on TR-03183-2 v2.1.0, https://www.bsi.bund.de/dok/TR-03183-en).

Python 3.12, standard library only, no network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

SPEC_VERSION = "1.6"
SHA512_HEX = re.compile(r"^[0-9a-f]{128}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

PROP_EXECUTABLE = "bsi:component:executable"
PROP_ARCHIVE = "bsi:component:archive"
PROP_STRUCTURED = "bsi:component:structured"
PROP_FILENAME = "bsi:component:filename"
PROP_LICENCE = "bsi:component:effectiveLicence"

ARCHIVE_SUFFIXES = (
    ".zip", ".whl", ".tar", ".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".jar", ".nupkg",
    ".vsix", ".crx", ".xpi", ".7z", ".deb", ".rpm", ".msi", ".gem",
)
EXECUTABLE_SUFFIXES = (".exe", ".msi", ".dmg", ".pkg", ".appimage", ".sh", ".bat", ".ps1", ".deb", ".rpm")
STRUCTURED_SUFFIXES = (".whl", ".jar", ".nupkg", ".vsix", ".crx", ".xpi", ".gem", ".deb", ".rpm", ".msi")


class SbomError(Exception):
    """Raised for unusable input; the CLI prints it and exits with status 2."""


def digest_file(path: Path) -> dict[str, str]:
    """Return the lower-case hex SHA-256 and SHA-512 of a file's bytes."""
    sha256, sha512 = hashlib.sha256(), hashlib.sha512()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
            sha512.update(chunk)
    return {"SHA-256": sha256.hexdigest(), "SHA-512": sha512.hexdigest()}


def file_purl(product: str, filename: str, version: str) -> str:
    """Package URL of a release file: pkg:generic/<product>/<filename>@<version>."""
    return f"pkg:generic/{quote(product, safe='')}/{quote(filename, safe='')}@{quote(version, safe='')}"


def image_purl(repository: str, digest: str) -> str:
    """Package URL of an image: pkg:oci/<name>@sha256:<hex>?repository_url=<registry/path>."""
    name = repository.rstrip("/").rsplit("/", 1)[-1]
    return f"pkg:oci/{quote(name, safe='')}@{quote(digest, safe=':')}?repository_url={quote(repository, safe='/')}"


def classify(filename: str) -> dict[str, str]:
    """Guess the three BSI flags of a release file from its name (reviewable defaults)."""
    lower = filename.lower()
    return {
        PROP_EXECUTABLE: "executable" if lower.endswith(EXECUTABLE_SUFFIXES) else "non-executable",
        PROP_ARCHIVE: "archive" if lower.endswith(ARCHIVE_SUFFIXES) else "no archive",
        PROP_STRUCTURED: "structured" if lower.endswith(STRUCTURED_SUFFIXES) else "unstructured",
    }


def _set_property(component: dict[str, Any], name: str, value: str) -> None:
    props = [p for p in component.get("properties", []) if p.get("name") != name]
    props.append({"name": name, "value": value})
    component["properties"] = props


def _licence_text(component: dict[str, Any]) -> str | None:
    """The licence of a component as one id, name or expression, if it has one."""
    found: list[str] = []
    for entry in component.get("licenses") or []:
        if "expression" in entry:
            found.append(str(entry["expression"]))
        elif isinstance(entry.get("license"), dict):
            lic = entry["license"]
            text = lic.get("id") or lic.get("name")
            if text:
                found.append(str(text))
    if not found:
        return None
    return found[0] if len(found) == 1 else " AND ".join(f"({x})" if " " in x else x for x in found)


def _add_root_dependency(bom: dict[str, Any], root_ref: str) -> None:
    deps = bom.setdefault("dependencies", [])
    if any(d.get("ref") == root_ref for d in deps):
        return
    refs = [c["bom-ref"] for c in bom.get("components", []) if c.get("bom-ref")]
    depended = {x for d in deps for x in d.get("dependsOn", [])}
    deps.insert(0, {"ref": root_ref, "dependsOn": [r for r in refs if r not in depended]})


def enrich(
    bom: dict[str, Any],
    *,
    kind: str,
    product: str,
    version: str,
    supplier_name: str,
    supplier_url: str,
    subject_file: Path | None = None,
    manifest_file: Path | None = None,
    repository: str = "",
    digest: str = "",
    name: str = "",
    licence: str = "",
    flags: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Make the deliverable the SBOM's main component and add the TR-03183-2 fields.

    For a file the hashes are computed from ``subject_file``. For an image the
    registry digest is the SHA-256 (verified against ``manifest_file``, the raw
    manifest bytes fetched by digest) and the SHA-512 is taken over the same
    manifest bytes, because an image has no single file to hash.
    """
    if kind not in ("file", "image"):
        raise SbomError(f"kind must be file or image, not {kind!r}")
    if bom.get("bomFormat") != "CycloneDX":
        raise SbomError("input is not a CycloneDX document")
    bom["specVersion"] = SPEC_VERSION
    bom["$schema"] = "http://cyclonedx.org/schema/bom-1.6.schema.json"
    bom["version"] = int(bom.get("version", 1))
    if not str(bom.get("serialNumber", "")).startswith("urn:uuid:"):
        bom["serialNumber"] = f"urn:uuid:{uuid.uuid4()}"

    if kind == "file":
        if subject_file is None or not subject_file.is_file():
            raise SbomError("a file deliverable needs --subject-file pointing at the real file")
        filename = subject_file.name
        hashes = digest_file(subject_file)
        purl = file_purl(product, filename, version)
        component_type = "file"
        component_name = name or filename
        props = dict(flags or classify(filename))
        props[PROP_FILENAME] = filename
    else:
        if not repository or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise SbomError("an image deliverable needs --repository and --digest sha256:<64 hex>")
        if manifest_file is None or not manifest_file.is_file():
            raise SbomError("an image deliverable needs --manifest-file (the raw manifest bytes)")
        hashes = digest_file(manifest_file)
        if f"sha256:{hashes['SHA-256']}" != digest:
            raise SbomError("the manifest bytes do not hash to the image digest")
        purl = image_purl(repository, digest)
        component_type = "container"
        component_name = name or repository
        props = dict(flags or {
            PROP_EXECUTABLE: "executable",
            PROP_ARCHIVE: "no archive",
            PROP_STRUCTURED: "structured",
        })

    supplier = {"name": supplier_name, "url": [supplier_url]}
    main: dict[str, Any] = {
        "bom-ref": purl,
        "type": component_type,
        "name": component_name,
        "version": version,
        "purl": purl,
        "supplier": supplier,
        "manufacturer": supplier,
        "hashes": [
            {"alg": "SHA-512", "content": hashes["SHA-512"]},
            {"alg": "SHA-256", "content": hashes["SHA-256"]},
        ],
        "externalReferences": [{"type": "website", "url": supplier_url}],
    }
    if licence:
        main["licenses"] = [{"expression": licence}]
        props[PROP_LICENCE] = licence
    main["properties"] = [{"name": k, "value": v} for k, v in props.items()]

    moment = (now or datetime.now(UTC)).astimezone(UTC)
    meta = bom.setdefault("metadata", {})
    meta["timestamp"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    meta["authors"] = [{"name": supplier_name}]
    meta["manufacturer"] = supplier
    meta["supplier"] = supplier
    meta["component"] = main
    tools = meta.setdefault("tools", {})
    if isinstance(tools, dict):
        tools.setdefault("components", []).append(
            {"type": "application", "author": supplier_name, "name": "sbom_tr03183.py"}
        )

    for component in bom.get("components", []):
        text = _licence_text(component)
        if text:
            _set_property(component, PROP_LICENCE, text)
    _add_root_dependency(bom, purl)
    return bom


def _props(component: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for prop in component.get("properties", []):
        out.setdefault(str(prop.get("name")), []).append(str(prop.get("value")))
    return out


def validate(bom: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Return (errors, coverage) for a document; no errors means it passes."""
    errors: list[str] = []
    if bom.get("bomFormat") != "CycloneDX":
        errors.append("bomFormat is not CycloneDX")
    if bom.get("specVersion") != SPEC_VERSION:
        errors.append(f"specVersion is {bom.get('specVersion')!r}, expected {SPEC_VERSION!r}")
    if not str(bom.get("serialNumber", "")).startswith("urn:uuid:"):
        errors.append("serialNumber (urn:uuid:...) is missing")
    meta = bom.get("metadata")
    if not isinstance(meta, dict):
        errors.append("metadata is missing")
        meta = {}
    stamp = meta.get("timestamp")
    if not stamp:
        errors.append("metadata.timestamp is missing")
    else:
        try:
            datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"metadata.timestamp is not a date: {stamp!r}")
    if not (meta.get("authors") or meta.get("manufacturer") or meta.get("supplier")):
        errors.append("the SBOM creator (metadata.authors / manufacturer) is missing")
    main = meta.get("component")
    if not isinstance(main, dict):
        errors.append("metadata.component (the deliverable) is missing")
        main = {}
    for key in ("name", "version", "purl"):
        if not main.get(key):
            errors.append(f"metadata.component.{key} is missing")
    if not (main.get("supplier") or main.get("manufacturer")):
        errors.append("metadata.component has no supplier or manufacturer")
    hashes = {h.get("alg"): str(h.get("content", "")) for h in main.get("hashes", [])}
    if not SHA512_HEX.match(hashes.get("SHA-512", "")):
        errors.append("metadata.component has no valid SHA-512 hash")
    if "SHA-256" in hashes and not SHA256_HEX.match(hashes["SHA-256"]):
        errors.append("metadata.component SHA-256 hash is malformed")
    props = _props(main)
    for name in (PROP_EXECUTABLE, PROP_ARCHIVE, PROP_STRUCTURED):
        if len(props.get(name, [])) != 1:
            errors.append(f"metadata.component must carry {name} exactly once")
    if len(props.get(PROP_FILENAME, [])) > 1:
        errors.append(f"{PROP_FILENAME} occurs more than once")
    if not bom.get("dependencies"):
        errors.append("dependencies[] is missing")

    components = bom.get("components") or []
    total = len(components)

    def share(test: Any) -> dict[str, Any]:
        count = sum(1 for c in components if test(c))
        return {"count": count, "total": total, "percent": round(100 * count / total, 1) if total else 0.0}

    coverage = {
        "purl": share(lambda c: bool(c.get("purl"))),
        "licence": share(lambda c: bool(c.get("licenses"))),
        "hash": share(lambda c: bool(c.get("hashes"))),
        "sha512": share(lambda c: any(h.get("alg") == "SHA-512" for h in c.get("hashes", []))),
    }
    return errors, coverage


def summary_markdown(label: str, errors: list[str], coverage: dict[str, Any]) -> str:
    """Render the validation result as Markdown for the step summary."""
    lines = [f"#### SBOM check: {label}", "", f"Result: {'FAILED' if errors else 'passed'}", ""]
    lines += [f"- ERROR: {e}" for e in errors]
    if errors:
        lines.append("")
    lines += ["| Components with | Count | Share |", "| --- | ---: | ---: |"]
    names = {"purl": "package URL", "licence": "licence", "hash": "any hash", "sha512": "SHA-512 hash"}
    for key, text in names.items():
        c = coverage[key]
        lines.append(f"| {text} | {c['count']} of {c['total']} | {c['percent']}% |")
    lines.append("")
    return "\n".join(lines)


def _load(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SbomError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SbomError(f"{path} is not a JSON object")
    return data


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("enrich", help="make the deliverable the main component and add BSI fields")
    e.add_argument("--input", required=True)
    e.add_argument("--output", required=True)
    e.add_argument("--kind", choices=("file", "image"), required=True)
    e.add_argument("--product", required=True)
    e.add_argument("--version", required=True)
    e.add_argument("--supplier-name", required=True)
    e.add_argument("--supplier-url", required=True)
    e.add_argument("--subject-file", help="the release file (kind file)")
    e.add_argument("--manifest-file", help="raw image manifest bytes (kind image)")
    e.add_argument("--repository", default="", help="registry/path without tag or digest (kind image)")
    e.add_argument("--digest", default="", help="sha256:<hex> (kind image)")
    e.add_argument("--name", default="", help="component name; default the filename or repository")
    e.add_argument("--licence", default="", help="SPDX expression of the deliverable, if known")
    v = sub.add_parser("validate", help="check the required fields and report coverage")
    v.add_argument("--input", required=True)
    v.add_argument("--label", default="")
    v.add_argument("--summary-file", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "enrich":
            bom = enrich(
                _load(args.input),
                kind=args.kind,
                product=args.product,
                version=args.version,
                supplier_name=args.supplier_name,
                supplier_url=args.supplier_url,
                subject_file=Path(args.subject_file) if args.subject_file else None,
                manifest_file=Path(args.manifest_file) if args.manifest_file else None,
                repository=args.repository,
                digest=args.digest,
                name=args.name,
                licence=args.licence,
            )
            Path(args.output).write_text(json.dumps(bom, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(f"wrote {args.output}")
            return 0
        errors, coverage = validate(_load(args.input))
        text = summary_markdown(args.label or args.input, errors, coverage)
        print(text)
        if args.summary_file:
            with open(args.summary_file, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(text + "\n")
        return 1 if errors else 0
    except SbomError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
