"""`faden version` says what the installation runs, not only which CLI it is."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from faden_cli.core import installation
from faden_cli.core.bundle import MARKER


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A source checkout with its install directory (shared/), an agent version and a runtime manifest."""
    repo = tmp_path / "fadenstack"
    (repo / "shared").mkdir(parents=True)
    (repo / "shared" / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    install = repo / "backend/faden_backend/web/api/install"
    install.mkdir(parents=True)
    (install / "__init__.py").write_text('AGENT_VERSION = "0.2.7"\n', encoding="utf-8")
    (repo / "runtime-image").mkdir()
    (repo / "runtime-image" / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "runtime_id": "faden-ray-vllm-gb10-ray2.58-vllm0.29.0",
                "certification": {"overall_status": "passed", "channel": "preview"},
            }
        ),
        encoding="utf-8",
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first")
    _git(repo, "tag", "v0.4.1")
    return repo


def _rows(install_dir: Path) -> dict[str, str]:
    rows = installation.describe(install_dir)
    return {k: v for k, v in rows if k}


def test_a_build_is_recorded_and_shown(checkout: Path) -> None:
    shared = checkout / "shared"
    record = installation.record_build(shared)
    assert record is not None and record["describe"] == "v0.4.1" and record["complete"] is True
    assert installation.read_build(shared)["commit"] == record["commit"]

    rows = _rows(shared)
    assert rows["Installed"] == "source checkout at v0.4.1 on main"
    assert rows["Images built from"].startswith("v0.4.1, ")
    assert "moved" not in rows["Images built from"]
    assert rows["Machine agent"].startswith("0.2.7")
    assert rows["Runtimes"] == "faden-ray-vllm-gb10-ray2.58-vllm0.29.0 (passed, preview)"


def test_a_checkout_that_moved_after_the_build_says_so(checkout: Path) -> None:
    shared = checkout / "shared"
    installation.record_build(shared)
    (checkout / "README.md").write_text("later\n", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "later")

    rows = _rows(shared)
    assert rows["Installed"].startswith("source checkout at v0.4.1-1-g")
    assert "checkout has moved since" in rows["Images built from"]


def test_local_changes_and_failed_builds_are_shown(checkout: Path) -> None:
    shared = checkout / "shared"
    (shared / "docker-compose.yaml").write_text("services: {a: {}}\n", encoding="utf-8")
    installation.record_build(shared, complete=False)

    rows = _rows(shared)
    assert rows["Installed"].endswith(", with local changes")
    assert "with local changes" in rows["Images built from"]
    assert "some images failed to build" in rows["Images built from"]


def test_files_the_install_rewrites_and_other_tags_do_not_count(checkout: Path) -> None:
    targets = checkout / "shared" / "prometheus" / "targets.json"
    targets.parent.mkdir()
    targets.write_text("[]\n", encoding="utf-8")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "targets")
    _git(checkout, "tag", "node-agent-v0.2.7")
    targets.write_text('[{"targets": ["a:1"]}]\n', encoding="utf-8")  # what the backend writes

    installed = _rows(checkout / "shared")["Installed"]
    assert installed.startswith("source checkout at v0.4.1-1-g")
    assert installed.endswith(" on main")  # no "with local changes"


def test_a_checkout_without_a_release_tag_is_named_by_its_commit(checkout: Path) -> None:
    _git(checkout, "tag", "-d", "v0.4.1")
    _git(checkout, "tag", "cli-v0.3.0")
    installed = _rows(checkout / "shared")["Installed"]
    assert installed.startswith("source checkout at ") and "cli-v" not in installed


def test_a_checkout_without_a_record_asks_for_the_next_upgrade(checkout: Path) -> None:
    assert _rows(checkout / "shared")["Images built from"].startswith("not recorded")


def test_an_install_from_a_release_names_the_release(tmp_path: Path) -> None:
    (tmp_path / MARKER).write_text(json.dumps({"version": "0.4.1"}), encoding="utf-8")
    (tmp_path / ".env").write_text("VERSION=0.4.1\n", encoding="utf-8")

    assert installation.record_build(tmp_path) is None  # nothing to record: the images are the release's
    rows = _rows(tmp_path)
    assert rows["Installed"] == "release 0.4.1 (published images)"
    assert rows["Image tag"] == "0.4.1"
    assert "Images built from" not in rows


def test_a_missing_install_directory_is_named(tmp_path: Path) -> None:
    rows = _rows(tmp_path / "gone")
    assert rows["Installed"].startswith("[red]not found")
