# Releasing Fadenstack

A release is a tag. The scripts in `fadenstack-dev/scripts/` bump what needs
bumping, tag, and push; GitHub Actions does all the building. Nothing is built
or uploaded from a developer's machine.

## Licensing and SBOM gate

Fadenstack-authored files in the core repository are Apache-2.0. Each release
must say so explicitly. Preserve the root and service `LICENSE` and `NOTICE`
files in source archives, the CLI's bundled `shared/` directory, and images.
Third-party components keep their own terms; see
[Third-party components](third-party-components.md). The image workflow checks
that the license notices referenced by vendored Swagger UI and ReDoc bundles
are present before a release tag can build images.

The release workflows call `sbom-release.yml` after uploading their artifacts.
It scans the published CLI assets and core images, agent binaries, and runtime
images with Syft. The resulting SPDX 2.3 JSON files are attached to the
matching GitHub release and retained as workflow artifacts. Core images also
carry BuildKit SBOM attestations. A manual run of `Release SBOMs` can regenerate
the documents for an existing release tag. Review the actual release assets
and SBOMs before calling the release complete. Archive scans unpack the CLI
wheel and source archive. The single-file agent executables are opaque to Syft,
so the workflow also publishes a separate inventory of the agent lockfile;
that inventory includes build inputs and is not a claim that every locked
package is embedded in each executable.

| Script | Tag | Workflow | Produces |
|---|---|---|---|
| `release-core.ps1 -Version 0.3.0` | `core-v0.3.0` | `docker-publish.yml` | `ghcr.io/fadenstack/{base,gateway,backend,frontend,pii,mcp,skills}:0.3.0` (and `0.3`, `latest`) |
| `release-cli.ps1 -Version 0.3.0` | `cli-v0.3.0` | `cli-release.yml` | `fadenstack` on PyPI, standalone CLI binaries, a GitHub release |
| `release-node-agent.ps1 -Version 0.1.14` | `node-agent-v0.1.14` | `node-agent-release.yml` | `faden-agent-linux-x86_64` and `-linux-aarch64` as GitHub release assets |
| `release-runtime.ps1 -Version 2.58.0-1` | `runtime-v2.58.0-1` | `runtime-image-release.yml` | `ghcr.io/fadenstack/ray-runtime-{gb10,x86_64}:2.58.0-1`, manifests on a pre-release |

Every script refuses a dirty tree and an existing tag, and takes `-DryRun`.

A plain `v0.3.0` tag starts `docker-publish.yml` and `cli-release.yml`
together; the CLI job waits until the images of its version exist, so nobody can
install a CLI whose images are missing.

## What each one checks

- **Core images.** The backend image carries the runtime manifests from
  `runtime-image/` (a named build context), because its catalogue of
  runtime images is built from them. Pushes to `main` rebuild only the
  services that changed.
- **Node agent.** The agent's `pyproject.toml`, its `__version__` and the
  backend's `AGENT_VERSION` must agree -- the backend is what tells machines
  which agent to install -- and `release-node-agent.ps1` bumps all three. Each
  binary is built in Debian bookworm on a runner of its own architecture and
  smoke-tested there, so it runs on glibc 2.36 and newer.
- **Runtime images.** Each image is built on a runner of its own
  architecture, validated inside itself (imports, `pip check`, Ray's
  `LLMConfig` against Fadenstack's deployment shapes) before anything is pushed,
  then pushed, and its manifest is minted from the pushed image by
  `runtime-image/rebuild_runtime_image.py` -- the script developers use.

## Runtime images need certifying before they ship

CI has no GB10 pair and no NVIDIA card, so the manifests it attaches to the
`runtime-v…` pre-release say **uncertified**. Before the backend points at a new
runtime image:

1. Certify it on its hardware (the suite is in
   `fadenstack-dev/llm_port_ray_migration/runtime_image/`).
2. Commit its manifest to `runtime-image/`
   (`runtime-manifest.json` for GB10, `runtime-manifest-x86_64.json` for x86_64).
3. Release core; the new backend image carries the manifest.

Until step 3 the released backend keeps the build it was certified with.

A server gets the runtime images when it is deployed or upgraded: the CLI
reads the manifests out of the release's backend image and pulls every image
they name from GHCR (`faden runtime-images` does the same on its own).
Machines then download theirs from the server. So a committed manifest must
name the published image -- `ghcr.io/fadenstack/ray-runtime-…:<version>`, which
is what CI mints -- or servers are told it is on no registry and cannot fetch
it.

## A full release, in order

1. `release-runtime.ps1` -- only when the runtime images changed; then certify
   and commit the manifests.
2. `release-node-agent.ps1` -- only when the agent changed. Its version is
   baked into the backend, so this comes before core.
3. `release-core.ps1`.
4. `release-cli.ps1` with the same version as core: `faden deploy` pulls the
   images tagged with the CLI's own version. It must also be above every CLI
   already on PyPI, or `pipx install fadenstack` keeps choosing the older one
   (this is why 0.0.3 was followed by 0.3.0: the CLI was at 0.2.9).
5. Upgrade a test install to the release and update its machines' agents, as
   [Upgrading](upgrading.md) describes.

Run each script on its own, one after another. Each pushes its own tag.
GitHub starts no workflow at all for a push that brings more than three new
tags, so tags made by hand and pushed together with `git push --tags` build
nothing.

The CI runs are public: `https://api.github.com/repos/fadenstack/fadenstack/actions/runs`
lists them without signing in, and `.../actions/runs/<id>/jobs` shows each
job and the step that failed. The logs themselves need a GitHub sign-in.

What releasing 0.3.0 found, and fixed in the scripts:

- `release-core.ps1` and `release-node-agent.ps1` rewrote every line ending in
  `version = "..."`, so the backend's ruff `target-version` and mypy
  `python_version` were set to the release number, breaking both tools. They
  now change only the package's own `version` line, as `release-cli.ps1`
  always did.
- `release-node-agent.ps1` did not update the agent's `uv.lock`, which kept
  the previous version until the next `uv run` changed it. It regenerates it
  now.

## Repository settings the workflows use

| Name | Kind | Needed for |
|---|---|---|
| `PYPI_API_TOKEN` | secret | `fadenstack` and `faden-agent` on PyPI -- both release workflows use this one secret; the agent's upload is skipped without it. A PyPI token covers one project or the whole account, so for both it must be an account-wide token. It uploaded agent 0.1.6-0.1.8 from this repository in April; for 0.3.0 the CLI published and the agent's upload failed, which a token replaced since by one scoped to `fadenstack` would explain (the agent's binaries, which installs use, were released regardless). A failed upload now shows PyPI's reason as a public annotation on the run |
| `NGC_API_KEY` | secret, optional | only if NVIDIA's base image for the GB10 runtime stops being public |
| `RUNTIME_RUNNER_GB10`, `RUNTIME_RUNNER_X86_64` | variables, optional | larger runners, if a runtime image build runs out of disk on the standard ones |

Images go to GHCR with the workflow's own token. A new package on GHCR starts
private: make `ray-runtime-gb10` and `ray-runtime-x86_64` public (or give the
servers a pull credential) after their first release.
