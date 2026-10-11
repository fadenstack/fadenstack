"""What a model is, read from what the Hugging Face Hub says about it.

Pure: every function takes plain values (the Hub's model info fields, a
``config.json`` dict) and returns plain values, so the rules are testable
without the network.

Two rules carried through everything here:

* **A number nobody could learn is ``None``, never zero.** A repo without
  safetensors metadata has an unknown size, not a size of 0 -- and 0 would
  read as "fits anywhere".
* **Guesses are labelled.** Capabilities and quantization inferred from a name
  or tag are best effort; the architecture facts from ``config.json`` are what
  the memory estimate is built on.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

#: Bytes per element for the dtypes the Hub reports in safetensors metadata.
#: Packed 4-bit formats (AWQ/GPTQ/NVFP4) store their weights as I32/U8
#: elements, so counting those elements at their storage width gives the real
#: size on disk, which is what occupies accelerator memory.
DTYPE_BYTES: dict[str, int] = {
    "F64": 8, "I64": 8, "U64": 8,
    "F32": 4, "I32": 4, "U32": 4,
    "BF16": 2, "F16": 2, "I16": 2, "U16": 2,
    "F8_E4M3": 1, "F8_E5M2": 1, "F8_E8M0": 1, "I8": 1, "U8": 1, "BOOL": 1,
}

#: The dtype a model computes in, by safetensors name.
_DTYPE_NAMES = {"BF16": "bfloat16", "F16": "float16", "F32": "float32", "F8_E4M3": "fp8", "F8_E5M2": "fp8"}

_QUANT_PATTERNS: list[tuple[str, str]] = [
    (r"nvfp4", "nvfp4"),
    (r"mxfp4", "mxfp4"),
    (r"(?<![a-z])awq(?![a-z])", "awq"),
    (r"gptq", "gptq"),
    (r"(?<![a-z])fp8(?![a-z])|w8a8-fp8|f8_e4m3", "fp8"),
    (r"(?<![a-z])int4(?![a-z])|w4a16", "int4"),
    (r"(?<![a-z])int8(?![a-z])|w8a8", "int8"),
    (r"bnb|bitsandbytes|4bit|8bit", "bitsandbytes"),
    (r"(?<![a-z])gguf(?![a-z])|q4_k_m|q8_0", "gguf"),
]

_PARAMS_IN_NAME = re.compile(r"(?<![0-9.])(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])")
_ACTIVE_IN_NAME = re.compile(r"-A(\d+(?:\.\d+)?)B", re.IGNORECASE)


def weights_bytes(safetensors_parameters: dict[str, int] | None) -> int | None:
    """Bytes the weights take, from the Hub's dtype -> element count breakdown."""
    if not safetensors_parameters:
        return None
    total = 0
    for dtype, count in safetensors_parameters.items():
        width = DTYPE_BYTES.get(str(dtype).upper())
        if width is None:
            return None  # a dtype we cannot size: unknown, not a guess
        total += int(count) * width
    return total or None


def compute_dtype(safetensors_parameters: dict[str, int] | None) -> str | None:
    """The dtype holding most of the weights, as vLLM names it."""
    if not safetensors_parameters:
        return None
    dominant = max(safetensors_parameters.items(), key=lambda kv: kv[1])[0].upper()
    return _DTYPE_NAMES.get(dominant)


def params_from_name(name: str) -> float | None:
    """``Qwen3-8B`` -> 8.0 (billions). ``None`` when the name does not say."""
    matches = [float(m.group(1)) for m in _PARAMS_IN_NAME.finditer(name.split("/")[-1])]
    # "Qwen3-30B-A3B": the total is the largest number, the active one is marked with A.
    return max(matches) if matches else None


def active_params_from_name(name: str) -> float | None:
    """``Qwen3-30B-A3B`` -> 3.0: the parameters a mixture-of-experts reads per token."""
    match = _ACTIVE_IN_NAME.search(name)
    return float(match.group(1)) if match else None


