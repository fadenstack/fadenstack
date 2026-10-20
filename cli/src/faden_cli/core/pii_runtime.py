"""Configure an explicit local PII runtime without changing existing policy floors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import click

from faden_cli.core.console import console, info, warning
from faden_cli.core.env_gen import read_env_file, set_env_values
from faden_cli.core.pii_catalog import catalog, model_for_backend, resolve

PROFILES = ("legacy", "cpu", "gpu-shared", "gpu-dedicated")


def configure(
    env_path: Path,
    profile: str | None,
    model_dir: Path | None,
    *,
    gpu_mib: int = 4096,
    threads: int | None = None,
    keep_gliner2_cpu: bool = False,
    model: str | None = None,
    locale: str = "europe-west-v1",
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
            FADEN_PII_ENTITY_PROFILE="standard",
            FADEN_PII_EXECUTION_PROFILE="cpu",
            FADEN_PII_BUILD_TARGET="prod",
            FADEN_PII_IMAGE="",
            FADEN_PII_MODEL_ID="",
            FADEN_PII_LOCALE_PACK="",
            FADEN_PII_MODEL_REVISION="",
            FADEN_PII_CATALOG_VERSION="",
            FADEN_PII_RULES_VERSION="",
        )
    else:
        if model_dir is None:
            raise ValueError(
                "Neural PII requires --pii-model-dir containing provisioned files and manifest.json"
            )
        root = model_dir.resolve(strict=True)
        data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        model, entry, pack = resolve(
            model
            or ("patronus-onnx-v1" if profile == "cpu" and not keep_gliner2_cpu else "gliner2-multi-v1"),
            locale,
            profile,
        )
        backend, revision = entry["adapter"], entry["revision"]
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
        target = entry["build_target"]
        saved_language = read_env_file(env_path).get("FADEN_PII_DEFAULT_LANGUAGE", "en")
        values.update(
            FADEN_PII_BACKEND=backend,
            FADEN_PII_MODEL_ID=model,
            FADEN_PII_LOCALE_PACK=locale,
            FADEN_PII_MODEL_REVISION=revision,
            FADEN_PII_CATALOG_VERSION=catalog()["version"],
            FADEN_PII_RULES_VERSION=catalog()["rules_version"],
            FADEN_PII_DEFAULT_LANGUAGE=saved_language
            if saved_language in pack["languages"]
            else pack["languages"][0],
            FADEN_PII_EXECUTION_PROFILE=profile,
            FADEN_PII_MODEL_DIR=root.as_posix(),
            FADEN_PII_MODEL_PATH="/models",
            FADEN_PII_MANIFEST_PATH="/models/manifest.json",
            FADEN_PII_PRECISION=entry["precision"],
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
    model: str | None = None,
    locale: str | None = None,
) -> bool:
    """One selection/migration path for deploy, upgrade and module enable."""
    from faden_cli.core.pii_hardware import CPU_NOTICE, detect_support
    from faden_cli.core.pii_artifacts import MODELS, provision
    from faden_cli.core.sysinfo import effective_threads

    saved = read_env_file(env_path)
    if requested == "legacy" and (model is not None or locale is not None):
        raise ValueError("Legacy PII cannot use a neural model or locale pack")
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
    chosen_locale = locale or saved.get("FADEN_PII_LOCALE_PACK") or catalog()["default_locale"]
    chosen_model = (
        model
        or saved.get("FADEN_PII_MODEL_ID")
        or (model_for_backend(old_backend) if old_backend != "presidio" else "auto")
    )
    if not existing and not yes:
        for key, pack in catalog()["locales"].items():
            console.print(f"  {key}: {pack['name']}")
        if locale is None:
            chosen_locale = click.prompt(
                "  PII content locale", type=click.Choice(list(catalog()["locales"])), default=chosen_locale
            )
        if model is None:
            for key, candidate in catalog()["models"].items():
                console.print(f"  {key}: {candidate['name']}; modes: {', '.join(candidate['execution'])}.")
            console.print(
                "  auto: recommended detector for the content locale; hardware is selected separately."
            )
            chosen_model = click.prompt(
                "  PII detector", type=click.Choice(["auto", *catalog()["models"]]), default=chosen_model
            )
    # Resolve auto once, then persist the concrete ID. Recommendations never migrate upgrades.
    chosen_model, entry, _ = resolve(chosen_model, chosen_locale, "cpu")
    if (
        existing
        and saved.get("FADEN_PII_MODEL_REVISION")
        and model is None
        and saved["FADEN_PII_MODEL_REVISION"] != entry["revision"]
    ):
        raise ValueError(
            "Installed PII revision differs from this catalog; select a model explicitly to migrate"
        )
    support = detect_support(gpu_mib=gpu_mib)
    console.print("\n[bold cyan]Local PII execution[/bold cyan]")
    for reason in support.reasons:
        warning(reason)
    selected = requested or (
        installed if existing and not (installed == "legacy" and (model or locale)) else "cpu"
    )
    if requested is None and support.available and not yes:
        console.print(f"  Compatible GPU available. Selected detector: {chosen_model}.")
        choices = [
            *(["legacy"] if existing and installed == "legacy" and not (model or locale) else []),
            *entry["execution"],
        ]
        if len(choices) > 1:
            selected = click.prompt(
                "  PII mode", type=click.Choice(choices), default=selected if selected in choices else "cpu"
            )
        else:
            selected = choices[0]
            info(
                f"The selected detector supports {selected} execution. Choose --pii-model gliner2-multi-v1 for compatible GPU execution."
            )
    elif requested is None and existing and not yes and selected in ("legacy", "cpu"):
        selected = click.prompt(
            "  PII CPU mode",
            type=click.Choice(["cpu"] if model or locale else ["legacy", "cpu"]),
            default=selected,
        )
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
    # Old --pii-profile cpu explicitly requested the ONNX migration. Preserve
    # that documented behavior only for installs predating independent selection.
    if (
        requested == "cpu"
        and model is None
        and not saved.get("FADEN_PII_MODEL_ID")
        and old_backend == "gliner2"
        and not fallback
    ):
        chosen_model = "patronus-onnx-v1"
    chosen_model, entry, _ = resolve(chosen_model, chosen_locale, selected)
    backend = entry["adapter"]
    keep_gliner2_cpu = selected == "cpu" and backend == "gliner2"
    if keep_gliner2_cpu:
        info(
            "Running GLiNER2 on CPU. The detector is preserved independently of execution mode. Use --pii-model patronus-onnx-v1 to migrate explicitly."
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
        env_path,
        selected,
        model_dir,
        gpu_mib=gpu_mib,
        threads=threads,
        keep_gliner2_cpu=keep_gliner2_cpu,
        model=chosen_model,
        locale=chosen_locale,
    )
    values = {"FADEN_PII_GPU_ID": device.uuid if device is not None else ""}
    if keep_gliner2_cpu:
        values.update(FADEN_PII_BATCH_SHORT="8", FADEN_PII_BATCH_LONG="2")
    if backend != old_backend or chosen_locale != (
        saved.get("FADEN_PII_LOCALE_PACK") or catalog()["default_locale"]
    ):
        values.update(FADEN_PII_CALIBRATION_PATH="", FADEN_PII_BATCH_SHORT="", FADEN_PII_BATCH_LONG="")
        warning(
            "PII detector or locale changed. Existing tenant policies are preserved; verify languages and explicitly adopt the installed profile in PII settings before protected traffic can use it."
        )
    # A bundled install pulls the separately published variant; source installs
    # build it. The runtime fingerprint still pins the model/artifact identity.
    if published:
        values["FADEN_PII_IMAGE"] = (
            f"{saved.get('REGISTRY') or 'ghcr.io/fadenstack'}/pii:{saved.get('VERSION') or 'latest'}-{'cpu' if backend == 'gliner-onnx' else 'gpu'}"
        )
    set_env_values(env_path, values)
    info(
        f"PII detector: {chosen_model}; locale: {chosen_locale}; mode: {selected}; {read_env_file(env_path)['FADEN_PII_CPU_THREADS']} native CPU thread(s)."
    )
    return needs_build and not published


def options(command):
    """The same explicit PII overrides for upgrade and module enable."""
    for args, kwargs in [
        (
            ("--pii-model",),
            {
                "type": click.Choice(["auto", *catalog()["models"]]),
                "default": None,
                "help": "Approved local detector; auto resolves once and upgrades preserve its ID.",
            },
        ),
        (
            ("--pii-locale",),
            {
                "type": click.Choice(list(catalog()["locales"])),
                "default": None,
                "help": "Supported content locale pack, independent of execution mode.",
            },
        ),
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


def warm_candidate(ctx) -> None:
    """Check the candidate artifact/adapter before replacing running services.

    CPU staging avoids competing for a running GPU owner's lock or allocator.
    The real container's readiness check still validates its selected device.
    No HTTP server, broker or database connection is started here.
    """
    import subprocess
    import uuid

    if ctx.env_file is None:
        return
    saved = read_env_file(ctx.env_file)
    if saved.get("FADEN_PII_BACKEND", "presidio") == "presidio":
        return
    info("Checking the selected local PII model and locale before service replacement…")
    code = (
        "import asyncio,os; from faden_pii.settings import settings; "
        "from faden_pii.services.pii.service import PIIService; "
        "s=PIIService.create(config=settings); assert s.runtime.healthy; "
        "assert s.runtime.detector.metadata['model_id']==(os.environ.get('FADEN_PII_MODEL_ID') or settings.pii_model_id); "
        "assert s.runtime.detector.metadata['locale_pack']==(os.environ.get('FADEN_PII_LOCALE_PACK') or settings.pii_locale_pack); "
        "asyncio.run(s.close()); print('Local PII candidate ready')"
    )
    name = "faden-pii-validate-" + uuid.uuid4().hex
    command = ctx.base_cmd() + [
        "run",
        "--rm",
        "--name",
        name,
        "--no-deps",
        "-T",
        "--entrypoint",
        "python",
        "-e",
        "FADEN_PII_EXECUTION_PROFILE=cpu",
        "-e",
        "FADEN_PII_DEVICE=cpu",
        "-e",
        "FADEN_PII_CPU_THREADS=1",
        "-e",
        "FADEN_PII_BATCH_SHORT=8",
        "-e",
        "FADEN_PII_BATCH_LONG=2",
        "faden-pii",
        "-c",
        code,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            "PII candidate validation could not complete; PII startup stopped. Check Docker, the selected image and local model files."
        ) from exc
    finally:
        # Killing a Docker CLI on timeout does not guarantee its container stops.
        # Remove only the uniquely named candidate owned by this invocation.
        try:
            cleanup = subprocess.run(
                [command[0], "rm", "-f", name], capture_output=True, text=True, timeout=15
            )
            if cleanup.returncode and "no such container" not in getattr(cleanup, "stderr", "").lower():
                warning(f"Check removal of PII validation container {name} before retrying.")
        except (OSError, subprocess.TimeoutExpired):
            warning(f"Could not remove PII validation container {name}; remove it before retrying.")
    if result.returncode:
        raise ValueError(
            "PII model/locale warm-up failed; PII startup stopped. Check the selected image, manifest, calibration and available RAM before retrying."
        )
    info("Selected local PII model and locale passed startup validation.")


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
