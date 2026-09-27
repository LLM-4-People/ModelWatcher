"""Test: the tiktoken encoder is acquired lazily, off the event loop, with a fallback.

Catches finding F2: backend/streaming.py called tiktoken.get_encoding() at import,
which downloads the encoding, so the server could not start without outbound
access to openaipublic.blob.core.windows.net (even with MW_DISABLE_TESTS=1).
"""
import logging
import threading
import time

import pytest

import backend.state as st
import backend.streaming as streaming

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
