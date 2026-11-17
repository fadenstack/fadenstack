#!/usr/bin/env python3
"""Write an in-toto SLSA v1 provenance statement from the GitHub Actions context.

Used when GitHub's own attestations are not available (a private repository on
the free plan). The statement names every subject by sha256, the builder (the
workflow ref), the source (repository and commit) and the run. ``--predicate-out``
writes the predicate alone, which is what ``cosign attest`` and
``cosign attest-blob`` wrap into their own statement for one subject and sign.

Python 3.12, standard library only, no network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
BUILD_TYPE = "https://actions.github.io/buildtypes/workflow/v1"
DIGEST_RE = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_predicate(env: Mapping[str, str], builder_id: str = "") -> dict[str, Any]:
    """The SLSA v1 predicate for the current run, from GITHUB_* variables."""
    server = env.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = env.get("GITHUB_REPOSITORY", "")
    sha = env.get("GITHUB_SHA", "")
    ref = env.get("GITHUB_REF", "")
    workflow_ref = env.get("GITHUB_WORKFLOW_REF", "")
    workflow_path = workflow_ref.split("@", 1)[0].removeprefix(f"{repo}/")
    run_id = env.get("GITHUB_RUN_ID", "")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
    return {
        "buildDefinition": {
            "buildType": BUILD_TYPE,
            "externalParameters": {
                "workflow": {"ref": ref, "repository": f"{server}/{repo}", "path": workflow_path},
            },
            "internalParameters": {
                "github": {
                    "event_name": env.get("GITHUB_EVENT_NAME", ""),
                    "repository_id": env.get("GITHUB_REPOSITORY_ID", ""),
                    "runner_environment": env.get("RUNNER_ENVIRONMENT", ""),
                },
            },
            "resolvedDependencies": [
                {"uri": f"git+{server}/{repo}@{ref}", "digest": {"gitCommit": sha}},
            ],
        },
        "runDetails": {
            "builder": {"id": builder_id or f"{server}/{workflow_ref}"},
            "metadata": {"invocationId": f"{server}/{repo}/actions/runs/{run_id}/attempts/{attempt}"},
        },
    }


def build_statement(subjects: list[dict[str, Any]], predicate: dict[str, Any]) -> dict[str, Any]:
    """The in-toto statement for the given subjects."""
    if not subjects:
        raise ValueError("at least one subject is required")
    return {"_type": STATEMENT_TYPE, "subject": subjects, "predicateType": PREDICATE_TYPE, "predicate": predicate}


def parse_subjects(files: list[str], digests: list[str]) -> list[dict[str, Any]]:
    """Subjects from --subject-file PATH and --subject-digest NAME=sha256:HEX."""
    subjects: list[dict[str, Any]] = []
    for item in files:
        path = Path(item)
        subjects.append({"name": path.name, "digest": {"sha256": sha256_file(path)}})
    for item in digests:
        name, _, digest = item.rpartition("=")
        match = DIGEST_RE.match(digest)
        if not name or not match:
            raise ValueError(f"expected NAME=sha256:<64 hex>, got {item!r}")
        subjects.append({"name": name, "digest": {"sha256": match.group(1)}})
    return subjects


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--subject-file", action="append", default=[], help="a release file (repeatable)")
    p.add_argument("--subject-digest", action="append", default=[], help="NAME=sha256:HEX, e.g. an image (repeatable)")
    p.add_argument("--builder-id", default="", help="default: the server URL plus GITHUB_WORKFLOW_REF")
    p.add_argument("--statement-out", required=True)
    p.add_argument("--predicate-out", default="")
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        subjects = parse_subjects(args.subject_file, args.subject_digest)
        predicate = build_predicate(os.environ, args.builder_id)
        statement = build_statement(subjects, predicate)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    Path(args.statement_out).write_text(json.dumps(statement, indent=2) + "\n", encoding="utf-8", newline="\n")
    if args.predicate_out:
        Path(args.predicate_out).write_text(json.dumps(predicate, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.statement_out} with {len(subjects)} subject(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
