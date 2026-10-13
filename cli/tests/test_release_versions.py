"""Every service carries the release's version, the CLI's: a release commit that bumps only the CLI fails here.

0.4.4 shipped its backend as 0.4.3, and the console's Updates page said so.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("backend", "gateway", "pii", "mcp", "skills")
if not (ROOT / "backend" / "pyproject.toml").exists():
    pytest.skip("not a checkout of the whole repository", allow_module_level=True)


def _cli_version() -> str:
    version = tomllib.loads((ROOT / "cli" / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    init = (ROOT / "cli" / "src" / "faden_cli" / "__init__.py").read_text(encoding="utf-8")
    assert re.search(rf'^__version__ = "{re.escape(version)}"$', init, re.M), "cli __init__ and pyproject differ"
    return version


@pytest.mark.parametrize("service", SERVICES)
def test_each_service_has_the_release_version(service: str) -> None:
    project = tomllib.loads((ROOT / service / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["version"] == _cli_version(), f"{service}/pyproject.toml: bump it with the release"

    # uv.lock records the project's own version too; a stale one fails `uv lock --check`.
    name = re.sub(r"[-_.]+", "-", project["name"]).lower()
    lock = tomllib.loads((ROOT / service / "uv.lock").read_text(encoding="utf-8"))
    own = [p for p in lock["package"] if p["name"] == name and "." in p.get("source", {}).values()]
    assert [p["version"] for p in own] == [project["version"]], f"{service}/uv.lock: bump {name} with the release"
