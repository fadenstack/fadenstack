"""An image is never read as a ``docker run`` option, whatever the backend sends.

The image goes right before the command: ``--privileged`` as the image was
read as an option and the command's first word as the image, past the flag
denylist, which looked only at the command.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from faden_agent.event_buffer import EventBuffer
from faden_agent.model_tls import NGINX_INDEX, NGINX_VERSION, server_image_tag
from faden_agent.runtime_manager import RuntimeManager, RuntimeManagerError
from faden_agent.runtimes import ContainerRuntimeError, check_image_ref
from faden_agent.runtimes.docker import DockerRuntime
from faden_agent.runtimes.podman import PodmanRuntime
from faden_agent.state_store import StateStore

DIGEST = "sha256:" + "a" * 64

REAL_IMAGES = [
    *RuntimeManager._DEFAULT_IMAGES.values(),
    "nvcr.io/nvidia/vllm:26.03-py3",
    "vllm/vllm-openai:v0.6.6",
    "faden/ray-vllm-gb10:ray2.58-nv26.09-images",
    "faden-ray-runtime-x86_64:2.58.0",
    "localhost:5000/faden/ray-vllm-gb10:ray2.58",
    f"ghcr.io/fadenstack/runtime@{DIGEST}",
    f"nginx@{NGINX_INDEX}",
    f"nginx:{NGINX_VERSION}",
    server_image_tag("x86_64"),
    server_image_tag("aarch64"),
    DIGEST,  # an image id
]


@pytest.mark.parametrize("image", REAL_IMAGES)
def test_the_images_the_product_runs_pass(image: str) -> None:
    assert check_image_ref(image) == image


@pytest.mark.parametrize("image", ["--privileged", "-v", "--ipc=host", "alpine --privileged", "", " alpine", "a\nb", "x" * 513])
def test_what_could_be_read_as_an_option_is_refused(image: str) -> None:
    with pytest.raises(ContainerRuntimeError):
        check_image_ref(image)


@pytest.mark.asyncio()
@pytest.mark.parametrize("runtime_cls", [DockerRuntime, PodmanRuntime])
async def test_run_and_pull_refuse_it_before_the_cli_runs(runtime_cls: type, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[tuple[str, ...]] = []

    async def _exec(self: Any, *args: str, **_: Any) -> tuple[int, str, str]:
        ran.append(args)
        return 0, "cid\n", ""

    monkeypatch.setattr(runtime_cls, "_exec", _exec)
    runtime = runtime_cls()
    with pytest.raises(ContainerRuntimeError):
        await runtime.run(image="--privileged", name="x", command=["alpine", "sh"])
    with pytest.raises(ContainerRuntimeError):
        await runtime.pull("--all-tags")
    assert ran == []
    assert await runtime.run(image="vllm/vllm-openai:latest", name="x", command=["serve"]) == "cid"
    assert ran[-1][-2:] == ("vllm/vllm-openai:latest", "serve")


# ── The deploy ────────────────────────────────────────────────────


class _Runtime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def exists(self, name: str) -> bool:
        self.calls.append("exists")
        return True

    async def remove(self, name: str, **_: Any) -> None:
        self.calls.append("remove")

    async def run(self, **_: Any) -> str:
        self.calls.append("run")
        return "cid"


def _manager(tmp_path: Path, runtime: _Runtime) -> RuntimeManager:
    return RuntimeManager(
        runtime=runtime,  # type: ignore[arg-type]
        state_store=StateStore(tmp_path / "state.json"),
        events=EventBuffer(),
        advertise_host="10.0.0.1",
    )


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "changes",
    [
        {"provider_config": {"image": "--privileged", "command": ["alpine", "sh"]}},
        {"image": "-v"},
        {"memory_limit": "--privileged"},
        {"provider_config": {"shm_size": "1g --privileged"}},
        {"cpu_limit": "all"},
        {"provider_config": {"ipc_mode": "container:faden-gateway"}},
    ],
)
async def test_a_deploy_with_such_a_value_changes_nothing_on_the_machine(
    tmp_path: Path, changes: dict[str, Any],
) -> None:
    runtime = _Runtime()
    payload = {"runtime_id": "rt-1", "provider_type": "vllm", "model_sync": {"hf_repo_id": "org/model"}, **changes}
    with pytest.raises(RuntimeManagerError):
        await _manager(tmp_path, runtime).deploy_workload(payload)
    assert runtime.calls == []  # the running container was not removed for it


@pytest.mark.parametrize(
    ("key", "value"),
    [("memory_limit", "16g"), ("memory_limit", "1.5GiB"), ("shm_size", "512m"), ("cpu_limit", "8"),
     ("cpu_limit", "1.5"), ("ipc_mode", "host"), ("ipc_mode", "private"), ("ipc_mode", "shareable")],
)
def test_the_values_the_console_offers_pass(key: str, value: str) -> None:
    RuntimeManager._check_run_values("vllm/vllm-openai:latest", {key: value}, {})


# ── The command's flags, in both forms ────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        ["--ipc", "host"],
        ["--ipc=host"],
        ["--ipc", "container:other"],
        ["--network", "host"],
        ["--net=host"],
        ["--network", "container:other"],
        ["--cap-add", "SYS_ADMIN"],
        ["--security-opt", "seccomp=unconfined"],
        ["--userns", "host"],
        ["--mount", "type=bind,source=/,target=/host"],
        ["--volume=/:/host"],
    ],
)
def test_a_denied_flag_is_refused_with_its_value_joined_or_apart(args: list[str]) -> None:
    with pytest.raises(RuntimeManagerError):
        RuntimeManager._sanitize_command_args(args)


def test_an_engine_command_passes() -> None:
    RuntimeManager._sanitize_command_args([
        "serve", "--model", "org/model", "--host", "0.0.0.0", "--port", "8000",
        "--max-model-len", "8192", "--enable-auto-tool-choice", "--tool-call-parser", "hermes",
    ])
    RuntimeManager._sanitize_command_args(["--ipc=private"])
