#!/bin/bash
# Publish the Mac runtime's mirrored wheels as assets of a prerelease.
# Run at release time by a maintainer (or CI with contents: write), never by the build.
#
#   publish_deps.sh <dir-from-collect_deps.py>
#
# 1. Verifies every wheel in the directory against deps.json (sha256).
# 2. Creates the prerelease named in deps.json (release_tag) on the repository
#    named there, if it does not exist. An existing release's assets are never
#    replaced: a new set of wheels gets a new tag (mac-runtime-deps-2, ...).
# 3. Uploads each wheel under its asset name and a SHA256SUMS file.
# 4. Downloads every asset back and checks its sha256.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${1:?usage: publish_deps.sh <dir-from-collect_deps.py>}"
command -v gh >/dev/null || { echo "gh is required" >&2; exit 2; }

read -r REPO TAG < <(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["repository"], d["release_tag"])' "$HERE/deps.json")

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
python3 -I - "$HERE/deps.json" "$SRC" "$STAGE" <<'PY'
import hashlib, json, shutil, sys
from pathlib import Path
pins, src, stage = json.load(open(sys.argv[1])), Path(sys.argv[2]), Path(sys.argv[3])
sums = []
for w in pins["wheels"]:
    f = src / w["file"]
    got = hashlib.sha256(f.read_bytes()).hexdigest()
    if got != w["sha256"]:
        sys.exit(f"{f.name}: sha256 {got}, pinned {w['sha256']}")
    shutil.copyfile(f, stage / w["asset"])
    sums.append(f"{got}  {w['asset']}")
(stage / "SHA256SUMS").write_text("\n".join(sums) + "\n")
PY

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  existing="$(gh release view "$TAG" --repo "$REPO" --json assets --jq '.assets[].name')"
  if [ -n "$existing" ]; then
    echo "$REPO $TAG already has assets; publish a new set under a new tag instead" >&2
    exit 1
  fi
else
  gh release create "$TAG" --repo "$REPO" --prerelease \
    --title "Mac runtime dependencies ($TAG)" \
    --notes "Off-PyPI wheels the Mac runtime (runtime-image/mac) installs, pinned by sha256 in runtime-image/mac/deps.json. Not a Fadenstack release."
fi
gh release upload "$TAG" --repo "$REPO" "$STAGE"/*

echo "== verifying the published assets"
CHECK="$(mktemp -d)"
trap 'rm -rf "$STAGE" "$CHECK"' EXIT
gh release download "$TAG" --repo "$REPO" --dir "$CHECK"
(cd "$CHECK" && shasum -a 256 -c SHA256SUMS)
echo "published $REPO $TAG"