def detect_quantization(name: str, tags: list[str], config: dict[str, Any] | None,
                        safetensors_parameters: dict[str, int] | None) -> str | None:
    """The quantization a checkpoint carries, if any (``None`` for full precision)."""
    quant_config = (config or {}).get("quantization_config") or {}
    method = str(quant_config.get("quant_method") or "").lower()
    if method:
        if method in {"compressed-tensors", "modelopt"}:
            fmt = str(quant_config.get("format") or quant_config.get("quant_algo") or "").lower()
            if "fp4" in fmt:
                return "nvfp4"
            if "fp8" in fmt or "float" in fmt:
                return "fp8"
            if "pack" in fmt or "int4" in fmt:
                return "int4"
            # The Hub's list answer carries the method without its algorithm;
            # "modelopt" names the tool, and the repository name says what it
            # made ("...-NVFP4").
            return _quantization_in_names(name, tags) or method
        return method
    named = _quantization_in_names(name, tags)
    if named:
        return named
    dtypes = {str(d).upper() for d in (safetensors_parameters or {})}
    if dtypes & {"F8_E4M3", "F8_E5M2"}:
        return "fp8"
    return None


def _quantization_in_names(name: str, tags: list[str]) -> str | None:
    haystack = " ".join([name.lower(), *[t.lower() for t in tags]])
    for pattern, label in _QUANT_PATTERNS:
        if re.search(pattern, haystack):
            return label
    return None


def detect_format(tags: list[str], library: str | None, has_safetensors: bool, siblings: list[str]) -> str:
    """``safetensors`` / ``diffusers`` / ``gguf`` / ``mlx`` / ``pytorch`` / ``other``.

    ``diffusers``: a picture pipeline laid out in component folders
    (``model_index.json``, ``transformer/``, ``text_encoder/``, ``vae/``).
    """
    lowered = {t.lower() for t in tags}
    names = [s.lower() for s in siblings]
    if "gguf" in lowered or any(n.endswith(".gguf") for n in names):
        return "gguf"
    if "mlx" in lowered or (library or "").lower() == "mlx":
        return "mlx"
    if (library or "").lower() == "diffusers" or "model_index.json" in names:
        return "diffusers"
    if has_safetensors or any(n.endswith(".safetensors") for n in names):
        return "safetensors"
    if any(n.endswith(".bin") and "pytorch_model" in n for n in names):
        return "pytorch"
    return "other"


#: The Hub's tag naming a diffusers repository's pipeline class: ``diffusers:ZImagePipeline``.
_PIPELINE_TAG = re.compile(r"^diffusers:([A-Za-z0-9_]+Pipeline)$")


def pipeline_of(tags: list[str]) -> str | None:
    """The diffusers pipeline a repository is (its ``model_index.json`` class), from the Hub's tag; None without."""
    for tag in tags:
        match = _PIPELINE_TAG.match(str(tag))
        if match:
            return match.group(1)
    return None


def detect_capabilities(*, repo_id: str, pipeline_tag: str | None, tags: list[str],
                        architectures: list[str], chat_template: str | None) -> list[str]:
    """What the model does beyond plain chat: tools, vision, reasoning, code, embeddings, images.

    Inferred, so best effort -- the detail view says so. ``tools`` is the one
    read from the model itself: a chat template that renders tool definitions.
    ``images`` is a model that makes pictures from a prompt, and nothing else.
    """
    name = repo_id.lower()
    lowered = {t.lower() for t in tags}
    if pipeline_tag == "text-to-image":
        return ["images"]
    arch = " ".join(a.lower() for a in architectures)
    caps: list[str] = []
    if pipeline_tag in {"feature-extraction", "sentence-similarity"} or "sentence-transformers" in lowered \
            or re.search(r"embed|(?<![a-z])bge-|(?<![a-z])e5-|gte-", name):
        caps.append("embedding")
    if pipeline_tag in {"image-text-to-text", "visual-question-answering", "image-to-text"} \
            or re.search(r"vl(?![a-z])|vision|llava|pixtral|forconditionalgeneration", arch + " " + name):
        caps.append("vision")
    if chat_template and re.search(r"\btools\b|tool_call", chat_template):
        caps.append("tools")
    if re.search(r"think|reason|(?<![a-z])r1(?![a-z0-9])|qwq|gpt-oss|magistral", name) \
            or ("qwen3" in name and "instruct-2507" not in name and "embedding" not in name
                and "coder" not in name and "vl" not in name):
        caps.append("reasoning")
    if re.search(r"coder|codestral|devstral|starcoder|codellama|(?<![a-z])code(?![a-z])", name):
        caps.append("code")
    if "embedding" in caps:
        # An embedding model answers /v1/embeddings and nothing else: whatever
        # its template mentions, it neither calls tools nor reasons.
        return ["embedding"]
    return caps


