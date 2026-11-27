# Mac runtime (vLLM-Metal)

A relocatable Python 3.12 + vLLM 0.34 + vLLM-Metal 0.34 + MLX tree, packed in an
OCI image used only as a carrier. Apple Silicon Macs fetch it through the backend
and run it natively under the `standalone` driver. Nothing runs it as a container.

| File | What it does |
|---|---|
| `build_mac_runtime.sh` | Builds, patches, smoke-tests, packs and mints. Never pushes. |
| `requirements-lock.txt` | Every package by exact version and sha256, installed with `--require-hashes`. |
| `deps.json` | The off-PyPI wheels, pinned by sha256, and where each came from. |
| `fetch_deps.py` | Fetches those wheels from the mirror release only, verifies them, and lints the lock. |
| `collect_deps.py` | Gathers the off-PyPI wheels from their origins (release time only). |
| `publish_deps.sh` | Uploads them as prerelease assets (release time only). |
| `hash_lock.py` | Writes the `--hash` lines of the lock. |
| `patch_mlx_lm_rlimit.py` | The raise-only RLIMIT_NOFILE patch (ml-explore/mlx-lm#1589). |
| `oci_pack.py` | The carrier image: FROM scratch, one layer, no entrypoint. |
| `mint_manifest.py` | The backend catalogue entry for a build. |

## Where the dependencies come from
A cold build in a year must install exactly what was certified. So the build
reads from exactly two places:

1. **PyPI, by exact version and sha256.** A PyPI release's files can't be replaced.
2. **The `mac-runtime-deps-<n>` prerelease on fadenstack/fadenstack, by sha256.** It holds every wheel that isn't on PyPI:

| Wheel | Origin | Why it is mirrored |
|---|---|---|
| torch, torchaudio, torchvision `*.dev20261013` | PyTorch nightly index | Nightlies are pruned after a few months. |
| vllm `0.34.0+cpu` | vLLM GitHub release | Another project's release, not PyPI. |
| vllm-metal `0.34.0` | the Gate 0d certified wheel | It exists nowhere else. |
| mlx-lm `0.32.3.dev12+g4cfc647` | git commit 4cfc647 | A git commit can be rewritten. |
| mlx-vlm `0.7.11` | git commit b7bec92 | Same as mlx-lm. |

No nightly index, git remote or other project's release is read at build time.
`fetch_deps.py` refuses a lock that would read one. It also refuses a lock line
without a hash, and a mirrored wheel whose hash isn't its `deps.json` pin.
`uv pip install --require-hashes` refuses any file whose hash isn't listed.

## Publishing the mirror (once per set of wheels, at release time)
On an Apple Silicon Mac that holds the Gate 0d stage (for the vllm-metal wheel):

```sh
python collect_deps.py /tmp/mac-deps          # fetch, build from git, verify against deps.json
./publish_deps.sh /tmp/mac-deps               # needs gh with contents: write on fadenstack/fadenstack
```

- `collect_deps.py` verifies every file against its pin. The git-built wheels are reproducible (`SOURCE_DATE_EPOCH` from the commit), so a re-collection gives the same bytes. This was verified on 27 Nov 2026.
- `publish_deps.sh` refuses to touch a tag that already has assets. It downloads everything back and checks `SHA256SUMS`.

## Changing a dependency
1. Edit `requirements-lock.txt`. For an off-PyPI wheel, also edit `deps.json` (name, file, asset, source).
2. Set `release_tag` in `deps.json` to the next `mac-runtime-deps-<n>`. A published set is never changed.
3. Run `python collect_deps.py /tmp/mac-deps --pin`, which writes the new sha256s.
4. Run `python hash_lock.py`, which rewrites the `--hash` lines from PyPI and `deps.json`.
5. Run `python -m pytest runtime-image/tests/test_mac_runtime.py`.
6. After review, run `./publish_deps.sh /tmp/mac-deps`. Then the CI build (`mac-runtime-release.yml`) can run.

## Building
```sh
# CI and a cold machine: wheels from the mirror release
runtime-image/mac/build_mac_runtime.sh --out /tmp/macbuild
# Before the mirror is published: the same wheels from a local collection (still checked by sha256)
runtime-image/mac/build_mac_runtime.sh --out /tmp/macbuild --deps /tmp/mac-deps
```
