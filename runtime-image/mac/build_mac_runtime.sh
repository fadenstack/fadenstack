#!/bin/bash
# Build the Fadenstack Mac runtime (vLLM-Metal) as a relocatable tree and pack it
# into an OCI carrier image. Runs on an Apple Silicon Mac, locally or in CI.
#
#   build_mac_runtime.sh [--out DIR] [--deps DIR] [--tag TAG] [--load]
#
#   --out     work and output directory (default: ./build)
#   --deps    a directory of the mirrored wheels to use before downloading
#             (e.g. collect_deps.py output); each is still checked by sha256
#   --tag     image tag (default: vllm0.34.0-metal0.34.0-1)
#   --load    also `docker load` the OCI tar into the local Docker (never pushes)
#
# Sources: PyPI by exact version, and the wheels in deps.json only from the
# mac-runtime-deps-<n> prerelease on fadenstack/fadenstack, verified by sha256
# (fetch_deps.py). No nightly index, git remote or other release is read.
#
# Never pushes. The release workflow pushes the OCI tar it produces.
#
# Layout of the image layer (what the agent extracts):
#   runtime/python/            uv-managed CPython 3.12.12 (python-build-standalone)
#   runtime/venv/              relocatable venv, installed from requirements-lock.txt
#   runtime/RUNTIME-MANIFEST.json
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$PWD/build"
DEPS=""
TAG="vllm0.34.0-metal0.34.0-1"
REPO="ghcr.io/fadenstack/mac-runtime-arm64"
LOAD=0
PYTHON_VERSION="3.12.12"

while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --deps) DEPS="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --load) LOAD=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ "$(uname -s)/$(uname -m)" = "Darwin/arm64" ] || { echo "needs an Apple Silicon Mac" >&2; exit 2; }
command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }

STAGE="$OUT/stage"
RT="$STAGE/runtime"
rm -rf "$STAGE"
mkdir -p "$RT"

# Keep the build off the user's caches and home: what goes into the image is
# only what the lock names.
export UV_PYTHON_INSTALL_DIR="$RT/python"
export UV_PYTHON_BIN_DIR="$OUT/python-shims"   # not ~/.local/bin
export UV_CACHE_DIR="${UV_CACHE_DIR:-$OUT/uv-cache}"   # locally: the cache Gate 0d resolved from
export UV_NO_CONFIG=1
export UV_PYTHON_PREFERENCE=only-managed

echo "== python $PYTHON_VERSION"
uv python install "$PYTHON_VERSION"
PY="$(uv python find "$PYTHON_VERSION")"
case "$PY" in "$RT/python/"*) ;; *) echo "python resolved outside the stage: $PY" >&2; exit 1 ;; esac

echo "== mirrored wheels (sha256-pinned)"
WHEELS="$OUT/wheels"
"$PY" -I "$HERE/fetch_deps.py" "$WHEELS" ${DEPS:+--from "$DEPS"}

echo "== venv"
uv venv --relocatable --seed --python "$PY" "$RT/venv"
# The interpreter symlink must be relative, or the tree does not move.
ln -sfn "$(python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$PY" "$RT/venv/bin")" "$RT/venv/bin/python"

echo "== install from the lock"
LOCK="$OUT/requirements-lock.resolved.txt"
sed "s#@WHEELS@#$(cd "$WHEELS" && pwd)#" "$HERE/requirements-lock.txt" > "$LOCK"
# --no-deps: vllm 0.34.0+cpu pins torch==2.14.0 while the lock carries the 2.16
# nightly vllm-metal was certified with (Gate 0d provenance). PyPI is the only
# index; everything else is a verified local wheel.
# --require-hashes: every line carries its sha256 (hash_lock.py), and a file
# whose hash is not listed, or a line without one, fails the build.
uv pip install --python "$RT/venv/bin/python" --no-deps --require-hashes \
  --index-url https://pypi.org/simple \
  -r "$LOCK"

echo "== lock check"
uv pip freeze --python "$RT/venv/bin/python" | sort > "$OUT/freeze.txt"
"$PY" -I "$HERE/fetch_deps.py" --print-requirements --lock "$LOCK" "$OUT" | sort > "$OUT/lock.sorted"
if ! diff -u <(sed -e 's#file://.*/#file:///#' -e 's/%2B/+/g' "$OUT/lock.sorted") <(sed -e 's#file://.*/#file:///#' -e 's/%2B/+/g' "$OUT/freeze.txt") > "$OUT/lock.diff"; then
  echo "installed set differs from the lock:" >&2; cat "$OUT/lock.diff" >&2; exit 1