def task_of(capabilities: list[str], pipeline_tag: str | None) -> str:
    """``embedding`` / ``vision`` / ``chat`` / ``images`` / ``other`` -- what a card is filed under."""
    if "images" in capabilities:
        return "images"
    if "embedding" in capabilities:
        return "embedding"
    if "vision" in capabilities:
        return "vision"
    if pipeline_tag in {"text-generation", "text2text-generation", "conversational", None, ""}:
        return "chat"
    return "other"


#: diffusers' variant files (``diffusion_pytorch_model.fp16.safetensors``): a
#: second copy of a component's weights, loaded only when asked for.
_VARIANT = re.compile(r"\.(fp16|bf16|fp32|fp8)\.safetensors$")


def diffusers_weight_files(names: list[str]) -> list[str]:
    """The safetensors a diffusers pipeline loads: each component folder's, one set per folder.

    Weights sit in component folders (``transformer/``, ``text_encoder/``,
    ``vae/``), never at the top. A folder that also ships a variant (a
    ``.fp16.`` copy) is counted once, by the set loaded by default.
    """
    folders: dict[str, list[str]] = {}
    for name in names:
        if name.endswith(".safetensors") and name.count("/") == 1:
            folders.setdefault(name.split("/", 1)[0], []).append(name)
    out: list[str] = []
    for files in folders.values():
        plain = [f for f in files if not _VARIANT.search(f)]
        out.extend(plain or files)
    return out


