"""Test: the tiktoken encoder is acquired lazily, off the event loop, with a fallback.

Catches finding F2: backend/streaming.py called tiktoken.get_encoding() at import,
which downloads the encoding, so the server could not start without outbound
access to openaipublic.blob.core.windows.net (even with MW_DISABLE_TESTS=1).
And F41: without the encoder, "effective" (per-token) ITL silently fell back to raw
per-chunk ITL, so batching providers looked several times slower and could trip
critical-tier degradation.
"""
import logging
import threading
import time

import pytest
import yaml

import backend.state as st
import backend.streaming as streaming
from backend.stats import find_critical_metrics

ENCODING = "test-encoding"
RETRY_S = 3600
JOIN_TIMEOUT_S = 10


class _FakeEncoder:
    """Counts one token per whitespace-separated word."""

    def encode_ordinary(self, text):
        return text.split()


@pytest.fixture
def encoder_env(monkeypatch):
    """Fresh encoder registry and config; returns a list recording get_encoding calls."""
    monkeypatch.setattr(st.c, "benchmark_token_encoding", ENCODING, raising=False)
    monkeypatch.setattr(st.c, "benchmark_token_encoding_retry", RETRY_S, raising=False)
    monkeypatch.setattr(streaming, "_encoders", {})
    monkeypatch.setattr(streaming, "_encoder_loading", set())
    monkeypatch.setattr(streaming, "_encoder_last_attempt", {})
    return []


def _join_loader():
    for thread in threading.enumerate():
        if thread.name == f"token-encoder-{ENCODING}":
            thread.join(JOIN_TIMEOUT_S)
            assert not thread.is_alive(), "encoder loader thread did not finish"


def _counts(tokens, answer_texts=None):
    return streaming._validate_token_counts(
        "P::m", False, 4000, tokens, answer_texts if answer_texts is not None else tokens, [],
        len(tokens), 0, None, None,
    )


def test_import_does_not_load_an_encoding(run_python):
    code = (
        "import tiktoken\n"
        "def _fail(name): raise RuntimeError('get_encoding called during import')\n"
        "tiktoken.get_encoding = _fail\n"
        "import backend.streaming\n"
    )
    run_python("-c", code)


def test_get_encoder_never_blocks_and_loads_once(encoder_env, monkeypatch):
    release = threading.Event()

    def slow_get_encoding(name):
        encoder_env.append(name)
        release.wait(JOIN_TIMEOUT_S)
        return _FakeEncoder()

    monkeypatch.setattr(streaming.tiktoken, "get_encoding", slow_get_encoding)
    started = time.monotonic()
    assert streaming.get_encoder() is None
    assert streaming.get_encoder() is None
    assert time.monotonic() - started < 1, "get_encoder blocked on the download"
    release.set()
    _join_loader()
    assert encoder_env == [ENCODING]
    assert isinstance(streaming.get_encoder(), _FakeEncoder)


def test_failed_load_logs_once_and_retries_after_interval(encoder_env, monkeypatch, caplog):
    def failing_get_encoding(name):
        encoder_env.append(name)
        raise ConnectionError("no route to host")

    monkeypatch.setattr(streaming.tiktoken, "get_encoding", failing_get_encoding)
    with caplog.at_level(logging.ERROR, logger=st.log.name):
        assert streaming.get_encoder() is None
        _join_loader()
        assert streaming.get_encoder() is None
        _join_loader()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and ENCODING in errors[0].getMessage()
    assert encoder_env == [ENCODING], "retried before the retry interval elapsed"

    streaming._encoder_last_attempt[ENCODING] -= RETRY_S
    streaming.get_encoder()
    _join_loader()
    assert encoder_env == [ENCODING, ENCODING]


def test_counts_fall_back_to_chunks_without_encoder(encoder_env):
    # A recent attempt keeps get_encoder from starting a loader thread
    streaming._encoder_last_attempt[ENCODING] = time.monotonic()
    tokens = ["one two", "three four five", "six"]
    tc = _counts(tokens)
    assert tc.per_chunk_tokens == []
    assert tc.tiktoken_total == 0
    assert tc.chunk_token_ratio is None
    assert tc.token_count_for_tps == len(tokens)
    assert tc.answer_token_estimate == len(tokens)


def test_counts_use_encoder_when_loaded(encoder_env, monkeypatch):
    monkeypatch.setattr(st.c, "batching_log_threshold", 100, raising=False)
    streaming._encoders[ENCODING] = _FakeEncoder()
    tc = _counts(["one two", "three four five", "six"])
    assert tc.per_chunk_tokens == [2, 3, 1]
    assert tc.tiktoken_total == 6
    assert tc.token_count_for_tps == 6
    assert tc.answer_token_estimate == 6


# A batching provider: 4 tokens per chunk, one chunk every 80 ms, so 20 ms per token
BATCHED_CHUNKS = ["w w w w"] * 12
CHUNK_TIMES = [n * 0.08 for n in range(len(BATCHED_CHUNKS))]


@pytest.fixture
def itl_config(monkeypatch):
    app = yaml.safe_load((st.CONFIG_DIR / "app.yaml.example").read_text())
    stalls = app["stalls"]
    for name, value in {"stall_visible_ms": stalls["visible_threshold_ms"], "stall_hiccup_ms": stalls["hiccup_threshold_ms"],
                        "hiccup_multiplier": stalls["hiccup_multiplier"], "batching_log_threshold": stalls["batching_log_threshold"],
                        "color_thresholds": app["color_thresholds"]}.items():
        monkeypatch.setattr(st.c, name, value, raising=False)


def _itl(tc):
    return streaming._compute_itl_statistics("P::m", CHUNK_TIMES, tc, st.TEST_BENCHMARK, None, None, len(CHUNK_TIMES))


def test_effective_itl_is_per_token_with_the_encoder(encoder_env, itl_config):
    streaming._encoders[ENCODING] = _FakeEncoder()
    itl = _itl(_counts(BATCHED_CHUNKS))
    assert itl.raw_median_itl_ms == 80.0
    assert itl.effective_median_itl_ms == 20.0


def test_effective_itl_is_unmeasured_without_the_encoder(encoder_env, itl_config):
    streaming._encoder_last_attempt[ENCODING] = time.monotonic()
    itl = _itl(_counts(BATCHED_CHUNKS))
    assert itl.raw_median_itl_ms == 80.0, "raw ITL does not depend on the encoder"
    effective = (itl.effective_median_itl_ms, itl.effective_avg_itl_ms, itl.effective_p99_itl_ms, itl.effective_itl_tail_ratio)
    assert effective == (None, None, None, None), "per-chunk ITL must not pass for per-token ITL"
    result = {"success": True, "effective_itl_tail_ratio": itl.effective_itl_tail_ratio}
    assert "effective_itl_tail_ratio" not in find_critical_metrics(result)
