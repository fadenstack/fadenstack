"""A picture model as a Serve application, beside the LLM ones.

Serve LLM (``build_openai_app``) serves what vLLM serves, and vLLM makes no
pictures. A text-to-image model runs on diffusers instead: one deployment per
model (``ImageServer:<model>``), which is also the application's ingress and
answers OpenAI's ``POST /v1/images/generations`` with ``b64_json`` pictures --
the shape vLLM-Omni takes too, so the gateway talks to either the same way.

The document comes inside ``llm_serving_args``, so the Node Agent passes it on
as it does an LLM one::

    {"image_configs": [{
        "model_loading_config": {"model_id": "z-image-turbo", "model_source": "Tongyi-MAI/Z-Image-Turbo"},
        "pipeline_config": {"dtype": "bfloat16", "num_inference_steps": 9, "guidance_scale": 0.0,
                            "size": "1024x1024", "max_pixels": 2359296},
        "deployment_config": {"num_replicas": 1, "ray_actor_options": {"num_gpus": 1}},
        "runtime_env": {"env_vars": {...}}}]}

One model per application, as the backend deploys one model per deployment.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import io
import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from ray import serve

log = logging.getLogger(__name__)

#: Where an image document keeps its models (an LLM one keeps them in ``llm_configs``).
IMAGE_KEY = "image_configs"
#: The model's deployment is ``ImageServer:<model>``, as Serve LLM's is ``LLMServer:<model>``.
SERVER_PREFIX = "ImageServer"

#: Pictures diffusion models take: sides in multiples of 16, within these bounds.
MIN_SIDE, MAX_SIDE, STEP = 256, 2048, 16
DEFAULT_SIZE = "1024x1024"
#: About 2.4 megapixels unless the deployment says otherwise: the memory a picture takes grows with it.
DEFAULT_MAX_PIXELS = 1536 * 1536
MAX_PICTURES = 4

#: What ``deployment_config`` may set on the deployment, as Serve names it.
_DEPLOYMENT_OPTIONS = (
    "num_replicas",
    "max_ongoing_requests",
    "max_queued_requests",
    "ray_actor_options",
    "autoscaling_config",
    "health_check_period_s",
    "health_check_timeout_s",
    "graceful_shutdown_timeout_s",
    "graceful_shutdown_wait_loop_s",
)


def is_image_app(args: Any) -> bool:
    """Whether a serving document is for a picture model rather than an LLM."""
    return isinstance(args, dict) and bool(args.get(IMAGE_KEY))


def size_of(size: Optional[str], default: str = DEFAULT_SIZE, max_pixels: int = DEFAULT_MAX_PIXELS) -> Tuple[int, int]:
    """``"WxH"`` as (width, height) a diffusion model takes, or ``ValueError`` saying why not.

    Sides are rounded down to multiples of 16 rather than refused: a caller
    asking for 1000x750 gets 992x736, which is what it meant.
    """
    text = (size or default or DEFAULT_SIZE).strip().lower()
    parts = text.split("x")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise ValueError(f"size must be WIDTHxHEIGHT, such as 1024x1024 (got {size!r})")
    width, height = (int(p) // STEP * STEP for p in parts)
    if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
        raise ValueError(f"each side must be between {MIN_SIDE} and {MAX_SIDE} pixels (got {size})")
    if width * height > max_pixels:
        raise ValueError(f"{width}x{height} is more than this model makes ({max_pixels} pixels at most)")
    return width, height


def diffusers_without_xformers() -> Any:
    """``diffusers``, told that xformers is not there.

    NVIDIA's vLLM image carries an xformers built for an older flash-attn than
    the one beside it, so ``import xformers.ops`` fails -- and diffusers imports
    it whenever the package is installed, which fails every pipeline it loads.
    Pictures use torch's own attention instead; vLLM's environment is left as
    NVIDIA built it.
    """
    from diffusers.utils import import_utils

    import_utils._xformers_available = False
    import diffusers

    return diffusers


def _error(status: int, message: str, kind: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": kind, "code": status}})


_api = FastAPI(title="Fadenstack image server")


@serve.deployment(max_ongoing_requests=8, health_check_period_s=30, health_check_timeout_s=60)
@serve.ingress(_api)
class ImageServer:
    """One picture model, loaded once per copy, making one request's pictures at a time.

    Requests wait their turn on a lock rather than running side by side: a
    diffusion pipeline is not safe across threads, and two at once would take
    twice the memory on a machine that shares it with a chat model.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        import torch

        DiffusionPipeline = diffusers_without_xformers().DiffusionPipeline
        loading = config.get("model_loading_config") or {}
        self.model_id = str(loading["model_id"])
        source = str(loading.get("model_source") or self.model_id)
        settings = dict(config.get("pipeline_config") or {})
        self.size = str(settings.get("size") or DEFAULT_SIZE)
        self.max_pixels = int(settings.get("max_pixels") or DEFAULT_MAX_PIXELS)
        self.steps = settings.get("num_inference_steps")
        self.guidance = settings.get("guidance_scale")
        dtype = getattr(torch, str(settings.get("dtype") or "bfloat16"))

        started = time.monotonic()
        pipe = DiffusionPipeline.from_pretrained(source, torch_dtype=dtype)
        pipe.to("cuda")
        pipe.set_progress_bar_config(disable=True)
        self.pipe = pipe
        self._takes = set(inspect.signature(pipe.__call__).parameters)
        self._lock = threading.Lock()
        self._torch = torch
        log.info("%s: %s loaded from %s in %.0f s", self.model_id, type(pipe).__name__, source,
                 time.monotonic() - started)

    def check_health(self) -> None:
        if self.pipe is None:  # pragma: no cover - the constructor raises first
            raise RuntimeError("no pipeline")

    @_api.get("/v1/models")
    async def models(self) -> Dict[str, Any]:
        return {"object": "list", "data": [{"id": self.model_id, "object": "model", "owned_by": "fadenstack"}]}

    @_api.post("/v1/images/generations")
    async def generations(self, request: Request) -> Any:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "the request is not JSON")
        if not isinstance(body, dict):
            return _error(400, "the request must be a JSON object")
        model = body.get("model")
        if model and str(model) != self.model_id:
            return _error(404, f"this server makes pictures with {self.model_id}, not {model}", "not_found_error")
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return _error(400, "prompt is required")
        if (body.get("response_format") or "b64_json") != "b64_json":
            return _error(400, "pictures come back as b64_json only")
        try:
            n = int(body.get("n") or 1)
            if not 1 <= n <= MAX_PICTURES:
                raise ValueError(f"n must be between 1 and {MAX_PICTURES}")
            width, height = size_of(body.get("size"), self.size, self.max_pixels)
            call = self._call(body, prompt, n, width, height)
        except (TypeError, ValueError) as exc:
            return _error(400, str(exc))
        try:
            pictures = await asyncio.to_thread(self._make, call, body.get("seed"))
        except Exception as exc:  # noqa: BLE001 - the caller gets the reason, the log the traceback
            log.exception("%s: generation failed", self.model_id)
            return _error(500, f"the picture could not be made: {exc}", "server_error")
        return {
            "created": int(time.time()),
            "data": [{"b64_json": picture} for picture in pictures],
        }

    def _call(self, body: Dict[str, Any], prompt: str, n: int, width: int, height: int) -> Dict[str, Any]:
        """The pipeline's arguments: the request's, else the deployment's, else the model's own defaults."""
        call: Dict[str, Any] = {"prompt": prompt, "width": width, "height": height, "num_images_per_prompt": n}
        steps = body.get("num_inference_steps")
        steps = self.steps if steps is None else steps
        if steps is not None:
            call["num_inference_steps"] = int(steps)
        guidance = body.get("guidance_scale")
        guidance = self.guidance if guidance is None else guidance
        if guidance is not None:
            call["guidance_scale"] = float(guidance)
        negative = body.get("negative_prompt")
        if negative and "negative_prompt" in self._takes:
            call["negative_prompt"] = str(negative)
        return {k: v for k, v in call.items() if k in self._takes}

    def _make(self, call: Dict[str, Any], seed: Any) -> List[str]:
        torch = self._torch
        with self._lock:
            if seed is not None and "generator" in self._takes:
                call = {**call, "generator": torch.Generator(device="cuda").manual_seed(int(seed))}
            with torch.inference_mode():
                images = self.pipe(**call).images
            # Memory goes back to the machine between requests: it is shared with a chat model.
            torch.cuda.empty_cache()
        encoded = []
        for image in images:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            encoded.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
        return encoded


def build_image_app(args: Dict[str, Any]) -> Any:
    """The Serve application for an image document (``is_image_app``)."""
    configs = args.get(IMAGE_KEY) or []
    if len(configs) != 1 or not isinstance(configs[0], dict):
        raise ValueError("an image application serves exactly one model")
    config = configs[0]
    model_id = (config.get("model_loading_config") or {}).get("model_id")
    if not model_id:
        raise ValueError("model_loading_config.model_id is required")
    deployment = config.get("deployment_config") or {}
    options: Dict[str, Any] = {"name": f"{SERVER_PREFIX}:{model_id}"}
    options.update({k: deployment[k] for k in _DEPLOYMENT_OPTIONS if k in deployment})
    runtime_env = config.get("runtime_env")
    if runtime_env:
        actor = dict(options.get("ray_actor_options") or {})
        actor["runtime_env"] = {**(actor.get("runtime_env") or {}), **runtime_env}
        options["ray_actor_options"] = actor
    return ImageServer.options(**options).bind(config)