def diffusers_memory(file_bytes: int, params_by_dtype: dict[str, int]) -> int:
    """What a pipeline's weights take served in bfloat16: their files, with float32 ones halved.

    Z-Image-Turbo keeps its transformer in float32 (24.6 GB of its 32.8 GB);
    the image app loads it in bfloat16, so it takes 20.5 GB. The Hub's dtype
    counts say how many parameters are float32.
    """
    f32 = int((params_by_dtype or {}).get("F32") or 0)
    return max(file_bytes - 2 * f32, file_bytes // 2)


@dataclass
class Architecture:
    """The shape of the network, from ``config.json``: what the memory estimate needs."""

    model_type: str | None = None
    architectures: list[str] = field(default_factory=list)
    num_layers: int | None = None
    hidden_size: int | None = None
    num_attention_heads: int | None = None
    num_kv_heads: int | None = None
    head_dim: int | None = None
    #: The context the model was trained for (``max_position_embeddings``).
    max_context: int | None = None
    #: True when loading needs the repository's own Python (``auto_map``).
    needs_remote_code: bool = False
    #: True for mixture-of-experts models.
    moe: bool = False
    #: The Transformers the config was saved with: a hint at how new the model type is.
    transformers_version: str | None = None
    #: A hybrid model's layers that keep a recurrent state instead of a KV cache
    #: (Mamba, linear attention); ``None`` for a model of attention layers only.
    recurrent_layers: int | None = None
    #: Its layers with a KV cache, when it is hybrid.
    attention_layers: int | None = None
    #: The recurrent state one sequence holds across those layers, in bytes, when the config says enough.
    state_bytes_per_sequence: int | None = None

    def kv_bytes_per_token(self, dtype_bytes: int = 2) -> int | None:
        """KV cache bytes one token of context occupies (keys and values, every attention layer)."""
        layers = self.attention_layers or self.num_layers
        if not (layers and self.num_kv_heads and self.head_dim):
            return None
        return 2 * layers * self.num_kv_heads * self.head_dim * dtype_bytes

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def architecture_from_config(config: dict[str, Any] | None) -> Architecture:
    """Read the architecture facts out of ``config.json``.

    Vision-language models keep the language model's shape under
    ``text_config``; that is the part the KV cache belongs to.
    """
    if not config:
        return Architecture()
    text = config.get("text_config") if isinstance(config.get("text_config"), dict) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            for source in (text, config):
                if source.get(key) is not None:
                    return source[key]
        return None

    heads = pick("num_attention_heads", "n_head", "num_heads")
    hidden = pick("hidden_size", "d_model", "n_embd")
    head_dim = pick("head_dim")
    if head_dim is None and heads and hidden:
        head_dim = int(hidden) // int(heads)
    kv_heads = pick("num_key_value_heads", "num_kv_heads", "multi_query_group_num") or heads
    num_layers = _int(pick("num_hidden_layers", "n_layer", "num_layers"))
    attention_layers, recurrent_layers = _layer_kinds(pick("layer_types"), pick("full_attention_interval"), num_layers)
    return Architecture(
        model_type=config.get("model_type"),
        architectures=list(config.get("architectures") or []),
        num_layers=num_layers,
        recurrent_layers=recurrent_layers,
        attention_layers=attention_layers,
        state_bytes_per_sequence=_state_bytes(pick, recurrent_layers),
        hidden_size=_int(hidden),
        num_attention_heads=_int(heads),
        num_kv_heads=_int(kv_heads),
        head_dim=_int(head_dim),
        max_context=_int(pick("max_position_embeddings", "n_positions", "max_sequence_length", "seq_length")),
        needs_remote_code=bool(config.get("auto_map")),
        moe=bool(pick("num_experts", "num_local_experts", "n_routed_experts")),
        transformers_version=str(config["transformers_version"]) if config.get("transformers_version") else None,
    )


#: ``layer_types`` entries that keep a recurrent state per sequence instead of a KV cache.
_RECURRENT_LAYERS = frozenset({"linear_attention", "mamba", "mamba2", "gated_delta_net"})


def _layer_kinds(layer_types: Any, interval: Any, num_layers: int | None) -> tuple[int | None, int | None]:
    """(attention layers, recurrent layers) of a hybrid model; (None, None) for one of attention layers only.

    Qwen3.5 and later list them (``layer_types``: 16 ``full_attention``, 48
    ``linear_attention`` in the 27B); Qwen3-Next says every fourth layer is
    attention (``full_attention_interval``). Sliding-window layers are attention.
    """
    if isinstance(layer_types, list) and layer_types:
        recurrent = sum(1 for kind in layer_types if str(kind) in _RECURRENT_LAYERS)
        return (len(layer_types) - recurrent, recurrent) if recurrent else (None, None)
    every = _int(interval)
    if every and every > 1 and num_layers:
        attention = num_layers // every
        return attention, num_layers - attention
    return None, None


def _state_bytes(pick: Any, recurrent_layers: int | None) -> int | None:
    """The recurrent state one sequence holds, for gated delta net layers (Qwen3-Next, Qwen3.5 and later).

    Per layer: the state matrix of each value head (``mamba_ssm_dtype``,
    float32 unless it says otherwise) and the short convolution's window over
    the query, key and value channels (bfloat16). Other recurrent layers
    (Mamba2) are not sized here: ``None``.
    """
    if not recurrent_layers:
        return None
    v_heads, k_heads = _int(pick("linear_num_value_heads")), _int(pick("linear_num_key_heads"))
    k_dim, v_dim = _int(pick("linear_key_head_dim")), _int(pick("linear_value_head_dim"))
    if not (v_heads and k_heads and k_dim and v_dim):
        return None
    state_dtype_bytes = 2 if str(pick("mamba_ssm_dtype") or "float32") in {"bfloat16", "float16"} else 4
    kernel = _int(pick("linear_conv_kernel_dim")) or 4
    state = v_heads * k_dim * v_dim * state_dtype_bytes
    conv = (2 * k_heads * k_dim + v_heads * v_dim) * max(kernel - 1, 0) * 2
    return recurrent_layers * (state + conv)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def vllm_runnable(fmt: str, task: str, pipeline: str | None = None) -> tuple[bool, str | None]:
    """Whether a cluster can serve this checkpoint, and if not, why (a code).

    vLLM serves the text models; a picture model runs in the runtime's image
    app, which loads a whole diffusers pipeline -- a repository without one
    (a LoRA, a single component) has nothing to load on its own.
    """
    if task == "images" or fmt == "diffusers":
        if task != "images":
            return False, "task"
        if fmt != "diffusers" or not pipeline:
            return False, "no_pipeline"
        return True, None
    if fmt == "gguf":
        return False, "gguf"
    if fmt == "mlx":
        return False, "mlx"
    if fmt == "other":
        return False, "no_weights"
    if task == "other":
        return False, "task"
    return True, None
