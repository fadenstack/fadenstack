# Fadenstack Ray runtime image

The container Fadenstack runs models in. Ray 2.58 + Ray Serve LLM + vLLM. The
GB10 image is one thin layer on top of NVIDIA's own vLLM image; the x86_64
image is built on `python:3.12` slim.

This is a release artifact, not a test fixture. The backend reads every
`runtime-manifest*.json` from this directory at run time to build its runtime
catalogue, so nothing here is optional: without them no deployment can
resolve a runtime.

It used to live in a separate repository, which meant a core checkout needed
that one beside it to start. The certification suite -- the scripts that prove
a built image works on real DGX hardware, and the reports they produced -- is
kept by the maintainers outside this repository, as it needs a DGX Spark pair.

## What is here

| | |
|---|---|
| `Dockerfile.gb10-vllm` | aarch64 / GB10 (DGX Spark, sm_121) on vLLM's own image (`vllm/vllm-openai`): the GB10 runtime that is published |
| `Dockerfile` | aarch64 / GB10 on NVIDIA's vLLM container (`nvcr.io/nvidia/vllm`): built and used on our own machines only (see Licence) |
| `Dockerfile.x86_64` | generic x86_64 NVIDIA, Turing (sm_75) through Blackwell (sm_120) |
| `llm_port_ray_runtime/` | the helper package baked into the image; provides the `llm-port-ray-runtime` CLI the agent drives, and the image app that serves picture models with diffusers (`image.py`) |
| `setup.py` | packaging for that helper |
| `fixtures.json`, `test_local_validation.py` | copied into the image; validate imports, dependencies and `LLMConfig` shapes from inside it |
| `check_ray_vllm.py` | run by both builds: every vLLM module and name Ray Serve LLM imports must resolve |
| `vllm_compat.py` | run by both GB10 builds, **temporary**: bridges two modules vLLM 0.29 moved away from where Ray 2.58 imports them |
| `runtime-manifest-<flavor>-<release>.json` | the minted identity of each runtime (see below) |
| `rebuild_runtime_image.py` | builds any of the images (`--flavor gb10-vllm`, `gb10` or `x86_64`), optionally pushes it, emits its manifest, and records its certification |

## Runtimes, flavors, channels

A **flavor** is a kind of machine: `gb10` or `x86_64`. What every runtime
of a flavor shares -- the hardware it is for, its mounts and the tuning
certified on that hardware -- lives in the backend
(`services/inference/bundles.py`, `_FLAVORS`).

A **runtime** is one built image of a flavor. Each has its own manifest, and
the manifest names its flavor and its `runtime_id`. This directory holds **at
most three per flavor** (a backend test holds it to that):

- the stable one
- a newer one as a preview
- an older one to go back to

Delete the oldest manifest when a fourth arrives. A cluster that still runs
it moves to its flavor's default the next time it is started.

Certification records a **channel** in the manifest:

- `stable`: what a machine gets unless its cluster chose another. The
  default among them is the best certified, then the most recently
  certified.
- `preview`: only a cluster whose operator chose it.

A cluster keeps the runtime it was started with until an operator switches
it (`docs/help/pages/operate/upgrading.md`), so shipping a new manifest never moves a running
cluster.

Today:

| Manifest | Base | vLLM | Certified | Channel |
|---|---|---|---|---|
| `runtime-manifest-gb10-nv26.08.json` | NVIDIA vLLM 26.08 | 0.27.1 | passed 11/11, 2026-09-29 | stable |
| `runtime-manifest-gb10-nv26.09.json` | NVIDIA vLLM 26.09 | 0.29.0 (+ `vllm_compat.py`), diffusers 0.41.0 for picture models | passed 11/11, 2026-10-09 | preview |
| `runtime-manifest-x86_64.json` | python:3.12 slim | 0.26.0 | no | stable |

## The manifests are minted, not written

Every field in a manifest that says what the image is -- the image reference,
its id, its layer digests, the stack versions, the architectures it was
compiled for, the model types it can load -- is read out of the built image.
None of it is typed by hand. That is the whole point: a catalogue entry that
disagrees with the artifact it names is a pin that pins nothing, and it
fails at deploy time on a node, which is the worst place to find out.

So a manifest is only ever written by building:

```bash
python rebuild_runtime_image.py --flavor gb10-vllm --build  # aarch64 / GB10 on vLLM's image, on a DGX node
python rebuild_runtime_image.py --build                   # aarch64 / GB10 on NVIDIA's container, on a DGX node
python rebuild_runtime_image.py --flavor x86_64 --build   # x86_64, on any x86_64 machine
```

