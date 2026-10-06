"""Build the Help site: for the public site (help.fadenstack.com) or for /help in the console, in every language.

    python docs/help/scripts/build.py --target public  --out build/help --build    # what the docs hub publishes
    python docs/help/scripts/build.py --target console --out /tmp/help --build     # what the frontend image serves

For each language (English at the root, German under de/) it puts together, in OUT/<lang>:
  - docs/       the pages (pages/, pages-de/) and the theme's assets (docs/theme/assets); a translation
                uses the English pictures, and a page not translated yet is the English one with a note
  - overrides/  the theme's template overrides (docs/theme/overrides)
  - zensical.toml  site.toml with the language's name, address, top-bar links, language switch and nav
and, with --build, runs `zensical build` (strict) in each and puts the sites together in OUT/site.

The two targets differ only where they must: the public site has its address (instant navigation needs it) and
links to the website and the Developers site; the console's copy is served at /help on whatever address the
install has, so it has no fixed address and links back to the console.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HELP = Path(__file__).resolve().parent.parent          # docs/help
THEME = HELP.parent / "theme"                           # docs/theme

#: Each language: its pages, its nav, where it is served below the site's root, and its words.
LANGS: dict[str, dict[str, str]] = {
    "en": {
        "pages": "pages",
        "nav": "zensical.toml",
        "prefix": "",
        "name": "English",
        "site_name": "Fadenstack Help",
        "description": "Using, administering and running Fadenstack: the chat, the console, the server and the GPU machines.",
        "privacy": "Privacy",
        "website": "Website",
        "help": "Help",
        "developers": "Developers",
        "console": "Console",
        "untranslated": "",
    },
    "de": {
        "pages": "pages-de",
        "nav": "nav-de.toml",
        "prefix": "de/",
        "name": "Deutsch",
        "site_name": "Fadenstack-Hilfe",
        "description": "Fadenstack nutzen, verwalten und betreiben: der Chat, die Konsole, der Server und die GPU-Maschinen.",
        "privacy": "Datenschutz",
        "website": "Website",
        "help": "Hilfe",
        "developers": "Developers",
        "console": "Konsole",
        "untranslated": '!!! note "Noch nicht übersetzt"\n    Diese Seite gibt es noch nicht auf Deutsch; hier steht die englische Fassung.\n\n',
    },
}

#: Titles for admonitions written without one: the theme names them after their kind, in English.
ADMONITION_TITLES: dict[str, dict[str, str]] = {
    "de": {"note": "Hinweis", "warning": "Achtung", "tip": "Tipp", "info": "Info", "danger": "Gefahr",
           "example": "Beispiel", "question": "Frage", "success": "Erledigt", "failure": "Fehlgeschlagen"},
}
_UNTITLED = re.compile(r"^(?P<indent>\s*)(?P<mark>!!!|\?\?\?\+?) (?P<kind>[a-z]+)\s*$", re.MULTILINE)


def _title_admonitions(docs: Path, lang: str) -> None:
    """Give the untitled admonitions of a translation a title in its language."""
    titles = ADMONITION_TITLES.get(lang)
    if not titles:
        return
    for page in docs.rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        titled = _UNTITLED.sub(
            lambda m: f'{m["indent"]}{m["mark"]} {m["kind"]} "{titles[m["kind"]]}"' if m["kind"] in titles else m[0],
            text,
        )
        if titled != text:
            page.write_text(titled, encoding="utf-8")

#: Where each target's Help lives, and its other links.
TARGETS: dict[str, dict[str, object]] = {
    "public": {"base": "https://help.fadenstack.com/", "site_url": True, "instant": True, "home": "https://fadenstack.com/"},
    "console": {"base": "/help/", "site_url": False, "instant": False, "home": "/"},
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


def _nav(lang: str) -> list:
    data = tomllib.loads((HELP / LANGS[lang]["nav"]).read_text(encoding="utf-8"))
    return data["project"]["nav"] if "project" in data else data["nav"]


def _files(nav: list) -> list[str]:
    out: list[str] = []
    for item in nav:
        for value in item.values() if isinstance(item, dict) else [item]:
            out.extend(_files(value) if isinstance(value, list) else [value])
    return out


def _sites(target: str, lang: str) -> list[dict[str, str]]:
    t, words = TARGETS[target], LANGS[lang]
    help_url = f"{t['base']}{words['prefix']}"
    first = (
        {"name": words["website"], "url": str(t["home"])}
        if target == "public"
        else {"name": words["console"], "url": str(t["home"])}
    )
    return [
        first,
        {"name": words["help"], "url": help_url, "key": "help"},
        {"name": words["developers"], "url": "https://developers.fadenstack.com/", "key": "developers"},
    ]


def _with_note(text: str, note: str) -> str:
    """*text* with *note* after its front matter and its title."""
    head = ""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            head, text = text[: end + 5], text[end + 5 :]
    lines = text.split("\n")
    at = next((i + 1 for i, line in enumerate(lines) if line.startswith("# ")), 0)
    return head + "\n".join([*lines[:at], "", note.rstrip("\n"), "", *lines[at:]])


def assemble(out: Path, target: str, lang: str = "en", theme: Path = THEME) -> list[str]:
    """Put one language's site together in *out*; returns the pages that fell back to English."""
    words, t = LANGS[lang], TARGETS[target]
    pages = HELP / words["pages"]
    english = _files(_nav("en"))
    if sorted(_files(_nav(lang))) != sorted(english):
        raise SystemExit(f"{words['nav']} must list the same pages as zensical.toml")

    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():  # empty it, but keep the folder: a preview server may be running in it
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copytree(pages, out / "docs", dirs_exist_ok=True) if pages.is_dir() else (out / "docs").mkdir()
    if lang != "en":
        # The English pictures, until there are others; a page not translated yet is the English one.
        shutil.copytree(HELP / "pages" / "images", out / "docs" / "images", dirs_exist_ok=True)
    fell_back = []
    for page in english:
        if not (out / "docs" / page).is_file():
            target_file = out / "docs" / page
            target_file.parent.mkdir(parents=True, exist_ok=True)
            text = (HELP / "pages" / page).read_text(encoding="utf-8")
            target_file.write_text(_with_note(text, words["untranslated"]), encoding="utf-8")
            fell_back.append(page)
    _title_admonitions(out / "docs", lang)
    shutil.copytree(theme / "assets", out / "docs" / "assets", dirs_exist_ok=True)
    shutil.copytree(theme / "overrides", out / "overrides")

    base = str(t["base"])
    alternate = [
        {"name": LANGS[code]["name"], "link": f"{base}{LANGS[code]['prefix']}", "lang": code} for code in LANGS
    ]
    copyright_line = (
        "copyright = '© 2026 Emagin8 UG (haftungsbeschränkt) · "
        '<a href="https://fadenstack.com/impressum/">Impressum</a> · '
        f'<a href="https://fadenstack.com/privacy/">{words["privacy"]}</a>\''
    )
    config = (HELP / "site.toml").read_text(encoding="utf-8")
    for marker, value in (
        ("#SITE_NAME#", f"site_name = {toml_value(words['site_name'])}"),
        ("#SITE_DESCRIPTION#", f"site_description = {toml_value(words['description'])}"),
        ("#COPYRIGHT#", copyright_line),
        ("#SITE_URL#", f'site_url = "{base}{words["prefix"]}"' if t["site_url"] else ""),
        ("#NAV#", "nav = " + toml_value(_nav(lang))),
        ("#SITES#", "sites = " + toml_value(_sites(target, lang))),
        ("#ALTERNATE#", "alternate = " + toml_value(alternate)),
        ("#LANGUAGE#", f'language = "{lang}"'),
        ("#INSTANT#", '"navigation.instant",' if t["instant"] else ""),
    ):
        if config.count(marker) != 1:
            raise SystemExit(f"site.toml needs exactly one {marker}")
        config = config.replace(marker, value)
    (out / "zensical.toml").write_text(config, encoding="utf-8")
    return fell_back


def build(out: Path) -> None:
    exe = shutil.which("zensical") or str(Path(sys.executable).with_name("zensical"))
    subprocess.run([exe, "build", "--strict"], cwd=out, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=sorted(TARGETS), required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--lang", choices=["all", *LANGS], default="all")
    ap.add_argument("--theme", type=Path, default=THEME, help="default: docs/theme next to docs/help")
    ap.add_argument("--build", action="store_true", help="also run `zensical build --strict` and put the sites together")
    args = ap.parse_args()
    langs = list(LANGS) if args.lang == "all" else [args.lang]
    for lang in langs:
        fell_back = assemble(args.out / lang, args.target, lang, args.theme)
        print(f"assembled {args.out / lang} ({args.target}, {lang})")
        if fell_back:
            print(f"  not translated yet, English for now: {', '.join(fell_back)}")
    if args.build:
        site = args.out / "site"
        shutil.rmtree(site, ignore_errors=True)
        for lang in langs:
            build(args.out / lang)
            shutil.copytree(args.out / lang / "site", site / LANGS[lang]["prefix"], dirs_exist_ok=True)
        print(f"built {site}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
