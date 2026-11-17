"""Tests for slsa_provenance.py."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import slsa_provenance as slsa  # noqa: E402

ENV = {
    "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_REPOSITORY": "example/repo",
    "GITHUB_SHA": "a" * 40,
    "GITHUB_REF": "refs/tags/v1.0.0",
    "GITHUB_WORKFLOW_REF": "example/repo/.github/workflows/release.yml@refs/tags/v1.0.0",
    "GITHUB_RUN_ID": "42",
    "GITHUB_RUN_ATTEMPT": "2",
    "GITHUB_EVENT_NAME": "push",
}


def test_statement_shape(tmp_path: Path) -> None:
    f = tmp_path / "a.whl"
    f.write_bytes(b"abc")
    subjects = slsa.parse_subjects([str(f)], ["ghcr.io/example/img=sha256:" + "b" * 64])
    st = slsa.build_statement(subjects, slsa.build_predicate(ENV))
    assert st["_type"] == "https://in-toto.io/Statement/v1"
    assert st["predicateType"] == "https://slsa.dev/provenance/v1"
    assert st["subject"][0] == {"name": "a.whl", "digest": {"sha256": hashlib.sha256(b"abc").hexdigest()}}
    assert st["subject"][1] == {"name": "ghcr.io/example/img", "digest": {"sha256": "b" * 64}}


def test_predicate_describes_builder_source_and_run() -> None:
    p = slsa.build_predicate(ENV)
    assert p["runDetails"]["builder"]["id"] == "https://github.com/example/repo/.github/workflows/release.yml@refs/tags/v1.0.0"
    dep = p["buildDefinition"]["resolvedDependencies"][0]
    assert dep["uri"] == "git+https://github.com/example/repo@refs/tags/v1.0.0"
    assert dep["digest"] == {"gitCommit": "a" * 40}
    assert p["runDetails"]["metadata"]["invocationId"] == "https://github.com/example/repo/actions/runs/42/attempts/2"
    assert p["buildDefinition"]["externalParameters"]["workflow"]["path"] == ".github/workflows/release.yml"


def test_builder_id_override() -> None:
    assert slsa.build_predicate(ENV, "https://example.invalid/b")["runDetails"]["builder"]["id"] == "https://example.invalid/b"


def test_bad_digest_and_no_subject_are_rejected() -> None:
    with pytest.raises(ValueError):
        slsa.parse_subjects([], ["name=sha256:xyz"])
    with pytest.raises(ValueError):
        slsa.build_statement([], {})


def test_cli_writes_statement_and_predicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    f = tmp_path / "x.bin"
    f.write_bytes(b"x")
    st, pr = tmp_path / "s.json", tmp_path / "p.json"
    assert slsa.main(["--subject-file", str(f), "--statement-out", str(st), "--predicate-out", str(pr)]) == 0
    assert json.loads(st.read_text(encoding="utf-8"))["predicate"] == json.loads(pr.read_text(encoding="utf-8"))
