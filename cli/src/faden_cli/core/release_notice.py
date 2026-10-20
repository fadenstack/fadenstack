"""This release is a Preview: said before ``faden deploy`` and ``faden upgrade`` go on, and who accepted it.

The notice comes before anything changes on the server. It asks; ``-y`` accepts it without asking, and says
so. What was accepted is kept in faden.yaml (``preview_accepted``: release, who, when, how), as
``modules_switched_off`` keeps who switched a module off.
"""

from __future__ import annotations

import getpass
import os
import sys
from datetime import UTC, datetime

import click

from faden_cli import __version__
from faden_cli.core.console import console, error
from faden_cli.core.settings import FadenConfig, save_config

#: Whether this release of the CLI and the images it installs is a Preview.
PREVIEW = True
#: Where the Preview is explained: Help (on the install) and the downloads page.
HELP_PAGE = "Help > Operate > Preview releases"
DOWNLOADS = "https://fadenstack.com/download/"

NOTICE = """[bold yellow]Fadenstack {version} is a Preview release.[/bold yellow]

  - It is for evaluation and testing. Parts of it may be incomplete or change in the next release.
  - Content marking (the provenance module, EU AI Act Art. 50(2)) is part of it and on by default. It
    supports marking AI-generated content; it does not by itself make your use of Fadenstack compliant,
    and its marks do not yet hold on every path (Help > Administer > Content marking).
  - Do not rely on it as the only control for legally significant decisions.
  - The provenance module is free proprietary software under its own licence; the rest of Fadenstack is
    Apache-2.0.

  Details: {help_page}, and {downloads}
"""


def _os_user() -> str:
    try:
        return getpass.getuser()
    except (ImportError, KeyError, OSError):
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def accept_preview(cfg: FadenConfig, *, yes: bool, dry_run: bool = False) -> None:
    """Show the Preview notice and go on only once it is accepted; exits when it is not.

    *yes* accepts it without asking (unattended deploys); a dry run shows it and records nothing.
    """
    if not PREVIEW:
        return
    console.print(NOTICE.format(version=__version__, help_page=HELP_PAGE, downloads=DOWNLOADS))
    if dry_run:
        return
    if yes:
        how = "-y"
        console.print("  Accepted with -y.\n")
    elif not sys.stdin.isatty():
        error("This is a Preview release: accept it interactively, or run again with -y.")
        sys.exit(1)
    elif click.confirm("  Continue with the Preview release?", default=False):
        how = "prompt"
    else:
        error("Stopped: the Preview release was not accepted. Nothing was changed.")
        sys.exit(1)
    note = {"by": _os_user(), "at": datetime.now(tz=UTC).isoformat(timespec="seconds"), "how": how}
    cfg.preview_accepted = {**(cfg.preview_accepted or {}), __version__: note}
    save_config(cfg)