A build writes `uncertified`, whatever the previous manifest said.

### A new release of a flavor

For GB10, change the `FROM` of `Dockerfile.gb10-vllm` (vLLM's image:
`v0.29.0-aarch64-ubuntu2404` makes `vllm0.29.0`) or of `Dockerfile` (NVIDIA's:
`nvcr.io/nvidia/vllm:26.09-py3` makes `nv26.09`); the runtime is named after
it, and its manifest, tag and runtime id follow, so the runtimes already
shipped keep theirs. Take vLLM's CUDA 13 build (no `-cu129`): the GB10 needs
CUDA 13. For x86_64, change the names in `FLAVORS` with the pins in its
Dockerfile. Build it.

### Certify, then record

The maintainers certify the image on its hardware, a DGX Spark pair, with the
certification suite; Fadenstack's cluster on the pair is stopped first. The
report records the image's layers on both machines. Put its verdict into the
manifest:

```bash
python rebuild_runtime_image.py --manifest runtime-manifest-gb10-nv26.09.json \
    --record-certification dgx_spark_2node_certification_report.json --channel preview
```

It refuses a report whose layers are not the manifest's, so evidence from one
build never certifies another. Commit the manifest; the next core release
ships it.

## What the GB10 build checks

The image is NVIDIA's with a layer of ours on top, and the build fails
rather than ship a layer that changes theirs:

- **Constraints.** Our pip installs resolve against the base's own package
  versions (`pip list --format=freeze`), so nothing NVIDIA ships is
  upgraded. Without this, the first 26.09 build replaced the base's
  `opentelemetry-sdk` 1.44 with 1.45.
- **`pip check`** may report nothing the base did not already report.
  NVIDIA's own `nvidia-cutlass-dsl` pin mismatch is theirs, and stays.
- **Metrics stack**: Ray's per-node metrics agent imports.
- **`check_ray_vllm.py`**: every vLLM import in Ray Serve LLM resolves. A
  vLLM that moved a module Ray imports failed only on the DGX nodes at
  deploy time before this. The x86_64 build runs it too.
- **The helper's verbs** the agent drives exist.

`vllm_compat.py` is a stopgap. Ray 2.58 imports
`vllm.entrypoints.openai.cli_args` and `vllm.entrypoints.openai.engine.protocol`,
which vLLM 0.29 moved to `vllm.entrypoints.launchers.cli_args` and
`vllm.entrypoints.serve.engine.protocol`. The script writes re-exporting
modules at the old paths, and only where they are missing: on 26.08 it
changes nothing. Remove it, and its line in the Dockerfile, with the first
Ray release that imports the new paths. `check_ray_vllm.py` will tell you
when it is no longer needed: it passes without it.

## Releases

A `runtime-v<version>` tag runs `.github/workflows/runtime-image-release.yml`: each image is built on a
runner of its own architecture, pushed to
`ghcr.io/fadenstack/ray-runtime-gb10:<version>` and
`ghcr.io/fadenstack/ray-runtime-x86_64:<version>`, and its manifest -- minted by
the same script, with the registry digest -- is attached to the
`runtime-v<version>` GitHub release.

Those manifests say `uncertified`, because CI has no GB10 pair or NVIDIA card.
Certify each image on its hardware, record the report, then commit its
manifest here; the next core release builds the backend with it, and from
then on the catalogue points at the published image.

The manifests above name local tags (`faden/ray-vllm-gb10:…`,
`faden-ray-runtime-x86_64:2.58.0`): those images are on the machines they were
built on (the GB10 ones on the DGX pair) and nowhere else yet. A server can hand out only
images it holds, so they reach a server that does not have them once they
are published to GHCR and their manifests name the registry.

## Licence

The files in this folder are Apache-2.0, like the rest of the repository. The images built from them are not, as
a whole: only the runtime helper in `/opt/llm-port/runtime` is. The published GB10 image is built on vLLM's own
image, which, like the x86_64 image, contains NVIDIA's CUDA and cuDNN packages under the CUDA EULA. The GB10 image
built on NVIDIA's vLLM container (`nvcr.io/nvidia/vllm`) falls under the NVIDIA Software License Agreement and the
Product-Specific Terms for NVIDIA AI Products, which do not allow publishing it in a public repository, so it is
built and used on our own machines only. Those terms stay with
the images: `NOTICE` (copied into each image) says so, the image label
`org.opencontainers.image.licenses` names them, and Help's
[Third-party components](../docs/help/pages/operate/third-party.md) page links them.
