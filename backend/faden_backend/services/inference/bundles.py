"""Runtime Bundle Manifest specification and registry manager (Phase 4B).

Decouples container image references, OCI digest pinning, hardware requirements
and platform workarounds from deployment specifications:

- Defines immutable :class:`RuntimeBundleManifest` models.
- Expresses container needs as **semantic requirements** (``network_mode``,
  ``ipc_mode``, devices, mounts) rather than raw Docker CLI flags, so a bundle
  is portable across ``docker`` and ``podman`` handlers.
- Pins the exact OCI image identity, mandatory and machine-checkable, and
  carries the certification evidence that identity was granted under.
- Validates node architecture and GPU hardware compatibility.
- Injects certified platform-specific tuning (e.g. ``RAY_memory_monitor_refresh_ms=0``
  on GB10 unified memory).

Bundle identity is **generated from the build/certification artifacts**
(a ``runtime-manifest*.json`` per runtime, produced by the image build in ``runtime-image``)
via :meth:`RuntimeBundleManifest.from_runtime_manifest` — never hand-written,
because a hand-written digest that disagrees with the artifact makes the
catalog authoritative-looking and wrong at the same time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from faden_backend.db.models.node_control import InfraNode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def compute_rootfs_digest(layer_diff_ids: list[str]) -> str:
    """Content identity of an image from its ordered RootFS layer diff IDs.

    ``docker image inspect --format '{{json .RootFS.Layers}}'`` on any host
    holding the image yields the same list for the same content, whether the
    image was built there, pulled, or side-loaded from a tarball - unlike the
    config ``.Id``, which the transfer can rewrite.  The agent computes this
    with the identical canonicalization.
    """
    return "sha256:" + hashlib.sha256("\n".join(layer_diff_ids).encode()).hexdigest()


class BundleValidationError(ValueError):
    """A bundle manifest is not usable as a pinned, verifiable runtime."""


class AcceleratorSpec(BaseModel):
    """Accelerator hardware constraints for a bundle.

    ``compute_capabilities`` is the set the image was **built for**, not a
    single card it belongs to. A stock x86_64 vLLM image compiles kernels for
    ``sm_75`` through ``sm_120`` plus a PTX fallback, which is every
    mainstream NVIDIA card since Turing -- so one image serves a whole
    platform and there is no reason to cut one per GPU model. Verified by
    reading ``torch.cuda.get_arch_list()`` out of the images themselves.

    What genuinely forces a separate bundle is the CPU architecture (no JIT
    across it), the accelerator vendor, and a vendor base image with its own
    tuning -- which is why the DGX bundle exists, not because GB10 needs
    bespoke kernels.
    """

    model_config = ConfigDict(extra="forbid")

    vendor: str = "nvidia"
    families: list[str] = Field(default_factory=list)
    compute_capabilities: list[str] = Field(default_factory=list)
    #: Lowest host driver version this bundle runs on, in the vendor's own
    #: numbering (NVIDIA display driver, ROCm, Level Zero...).
    min_driver_version: str | None = None


class TargetArchitecture(BaseModel):
    """Target host architecture."""

    model_config = ConfigDict(extra="forbid")

    cpu: str = "aarch64"  # or "x86_64"
    os: str = "linux"
    accelerator: AcceleratorSpec = Field(default_factory=AcceleratorSpec)


class ContainerMount(BaseModel):
    """Container volume mount definition."""

    model_config = ConfigDict(extra="forbid")

    host_path: str
    container_path: str
    mode: str = "ro"


def translate_host_path_to_container(
    host_path: str,
    mounts: list[ContainerMount],
) -> str | None:
    """Translate a host path to its corresponding container path based on bundle mounts.

    Returns the mapped container path, or ``None`` if the host path does not fall
    under any configured mount.
    """
    clean_host = host_path.replace("\\", "/").rstrip("/")
    for mount in mounts:
        m_host = mount.host_path.replace("\\", "/").rstrip("/")
        if clean_host == m_host:
            return mount.container_path.replace("\\", "/").rstrip("/")
        if clean_host.startswith(m_host + "/"):
            sub = clean_host[len(m_host) :]
            m_cont = mount.container_path.replace("\\", "/").rstrip("/")
            return f"{m_cont}{sub}"
    return None


class ContainerRequirements(BaseModel):
    """Semantic container requirements.

    Deliberately *not* a list of CLI flags: the node agent maps these onto
    whichever runtime handler it has (``docker`` / ``podman``), and a plan
    reviewer can read what the runtime needs without parsing argv.
    """

    model_config = ConfigDict(extra="forbid")

    network_mode: str = "host"
    ipc_mode: str = "host"
    pid_mode: str | None = None
    # "all", "none", or a device count/spec understood by the handler.
    gpus: str = "all"
    devices: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    shm_size: str | None = None
    ulimits: dict[str, str] = Field(default_factory=dict)


class ContainerSpec(BaseModel):
    """OCI container image specification, pinned by identity."""

    model_config = ConfigDict(extra="forbid")

    image: str
    # The image's *config* identity, i.e. what
    # ``docker image inspect --format '{{.Id}}'`` reports.  Mandatory, but NOT
    # sufficient on its own: verified on the DGX pair, the two nodes hold
    # byte-identical copies of this image (same 50 RootFS layer diff IDs) under
    # two different config IDs, because the side-load rewrote the config.  A
    # check against ``.Id`` alone would reject a node running exactly the
    # certified bits.
    digest: str
    # Content identity: sha256 over the image's ordered RootFS layer diff IDs
    # (see ``compute_rootfs_digest``).  This is what actually survives a
    # ``docker save``/``load``/``import`` round trip, so it is the primary
    # check; ``digest`` and ``repo_digest`` are accepted as equivalents.
    rootfs_digest: str | None = None
    # Registry manifest digest (``RepoDigests``), when the image was pulled
    # rather than side-loaded.  Optional for exactly that reason.
    repo_digest: str | None = None
    runtime_handler: str = "docker"  # or "podman"
    requirements: ContainerRequirements = Field(default_factory=ContainerRequirements)
    mounts: list[ContainerMount] = Field(default_factory=list)

    @field_validator("digest", "rootfs_digest", "repo_digest")
    @classmethod
    def _validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DIGEST_RE.match(value):
            raise ValueError(
                f"{value!r} is not a sha256 OCI digest (expected 'sha256:' + 64 hex chars)"
            )
        return value


class RayPlatformTuning(BaseModel):
    """Platform-specific Ray environment overrides."""

    model_config = ConfigDict(extra="forbid")

    env: dict[str, str] = Field(default_factory=dict)
    start_args: dict[str, Any] = Field(default_factory=dict)


class NcclPlatformTuning(BaseModel):
    """Platform-specific NCCL communication environment overrides."""

    model_config = ConfigDict(extra="forbid")

    env: dict[str, str] = Field(default_factory=dict)


class PlatformTuning(BaseModel):
    """Platform-specific system and framework tunings."""

    model_config = ConfigDict(extra="forbid")

    ray: RayPlatformTuning = Field(default_factory=RayPlatformTuning)
    nccl: NcclPlatformTuning = Field(default_factory=NcclPlatformTuning)
    # Diagnostic-only settings.  Never injected automatically: they are merged
    # by ``inject_platform_tuning(..., diagnostics=True)`` so that verbose
    # per-rank output is an operator decision, not a default.
    diagnostics: dict[str, str] = Field(default_factory=dict)


class CompatibilityMatrix(BaseModel):
    """Software stack shipped inside the bundle image.

    Every field is a fact read off the built artifact; §4B requires the full
    Ray/vLLM/Python/Torch/CUDA/NCCL/Triton set, not just the headline three.
    """

    model_config = ConfigDict(extra="forbid")

    ray_version: str
    vllm_version: str
    cuda_version: str
    python_version: str | None = None
    torch_version: str | None = None
    nccl_version: str | None = None
    triton_version: str | None = None
    transformers_version: str | None = None
    #: The diffusers its image app runs picture models on; None: it has none.
    diffusers_version: str | None = None
    supported_fabrics: list[str] = Field(default_factory=lambda: ["roce", "infiniband", "ethernet"])
    #: Model types the image's Transformers and vLLM can read a config.json of,
    #: and the architectures its vLLM runs natively (``runtime-image``
    #: ``rebuild_runtime_image.py`` reads them off the image). Empty: not known.
    supported_model_types: list[str] = Field(default_factory=list)
    supported_architectures: list[str] = Field(default_factory=list)
    #: The picture pipelines (a diffusers model_index.json's ``_class_name``)
    #: its diffusers runs. Empty: the runtime serves no picture models.
    supported_pipelines: list[str] = Field(default_factory=list)


class CertificationEvidence(BaseModel):
    """What was actually certified, when, and with what result."""

    model_config = ConfigDict(extra="forbid")

    status: str = "uncertified"  # "passed", "partial", "failed", "uncertified"
    #: "stable": what a machine of the flavor gets unless its cluster chose
    #: another. "preview": only a cluster whose operator chose it.
    channel: str = "stable"
    hardware_target: str | None = None
    timestamp: str | None = None
    checks_passed: int = 0
    checks_total: int = 0
    report_ref: str | None = None
    notes: list[str] = Field(default_factory=list)


class RuntimeBundleManifest(BaseModel):
    """Top-level immutable runtime bundle manifest document."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "bundle.fadenstack.com/v1alpha1"
    bundle_id: str
    display_name: str
    description: str = ""
    #: Which orchestrator this image can serve.
    #:
    #: A bundle is an artifact for a *driver on a platform*, and the driver
    #: half was implicit while Ray was the only one. Making it explicit is
    #: what lets an EXO or Dynamo image sit in the same registry and be
    #: resolved by the same rule instead of by a second mechanism.
    driver: str = "ray"
    target_architecture: TargetArchitecture = Field(default_factory=TargetArchitecture)
    container: ContainerSpec
    platform_tuning: PlatformTuning = Field(default_factory=PlatformTuning)
    compatibility_matrix: CompatibilityMatrix
    certification: CertificationEvidence = Field(default_factory=CertificationEvidence)

    @classmethod
    def from_runtime_manifest(
        cls,
        manifest: dict[str, Any],
        *,
        bundle_id: str,
        display_name: str,
        description: str = "",
        driver: str = "ray",
        target_architecture: TargetArchitecture | None = None,
        platform_tuning: PlatformTuning | None = None,
        mounts: list[ContainerMount] | None = None,
        requirements: ContainerRequirements | None = None,
        report_ref: str | None = None,
    ) -> "RuntimeBundleManifest":
        """Build a bundle entry from a built image's runtime manifest.

        This is the only supported way to mint a bundle's *identity*: image
        reference, digest, stack versions and certification evidence all come
        from the artifact the image build emitted, so the catalog cannot drift
        from what was certified.
        """
        stack = manifest.get("stack_components") or manifest.get("components") or {}
        image = manifest.get("release_tag") or manifest.get("target_image")
        digest = manifest.get("image_id")
        rootfs_digest = manifest.get("rootfs_digest")
        if not rootfs_digest and manifest.get("rootfs_layers"):
            rootfs_digest = compute_rootfs_digest(list(manifest["rootfs_layers"]))
        if not image:
            raise BundleValidationError("runtime manifest has no release_tag/target_image")
        if not digest:
            raise BundleValidationError(f"runtime manifest for {image} has no image_id")

        cert = manifest.get("certification") or {}
        checks = cert.get("checks") or []
        passed = sum(1 for c in checks if str(c.get("status", "")).upper() == "PASS")
        notes = [
            f"{c.get('name') or 'check'}: {c.get('status')} - {c.get('detail')}"
            for c in checks
            if str(c.get("status", "")).upper() != "PASS"
        ]
        status = str(cert.get("overall_status", "uncertified")).lower()
        if checks and passed < len(checks) and status == "passed":
            status = "partial"

        return cls(
            bundle_id=bundle_id,
            display_name=display_name,
            description=description,
            driver=driver,
            target_architecture=target_architecture or TargetArchitecture(),
            container=ContainerSpec(
                image=str(image),
                digest=str(digest),
                rootfs_digest=str(rootfs_digest) if rootfs_digest else None,
                requirements=requirements or ContainerRequirements(),
                mounts=mounts or [],
            ),
            platform_tuning=platform_tuning or PlatformTuning(),
            compatibility_matrix=CompatibilityMatrix(
                ray_version=str(stack.get("ray", "")),
                vllm_version=str(stack.get("vllm", "")),
                cuda_version=str(stack.get("cuda", "")),
                python_version=_opt(stack.get("python")),
                torch_version=_opt(stack.get("torch")),
                nccl_version=_opt(stack.get("nccl")),
                triton_version=_opt(stack.get("triton")),
                transformers_version=_opt(stack.get("transformers")),
                diffusers_version=_opt(stack.get("diffusers")),
                supported_model_types=[str(t) for t in manifest.get("supported_model_types") or []],
                supported_architectures=[str(a) for a in manifest.get("supported_architectures") or []],
                supported_pipelines=[str(p) for p in manifest.get("supported_pipelines") or []],
            ),
            certification=CertificationEvidence(
                status=status,
                channel=str(cert.get("channel") or "stable").lower(),
                hardware_target=_opt(cert.get("hardware_target")),
                timestamp=_opt(cert.get("timestamp")),
                checks_passed=passed,
                checks_total=len(checks) or int(cert.get("checks_total", 0) or 0),
                report_ref=report_ref,
                notes=notes,
            ),
        )


