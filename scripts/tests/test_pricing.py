"""Unit tests for extract_model_info() pricing rules in backend/model_info.py.

Covers pricing normalization across provider formats (per-token, per-million,
cents-per-million) and priority resolution when multiple pricing sources present.
"""
import pytest

from backend.model_info import extract_model_info


@pytest.mark.parametrize(
    "flat_input, expected",
    [
        # Lilac-style: per-token STRINGS, NO pricing.unit
        (
            {
                "pricing.prompt": "0.0000007",
                "pricing.completion": "0.0000035",
                "pricing.input_cache_read": "0.0000002",
            },
            {"input_price": 0.7, "output_price": 3.5, "cache_price": 0.2, "supports_cache": 1},
        ),
        # NanoGPT: per-token FLOATS + pricing.unit = per_million_tokens -> undo
        (
            {
                "pricing.prompt": 0.5,
                "pricing.completion": 2.6,
                "pricing.cacheReadInputPer1kTokens": 0.000125,
                "pricing.unit": "per_million_tokens",
            },
            {"input_price": 0.5, "output_price": 2.6, "cache_price": 0.125, "supports_cache": 1},
        ),
        # NeuralWatt: per-million FLOATS, no pricing.unit needed
        (
            {
                "metadata.pricing.input_per_million": 0.29,
                "metadata.pricing.output_per_million": 1.15,
                "metadata.pricing.cached_input_per_million": 0.05,
            },
            {"input_price": 0.29, "output_price": 1.15, "cache_price": 0.05, "supports_cache": 1},
        ),
        # Wafer: cents-per-million
        (
            {
                "wafer.pricing.input_cents_per_million": 150,
                "wafer.pricing.output_cents_per_million": 450,
                "wafer.pricing.cache_read_cents_per_million": 15,
            },
            {"input_price": 1.5, "output_price": 4.5, "cache_price": 0.15, "supports_cache": 1},
        ),
        # Price int rounding: 1.0 -> 1 (int)
        ({"metadata.pricing.input_per_million": 1.0}, {"input_price": 1}),
        # Price decimal rounding: 0.29 stays float
        ({"metadata.pricing.input_per_million": 0.29}, {"input_price": 0.29}),
        # Priority: metadata.pricing.input_per_million (p1) > pricing.prompt (p0) when both present
        (
            {"pricing.prompt": "0.0000005", "metadata.pricing.input_per_million": 0.29},
            {"input_price": 0.29},
        ),
        # Combined: metadata pricing wins over wafer pricing when both present
        (
            {
                "metadata.pricing.input_per_million": 0.29,
                "wafer.pricing.input_cents_per_million": 150,
            },
            {"input_price": 0.29},
        ),
    ],
    ids=[
        "lilac-per-token-strings-no-unit",
        "nanogpt-per-token-floats-with-unit-undo",
        "neuralwatt-per-million-direct",
        "wafer-cents-per-million",
        "price-int-rounding",
        "price-decimal-rounding",
        "pricing-priority-metadata-over-pricing",
        "pricing-priority-metadata-over-wafer",
    ],
)
def test_pricing_extraction(flat_input, expected):
    result = extract_model_info(flat_input)
    for k, v in expected.items():
        assert k in result, f"expected key {k!r} missing; got {result!r}"
        assert result[k] == v, f"key {k!r}: expected {v!r}, got {result[k]!r}"
