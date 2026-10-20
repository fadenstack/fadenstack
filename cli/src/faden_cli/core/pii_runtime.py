"""Configure an explicit local PII runtime without changing existing policy floors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import click

from faden_cli.core.console import console, info, warning
from faden_cli.core.env_gen import read_env_file, set_env_values

PROFILES = ("legacy", "cpu", "gpu-shared", "gpu-dedicated")


def configure(
    env_path: Path,
    profile: str | None,
    model_dir: Path | None,
    *,
    gpu_mib: int = 4096,
    threads: int | None = None,
    keep_gliner2_cpu: bool = False,
) -> bool:
    """Write a verified preset; return whether its source image must be built."""
    if profile is None:
        return False
    if profile not in PROFILES:
        raise ValueError("Unknown PII execution profile")
    from faden_cli.core.sysinfo import effective_threads, pii_threads

    available = effective_threads()
    thread_mode = "manual" if threads is not None else "auto"
    threads = (
        min(threads, available)
        if threads is not None
        else pii_threads(available, gpu=profile.startswith("gpu-"))
    )
    values = {
        "FADEN_PII_WORKERS_COUNT": "1",
        "FADEN_PII_CPU_THREADS": str(threads),
        "FADEN_PII_CPU_THREADS_MODE": thread_mode,
        "FADEN_PII_GPU_MEMORY_MIB": str(gpu_mib),
        "FADEN_PII_DEVICE": "cuda:0" if profile.startswith("gpu-") else "cpu",
    }
    if profile == "legacy":
        values.update(
            FADEN_PII_BACKEND="presidio",
            FADEN_PII_EXECUTION_PROFILE="cpu",
            FADEN_PII_BUILD_TARGET="prod",
            FADEN_PII_IMAGE="",
        )
    else:
        if model_dir is None:
            raise ValueError(
                "Neural PII requires --pii-model-dir containing provisioned files and manifest.json"
            )
        root = model_dir.resolve(strict=True)
        data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        backend = "gliner-onnx" if profile == "cpu" and not keep_gliner2_cpu else "gliner2"
        revision = (
            "6cfcbd93d7fc6f5e1658f80afa38f73891b91693"
            if backend == "gliner-onnx"
            else "1cb4166094dc58fa8d836429f060d6c95f62b495"
        )
        if (
            data.get("version") != 1
            or data.get("backend") != backend
            or data.get("revision") != revision
            or not data.get("files")
        ):
            raise ValueError("The model manifest does not match the selected PII profile")
        for name, expected in data["files"].items():
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError("Invalid PII artifact path")
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise ValueError("PII artifact checksum mismatch")
        target = "prod-cpu" if backend == "gliner-onnx" else "prod-gpu"
        values.update(
            FADEN_PII_BACKEND=backend,
            FADEN_PII_EXECUTION_PROFILE=profile,
            FADEN_PII_MODEL_DIR=root.as_posix(),
            FADEN_PII_MODEL_PATH="/models",
            FADEN_PII_MANIFEST_PATH="/models/manifest.json",
            FADEN_PII_PRECISION="float16" if backend == "gliner-onnx" else "float32",
            FADEN_PII_BUILD_TARGET=target,
            FADEN_PII_IMAGE=f"faden-pii-{target}:local",
        )
    set_env_values(env_path, values)
    return profile != "legacy"


def deploy_step(
    env_path: Path,
    *,
    requested: str | None = None,
    model_dir: Path | None = None,
    gpu_mib: int | None = None,
    threads: int | None = None,
    gpu_id: str | None = None,
    yes: bool = False,
    published: bool = False,
    upgrade: bool = False,
) -> bool:
    """One selection/migration path for deploy, upgrade and module enable."""
    from faden_cli.core.pii_hardware import CPU_NOTICE, detect_support
    from faden_cli.core.pii_artifacts import MODELS, provision
    from faden_cli.core.sysinfo import effective_threads

    saved = read_env_file(env_path)
    gpu_mib = gpu_mib if gpu_mib is not None else int(saved.get("FADEN_PII_GPU_MEMORY_MIB") or 4096)
    if (
        threads is None
        and saved.get("FADEN_PII_CPU_THREADS")
        and saved.get("FADEN_PII_CPU_THREADS_MODE") != "auto"
    ):
        threads = int(saved["FADEN_PII_CPU_THREADS"])
    old_backend = saved.get("FADEN_PII_BACKEND", "presidio")
    installed = (
        "legacy"
        if old_backend == "presidio"
        else "cpu"
        if old_backend == "gliner-onnx"
        else saved.get("FADEN_PII_EXECUTION_PROFILE", "gpu-shared")
    )
    # Existing installs without a backend setting are the legacy English model.
    existing = "FADEN_PII_BACKEND" in saved or upgrade
    support = detect_support(gpu_mib=gpu_mib)
    console.print("\n[bold cyan]Local PII execution[/bold cyan]")
    for reason in support.reasons:
        warning(reason)
    selected = requested or (installed if existing else "gpu-shared" if support.available else "cpu")
    if requested is None and support.available and not yes:
        console.print("  Compatible GPU available. CPU uses Patronus ONNX; GPU uses GLiNER2.")
        selected = click.prompt("  PII mode", type=click.Choice(list(PROFILES)), default=selected)
    elif requested is None and existing and not yes and selected in ("legacy", "cpu"):
        selected = click.prompt("  PII CPU mode", type=click.Choice(["legacy", "cpu"]), default=selected)
    fallback = selected.startswith("gpu-") and not support.available
    if fallback:
        warning("PII GPU execution is unavailable; falling back to CPU mode.")
        selected = "cpu"
    device = None
    if selected.startswith("gpu-"):
        identity = gpu_id or saved.get("FADEN_PII_GPU_ID")
        if identity:
            device = next((d for d in support.devices if identity in (d.uuid, d.index)), None)
            if device is None:
                warning("The selected PII GPU is unavailable/incompatible; falling back to CPU mode.")
                selected, fallback = "cpu", True
        elif len(support.devices) > 1 and not yes:
            for candidate in support.devices:
                console.print(
                    f"  {candidate.index}: {candidate.name}, {candidate.free_mib} MiB free ({candidate.uuid})"
                )
            identity = click.prompt("  PII GPU index", type=click.Choice([d.index for d in support.devices]))
            device = next(d for d in support.devices if d.index == identity)
        else:
            device = support.devices[0]
    if not selected.startswith("gpu-"):
        warning(CPU_NOTICE)
    available = effective_threads()
    if threads is not None and threads > available:
        warning(f"PII CPU threads reduced from {threads} to {available}, the available CPU limit.")
    if selected == "legacy":
        info(
            "Retaining the legacy English-only PII model. Select --pii-profile cpu for local multilingual ONNX."
        )
        return configure(env_path, selected, None, gpu_mib=gpu_mib, threads=threads)
    keep_gliner2_cpu = selected == "cpu" and old_backend == "gliner2" and (fallback or requested is None)
    backend = "gliner-onnx" if selected == "cpu" and not keep_gliner2_cpu else "gliner2"
    if keep_gliner2_cpu:
        info(
            "Running the installed GLiNER2 model on CPU to preserve its policy profile and calibration. Use --pii-profile cpu to explicitly migrate to Patronus ONNX."
        )
    if fallback and model_dir is not None:
        metadata = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
        if metadata.get("backend") != backend:
            warning(
                "The supplied GPU artifact cannot run as the CPU ONNX model; provisioning the matching CPU artifact separately."
            )
            model_dir = None
    if model_dir is None and backend == old_backend and saved.get("FADEN_PII_MODEL_DIR"):
        model_dir = Path(saved["FADEN_PII_MODEL_DIR"])
    if model_dir is None:
        import httpx

        try:
            model_dir = provision(backend, env_path.parent / "pii-models" / backend / MODELS[backend][1])
        except httpx.HTTPError as exc:
            raise ValueError(
                "PII model provisioning failed. Check network access or supply a verified --pii-model-dir. Existing service containers have not been replaced."
            ) from exc
    needs_build = configure(
        env_path, selected, model_dir, gpu_mib=gpu_mib, threads=threads, keep_gliner2_cpu=keep_gliner2_cpu
    )
    values = {"FADEN_PII_GPU_ID": device.uuid if device is not None else ""}
    if keep_gliner2_cpu:
        values.update(FADEN_PII_BATCH_SHORT="8", FADEN_PII_BATCH_LONG="2")
    if backend != old_backend:
        values.update(FADEN_PII_CALIBRATION_PATH="", FADEN_PII_BATCH_SHORT="", FADEN_PII_BATCH_LONG="")
        warning(
            "PII model profile changed. Existing tenant policies are preserved; explicitly adopt the installed profile in PII settings before protected traffic can use it."
        )
    # A bundled install pulls the separately published variant; source installs
    # build it. The runtime fingerprint still pins the model/artifact identity.
    if published:
        values["FADEN_PII_IMAGE"] = (
            f"{saved.get('REGISTRY') or 'ghcr.io/fadenstack'}/pii:{saved.get('VERSION') or 'latest'}-{'cpu' if backend == 'gliner-onnx' else 'gpu'}"
        )
    set_env_values(env_path, values)
    info(f"PII mode: {selected}; {read_env_file(env_path)['FADEN_PII_CPU_THREADS']} native CPU thread(s).")
    return needs_build and not published


def options(command):
    """The same explicit PII overrides for upgrade and module enable."""
    for args, kwargs in [
        (("--pii-profile",), {"type": click.Choice(list(PROFILES)), "default": None}),
        (
            ("--pii-model-dir",),
            {"type": click.Path(file_okay=False, exists=True, path_type=Path), "default": None},
        ),
        (("--pii-gpu-id",), {"default": None}),
        (("--pii-gpu-memory-mib",), {"type": click.IntRange(min=1024), "default": None}),
        (("--pii-cpu-threads",), {"type": click.IntRange(min=1, max=64), "default": None}),
    ]:
        command = click.option(*args, **kwargs)(command)
    return command


def gpu_overlay(env_file: Path | None, project_dir: Path | None) -> Path | None:
    """Use the same PII GPU reservation on deploy, restart, upgrade and status."""
    if env_file is None or project_dir is None:
        return None
    selected = read_env_file(env_file).get("FADEN_PII_EXECUTION_PROFILE", "cpu")
    if selected not in ("gpu-shared", "gpu-dedicated"):
        return None
    path = project_dir / "docker-compose.pii-gpu.yaml"
    if not path.is_file():
        raise ValueError("The installed PII GPU overlay is missing; update the shared deployment files")
    return path
