"""Help that lives in other repositories: the docs hub's sources.toml entries with site "help" (README.md, "Help from
other repositories"). Read by build.py, which mounts them on the public site, and by check.py, which checks them.

Each source is an agent's `docs/help/`: pages/, pages-de/, zensical.toml ([project] nav: one titled section),
nav-de.toml, questions.toml and questions-de.toml, mounted at agents/<agent>/.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

MOUNT = re.compile(r"agents/[a-z0-9][a-z0-9-]*")
#: Each language's files in a source, as in docs/help itself.
FILES: dict[str, dict[str, str]] = {
    "en": {"pages": "pages", "nav": "zensical.toml", "questions": "questions.toml"},
    "de": {"pages": "pages-de", "nav": "nav-de.toml", "questions": "questions-de.toml"},
}


@dataclass(frozen=True)
class Source:
    """One mounted Help: its key, where it is mounted, and its docs/help folder."""

    key: str
    mount: str
    root: Path

    def pages(self, lang: str) -> Path:
        return self.root / FILES[lang]["pages"]

    def questions(self, lang: str) -> dict:
        return tomllib.loads((self.root / FILES[lang]["questions"]).read_text(encoding="utf-8"))

    def section(self, lang: str) -> tuple[str, list]:
        """The source's one titled section in *lang*: its title and its items, paths relative to its pages."""
        name = FILES[lang]["nav"]
        data = tomllib.loads((self.root / name).read_text(encoding="utf-8"))
        nav = data["project"]["nav"] if "project" in data else data.get("nav")
        if not (isinstance(nav, list) and len(nav) == 1 and isinstance(nav[0], dict) and len(nav[0]) == 1):
            raise SystemExit(f"{self.key}: {name} needs a nav of one titled section")
        ((title, items),) = nav[0].items()
        if not isinstance(items, list):
            raise SystemExit(f"{self.key}: {name}'s section {title!r} needs a list of pages")
        return title, items

    def mounted(self, lang: str) -> dict[str, list]:
        """The section with its paths below the mount, for the site's nav."""
        title, items = self.section(lang)
        return {title: prefixed(items, self.mount)}

    def files(self, lang: str = "en") -> list[str]:
        """The pages the nav lists, relative to the source's pages."""
        return nav_files(self.section(lang)[1])


def nav_files(nav: list) -> list[str]:
    out: list[str] = []
    for item in nav:
        for value in item.values() if isinstance(item, dict) else [item]:
            out.extend(nav_files(value) if isinstance(value, list) else [value])
    return out


def prefixed(items: list, mount: str) -> list:
    out: list = []
    for item in items:
        if isinstance(item, str):
            out.append(f"{mount}/{item}")
        else:
            ((title, value),) = item.items()
            out.append({title: prefixed(value, mount) if isinstance(value, list) else f"{mount}/{value}"})
    return out


def load(sources_toml: Path, checkouts: Path | None = None, allow_missing: bool = False) -> list[Source]:
    """The Help sources of *sources_toml*, in its order, each looked up as <checkouts>/<repo name>/<docs>.

    *checkouts* defaults to the folder that holds the hub (sibling checkouts). A source not checked out stops the
    build, or with *allow_missing* is left out (and said so).
    """
    entries = tomllib.loads(sources_toml.read_text(encoding="utf-8")).get("source", [])
    base = checkouts or sources_toml.resolve().parent.parent
    found: list[Source] = []
    for entry in (e for e in entries if e.get("site") == "help"):
        key, mount = entry.get("key", ""), entry.get("mount", "")
        if not MOUNT.fullmatch(mount):
            raise SystemExit(f"sources.toml: {key or entry.get('repo')}: mount {mount!r} is not agents/<agent>")
        if any(s.key == key or s.mount == mount for s in found):
            raise SystemExit(f"sources.toml: {key} ({mount}) is listed twice")
        root = base / entry["repo"].split("/")[-1] / entry.get("docs", "docs/help")
        missing = [n for lang in FILES for n in FILES[lang].values() if not (root / n).exists()]
        if missing:
            if not allow_missing:
                raise SystemExit(f"{key}: {root} lacks {', '.join(missing)} (check the repository out, or --allow-missing)")
            print(f"left out {key}: {root} lacks {', '.join(missing)}")
            continue
        source = Source(key, mount, root)
        if sorted(source.files("de")) != sorted(source.files("en")):
            raise SystemExit(f"{key}: nav-de.toml must list the same pages as zensical.toml")
        found.append(source)
    return found
