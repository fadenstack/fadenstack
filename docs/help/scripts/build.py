"""Build the Help site: for the public site (help.fadenstack.com) or for /help in the console.

    python docs/help/scripts/build.py --target public  --out build/help                  # then scrub, then build
    python docs/help/scripts/build.py --target console --out /tmp/help --build            # what the frontend image runs

Puts together, in OUT:
  - docs/       the pages (pages/) and the theme's assets (docs/theme/assets)
  - overrides/  the theme's template overrides (docs/theme/overrides)
  - zensical.toml  site.toml with the address, the top bar's links and the nav (zensical.toml here) filled in
and, with --build, runs `zensical build` (strict) there, so the site is in OUT/site.

The two targets differ only where they must: the public site has its address (instant navigation needs it) and
links to the website and the Developers site; the console's copy is served at /help on whatever address the
install has, so it has no fixed address and links back to the console.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HELP = Path(__file__).resolve().parent.parent          # docs/help
THEME = HELP.parent / "theme"                           # docs/theme

TARGETS = {
    "public": {
        "site_url": 'site_url = "https://help.fadenstack.com/"',
        "sites": [
            {"name": "Website", "url": "https://fadenstack.com/"},
            {"name": "Help", "url": "https://help.fadenstack.com/", "key": "help"},
            {"name": "Developers", "url": "https://developers.fadenstack.com/", "key": "developers"},
        ],
        "instant": '"navigation.instant",',
    },
    "console": {
        "site_url": "",
        "sites": [
            {"name": "Console", "url": "/"},
            {"name": "Help", "url": "/help/", "key": "help"},
            {"name": "Developers", "url": "https://developers.fadenstack.com/", "key": "developers"},
        ],
        "instant": "",
    },
}


def toml_value(v) -> str:
    """Inline TOML for the nav and the sites: strings, lists and single-line tables."""
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{json.dumps(k, ensure_ascii=False)} = {toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"unexpected value: {v!r}")


def assemble(out: Path, target: str, theme: Path = THEME) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():  # empty it, but keep the folder: a preview server may be running in it
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copytree(HELP / "pages", out / "docs")
    shutil.copytree(theme / "assets", out / "docs" / "assets", dirs_exist_ok=True)
    shutil.copytree(theme / "overrides", out / "overrides")

    nav = tomllib.loads((HELP / "zensical.toml").read_text(encoding="utf-8"))["project"]["nav"]
    t = TARGETS[target]
    config = (HELP / "site.toml").read_text(encoding="utf-8")
    for marker, value in (
        ("#SITE_URL#", t["site_url"]),
        ("#NAV#", "nav = " + toml_value(nav)),
        ("#SITES#", "sites = " + toml_value(t["sites"])),
        ("#INSTANT#", t["instant"]),
    ):
        if config.count(marker) != 1:
            raise SystemExit(f"site.toml needs exactly one {marker}")
        config = config.replace(marker, value)
    (out / "zensical.toml").write_text(config, encoding="utf-8")


def build(out: Path) -> None:
    exe = shutil.which("zensical") or str(Path(sys.executable).with_name("zensical"))
    subprocess.run([exe, "build", "--strict"], cwd=out, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=sorted(TARGETS), required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--theme", type=Path, default=THEME, help="default: docs/theme next to docs/help")
    ap.add_argument("--build", action="store_true", help="also run `zensical build --strict` in OUT")
    args = ap.parse_args()
    assemble(args.out, args.target, args.theme)
    print(f"assembled {args.out} ({args.target})")
    if args.build:
        build(args.out)
        print(f"built {args.out / 'site'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
