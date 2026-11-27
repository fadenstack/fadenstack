"""Make mlx-lm's open-file limit change raise-only.

``mlx_lm/utils.py`` runs ``resource.setrlimit(RLIMIT_NOFILE, (2048, 4096))`` at
import, unconditionally. That *lowers* the hard limit to 4096, after which
vLLM's own ``set_ulimit`` cannot raise the soft limit and every vLLM-Metal
process is capped at 2048 open files whatever launchd allows (Gate 0d).
Upstream issue ml-explore/mlx-lm#1589 is open; the fix PR #1590 was closed
unmerged, so the bundle carries this patch.

The rewrite only ever raises the soft limit and never touches the hard one.
It refuses to run unless the exact original line is present once, so a moved
or changed upstream line fails the build instead of shipping unpatched.

Usage: python patch_mlx_lm_rlimit.py <site-packages>
"""

from __future__ import annotations

import sys
from pathlib import Path

ORIGINAL = "resource.setrlimit(resource.RLIMIT_NOFILE, (2048, 4096))\n"
MARKER = "# fadenstack: raise-only RLIMIT_NOFILE (ml-explore/mlx-lm#1589)"
REPLACEMENT = (
    f"{MARKER}\n"
    "_fs_soft, _fs_hard = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
    "_fs_want = 2048 if _fs_hard == resource.RLIM_INFINITY else min(2048, _fs_hard)\n"
    "if _fs_soft < _fs_want:\n"
    "    resource.setrlimit(resource.RLIMIT_NOFILE, (_fs_want, _fs_hard))\n"
    "del _fs_soft, _fs_hard, _fs_want\n"
)


def patch(site_packages: Path) -> str:
    """Patch ``mlx_lm/utils.py`` under *site_packages*; return "patched" or "already"."""
    target = site_packages / "mlx_lm" / "utils.py"
    text = target.read_text(encoding="utf-8")
    if MARKER in text:
        return "already"
    count = text.count(ORIGINAL)
    if count != 1:
        raise SystemExit(f"{target}: expected the original setrlimit line once, found {count}; refusing to patch")
    target.write_text(text.replace(ORIGINAL, REPLACEMENT), encoding="utf-8")
    # A stale bytecode file would still lower the limit.
    for pyc in (target.parent / "__pycache__").glob("utils.*.pyc"):
        pyc.unlink()
    return "patched"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(patch(Path(sys.argv[1])))
