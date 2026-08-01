"""Unit tests for extract_model_info() rules in backend/model_info.py.

Covers context window, output context, tool/vision/structured-output capability
detection, thinking/reasoning, modalities, suffix rules (Ollama), owned_by routing,
cache inference, and two combined real-world model fixtures.
"""
import pytest

from backend.model_info import extract_model_info


CASES = [
    # metadata.limits.max_output_tokens -> output_context
    ({"metadata.limits.max_output_tokens": 131072}, {"output_context": 131072}),
    # metadata.pricing.cached_input_per_million -> cache_price
    (
        {
            "metadata.pricing.input_per_million": 0.29,
            "metadata.pricing.cached_input_per_million": 0.05,
            "metadata.pricing.output_per_million": 1.15,
        },
        {"input_price": 0.29, "cache_price": 0.05, "output_price": 1.15},
    ),
    # capabilities.parallel_tool_calls -> supports_tools
    ({"capabilities.parallel_tool_calls": True}, {"supports_tools": True}),
    # parallel_tool_calls False should NOT set supports_tools=False
    ({"capabilities.parallel_tool_calls": False}, {}),
    # architecture.modality priority > architecture.output_modalities
    (
        {"architecture.modality": "text+image->text", "architecture.output_modalities": ["text"]},
        {"modalities": "text+image->text"},
    ),
    # output_modalities when no modality
    ({"architecture.output_modalities": ["text"]}, {"modalities": "text"}),
    # metadata.capabilities.reasoning_effort -> thinking
    ({"metadata.capabilities.reasoning_effort": True}, {"thinking": "effort"}),
    # reasoning_effort False -> None (skip)
    ({"metadata.capabilities.reasoning_effort": False}, {}),
    # Suffix rule: max_context_length
    ({"some_provider.max_context_length": 100000}, {"context_window": 100000}),
    # Suffix rule: max_output_tokens
    ({"some_provider.max_output_tokens": 65536}, {"output_context": 65536}),
    # Existing rule: context_length
    ({"context_length": 8192}, {"context_window": 8192}),
    # pricing.prompt per-token
    (
        {"pricing.prompt": "0.0000005", "pricing.completion": "0.0000015"},
        {"input_price": 0.5, "output_price": 1.5},
    ),
    # NanoGPT pricing.unit undo
    (
        {"pricing.prompt": 0.5, "pricing.completion": 2.6, "pricing.unit": "per_million_tokens"},
        {"input_price": 0.5, "output_price": 2.6},
    ),
    # Wafer cents pricing
    (
        {"wafer.pricing.input_cents_per_million": 150, "wafer.pricing.output_cents_per_million": 450},
        {"input_price": 1.5, "output_price": 4.5},
    ),
    # NeuralWatt per_million pricing
    (
        {"metadata.pricing.input_per_million": 0.29, "metadata.pricing.output_per_million": 1.15},
        {"input_price": 0.29, "output_price": 1.15},
    ),
    # owned_by -> served_by (vllm)
    ({"owned_by": "vllm"}, {"served_by": "vllm"}),
    # owned_by -> owner (non-served)
    ({"owned_by": "deepseek"}, {"owner": "deepseek"}),
    # capabilities list -> supports_vision + supports_tools + thinking
    (
        {"capabilities": ["vision", "tools", "thinking", "completion"]},
        {"supports_vision": True, "supports_tools": True, "thinking": "enabled"},
    ),
    # Ollama capabilities list
    (
        {"capabilities": ["vision", "thinking", "completion", "tools"]},
        {"supports_vision": True, "supports_tools": True, "thinking": "enabled"},
    ),
    # input_modalities contains image
    ({"architecture.input_modalities": ["text", "image"]}, {"supports_vision": True}),
    # input_modalities no image
    ({"architecture.input_modalities": ["text"]}, {}),
    # NanoGPT cache pricing
    (
        {
            "pricing.cacheReadInputPer1kTokens": 0.000125,
            "pricing.prompt": 0.5,
            "pricing.unit": "per_million_tokens",
        },
        {"cache_price": 0.125, "input_price": 0.5, "supports_cache": 1},
    ),
    # Pricing rounding: whole number
    ({"metadata.pricing.input_per_million": 1.0}, {"input_price": 1}),
    # Pricing rounding: decimal
    ({"metadata.pricing.input_per_million": 0.29}, {"input_price": 0.29}),
    # Ollama suffix context_length
    ({"model_info.kimi-k2.context_length": 262144}, {"context_window": 262144}),
    # Ollama suffix general.architecture
    ({"model_info.general.architecture": "kimi-k2"}, {"architecture": "kimi-k2"}),
    # Ollama suffix general.parameter_count
    ({"model_info.general.parameter_count": 1042000000000}, {"param_count": "1.0T"}),
    # cache_price -> supports_cache
    (
        {"metadata.pricing.cached_input_per_million": 0.05},
        {"cache_price": 0.05, "supports_cache": 1},
    ),
    # metadata.huggingface_id (skipped from output)
    ({"metadata.huggingface_id": "Qwen/Qwen3-35B"}, {}),
    # pricing.currency (skipped from output)
    ({"pricing.currency": "USD"}, {}),
    # Combined NeuralWatt model
    (
        {
            "max_model_len": 131056,
            "metadata.capabilities.reasoning": True,
            "metadata.capabilities.tools": True,
            "metadata.capabilities.vision": True,
            "metadata.description": "Qwen3.6 35B MoE",
            "metadata.display_name": "Qwen3.6 35B",
            "metadata.pricing.input_per_million": 0.29,
            "metadata.pricing.output_per_million": 1.15,
            "metadata.provider": "Qwen",
            "owned_by": "vllm",
            "created": 1779575336,
        },
        {
            "context_window": 131056,
            "thinking": "enabled",
            "supports_tools": True,
            "supports_vision": True,
            "description": "Qwen3.6 35B MoE",
            "display_name": "Qwen3.6 35B",
            "input_price": 0.29,
            "output_price": 1.15,
            "owner": "Qwen",
            "served_by": "vllm",
            "created": 1779575336.0,
        },
    ),
    # Combined Lilac model
    (
        {
            "architecture.input_modalities": ["text", "image"],
            "architecture.modality": "text+image->text",
            "architecture.output_modalities": ["text"],
            "architecture.tokenizer": "Kimi",
            "context_length": 262144,
            "description": "Kimi K2.6 is great",
            "display_name": "Kimi K2.6",
            "pricing.prompt": "0.0000007",
            "pricing.completion": "0.0000035",
            "pricing.input_cache_read": "0.0000002",
            "top_provider.max_completion_tokens": 262144,
            "owned_by": "moonshotai",
            "supported_parameters": ["tools", "stream"],
        },
        {
            "supports_vision": True,
            "modalities": "text+image->text",
            "tokenizer": "Kimi",
            "context_window": 262144,
            "description": "Kimi K2.6 is great",
            "display_name": "Kimi K2.6",
            "input_price": 0.7,
            "output_price": 3.5,
            "cache_price": 0.2,
            "output_context": 262144,
            "supports_tools": True,
            "supports_cache": 1,
            "owner": "moonshotai",
        },
    ),
]