def _opt(value: Any) -> str | None:
    """Normalize an optional manifest field to ``str | None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# The catalogue: one runtime per minted manifest
# ---------------------------------------------------------------------------
#
# Read from the manifests the image build writes, not transcribed beside them.
#
# This used to be a hand-copied dict "read off the images that are actually on
# the two DGX Spark nodes". It was accurate the day it was typed, and then it
# was the only record of an image that lived nowhere else: never pushed to a
# registry, held only on the two nodes. When the nodes were cleaned, the image
# went with them, and the catalogue went on pinning an artefact that no longer
# existed. The backend meanwhile distributed a *different* build -- the one the
# manifest file described -- which the integrity check then correctly refused,
# 303 times, on every cluster start.
#
# One record makes that impossible. ``rebuild_runtime_image.py`` builds the
# image and writes its manifest from ``docker image inspect`` and the in-image
# helper; this reads that file. Every ``runtime-manifest*.json`` is one
# runtime -- several per flavor, so a newer one can ship as a preview beside
# the certified one clusters run, and an older one stays to go back to.
def _manifest_paths() -> list[Path]:
    """The minted manifests shipped beside the code, one per runtime."""
    here = Path(__file__).resolve()
    for root in (
        # Canonical: the runtime image component of this repo.
        here.parents[4] / "runtime-image",
        # A deployment that vendors the manifests beside the backend.
        here.parents[3] / "runtime-image",
    ):
        if paths := sorted(root.glob("runtime-manifest*.json")):
            return paths
    return []


def _described(manifest: dict[str, Any], what: str) -> str:
    """What the image carries, in words, from its own manifest: typed beside it, it went stale."""
    stack = manifest.get("stack_components") or {}
    base = manifest.get("base_image") or {}
    base_tag = f" ({base.get('repository')}:{base.get('tag')})" if base.get("tag") else ""
    return f"Ray {stack.get('ray', '?')} + vLLM {stack.get('vllm', '?')}{base_tag} image {what}"


#: What every runtime of a flavor shares, whichever build it is: the machine
#: it is for, what its container needs and the tuning certified there. A
#: runtime's identity -- image, digest, stack, certification -- is its
#: manifest's, and the manifest names its flavor.
_FLAVORS: dict[str, dict[str, Any]] = {
    "gb10": {
        "display_name": "Fadenstack runtime for NVIDIA DGX Spark (GB10)",
        "what": "with Blackwell GB10 support over 200 Gb/s RoCE",
        "target_architecture": TargetArchitecture(
            cpu="aarch64",
            os="linux",
            accelerator=AcceleratorSpec(
                vendor="nvidia",
                families=["Blackwell", "GB10"],
                compute_capabilities=["12.1"],
                min_driver_version="570.86.10",
            ),
        ),
        "requirements": ContainerRequirements(
            network_mode="host",
            ipc_mode="host",
            gpus="all",
            # Verified present on both DGX Spark nodes.
            devices=["/dev/infiniband"],
            capabilities=["IPC_LOCK"],
        ),
        "mounts": [
            ContainerMount(host_path="/srv/faden/models", container_path="/models", mode="ro"),
            # Mounted at Hugging Face's own default location rather than nested
            # under /models.  The previous target, /models/huggingface, sat inside
            # the read-only bind mount above, and the host source of that mount has
            # no "huggingface" directory -- so runc could not create the mountpoint
            # and every container start failed with "read-only file system".
            # /root/.cache is ordinary image rootfs, so the mountpoint is created
            # normally, and HF_HOME needs no override because this *is* the default.
            ContainerMount(
                host_path="/home/sachi/.cache/huggingface",
                container_path="/root/.cache/huggingface",
                mode="ro",
            ),
            # Ray's session directory, on the host.
            #
            # Every Serve replica writes its own log file under
            # ``session_latest/logs/serve/``, and the runtime container itself
            # idles on ``sleep infinity`` -- so its console is empty and the only
            # record of what a replica did lives in these files. Without the
            # mount the agent has to shell into the container to read them, which
            # costs an exec per poll and loses the byte offsets that make tailing
            # cheap. With it they are ordinary files.
            #
            # Read-write because Ray owns the directory and writes to it; a
            # read-only mount would stop the cluster starting at all.
            ContainerMount(
                host_path="/var/lib/faden/ray",
                container_path="/tmp/ray",
                mode="rw",
            ),
        ],
        "platform_tuning": PlatformTuning(
            ray=RayPlatformTuning(
                env={
                    # Certified necessary: the GB10's unified memory makes Ray's
                    # host memory monitor evict workers spuriously.
                    "RAY_memory_monitor_refresh_ms": "0",
                    # Raylet and Python worker logs do not rotate by default.
                    # That is survivable while the session directory lives inside
                    # a container that gets recreated; once it is persisted on the
                    # host (see the /tmp/ray mount above) an unbounded log
                    # directory is how a node's disk fills, quietly, weeks later.
                    #
                    # 256 MiB across 5 files per log is roughly 1.2 GiB worst case
                    # per log family -- enough to keep a long incident readable,
                    # small enough to sit on a system disk.
                    "RAY_ROTATION_MAX_BYTES": str(256 * 1024 * 1024),
                    "RAY_ROTATION_BACKUP_COUNT": "5",
                },
                start_args={
                    "disable_usage_stats": True,
                    "metrics_export_port": 8089,
                },
            ),
            nccl=NcclPlatformTuning(
                env={
                    "NCCL_IB_RETRY_CNT": "7",
                    "NCCL_BUFFSIZE": "16777216",
                },
            ),
            # Opt-in only: NCCL_DEBUG=INFO produces verbose per-rank output on
            # every collective and is a diagnostic, not a runtime setting.
            diagnostics={"NCCL_DEBUG": "INFO"},
        ),
    },
    # The generic x86_64 NVIDIA runtime.
    #
    # One flavor for the whole platform, not one per card. Its kernels are
    # compiled for sm_75 through sm_120 -- Turing to Blackwell -- so a TITAN RTX,
    # an A100 and an RTX 5090 all run the same image, and adding a GPU model is
    # usually adding nothing at all.
    #
    # Ray 2.58.0 matches the DGX runtime on purpose: Ray refuses to form a
    # cluster across mismatched versions, so the two platforms move together.
    #
    # It carries no mounts. The DGX flavor's paths are that machine's
    # (``/home/sachi/.cache/huggingface``), and a generic runtime has no business
    # guessing where a model store lives -- the agent supplies it.
    "x86_64": {
        "display_name": "Fadenstack runtime for x86_64 NVIDIA GPUs (generic)",
        "what": "for any mainstream NVIDIA card on x86_64, from Turing (sm_75) to Blackwell (sm_120)",
        "target_architecture": TargetArchitecture(
            cpu="x86_64",
            os="linux",
            accelerator=AcceleratorSpec(
                vendor="nvidia",
                families=[],  # deliberately open: the capability list is the gate
                compute_capabilities=["7.5", "8.0", "8.6", "9.0", "10.0", "12.0"],
            ),
        ),
    },
}


def _catalogue() -> dict[str, RuntimeBundleManifest]:
    """Every runtime shipped beside the code, by runtime id."""
    paths = _manifest_paths()
    if not paths:  # pragma: no cover - a slim checkout without runtime-image/
        log.warning("No runtime manifests shipped with this backend; no machine can run a cluster")
    runtimes: dict[str, RuntimeBundleManifest] = {}
    for path in paths:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        flavor = dict(_FLAVORS.get(str(manifest.get("flavor")), {}))
        runtime_id = str(manifest.get("runtime_id") or "")
        if not flavor or not runtime_id:
            raise BundleValidationError(
                f"{path.name} names no known flavor ({manifest.get('flavor')!r}) or no runtime_id; "
                "mint it with runtime-image/rebuild_runtime_image.py"
            )
        if runtime_id in runtimes:
            raise BundleValidationError(f"{path.name}: runtime {runtime_id} is minted twice")
        runtimes[runtime_id] = RuntimeBundleManifest.from_runtime_manifest(
            manifest,
            bundle_id=runtime_id,
            description=_described(manifest, flavor.pop("what")),
            report_ref=f"runtime-image/{path.name}",
            **flavor,
        )
    return runtimes


#: The order a machine's default runtime is picked in, best evidence first.
#: A failed certification is never a default.
_EVIDENCE = ("passed", "partial", "uncertified")


def _certified_at(bundle: RuntimeBundleManifest) -> float:
    """When the runtime was certified, as a number to sort by; 0 when never."""
    timestamp = bundle.certification.timestamp
    try:
        return datetime.fromisoformat(timestamp).timestamp() if timestamp else 0.0
    except ValueError:
        return 0.0


#: Where a cluster keeps its runtime: ``config_json[RUNTIME_KEY]`` is the one it
#: was switched to, ``observed_status_json[RUNTIME_KEY]`` what each machine
#: was started with (by node id).
RUNTIME_KEY = "runtime"


def started_runtimes(environment: Any) -> dict[str, str]:
    """The runtime each of a cluster's machines was started with, by node id."""
    return dict((environment.observed_status_json or {}).get(RUNTIME_KEY) or {})


