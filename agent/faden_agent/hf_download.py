"""Download a model from Hugging Face on this machine, into its model store.

For a cluster whose machines reach the internet themselves (the cluster's
"machines download models" switch): each machine fetches the model from the
Hub instead of copying it from the Fadenstack server. On the release VM the
server downloaded a 132 GB model at 12 MB/s and would then have copied it to
both machines over the same link -- nine hours -- while the machines had
lines of their own.

The engine stays offline either way: the files land in the same store, in
the same Hugging Face cache layout a copy from the server makes, and vLLM
loads them from there.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: How often the size of the file being downloaded is read for progress.
PROGRESS_POLL_SEC = 5.0

#: Weights in a format vLLM loads, at the top of a repository.
_WEIGHTS = (".safetensors", ".bin", ".pt", ".pth")
#: The same weights exported for other runtimes.
_EXPORTS = (".onnx", ".onnx_data", ".gguf", ".h5", ".msgpack", ".ot", ".tflite", ".mlmodel")
#: Folders of such exports, of a second copy for another runtime, or of a training run.
_OTHER_FOLDERS = frozenset({"onnx", "openvino", "coreml", "gguf", "tflite", "metal", "original", "runs"})
#: What a training run leaves beside the model.
_TRAINING_STATE = ("training_args.bin", "optimizer.", "scheduler.", "rng_state", "events.out.tfevents")


class HfDownloadCanceled(Exception):
    """The download was stopped before it finished."""


def hub_endpoint() -> str:
    """The Hub this machine downloads from: ``HF_ENDPOINT`` (a mirror), else huggingface.co."""
    return (os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")


def files_to_fetch(names: list[str]) -> list[str]:
    """The files of a repository that vLLM reads -- the same choice the server makes.

    Only a layout this knows is filtered: weights vLLM loads at the top of the
    repository. Anything else (a GGUF-only repository, say) is fetched whole.
    """
    def training_state(name: str) -> bool:
        return name.rsplit("/", 1)[-1].startswith(_TRAINING_STATE)

    top_weights = [n for n in names if "/" not in n and n.endswith(_WEIGHTS) and not training_state(n)]
    if not top_weights:
        return list(names)
    safetensors = any(n.endswith(".safetensors") for n in top_weights)
    keep = []
    for name in names:
        folder = name.split("/", 1)[0] if "/" in name else ""
        if folder in _OTHER_FOLDERS or folder.startswith("checkpoint-"):
            continue
        if name.lower().endswith(_EXPORTS) or training_state(name):
            continue
        if safetensors and name.endswith((".bin", ".pt", ".pth")):
            continue  # the same weights again, in PyTorch's format
        keep.append(name)
    return keep


def _owner(path: Path) -> str:
    try:
        import pwd  # noqa: PLC0415 - POSIX only

        return pwd.getpwuid(path.stat().st_uid).pw_name
    except (ImportError, KeyError, OSError):
        return "another user"


def store_problem(cache_dir: str, hf_repo_id: str | None = None) -> str | None:
    """Why this agent cannot write a model into *cache_dir*, with the fix; None when it can.

    The Hub's client takes a lock under ``<store>/.locks/<model>`` for every
    file. On a DGX Spark a container once run as root had left ``.locks``
    owned by root, and the download failed on its first file with
    "Permission denied" -- the machine reached the Hub fine. Checked before a
    download starts, and when the server asks whether the machine can
    download at all, so the answer names the directory and the command.
    """
    root = Path(cache_dir)
    candidates = [root, root / ".locks"]
    if hf_repo_id:
        folder = f"models--{hf_repo_id.replace('/', '--')}"
        candidates += [root / folder, root / ".locks" / folder]
        candidates += [root / folder / part for part in ("blobs", "refs", "snapshots")]
    for path in candidates:
        if path.exists() and not os.access(path, os.W_OK | os.X_OK):
            user = os.environ.get("USER") or "the agent's user"
            return (
                f"{path} belongs to {_owner(path)} and the agent (running as {user}) cannot write to it. "
                f"Fix it on the machine with: sudo chown -R {user}: {path}"
            )
    return None


async def check_internet(payload: dict[str, Any], *, model_store_root: str | None = None) -> dict[str, Any]:
    """``CHECK_INTERNET``: can this machine download a model from the Hub itself?

    Asks the Hub's API for one model, through the proxy the environment names
    if any, as a download would. Any answer from the Hub -- 429 included --
    means it is reachable; no answer, or a proxy's error page, means not. And
    the model store must be the agent's to write (``store_problem``).
    """
    timeout = min(30.0, max(1.0, float(payload.get("timeout_sec") or 8.0)))
    url = f"{hub_endpoint()}/api/models?limit=1"
    problem = store_problem(model_store_root) if model_store_root else None
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "faden-agent"})
    except httpx.HTTPError as exc:
        return {
            "reachable": False,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}"[:300] or type(exc).__name__,
            "store_problem": problem,
        }
    latency_ms = int((time.monotonic() - started) * 1000)
    reachable = response.status_code < 500 and response.status_code not in (407,)
    result: dict[str, Any] = {
        "reachable": reachable,
        "url": url,
        "status_code": response.status_code,
        "latency_ms": latency_ms,
        "store_problem": problem,
    }
    if not reachable:
        result["error"] = f"The Hub answered {response.status_code}"
    return result


def _adopt_files(hf_cache: Path, model_dir: Path, commit: str, siblings: list[Any]) -> None:
    """Link files of *commit* that the machine's Hugging Face cache holds in full into *model_dir*'s blobs."""
    from faden_agent.model_puller import adopt_blob  # noqa: PLC0415

    (model_dir / "blobs").mkdir(parents=True, exist_ok=True)
    for sibling in siblings:
        source = (hf_cache / model_dir.name / "snapshots" / commit / sibling.rfilename).resolve(strict=False)
        size = int(getattr(sibling, "size", 0) or 0)
        # The blob keeps the name the Hub's client gave it (its etag), which hf_hub_download looks for.
        if source.is_file() and (not size or source.stat().st_size == size):
            adopt_blob(source, model_dir / "blobs" / source.name)


def download_model(
    *,
    hf_repo_id: str,
    revision: str | None,
    cache_dir: str,
    token: str | None,
    progress: Callable[[int, int, str], None] | None = None,
    cancel: threading.Event | None = None,
    machine_hf_cache: str | None = None,
) -> dict[str, Any]:
    """Download what vLLM reads of *hf_repo_id* into *cache_dir*; blocking, run it in a thread.

    Every file is fetched at the commit *revision* pointed to when the
    download started, so a push to the repository halfway through cannot mix
    two versions; ``refs/<revision>`` is written at the end. Files already in
    the store are not fetched again, and a file that was partly downloaded
    continues where it stopped. *progress* is called with (bytes done, bytes
    in all, file) every few seconds. A file the machine's own Hugging Face cache
    (*machine_hf_cache*) already holds at that commit is linked into the store
    first, so it is neither downloaded nor kept twice.
    """
    from huggingface_hub import HfApi, hf_hub_download  # noqa: PLC0415

    wanted_revision = revision or "main"
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    problem = store_problem(cache_dir, hf_repo_id)
    if problem is not None:
        raise PermissionError(problem)
    info = HfApi(endpoint=hub_endpoint(), token=token).model_info(
        hf_repo_id, revision=wanted_revision, files_metadata=True,
    )
    commit = str(info.sha or wanted_revision)
    listed = info.siblings or []
    wanted = set(files_to_fetch([s.rfilename for s in listed]))
    siblings = [s for s in listed if s.rfilename in wanted]
    total = sum(int(getattr(s, "size", 0) or 0) for s in siblings)
    model_dir = Path(cache_dir) / f"models--{hf_repo_id.replace('/', '--')}"
    blobs = model_dir / "blobs"
    log.info("Downloading %d files (%d bytes) of %s at %s from %s", len(siblings), total, hf_repo_id, commit, hub_endpoint())

    if machine_hf_cache:
        _adopt_files(Path(machine_hf_cache), model_dir, commit, siblings)

    done = 0
    current = ""
    stop = threading.Event()

    def _watch() -> None:
        while not stop.wait(PROGRESS_POLL_SEC):
            try:
                partial = sum(p.stat().st_size for p in blobs.glob("*.incomplete")) if blobs.is_dir() else 0
            except OSError:
                partial = 0
            if progress is not None:
                progress(min(done + partial, total), total, current)

    watcher = threading.Thread(target=_watch, name=f"hf-download-{hf_repo_id}", daemon=True)
    watcher.start()
    try:
        for sibling in siblings:
            if cancel is not None and cancel.is_set():
                raise HfDownloadCanceled(hf_repo_id)
            current = sibling.rfilename
            hf_hub_download(
                repo_id=hf_repo_id,
                filename=sibling.rfilename,
                revision=commit,
                cache_dir=cache_dir,
                token=token,
                endpoint=hub_endpoint(),
            )
            done += int(getattr(sibling, "size", 0) or 0)
    finally:
        stop.set()
        watcher.join(timeout=PROGRESS_POLL_SEC * 2)

    # What the revision named, recorded as the Hub's own client does -- the
    # commit, no newline -- so a cache reader resolves "main" to these files.
    if wanted_revision != commit:
        refs = model_dir / "refs"
        refs.mkdir(parents=True, exist_ok=True)
        (refs / wanted_revision).write_text(commit, encoding="utf-8")
    if progress is not None:
        progress(total, total, "")
    return {
        "commit": commit,
        "total_size": total,
        "files": len(siblings),
        "model_dir_name": model_dir.name,
        "snapshot_dir": str(model_dir / "snapshots" / commit),
    }


#: Where the agent asks the server for the Hugging Face token to download with.
TOKEN_PATH = "/api/node-files/hf-token"


async def fetch_token(client: httpx.AsyncClient, credential: str) -> str | None:
    """The server's Hugging Face token, for a gated model; None when it has none.

    Asked for each download and never written to disk: it stays the server's
    secret, handed out only to machines of a cluster that downloads models
    itself. A server that says no (the cluster's switch is off) stops the
    download; an older server without the endpoint means no token.
    """
    response = await client.get(TOKEN_PATH, headers={"Authorization": f"Bearer {credential}"})
    if response.status_code == 404:
        return None
    if response.status_code == 403:
        detail = ""
        try:
            detail = str(response.json().get("detail") or "")
        except ValueError:
            pass
        raise PermissionError(detail or "The server does not let this machine download models itself.")
    response.raise_for_status()
    token = response.json().get("token")
    return str(token) if token else None