fi

echo "== patch mlx_lm (raise-only RLIMIT_NOFILE)"
SITE="$("$RT/venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
"$RT/venv/bin/python" -I "$HERE/patch_mlx_lm_rlimit.py" "$SITE"

echo "== smoke"
SMOKE="$("$RT/venv/bin/python" -I - <<'PY'
import json, resource
soft0, hard0 = resource.getrlimit(resource.RLIMIT_NOFILE)
import mlx.core as mx, mlx_lm, vllm, vllm_metal  # noqa: E401
soft1, hard1 = resource.getrlimit(resource.RLIMIT_NOFILE)
assert hard1 == hard0, f"importing mlx_lm changed the hard limit: {hard0} -> {hard1}"
assert soft1 >= soft0, f"importing mlx_lm lowered the soft limit: {soft0} -> {soft1}"
from importlib.metadata import version
print(json.dumps({
    "python": __import__("platform").python_version(),
    "vllm": version("vllm"), "vllm_metal": version("vllm-metal"),
    "mlx": version("mlx"), "mlx_lm": version("mlx-lm"),
    "torch": version("torch"), "transformers": version("transformers"),
    "rlimit_nofile_before": [soft0, hard0], "rlimit_nofile_after": [soft1, hard1],
    "metal_available": bool(mx.metal.is_available()),
}))
PY
)"
echo "$SMOKE"

echo "== relocation check"
rm -rf "$OUT/reloc" && mkdir -p "$OUT/reloc" && cp -R "$RT" "$OUT/reloc/runtime"
"$OUT/reloc/runtime/venv/bin/python" -I -c 'import vllm_metal, sys; assert sys.prefix.endswith("reloc/runtime/venv"), sys.prefix'
rm -rf "$OUT/reloc"

echo "== manifest"
# No __pycache__ from the smoke run, so the layer depends on the lock only.
find "$RT" -name '__pycache__' -type d -prune -exec rm -rf {} +
PY_REL="$(python3 -c 'import os,sys; print(os.path.relpath(os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])))' "$PY" "$RT")"
"$RT/venv/bin/python" -I - "$RT/RUNTIME-MANIFEST.json" "$SMOKE" "$OUT/freeze.txt" "$PY_REL" <<'PY'
import hashlib, json, sys
out, smoke, freeze, py_rel = sys.argv[1], json.loads(sys.argv[2]), open(sys.argv[3]).read(), sys.argv[4]
json.dump({
    "schema_version": "1.0.0",
    "kind": "python-venv",
    "target": {"os": "darwin", "cpu": "arm64", "accelerator": "apple", "capabilities": ["metal"], "min_os_version": "26.0"},
    "interpreter": {"implementation": "cpython", "version": smoke["python"], "abi": "cp312",
                    "path": py_rel},
    # pyvenv.cfg ``home`` is absolute; the agent rewrites it to <runtime_root>/<dirname(path)> after extracting.
    "pyvenv_home_rewrite": True,
    "venv": "venv",
    "entrypoint": ["venv/bin/python", "-m", "vllm.entrypoints.openai.api_server"],
    "engines": {k: smoke[k] for k in ("vllm", "vllm_metal", "mlx", "mlx_lm")},
    "stack": {k: smoke[k] for k in ("python", "torch", "transformers")},
    "patches": {"mlx_lm_fd_cap_raise_only": True},
    "lock_sha256": hashlib.sha256(freeze.encode()).hexdigest(),
}, open(out, "w"), indent=2, sort_keys=True)
PY

echo "== pack"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"
"$RT/venv/bin/python" -I "$HERE/oci_pack.py" "$STAGE" "$OUT/mac-runtime.oci.tar" "$REPO:$TAG" "$OUT/digests.json"
cp "$RT/RUNTIME-MANIFEST.json" "$OUT/RUNTIME-MANIFEST.json"
echo "$SMOKE" > "$OUT/smoke.json"
# The backend catalogue entry, from the artifact (commit it to runtime-image/ once pushed).
"$RT/venv/bin/python" -I "$HERE/mint_manifest.py" "$OUT" "$OUT"

if [ "$LOAD" = 1 ]; then
  echo "== docker load (local only, no push)"
  docker load -i "$OUT/mac-runtime.oci.tar"
fi

echo "done: $OUT/mac-runtime.oci.tar"
du -sh "$RT" "$OUT/mac-runtime.oci.tar"
