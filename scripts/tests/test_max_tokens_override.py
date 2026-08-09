"""Test: per-model/per-provider request_options.max_tokens override applies
to health, benchmark, AND probe requests.

Root cause: reasoning models (e.g. NanoGPT's meta/muse-spark-1.2-contributor)
need a larger output budget than the default health max_tokens (10) to emit an
answer after their reasoning prefix. With a tiny ceiling the gateway's
empty_response error is returned (very low max_tokens). The fix lets a model
config declare request_options.max_tokens, honored by _build_stream_request()
(streaming.py) and the probe runner (probe.py). Other models keep the
per-test-type defaults.
"""
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[2] / "backend"


def test_streaming_honors_request_options_max_tokens():
    """_build_stream_request must read req_opts.max_tokens and use it as the
    token ceiling when set (a positive value wins over defaults)."""
    src = (BACKEND / "streaming.py").read_text()
    assert "req_max_tokens = req_opts.get(\"max_tokens\")" in src, \
        "_build_stream_request must read the max_tokens override from request_options"
    # Both health and benchmark branches must honor it
    lines = src.splitlines()
    hits = [ln for ln in lines if "req_max_tokens if req_max_tokens else" in ln]
    assert len(hits) >= 3, (
        "max_tokens override must be honored in the health, anthropic-thinking, "
        "and default (openai benchmark) branches - found: %d" % len(hits)
    )


def test_probe_honors_request_options_max_tokens():
    """probe.run_probe_test must resolve max_tokens via _resolve_max_tokens so
    reasoning models with an override get a sufficient budget."""
    src = (BACKEND / "probe.py").read_text()
    assert "def _resolve_max_tokens" in src, \
        "probe.py must define _resolve_max_tokens"
    assert "_resolve_max_tokens(provider_cfg, st.c.probe_max_tokens)" in src, \
        "run_probe_test must call _resolve_max_tokens with the probe default"


def test_defaults_preserved_for_models_without_override():
    """Models WITHOUT a max_tokens override must keep the per-test-type
    defaults (health_max_tokens for health, benchmark_target_tokens for
    benchmark, probe_max_tokens for probes)."""
    streaming = (BACKEND / "streaming.py").read_text()
    # Health default preserved
    assert "c.health_max_tokens" in streaming, \
        "health default (c.health_max_tokens) must remain the fallback"
    # Benchmark default preserved (non-anthropic path)
    assert "c.benchmark_target_tokens" in streaming, \
        "benchmark default (c.benchmark_target_tokens) must remain the fallback"
    probe = (BACKEND / "probe.py").read_text()
    assert "st.c.probe_max_tokens" in probe, \
        "probe default (st.c.probe_max_tokens) must remain the fallback"
