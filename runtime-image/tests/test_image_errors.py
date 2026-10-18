"""What a picture request that fails tells its caller: a sentence of the server's own, never an exception's text.

The image app runs inside the runtime image, on Ray Serve with torch and diffusers. Here ``ray.serve`` is a
stand-in when Ray is not installed, and no pipeline is loaded: the handler runs on a server made without its
constructor, with a ``_make`` that fails as a GPU would. Run with any Python that has FastAPI::

    python -m pytest runtime-image/tests
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

pytest.importorskip("fastapi")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _stand_in_ray() -> None:
    try:
        import ray.serve  # noqa: F401
    except ImportError:
        ray = types.ModuleType("ray")
        serve = types.ModuleType("ray.serve")
        serve.deployment = lambda *args, **kwargs: (lambda cls: cls)  # type: ignore[attr-defined]
        serve.ingress = lambda app: (lambda cls: cls)  # type: ignore[attr-defined]
        ray.serve = serve  # type: ignore[attr-defined]
        sys.modules["ray"] = ray
        sys.modules["ray.serve"] = serve


_stand_in_ray()

from llm_port_ray_runtime import image  # noqa: E402

#: What CUDA and torch say, with the server's insides in it.
CUDA_TEXT = "CUDA error: an illegal memory access was encountered (/opt/venv/lib/python3.12/site-packages/torch)"
OOM_TEXT = "CUDA out of memory. Tried to allocate 2.00 GiB (GPU 0; 23.65 GiB total capacity; 21.1 GiB reserved)"


class _OutOfMemory(RuntimeError):
    """Stands in for ``torch.cuda.OutOfMemoryError``."""


class _Request:
    def __init__(self, body: Any) -> None:
        self._body = body

    async def json(self) -> Any:
        return self._body


def _server(make: Callable[[Dict[str, Any], Optional[int]], List[str]], torch: Any = None) -> Any:
    cls = getattr(image.ImageServer, "func_or_class", image.ImageServer)
    server = object.__new__(cls)
    server.model_id = "z-image-turbo"
    server.size = image.DEFAULT_SIZE
    server.max_pixels = image.DEFAULT_MAX_PIXELS
    server.steps = None
    server.guidance = None
    server._takes = {
        "prompt", "width", "height", "num_images_per_prompt", "num_inference_steps", "guidance_scale", "generator",
    }
    server._torch = torch if torch is not None else types.SimpleNamespace(
        cuda=types.SimpleNamespace(OutOfMemoryError=_OutOfMemory),
    )
    server._make = make
    return server


def _ask(server: Any, body: Any) -> Tuple[int, Dict[str, Any]]:
    response = asyncio.run(server.generations(_Request(body)))
    if isinstance(response, dict):
        return 200, response
    return response.status_code, json.loads(response.body)


def _failing(exc: BaseException) -> Callable[[Dict[str, Any], Optional[int]], List[str]]:
    def make(call: Dict[str, Any], seed: Optional[int]) -> List[str]:
        raise exc

    return make


def test_a_failed_picture_says_nothing_of_the_servers_insides(caplog: pytest.LogCaptureFixture) -> None:
    status, body = _ask(_server(_failing(RuntimeError(CUDA_TEXT))), {"prompt": "a lighthouse"})
    assert status == 500
    assert body["error"] == {
        "message": "the picture could not be made; the model server's log says why",
        "type": "server_error",
        "code": 500,
    }
    assert "illegal memory access" in caplog.text, "the log keeps the reason and its traceback"


def test_out_of_gpu_memory_is_a_503_to_try_again() -> None:
    status, body = _ask(_server(_failing(_OutOfMemory(OOM_TEXT))), {"prompt": "a lighthouse"})
    assert status == 503
    assert "GPU memory" in body["error"]["message"] and "try again" in body["error"]["message"]
    assert "Tried to allocate" not in body["error"]["message"]
    assert body["error"]["type"] == "server_error"


def test_a_torch_without_the_out_of_memory_error_still_answers_500() -> None:
    server = _server(_failing(_OutOfMemory(OOM_TEXT)), torch=types.SimpleNamespace())
    status, body = _ask(server, {"prompt": "a lighthouse"})
    assert status == 500
    assert "Tried to allocate" not in body["error"]["message"]


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"n": "many"}, "n must be a whole number between 1 and 4"),
        ({"n": 9}, "n must be a whole number between 1 and 4"),
        ({"size": "big"}, "size must be WIDTHxHEIGHT, such as 1024x1024 (got 'big')"),
        ({"size": 1024}, "size must be WIDTHxHEIGHT, such as 1024x1024 (got 1024)"),
        ({"size": "4096x4096"}, "each side must be between 256 and 2048 pixels (got 4096x4096)"),
        ({"num_inference_steps": "lots"}, "num_inference_steps must be a whole number"),
        ({"guidance_scale": [1]}, "guidance_scale must be a number"),
        ({"seed": "abc"}, "seed must be a whole number"),
    ],
)
def test_the_callers_own_mistakes_are_said_in_the_servers_words(change: Dict[str, Any], says: str) -> None:
    made: List[Any] = []
    status, body = _ask(_server(lambda call, seed: made.append(call) or []), {"prompt": "a lighthouse", **change})
    assert status == 400
    assert body["error"]["message"] == says
    assert made == [], "nothing was made"


def test_a_good_request_reaches_the_pipeline() -> None:
    made: List[Tuple[Dict[str, Any], Optional[int]]] = []

    def make(call: Dict[str, Any], seed: Optional[int]) -> List[str]:
        made.append((call, seed))
        return ["UE5H"]

    status, body = _ask(_server(make), {
        "prompt": "a lighthouse", "size": "1000x750", "n": "2", "num_inference_steps": 9, "guidance_scale": "0",
        "seed": "7", "negative_prompt": "fog",
    })
    assert status == 200 and body["data"] == [{"b64_json": "UE5H"}]
    call, seed = made[0]
    assert call == {
        "prompt": "a lighthouse", "width": 992, "height": 736, "num_images_per_prompt": 2,
        "num_inference_steps": 9, "guidance_scale": 0.0,
    }, "a negative prompt goes only to a pipeline that takes one"
    assert seed == 7