def _preference(environment: Any, node_id: Any) -> tuple[str, ...]:
    """The runtimes a cluster's machine runs, first say first (``runtime_choice``)."""
    if environment is None:
        return ()
    chosen = (environment.config_json or {}).get(RUNTIME_KEY)
    started = started_runtimes(environment).get(str(node_id))
    return tuple(str(runtime) for runtime in (chosen, started) if runtime)


#: Container paths a node fills from its own configuration.
#:
#: ``(key in capabilities_json["paths"], container path, mode)``. These are
#: the paths every Ray runtime image expects regardless of platform: the model
#: store it reads weights from, and the session directory Ray writes its logs
#: to. Anything image-specific stays in the bundle's own mount list.
_NODE_MOUNT_POINTS: tuple[tuple[str, str, str], ...] = (
    ("model_store", "/models", "ro"),
    ("ray_session", "/tmp/ray", "rw"),
)


class RuntimeBundleRegistry:
    """In-memory and file-backed registry for certified runtime bundles."""

    def __init__(self) -> None:
        self._bundles: dict[str, RuntimeBundleManifest] = _catalogue()

    def register_bundle(self, bundle: RuntimeBundleManifest) -> None:
        """Register a new or updated bundle after validating its identity."""
        self.validate_bundle(bundle)
        self._bundles[bundle.bundle_id] = bundle

    @staticmethod
    def validate_bundle(bundle: RuntimeBundleManifest) -> None:
        """Reject a bundle that cannot be pinned or resolved.

        The digest format is enforced by the model; this additionally refuses a
        bundle whose image reference or stack identity is empty, which would
        otherwise register as an authoritative-looking entry nothing can honour.
        """
        if not bundle.container.image.strip():
            raise BundleValidationError(f"bundle {bundle.bundle_id}: container.image is empty")
        matrix = bundle.compatibility_matrix
        missing = [
            name
            for name, value in (
                ("ray_version", matrix.ray_version),
                ("vllm_version", matrix.vllm_version),
                ("cuda_version", matrix.cuda_version),
            )
            if not str(value).strip()
        ]
        if missing:
            raise BundleValidationError(
                f"bundle {bundle.bundle_id}: compatibility_matrix.{', '.join(missing)} is empty"
            )

    def resolve_for_node(
        self, node: InfraNode, *, driver: str = "ray", environment: Any = None
    ) -> RuntimeBundleManifest | None:
        """The runtime that runs *driver* on *node*, as a member of *environment*.

        The one the cluster was switched to, else the one the machine was
        started with, else the machine's default (``runtime_choice`` says
        why). No *environment*: the default.

        This replaces pinning a bundle id on the environment. Architecture is
        a property of a machine, so an artifact keyed by architecture cannot
        belong to the cluster: a cluster with an aarch64 and an x86_64 node
        has no single right answer, and pinning one meant the wrong image was
        pushed to the odd node out and the container simply failed to start.

        Resolution is by platform, not by card. A bundle declares the
        capability set it was built for, and one image covers a whole
        generation range -- so adding a GPU model is usually adding nothing.

        ``None`` means no certified bundle covers this machine, which the
        caller must treat as a refusal rather than a reason to fall back to
        something that will not run.
        """
        matches = self.runtimes_for(node, driver=driver)
        for runtime_id in _preference(environment, node.id):
            chosen = next((b for b in matches if b.bundle_id == runtime_id), None)
            if chosen is not None:
                return chosen
        return self.default_among(matches)

    def runtimes_for(self, node: InfraNode, *, driver: str = "ray") -> list[RuntimeBundleManifest]:
        """Every runtime that runs *driver* on *node*.

        Empty for a node that has not reported its platform:
        ``validate_node_compatibility`` answers "is there a reason this cannot
        work", and an unknown field is not one, so an unknown machine would
        otherwise match every runtime -- an aarch64 image for an x86 box.
        """
        if not str((node.capabilities_json or {}).get("machine") or "").strip():
            return []
        return [
            bundle
            for bundle in self._bundles.values()
            if bundle.driver == driver and self.validate_node_compatibility(bundle, node)[0]
        ]

    @staticmethod
    def default_among(matches: list[RuntimeBundleManifest]) -> RuntimeBundleManifest | None:
        """The runtime a machine gets when its cluster chose none.

        Never a preview -- that is for a cluster whose operator chose it --
        and never one that failed certification. Among the rest: the narrowest
        capability set (a runtime built more specifically for this hardware),
        then the best evidence, then the most recently certified, then the id,
        so a cluster's image never depends on dict ordering.
        """
        candidates = [
            b for b in matches
            if b.certification.channel != "preview" and b.certification.status in _EVIDENCE
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda b: (
                len(b.target_architecture.accelerator.compute_capabilities) or 999,
                _EVIDENCE.index(b.certification.status),
                -_certified_at(b),
                b.bundle_id,
            ),
        )

    def defaults(self) -> set[str]:
        """The runtime each kind of machine gets unless its cluster chose another."""
        kinds: dict[str, list[RuntimeBundleManifest]] = {}
        for bundle in self._bundles.values():
            kinds.setdefault(bundle.target_architecture.model_dump_json(), []).append(bundle)
        return {default.bundle_id for group in kinds.values() if (default := self.default_among(group))}

    def bundles_for_driver(self, driver: str) -> list[RuntimeBundleManifest]:
        """Every bundle that serves *driver*, for the operator-facing list."""
        return [b for b in self._bundles.values() if b.driver == driver]

    def get_bundle(self, bundle_id: str) -> RuntimeBundleManifest | None:
        """Fetch a bundle by ID."""
        return self._bundles.get(bundle_id)

    def list_bundles(self) -> list[RuntimeBundleManifest]:
        """List all registered bundles."""
        return list(self._bundles.values())

    def load_from_yaml(self, path: Path | str) -> RuntimeBundleManifest:
        """Load, validate, and register a bundle from a YAML file."""
        file_path = Path(path)
        content = file_path.read_text(encoding="utf-8")
        raw_data = yaml.safe_load(content)
        manifest = RuntimeBundleManifest.model_validate(raw_data)
        self.register_bundle(manifest)
        return manifest

    def load_from_runtime_manifest(
        self,
        path: Path | str,
        *,
        bundle_id: str,
        display_name: str,
        **kwargs: Any,
    ) -> RuntimeBundleManifest:
        """Build and register a bundle from a built image's runtime manifest."""
        file_path = Path(path)
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        manifest = RuntimeBundleManifest.from_runtime_manifest(
            raw,
            bundle_id=bundle_id,
            display_name=display_name,
            report_ref=str(file_path),
            **kwargs,
        )
        self.register_bundle(manifest)
        return manifest

    def validate_node_compatibility(
        self,
        bundle: RuntimeBundleManifest,
        node: InfraNode,
    ) -> tuple[bool, str]:
        """Check if an InfraNode meets the bundle's hardware requirements."""
        caps = node.capabilities_json or {}
        gpu_info = caps.get("gpu") or {}
        gpus = gpu_info.get("devices") or []

        # Check GPU vendor
        vendor = (gpu_info.get("vendor") or caps.get("gpu_vendor") or "nvidia").lower()
        if vendor != bundle.target_architecture.accelerator.vendor.lower():
            return False, (
                f"Incompatible GPU vendor: expected "
                f"{bundle.target_architecture.accelerator.vendor}, found {vendor}"
            )

        # Host CPU architecture, when the node reported it.
        machine = str(caps.get("machine") or "").lower()
        expected_cpu = bundle.target_architecture.cpu.lower()
        if machine and expected_cpu:
            aliases = {"aarch64": {"aarch64", "arm64"}, "x86_64": {"x86_64", "amd64"}}
            accepted = aliases.get(expected_cpu, {expected_cpu})
            if machine not in accepted:
                return False, (
                    f"Incompatible CPU architecture: bundle targets {expected_cpu}, "
                    f"node reports {machine}"
                )

        # If bundle requires specific compute capability, verify if present
        required_caps = set(bundle.target_architecture.accelerator.compute_capabilities)
        if required_caps and gpus:
            node_caps = {
                str(g.get("compute_capability")) for g in gpus if g.get("compute_capability")
            }
            if node_caps and not required_caps.intersection(node_caps):
                return False, (
                    f"Compute capability mismatch: required {required_caps}, found {node_caps}"
                )

        return True, "Node is fully compatible with runtime bundle"

    def inject_platform_tuning(
        self,
        bundle: RuntimeBundleManifest,
        *,
        env_vars: dict[str, str] | None = None,
        diagnostics: bool = False,
    ) -> dict[str, str]:
        """Merge bundle platform tunings into the execution environment variables.

        ``diagnostics`` opts into the bundle's diagnostic settings (verbose
        NCCL tracing and similar); they are never applied by default.
        """
        merged: dict[str, str] = dict(env_vars or {})
        # Inject Ray tuning env vars
        for k, v in bundle.platform_tuning.ray.env.items():
            merged.setdefault(k, str(v))
        # Inject NCCL tuning env vars
        for k, v in bundle.platform_tuning.nccl.env.items():
            merged.setdefault(k, str(v))
        if diagnostics:
            for k, v in bundle.platform_tuning.diagnostics.items():
                merged.setdefault(k, str(v))
        return merged

    @staticmethod
    def mounts_for_node(
        bundle: RuntimeBundleManifest, node: "InfraNode | None"
    ) -> list[ContainerMount]:
        """The bundle's mounts, with the node's own paths filled in.

        A bundle knows which container paths its image expects; it cannot know
        where they live on a machine it has never seen. The node reports that
        under ``capabilities_json["paths"]``, so the two are composed here
        rather than one of them guessing.

        A mount the bundle declares explicitly wins, so a bundle certified for
        one machine's layout keeps it.
        """
        declared = list(bundle.container.mounts)
        taken = {m.container_path for m in declared}
        paths = ((node.capabilities_json or {}).get("paths") or {}) if node else {}

        for key, container_path, mode in _NODE_MOUNT_POINTS:
            if container_path in taken:
                continue
            host_path = str(paths.get(key) or "").strip()
            if not host_path:
                continue
            declared.append(
                ContainerMount(
                    host_path=host_path, container_path=container_path, mode=mode
                )
            )
        return declared

    @staticmethod
    def container_launch_spec(
        bundle: RuntimeBundleManifest,
        *,
        name: str,
        env: dict[str, str] | None = None,
        node: "InfraNode | None" = None,
    ) -> dict[str, Any]:
        """Render a bundle into the node-agent container launch contract.

        The semantic requirements are carried over as-is; turning them into
        handler flags is the agent's job (it owns the ``docker``/``podman``
        difference), which is exactly why they are not CLI strings here.
        """
        req = bundle.container.requirements
        return {
            "name": name,
            "image": bundle.container.image,
            "digest": bundle.container.digest,
            "rootfs_digest": bundle.container.rootfs_digest,
            "repo_digest": bundle.container.repo_digest,
            "runtime_handler": bundle.container.runtime_handler,
            # The agent maps ``requirements.gpus`` onto its handler's flags,
            # and which flags those are depends on the vendor -- ``--gpus`` is
            # the NVIDIA toolkit's spelling, not a universal one.
            "target_architecture": {
                "cpu": bundle.target_architecture.cpu,
                "os": bundle.target_architecture.os,
                "accelerator_vendor": bundle.target_architecture.accelerator.vendor,
                "accelerator_families": list(bundle.target_architecture.accelerator.families),
            },
            "requirements": req.model_dump(),
            "mounts": [
                m.model_dump()
                for m in RuntimeBundleRegistry.mounts_for_node(bundle, node)
            ],
            "env": dict(env or {}),
        }


