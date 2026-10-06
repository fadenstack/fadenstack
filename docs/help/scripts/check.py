"""Check Help pages before they go anywhere.

    python docs/help/scripts/check.py [PATH ...]      # default: docs/help/pages/

Two checks:
  - the docs hub's scrub patterns (private addresses, lab names, internal wording, timings, e-mail addresses,
    secrets): the same rules the hub applies when it builds help.fadenstack.com. Read from the hub, so they are
    kept in one place: FADEN_DOCS_HUB, or the hub checked out next to this repo (../docs);
  - every relative link to a Markdown page, and every picture, points at a file that exists.

A reviewed line passes the scrub when it ends with `<!-- scrub: ok -->`, as in the hub.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

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


def main() -> int:
    targets = [Path(a) for a in sys.argv[1:]] or [HELP / "pages"]
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
                if not (page.parent / target).resolve().is_file():
                    hits.append(f"{where}: link to a file that does not exist: {target}")
    if hits:
        print("\n".join(hits))
        print(f"\n{len(hits)} line(s) need a look.")
        return 1
    print(f"check: {len(pages)} page(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
