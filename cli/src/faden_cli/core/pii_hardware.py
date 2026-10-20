"""Host checks for the pinned Torch 2.10/CUDA 12.8 PII image."""

from __future__ import annotations

from dataclasses import dataclass, field
import csv
import io
import re
import shutil
import subprocess

from faden_cli.core.detect import GpuInfo, detect_gpu

CPU_NOTICE = "CPU-based PII handling is slower, especially for parallel requests and long text."


@dataclass(frozen=True)
class PIIGpu:
    index: str
    uuid: str
    name: str
    capability: str
    free_mib: int


@dataclass
class PIIGpuSupport:
    devices: list[PIIGpu] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.devices)


def detect_support(gpu: GpuInfo | None = None, *, gpu_mib: int = 4096) -> PIIGpuSupport:
    """Conservative local prerequisites; startup still runs actual model smoke checks.

    CUDA 12.8 wheels exclude Maxwell/Pascal. Require a verified architecture,
    driver CUDA support and Docker access rather than inferring support from a
    product name. Free memory is a snapshot, not a reservation.
    """
    from faden_cli.core.compose import has_nvidia_gpu

    gpu = gpu if gpu is not None else detect_gpu()
    support = PIIGpuSupport()
    if gpu.vendor != "nvidia" or not gpu.has_gpu:
        support.reasons.append(
            "No supported NVIDIA CUDA GPU detected for the GLiNER2 image (AMD/Apple GPUs use CPU)."
        )
        return support
    version = re.fullmatch(r"(\d+)\.(\d+)", gpu.cuda_version)
    if version is None or tuple(map(int, version.groups())) < (12, 8):
        support.reasons.append("The NVIDIA driver must report CUDA 12.8 or newer for the pinned PII image.")
        return support
    if gpu_mib < 4096:
        support.reasons.append(
            "The validated GLiNER2 GPU preset needs a 4096 MiB or larger allocator budget."
        )
        return support
    if not has_nvidia_gpu():
        support.reasons.append(
            "NVIDIA GPU access is unavailable to Docker; check the driver and NVIDIA Container Toolkit."
        )
        return support
    smi = shutil.which("nvidia-smi")
    if smi is None:
        support.reasons.append("nvidia-smi is unavailable; GPU compatibility cannot be verified.")
        return support
    try:
        result = subprocess.run(
            [smi, "--query-gpu=index,uuid,compute_cap,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("GPU query failed")
        devices = {str(d.index): d for d in gpu.devices}
        for row in csv.reader(io.StringIO(result.stdout)):
            if len(row) != 4:
                continue
            index, uuid, capability, free = (value.strip() for value in row)
            device = devices.get(index)
            if device is None:
                continue
            # The pinned wheel contains sm70/75/80/86/90/100/120. Cubins are
            # forward compatible within a major capability (e.g. 8.9 -> 8.6).
            if not re.fullmatch(r"(?:7\.[05]|8\.[069]|9\.0|10\.0|12\.0)", capability):
                support.reasons.append(
                    f"{device.name}: compute capability {capability} is outside the pinned image's supported set."
                )
                continue
            if not re.fullmatch(r"GPU-[a-fA-F0-9-]+", uuid):
                raise ValueError("Invalid GPU identity")
            free_mib = int(float(free))
            if free_mib < gpu_mib + 512:
                support.reasons.append(
                    f"{device.name}: {free_mib} MiB free; need {gpu_mib} MiB plus 512 MiB headroom."
                )
                continue
            support.devices.append(PIIGpu(index, uuid, device.name, capability, free_mib))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        support.devices.clear()
        support.reasons.append(
            "GPU architecture/free-memory checks failed; compatibility could not be verified."
        )
    if not support.devices and not support.reasons:
        support.reasons.append("No compatible GPU with sufficient free memory was found.")
    return support
