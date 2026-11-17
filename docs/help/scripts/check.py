"""Check Help pages before they go anywhere.

    python docs/help/scripts/check.py [PATH ...]      # default: docs/help/pages/ and docs/help/pages-de/
    python docs/help/scripts/check.py --sources ../docs/sources.toml    # and the Help mounted from other repos

Two checks:
  - the docs hub's scrub patterns (private addresses, lab names, internal wording, timings, e-mail addresses,
    secrets): the same rules the hub applies when it builds help.fadenstack.com. Read from the hub, so they are
    kept in one place: FADEN_DOCS_HUB, or the hub checked out next to this repo (../docs);
  - every relative link to a Markdown page, and every picture, points at a file that exists in the site as it is
    put together: this Help at the root, each source at its mount (README.md, "Help from other repositories").
    A translation also finds the English files, as the build falls back to them (pictures, pages not translated).
    Without --sources nothing is mounted, so a page here that links into agents/ fails: the console has none.

A reviewed line passes the scrub when it ends with `<!-- scrub: ok -->`, as in the hub.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import posixpath
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources import FILES, Source  # noqa: E402  (scripts/sources.py: Help from other repositories)
from sources import load as load_sources  # noqa: E402

HELP = Path(__file__).resolve().parent.parent          # docs/help
REPO = HELP.parent.parent                               # the core repo
HUB = Path(os.environ.get("FADEN_DOCS_HUB") or REPO.parent / "docs")
HUB_SCRUB = HUB / "scripts" / "scrub.py"
LINK = re.compile(r"\]\(([^)\s]+?\.(?:md|png|jpe?g|svg|webp))(?:#[^)\s]*)?\)")


def scrub_rules() -> dict[str, re.Pattern[str]]:
    if not HUB_SCRUB.is_file():
        raise SystemExit(f"needs the docs hub (FADEN_DOCS_HUB, or ../docs next to this repo): {HUB_SCRUB} not found")
    spec = importlib.util.spec_from_file_location("hub_scrub", HUB_SCRUB)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rules = {name: re.compile(rx) for name, rx in module.PATTERNS.items()}
    for i, rx in enumerate(l for l in os.environ.get("SCRUB_EXTRA", "").splitlines() if l.strip()):
        rules[f"private pattern {i + 1}"] = re.compile(rx)
    return rules


def site_trees(sources: list[Source]) -> list[tuple[str, Path, str]]:
    """Every tree of pages and where it lands in the site: (language, folder, mount)."""
    trees = [(lang, HELP / files["pages"], "") for lang, files in FILES.items()]
    trees += [(lang, s.pages(lang), s.mount + "/") for s in sources for lang in FILES]
    return [(lang, folder, mount) for lang, folder, mount in trees if folder.is_dir()]


def site_files(trees: list[tuple[str, Path, str]]) -> dict[str, set[str]]:
    """Each language's files by their path in the site (a translation also has the English ones: its fallback)."""
    files: dict[str, set[str]] = {lang: set() for lang in FILES}
    for lang, folder, mount in trees:
        files[lang] |= {mount + p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    for lang in files:
        files[lang] |= files["en"]
    return files


def in_site(page: Path, trees: list[tuple[str, Path, str]]) -> tuple[str, str] | None:
    """The language and the site path of *page*, if it belongs to one of the trees."""
    for lang, folder, mount in trees:
        if page.resolve().is_relative_to(folder.resolve()):
            return lang, mount + page.resolve().relative_to(folder.resolve()).as_posix()
    return None


def broken(page: Path, target: str, trees: list[tuple[str, Path, str]], files: dict[str, set[str]]) -> str:
    """What is wrong with a relative link of *page* in the site as it is put together (the mounts included), or "".
    A page outside the trees is looked up beside it."""
    placed = in_site(page, trees)
    if placed is None:
        return "" if (page.parent / target).resolve().is_file() else "link to a file that does not exist"
    lang, at = placed
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(at), target))
    if not at.startswith("agents/") and resolved.startswith("agents/"):
        return "link into agents/, which the console's /help does not have"
    if resolved.startswith("../") or resolved not in files[lang]:
        return "link to a file that does not exist"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Check Help pages: the hub's scrub, and every relative link.")
    ap.add_argument("paths", nargs="*", type=Path, help="pages or folders (default: every tree)")
    ap.add_argument("--sources", type=Path, help="the docs hub's sources.toml: check its Help sources too")
    ap.add_argument("--checkouts", type=Path, help="where the sources' repositories are (default: next to the hub)")
    ap.add_argument("--allow-missing", action="store_true", help="leave out a source that is not checked out")
    args = ap.parse_args()
    sources = load_sources(args.sources, args.checkouts, args.allow_missing) if args.sources else []
    trees = site_trees(sources)
    files = site_files(trees)
    targets = args.paths or [folder for _, folder, _ in trees]
    pages = sorted(p for t in targets for p in ([t] if t.is_file() else t.rglob("*.md")))
    rules = scrub_rules()
    hits = []
    for page in pages:
        in_code = False
        for n, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{page.relative_to(REPO) if page.is_relative_to(REPO) else page}:{n}"
            if line.lstrip().startswith("```"):
                in_code = not in_code
            if not line.rstrip().endswith("<!-- scrub: ok -->"):
                for name, rx in rules.items():
                    m = rx.search(line)
                    if m:
                        hits.append(f"{where}: {name}: {m.group(0)!r}")
            if in_code:
                continue
            for target in LINK.findall(line):
                if "://" in target:
                    continue
                problem = broken(page, target, trees, files)
                if problem:
                    hits.append(f"{where}: {problem}: {target}")
    if hits:
        print("\n".join(hits))
        print(f"\n{len(hits)} line(s) need a look.")
        return 1
    print(f"check: {len(pages)} page(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
