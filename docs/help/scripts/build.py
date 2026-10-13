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
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tomllib
import unicodedata
from pathlib import Path

HELP = Path(__file__).resolve().parent.parent          # docs/help
THEME = HELP.parent / "theme"                           # docs/theme

#: Each language: its pages, its nav, where it is served below the site's root, its page on the website, and its
#: words.
LANGS: dict[str, dict[str, str]] = {
    "en": {
        "pages": "pages",
        "nav": "zensical.toml",
        "questions": "questions.toml",
        "prefix": "",
        "website_path": "",
        "name": "English",
        "site_name": "Fadenstack Help",
        "description": "Using, administering and running Fadenstack: the chat, the console, the server and the GPU machines.",
        "privacy": "Privacy",
        "website": "fadenstack.com",
        "help": "Help",
        "developers": "Developers",
        "console": "Console",
        "untranslated": "",
    },
    "de": {
        "pages": "pages-de",
        "nav": "nav-de.toml",
        "questions": "questions-de.toml",
        "prefix": "de/",
        "website_path": "de/",
        "name": "Deutsch",
        "site_name": "Fadenstack-Hilfe",
        "description": "Fadenstack nutzen, verwalten und betreiben: der Chat, die Konsole, der Server und die GPU-Maschinen.",
        "privacy": "Datenschutz",
        "website": "fadenstack.com",
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

#: The IndexNow key of the Fadenstack sites (public by design): the public Help serves it at /<key>.txt, so
#: search engines accept its pages when a deploy submits them (fadenstack.com repository, deploy/indexnow.mjs).
INDEXNOW_KEY = "c60415486c9a45d5b65688c12bff17ba"

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
    # The website (or, in the console, the console's start) shows as a house; its name is the tooltip.
    first = (
        {"name": words["website"], "url": f"{t['home']}{words['website_path']}", "icon": "home"}
        if target == "public"
        else {"name": words["console"], "url": str(t["home"]), "icon": "home"}
    )
    return [
        first,
        {"name": words["help"], "url": help_url, "key": "help"},
        {"name": words["developers"], "url": "https://developers.fadenstack.com/", "key": "developers"},
    ]


_HEADING = re.compile(r"^(#{1,6}) +(.+?)\s*$")
_EXPLICIT_ID = re.compile(r"\{#([\w-]+)\}\s*$")


def _slug(text: str) -> str:
    """The anchor the toc extension gives a heading."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def _anchors(text: str) -> tuple[str, set[str]]:
    """A page's title and the anchors of its headings (code blocks skipped)."""
    title, anchors, fenced = "", set(), False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        m = None if fenced else _HEADING.match(line)
        if not m:
            continue
        heading = m[2]
        explicit = _EXPLICIT_ID.search(heading)
        heading = _EXPLICIT_ID.sub("", heading).strip()
        if m[1] == "#" and not title:
            title = heading
        anchors.add(explicit[1] if explicit else _slug(heading))
    return title, anchors


_IMAGE = re.compile(r"(\]\()((?:\.\./)*)images/([^)\s]+?\.png)")


def _local_images(docs: Path, lang: str) -> None:
    """Point a translation's pictures at pages/images/<lang>/ where there is one, and keep only its language's."""
    for other in LANGS:
        if other not in ("en", lang):
            shutil.rmtree(docs / "images" / other, ignore_errors=True)
    if lang == "en":
        return

    def swap(m: re.Match) -> str:
        local = HELP / "pages" / "images" / lang / m[3]
        return f"{m[1]}{m[2]}images/{lang}/{m[3]}" if local.is_file() else m[0]

    for page in docs.rglob("*.md"):
        text = page.read_text(encoding="utf-8")
        localised = _IMAGE.sub(swap, text)
        if localised != text:
            page.write_text(localised, encoding="utf-8")


def _page_url(page: str) -> str:
    """The address of a page below the site's root: use/chat.md -> use/chat/, index.md -> ''."""
    stem = page[: -len(".md")]
    if stem == "index":
        return ""
    return (stem[: -len("index")] if stem.endswith("/index") else stem + "/")


def _questions(lang: str, docs: Path) -> None:
    """Write assets/questions.json for the front page's question search, from the language's questions file."""
    words = LANGS[lang]
    data = tomllib.loads((HELP / words["questions"]).read_text(encoding="utf-8"))
    pages = set(_files(_nav("en")))
    out = []
    for page, questions in data.items():
        if page not in pages:
            raise SystemExit(f"{words['questions']}: {page} is not a page in the navigation")
        _, anchors = _anchors((HELP / "pages" / page).read_text(encoding="utf-8"))
        title, _ = _anchors((docs / page).read_text(encoding="utf-8"))
        for question, anchor in questions.items():
            if anchor and anchor not in anchors:
                raise SystemExit(f"{words['questions']}: {page} has no section #{anchor} ({question})")
        out.append({
            "url": _page_url(page),
            "title": title,
            "questions": [{"q": q, "at": a} for q, a in questions.items()],
        })
    target = docs / "assets" / "questions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"pages": out}, ensure_ascii=False), encoding="utf-8")


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
        # The pictures: the language's own under images/<lang>/ where taken (_local_images), the English ones
        # otherwise; a page not translated yet is the English one.
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
    _local_images(out / "docs", lang)
    shutil.copytree(theme / "assets", out / "docs" / "assets", dirs_exist_ok=True)
    _questions(lang, out / "docs")
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
    config = _fingerprint(out / "docs" / "assets", config)
    (out / "zensical.toml").write_text(config, encoding="utf-8")
    return fell_back


#: The theme's own files that pages load by name. They are built with a hash of their content in the name, so a
#: cache (the browser's, a proxy's) never serves an old one with a new page; Zensical's own files are named so already.
FINGERPRINTED = ("stylesheets/fonts.css", "stylesheets/fadenstack.css", "javascripts/fs-ask.js")


def _fingerprint(assets: Path, config: str) -> str:
    """Rename the theme's own CSS and scripts to name.<hash>.ext and point *config* at the new names."""
    for rel in FINGERPRINTED:
        src = assets / rel
        if not src.is_file():
            continue
        digest = hashlib.sha256(src.read_bytes()).hexdigest()[:10]
        hashed = src.with_name(f"{src.stem}.{digest}{src.suffix}")
        src.replace(hashed)
        config = config.replace(f'"assets/{rel}"', f'"assets/{Path(rel).parent.as_posix()}/{hashed.name}"')
    return config


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
        if args.target == "public":
            # Each language has its own sitemap; crawlers find them through robots.txt.
            base = TARGETS["public"]["base"]
            maps = "".join(f"Sitemap: {base}{LANGS[lang]['prefix']}sitemap.xml\n" for lang in langs)
            (site / "robots.txt").write_text("User-agent: *\nAllow: /\n\n" + maps, encoding="utf-8", newline="\n")
            (site / f"{INDEXNOW_KEY}.txt").write_text(INDEXNOW_KEY, encoding="utf-8", newline="\n")
        print(f"built {site}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