# Default singleton registry instance
default_bundle_registry = RuntimeBundleRegistry()


#: The Ray runtime container's name on a node.
#:
#: One name on every node, deliberately: the image behind it differs per
#: platform, but an operator reading ``docker ps`` on any member of a cluster
#: should see the same thing running.
RUNTIME_CONTAINER_NAME = "faden-ray-runtime"


async def runtime_bundle_payload_for(
    session: "AsyncSession",
    node_id: "uuid.UUID | str | None",
    *,
    environment: Any,
    driver: str = "ray",
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """The launch contract for *driver*'s container on the machine *node_id*.

    ``None`` when the node has no certified bundle, which the agent reads as
    "run on the host runtime". That fallback is only ever right for a node
    that genuinely has one; :func:`~...planner` refuses a deployment onto a
    node without a bundle before it gets this far.
    """
    node, bundle = await node_and_bundle_for(session, node_id, driver=driver, environment=environment)
    if node is None or bundle is None:
        return None
    return default_bundle_registry.container_launch_spec(
        bundle, name=RUNTIME_CONTAINER_NAME, env=env or {}, node=node,
    )


async def node_and_bundle_for(
    session: "AsyncSession",
    node_id: "uuid.UUID | str | None",
    *,
    environment: Any,
    driver: str = "ray",
) -> "tuple[InfraNode | None, RuntimeBundleManifest | None]":
    """The node row and the runtime it runs as a member of *environment*.

    Callers need both -- the bundle for the image, the row for the host paths
    the launch spec is composed from -- and fetching the row twice for one
    command is the kind of thing that only shows up under load. *environment*
    is required (None only for a machine outside any cluster): resolved
    without it, a probe named the default runtime's image while the machine
    ran the one its cluster chose.
    """
    node = await _node_row(session, node_id)
    if node is None:
        return None, None
    return node, default_bundle_registry.resolve_for_node(node, driver=driver, environment=environment)


async def _node_row(
    session: "AsyncSession", node_id: "uuid.UUID | str | None"
) -> "InfraNode | None":
    """The node row behind an id, or ``None`` for anything unusable."""
    if node_id is None:
        return None
    from faden_backend.db.models.node_control import InfraNode  # noqa: PLC0415

    try:
        key = node_id if isinstance(node_id, uuid.UUID) else uuid.UUID(str(node_id))
    except (ValueError, TypeError):
        return None
    return await session.get(InfraNode, key)
