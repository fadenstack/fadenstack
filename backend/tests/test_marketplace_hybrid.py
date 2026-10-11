"""A hybrid model's suggestion must start.

Qwen3.8-27B-FP8 has 16 attention layers and 48 linear-attention (gated delta
net) layers. The host dialog sized a copy as if all 64 kept a KV cache, left
the recurrent state out, and left vLLM's ``max_num_seqs`` at 256. vLLM keeps a
state block per sequence and refused: "max_num_seqs (256) exceeds available
Mamba cache blocks (148)" -- on the DGX pair, 11 October 2026.
"""

from __future__ import annotations

from faden_backend.services.marketplace import facts, fit, settings

GB = 10**9
GIB = 1024**3

#: The parts of Qwen3.8-27B-FP8's config.json the estimate reads (its text_config).
QWEN38_27B = {
    "model_type": "qwen3_5",
    "text_config": {
        "model_type": "qwen3_5_text",
        "num_hidden_layers": 64,
        "num_attention_heads": 24,
        "num_key_value_heads": 4,
        "head_dim": 256,
        "hidden_size": 5120,
        "max_position_embeddings": 262144,
        "layer_types": (["linear_attention"] * 3 + ["full_attention"]) * 16,
        "linear_conv_kernel_dim": 4,
        "linear_key_head_dim": 128,
        "linear_num_key_heads": 16,
        "linear_num_value_heads": 48,
        "linear_value_head_dim": 128,
        "mamba_ssm_dtype": "float32",
    },
}


def _gb10() -> fit.ClusterHardware:
    return fit.ClusterHardware(
        "env", "dgx-pair", "ready",
        machines=[fit.Machine(f"n{i}", f"spark-{i}", [fit.Gpu("NVIDIA GB10", 131 * GB, 131 * GB)]) for i in range(2)],
    )


def test_the_27b_is_read_as_hybrid() -> None:
    arch = facts.architecture_from_config(QWEN38_27B)
    assert (arch.attention_layers, arch.recurrent_layers) == (16, 48)
    # Keys and values of the 16 attention layers only: a quarter of what all 64 would take.
    assert arch.kv_bytes_per_token() == 2 * 16 * 4 * 256 * 2
    # Per linear layer: 48 value heads x 128 x 128 float32, and a 3-step window over 10,240 channels in bf16.
    assert arch.state_bytes_per_sequence == 48 * (48 * 128 * 128 * 4 + (2 * 16 * 128 + 48 * 128) * 3 * 2)


def test_every_fourth_layer_attention_counts_as_hybrid() -> None:
    arch = facts.architecture_from_config({"num_hidden_layers": 48, "full_attention_interval": 4,
                                           "num_key_value_heads": 2, "head_dim": 256})
    assert (arch.attention_layers, arch.recurrent_layers) == (12, 36)


def test_sliding_window_layers_are_attention() -> None:
    arch = facts.architecture_from_config({
        "num_hidden_layers": 4, "num_key_value_heads": 8, "head_dim": 128,
        "layer_types": ["sliding_attention", "sliding_attention", "sliding_attention", "full_attention"],
    })
    assert arch.recurrent_layers is None
    assert arch.kv_bytes_per_token() == 2 * 4 * 8 * 128 * 2


def _needs(arch: facts.Architecture) -> fit.ModelNeeds:
    return fit.ModelNeeds(
        weights_bytes=int(30.9 * GB), kv_bytes_per_token=arch.kv_bytes_per_token(), max_context=arch.max_context,
        attention_heads=arch.num_attention_heads, kv_heads=arch.num_kv_heads,
        recurrent=bool(arch.recurrent_layers), state_bytes_per_sequence=arch.state_bytes_per_sequence,
    )


def test_the_plan_holds_the_state_of_the_sequences_it_suggests() -> None:
    arch = facts.architecture_from_config(QWEN38_27B)
    plan = fit.plan(_needs(arch), _gb10())

    assert plan.status == "fits"
    assert plan.max_num_seqs == fit.RECURRENT_SEQUENCES
    assert plan.max_num_seqs <= 148  # what vLLM had room for on a 35 % share
    without_state = int(30.9 * GB) + arch.kv_bytes_per_token() * plan.context + fit.OVERHEAD_BYTES
    assert plan.needed_bytes_per_gpu - without_state >= plan.max_num_seqs * arch.state_bytes_per_sequence - 1
    # The share leaves room for those states on top of the weights and the context cache.
    assert plan.suggested_gpu_memory_utilization * 131 * GB >= plan.needed_bytes_per_gpu


def test_a_model_of_attention_layers_gets_no_sequence_limit() -> None:
    plain = fit.ModelNeeds(weights_bytes=16 * GB, kv_bytes_per_token=128 * 1024, max_context=32768)
    assert fit.plan(plain, _gb10()).max_num_seqs is None


def test_the_suggestion_sets_max_num_seqs_for_a_hybrid_model() -> None:
    arch = facts.architecture_from_config(QWEN38_27B)
    plan = fit.plan(_needs(arch), _gb10())
    suggestion = settings.suggest(
        {"repo_id": "Qwen/Qwen3.8-27B-FP8", "capabilities": ["tools"], "max_context": arch.max_context}, plan,
    )
    assert suggestion["config"]["max_num_seqs"] == fit.RECURRENT_SEQUENCES
    assert suggestion["reasons"]["max_num_seqs"] == "recurrent_state"
    assert "gpu_memory_utilization" in suggestion["config"]