_IDS = [
    "nw-max-output-tokens",
    "nw-cached-input-per-million",
    "parallel-tool-calls-true",
    "parallel-tool-calls-false",
    "modality-priority-over-output-modalities",
    "output-modalities-no-modality",
    "reasoning-effort-true",
    "reasoning-effort-false",
    "suffix-max-context-length",
    "suffix-max-output-tokens",
    "context-length",
    "pricing-prompt-per-token",
    "nanogpt-pricing-unit-undo",
    "wafer-cents-pricing",
    "neuralwatt-per-million-pricing",
    "owned-by-vllm-served-by",
    "owned-by-deepseek-owner",
    "capabilities-list-vision-tools-thinking",
    "ollama-capabilities-list",
    "input-modalities-contains-image",
    "input-modalities-no-image",
    "nanogpt-cache-pricing",
    "price-rounding-whole",
    "price-rounding-decimal",
    "ollama-suffix-context-length",
    "ollama-suffix-general-architecture",
    "ollama-suffix-general-parameter-count",
    "cache-price-infers-supports-cache",
    "metadata-huggingface-id-skipped",
    "pricing-currency-skipped",
    "neuralwatt-qwen3.6-combined",
    "lilac-kimi-k2.6-combined",
]


@pytest.mark.parametrize("flat_input, expected", CASES, ids=_IDS)
def test_extract_rules(flat_input, expected):
    result = extract_model_info(flat_input)
    for k, v in expected.items():
        assert k in result, f"expected key {k!r} missing; got {result!r}"
        assert result[k] == v, f"key {k!r}: expected {v!r}, got {result[k]!r}"
