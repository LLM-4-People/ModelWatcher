"""SSE parsing, provider-specific parsers, streaming test execution, and metrics computation.

Two provider paths: Anthropic (x-api-key, /messages, content_block events) and
OpenAI-compatible (Bearer auth, /chat/completions, choices[0].delta). Detection
is based on "anthropic" in the API URL. Token counting uses tiktoken (o200k_base)
to cross-validate provider-reported completion_tokens, which are frequently
unreliable. ITL metrics divide per-chunk gaps by per-chunk token counts to
normalize for provider batching.
"""

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field

import httpx
import tiktoken

from backend.state import c, log, THINK_END, TEST_HEALTH, TEST_BENCHMARK, update_provider_rtt, get_provider_jitter
import backend.state as st
from backend.security import scrub_pii, format_api_error, extract_stream_error, safe_internal_error, is_internal_error
from backend.stats import compute_stall_metrics, empty_stall_metrics, compute_consistency_score, compute_speed_score, percentile

_enc = tiktoken.get_encoding("o200k_base")


def _count_tokens(text: str) -> int:
    return len(_enc.encode_ordinary(text))


# ── SSE event iterator ───────────────────────────────────────────────────────

class StreamStalledError(Exception):
    pass


async def aiter_sse_events(resp, activity_timeout: float = 30, frame_tracker: dict | None = None, deadline: float | None = None):
    """Iterate over SSE events from an httpx streaming response.

    Yields (event_name, data_str) tuples. If frame_tracker is provided (a
    mutable dict), populates it with frame-level batch tracking data for
    computing frame_batch_pct after the stream completes.

    deadline: optional monotonic time at which the total request should time
    out. When reached, processes any remaining buffer and raises
    asyncio.TimeoutError. Takes priority over per-chunk activity_timeout.
    """
    event_name = None
    data_lines = []

    buf = b""
    if frame_tracker is not None:
        frame_tracker.setdefault("tokens_in_multi_event_chunks", 0)
        frame_tracker["_pending_tokens"] = 0
        frame_tracker["_chunk_event_count"] = 0

    ait = resp.aiter_bytes().__aiter__()
    while True:
        # Respect both activity_timeout and total deadline; deadline wins.
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # Total deadline reached - flush remaining buffer before aborting.
                if buf.strip():
                    remaining_text = buf.decode("utf-8", errors="replace")
                    for ev_name, ev_data in _parse_sse_text(remaining_text, event_name, data_lines):
                        if frame_tracker is not None:
                            frame_tracker["_chunk_event_count"] += 1
                        yield (ev_name, ev_data)
                raise asyncio.TimeoutError()
            effective_timeout = min(activity_timeout, remaining)
        else:
            effective_timeout = activity_timeout

        try:
            chunk = await asyncio.wait_for(ait.__anext__(), timeout=effective_timeout)
        except StopAsyncIteration:
            # Flush remaining buffer on stream end.
            if buf.strip():
                remaining = buf.decode("utf-8", errors="replace")
                for ev_name, ev_data in _parse_sse_text(remaining, event_name, data_lines):
                    yield (ev_name, ev_data)
            break
        except asyncio.TimeoutError:
            # Per-chunk activity timeout - flush remaining buffer before stalling.
            if buf.strip():
                remaining_text = buf.decode("utf-8", errors="replace")
                for ev_name, ev_data in _parse_sse_text(remaining_text, event_name, data_lines):
                    if frame_tracker is not None:
                        frame_tracker["_chunk_event_count"] += 1
                    yield (ev_name, ev_data)
            # Re-raise as deadline timeout if the deadline was the trigger.
            if deadline is not None and time.monotonic() >= deadline:
                raise
            raise StreamStalledError(
                f"No data received for {activity_timeout}s - stream stalled"
            )

        buf += chunk
        buf = buf.replace(b"\r\n", b"\n")

        # Track events per aiter_bytes() chunk. Reset both counters per chunk:
        # without resetting _pending_tokens, tokens from single-event chunks
        # would carry over and be incorrectly attributed to the next multi-event chunk.
        if frame_tracker is not None:
            frame_tracker["_chunk_event_count"] = 0
            frame_tracker["_pending_tokens"] = 0

        # Split buffer on \n\n boundaries, keeping the incomplete tail.
        while b"\n\n" in buf:
            event_bytes, buf = buf.split(b"\n\n", 1)
            # Normalize \r\n → \n so _parse_sse_text handles both line endings
            event_text = event_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n")

            for ev_name, ev_data in _parse_sse_text(event_text, None, []):
                if frame_tracker is not None:
                    frame_tracker["_chunk_event_count"] += 1
                yield (ev_name, ev_data)

        # After processing all complete events from this chunk: if >=2 events
        # were found, tokens recorded during them count as batched.
        if frame_tracker is not None and frame_tracker["_chunk_event_count"] >= 2:
            frame_tracker["tokens_in_multi_event_chunks"] += frame_tracker.get("_pending_tokens", 0)
            frame_tracker["_pending_tokens"] = 0


def _parse_sse_text(text: str, event_name_init=None, data_lines_init=None):
    """Parse complete SSE event text (between \\n\\n boundaries) into events.

    Yields (event_name, data_str) tuples for each complete event found.
    Since text is already split on \\n\\n boundaries, each call processes one
    complete event - we always flush at the end.
    """
    event_name = event_name_init
    data_lines = list(data_lines_init) if data_lines_init else []

    for line in text.split("\n"):
        line = line.rstrip("\r")

        if not line:
            # Internal blank line within the block - flush accumulated event
            if data_lines:
                yield (event_name, "\n".join(data_lines))
            event_name = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
            continue

        if line.startswith("data:"):
            data_str = line[len("data:"):].strip()
            if data_str == "[DONE]":
                if data_lines:
                    yield (event_name, "\n".join(data_lines))
                event_name = None
                data_lines = []
                yield ("done", "[DONE]")
                continue
            data_lines.append(data_str)
            continue

        if line.startswith(" ") and data_lines:
            data_lines[-1] += "\n" + line[1:]
            continue

    # Flush any remaining event data (no trailing blank line in this block)
    if data_lines:
        yield (event_name, "\n".join(data_lines))


# ── Think→answer boundary ────────────────────────────────────────────────────

def split_thinking(content: str | None, reasoning: str | None, thinking_ended: bool):
    """Split a streaming delta that may contain the think->answer boundary.

    Some OpenAI-compatible providers (e.g. Qwen) send reasoning and content
    in the same delta field, separated by THINK_END (12 literal newlines - not
    configurable, not a regex). Returns (content, reasoning, thinking_ended)
    with the boundary resolved. Only called for OpenAI providers; Anthropic
    uses explicit content_block_start type transitions.
    """
    if thinking_ended and reasoning is not None and content is None:
        content = reasoning
        reasoning = None

    if reasoning is not None and THINK_END in reasoning:
        before, after = reasoning.split(THINK_END, 1)
        reasoning = before if before else None
        content = after if after else content
        thinking_ended = True

    if content is not None and THINK_END in content and not reasoning:
        before, after = content.split(THINK_END, 1)
        reasoning = before if before else None
        content = after if after else None
        if content is not None and content.strip() == "":
            content = None
        thinking_ended = True

    return content, reasoning, thinking_ended


# ── Provider-specific parsers ────────────────────────────────────────────────

def parse_anthropic_event(event_name, chunk) -> dict:
    """Parse an Anthropic SSE event into a normalized result dict.

    Handles message_start, content_block_start/delta/stop, message_delta,
    message_stop, error, and ping events. Returns a dict with content,
    reasoning, finish_reason, usage_update, and flags for stream end/errors.
    """
    result = {
        "content": None,
        "reasoning": None,
        "finish_reason": None,
        "usage_update": None,
        "is_stream_end": False,
        "is_error": False,
        "block_type_start": None,
        "block_index": None,
        "delta_type": None,
    }

    chunk_type = chunk.get("type", "") if isinstance(chunk, dict) else ""

    if event_name == "error" or chunk_type == "error":
        result["is_error"] = True
        return result

    if event_name == "ping" or chunk_type == "ping":
        return result

    if chunk_type == "message_start":
        msg = chunk.get("message", {})
        if msg.get("usage"):
            result["usage_update"] = ("message_start", msg["usage"])
        return result

    if chunk_type == "content_block_start":
        block = chunk.get("content_block", {})
        idx = chunk.get("index")
        result["block_type_start"] = block.get("type")
        result["block_index"] = idx
        return result

    if chunk_type == "content_block_delta":
        delta = chunk.get("delta", {})
        delta_type = delta.get("type", "")
        result["delta_type"] = delta_type
        result["block_index"] = chunk.get("index")

        if delta_type == "text_delta":
            text = delta.get("text")
            if text is not None:
                result["content"] = text
        elif delta_type == "thinking_delta":
            thinking = delta.get("thinking")
            if thinking is not None:
                result["reasoning"] = thinking
        elif delta_type == "signature_delta":
            pass
        elif delta_type == "input_json_delta":
            pass
        return result

    if chunk_type == "content_block_stop":
        return result

    if chunk_type == "message_delta":
        delta = chunk.get("delta", {})
        if delta.get("stop_reason"):
            result["finish_reason"] = delta["stop_reason"]
        if chunk.get("usage"):
            result["usage_update"] = ("message_delta", chunk["usage"])
        return result

    if chunk_type == "message_stop":
        result["is_stream_end"] = True
        return result

    return result


def parse_openai_chunk(chunk) -> dict:
    """Parse an OpenAI-compatible SSE chunk into a normalized result dict.

    Extracts content, reasoning (from reasoning_content/thinking_content/
    reasoning fields), finish_reason, and usage from choices[0].delta.
    Handles both inline usage (with choices) and final usage (without).
    """
    result = {
        "content": None,
        "reasoning": None,
        "finish_reason": None,
        "usage_update": None,
        "is_stream_end": False,
        "is_error": False,
    }

    if not isinstance(chunk, dict):
        return result

    if "error" in chunk and chunk["error"]:
        result["is_error"] = True
        return result

    if "choices" in chunk and chunk["choices"]:
        choice = chunk["choices"][0]
        delta = choice.get("delta", {})
        content = delta.get("content")
        if content is not None:
            result["content"] = content
        reasoning = (
            delta.get("reasoning_content")
            or delta.get("thinking_content")
            or delta.get("reasoning")
        )
        if reasoning is not None:
            result["reasoning"] = reasoning
        fr = choice.get("finish_reason")
        if fr:
            result["finish_reason"] = fr
    elif "usage" in chunk:
        pass

    if "usage" in chunk and chunk["usage"]:
        has_choices = bool(chunk.get("choices"))
        result["usage_update"] = ("final_usage" if not has_choices else "inline_usage", chunk["usage"])

    return result


# ── Result dict factory ──────────────────────────────────────────────────────

def make_result(**overrides) -> dict:
    """Build a result dict with sensible defaults, overridden by keyword args.

    Single factory for all test result dicts - ensures every field exists
    so downstream code can safely .get() any key without KeyError checks.
    """
    base = {
        "success": False,
        "error": None,
        "error_trace": None,
        "ttft_ms": None,
        "tps": None,
        "tpot_ms": None,
        "total_latency_ms": None,
        "token_count": None,
        "completion_tokens": None,
        "reasoning_tokens": None,
        "chunk_token_ratio": None,
        "chunk_token_cv": None,
        "chunk_token_max": None,
        "finish_reason": None,
        "stall_count": None,
        "hiccup_count": None,
        "raw_max_itl_ms": None,
        "raw_median_itl_ms": None,
        "raw_avg_itl_ms": None,
        "raw_p99_itl_ms": None,
        "effective_median_itl_ms": None,
        "effective_avg_itl_ms": None,
        "effective_p99_itl_ms": None,
        "effective_itl_tail_ratio": None,
        "effective_itl_tail_ratio_estimated": False,
        "network_rtt_ms": None,
        "network_jitter_ms": None,
        "burst_arrivals": None,
        "burst_arrival_pct": None,
        "shrinkage_factor": None,
        "thinking_duration_ms": None,
        "itl_reliable": None,
        "degraded": False,
        "degraded_reason": None,
        "test_type": TEST_BENCHMARK,
        "request_id": None,
        "consistency_score": None,
        "speed_score": None,
        "stall_first_pct": None,
        "stall_last_pct": None,
        "stall_clusters": 0,
        "stall_ratio": None,
    }
    base.update(overrides)
    return base


# Fields kept for health check results (everything else is deleted by
# strip_health_metrics - health checks with max_tokens=10 produce
# unreliable TPS, ITL, stall, and batching metrics).
_HEALTH_KEEP = frozenset({
    "success", "error", "error_trace", "ttft_ms",
    "token_count", "completion_tokens", "total_latency_ms", "network_rtt_ms",
    "degraded", "degraded_reason", "test_type", "finish_reason",
    "network_jitter_ms", "shrinkage_factor",
    "consistency_score", "speed_score", "request_id",
})


def strip_health_metrics(record: dict) -> dict:
    """Delete metrics that aren't meaningful for health checks.

    Uses del (not None assignment) for consistency with strip_internal() -
    deleted keys go into SQLite as NULL and are stripped at the API boundary.
    """
    for key in list(record.keys()):
        if key.startswith("_") or key in _HEALTH_KEEP:
            continue
        del record[key]
    return record


# Token validation thresholds: >20 tok/chunk is implausible unless tiktoken
# confirms heavy batching (>=3 tok/chunk). Each guard exists because a real
# provider exhibited that behavior - don't simplify them.
_IMPLAUSIBLE_RATIO = 20
_BATCHING_CONFIRM_MIN = 3




# ── Token counting ───────────────────────────────────────────────────────────

def extract_completion_tokens(usage_info: dict | None, is_anthropic: bool, max_tokens: int | None = None) -> int:
    """Extract and validate completion token count from provider usage info.

    Provider-reported completion_tokens are frequently unreliable. Rejects
    None, <=0, and values exceeding max_tokens * 1.2 (implausible). Returns
    None for rejected values so callers fall back to tiktoken/chunk count.
    """
    if max_tokens is None:
        max_tokens = c.benchmark_target_tokens
    if not usage_info:
        return None
    raw = None
    if is_anthropic:
        raw = usage_info.get("output_tokens")
    else:
        raw = usage_info.get("completion_tokens")
    if raw is None:
        return None
    if raw <= 0:
        return None
    if max_tokens > 0 and raw > max_tokens * 1.2:
        return None
    return raw


# ── Network RTT ──────────────────────────────────────────────────────────────

def calc_network_rtt_ms(
    connect_start: float | None,
    connect_end: float | None,
    tls_start: float | None,
    tls_end: float | None,
) -> float | None:
    """Compute network RTT from TCP connect + TLS handshake times.

    Returns TCP+TLS when both available, otherwise whichever is present
    (partial-data fallback). None when neither is available.
    """
    connect_ms = round((connect_end - connect_start) * 1000, 1) if connect_start and connect_end else None
    tls_ms = round((tls_end - tls_start) * 1000, 1) if tls_start and tls_end else None
    if connect_ms is not None and tls_ms is not None:
        return round(connect_ms + tls_ms, 1)
    if connect_ms is not None:
        return connect_ms
    if tls_ms is not None:
        return tls_ms
    return None


# ── Metrics computation: sub-functions ────────────────────────────────────────

@dataclass
class _TokenCounts:
    completion_tokens: int | None
    per_chunk_tokens: list[int]
    tiktoken_total: int
    answer_token_estimate: int
    reasoning_token_estimate: int
    chunk_token_ratio: float | None
    chunk_token_cv: float | None
    chunk_token_max: int | None
    token_count_for_tps: int


def _validate_token_counts(
    model_label: str,
    is_anthropic: bool,
    api_token_limit: int,
    tokens: list[str],
    answer_texts: list[str] | None,
    reasoning_texts: list[str] | None,
    answer_token_count: int,
    reasoning_token_count_observed: int,
    usage_info: dict | None,
    usage_source: str | None,
) -> _TokenCounts:
    completion_tokens = extract_completion_tokens(usage_info, is_anthropic, api_token_limit)
    chunk_count = len(tokens)

    # Anthropic message_start usage is often incomplete (output_tokens <= 2) when
    # no message_delta follows - fall back to chunk count.
    if is_anthropic and usage_source == "message_start" and completion_tokens is not None and completion_tokens <= 2:
        log.info(
            "%s: only message_start usage available (output_tokens=%d) - likely incomplete, "
            "falling back to chunk_count=%d",
            model_label, completion_tokens, chunk_count,
        )
        completion_tokens = None

    # extract_completion_tokens guarantees >0 for non-None values, so no <=0 check needed here.
    per_chunk_tokens = [_count_tokens(t) for t in tokens] if tokens else []
    tiktoken_total = sum(per_chunk_tokens) if per_chunk_tokens else 0
    answer_token_estimate = sum(_count_tokens(t) for t in answer_texts) if answer_texts is not None else answer_token_count
    reasoning_token_estimate = sum(_count_tokens(t) for t in reasoning_texts) if reasoning_texts is not None else reasoning_token_count_observed

    avg_tok_per_chunk = tiktoken_total / chunk_count if tiktoken_total > 0 and chunk_count else 0.0

    if completion_tokens is not None and chunk_count > 0:
        ratio = completion_tokens / chunk_count
        if ratio > _IMPLAUSIBLE_RATIO:
            if avg_tok_per_chunk >= _BATCHING_CONFIRM_MIN:
                log.info(
                    "%s: completion_tokens=%d vs chunk_count=%d (ratio=%.1f) - "
                    "heavy batching confirmed by tiktoken (%.1f tok/chunk)",
                    model_label, completion_tokens, chunk_count, ratio, avg_tok_per_chunk,
                )
            else:
                log.warning(
                    "%s: completion_tokens=%d vs chunk_count=%d (ratio=%.1f) - "
                    "implausibly high, tiktoken (%s) does not confirm, falling back to chunk_count",
                    model_label, completion_tokens, chunk_count, ratio,
                    "%.1f tok/chunk" % avg_tok_per_chunk if tiktoken_total else "N/A",
                )
                completion_tokens = None

    chunk_token_ratio = round(avg_tok_per_chunk, 2) if avg_tok_per_chunk > 0 else None
    if chunk_token_ratio and chunk_token_ratio > c.batching_log_threshold:
        log.debug(
            "%s: provider batches %.1f tok/chunk (chunks=%d, tiktoken_total=%d) - "
            "ITL metrics adjusted per-chunk",
            model_label, chunk_token_ratio, chunk_count, tiktoken_total,
        )

    chunk_token_cv = None
    chunk_token_max = max(per_chunk_tokens) if per_chunk_tokens else None
    if len(per_chunk_tokens) >= 2 and avg_tok_per_chunk > 0:
        _var = sum((n - avg_tok_per_chunk) ** 2 for n in per_chunk_tokens) / len(per_chunk_tokens)
        chunk_token_cv = round((_var ** 0.5) / avg_tok_per_chunk, 3)

    token_count_for_tps = completion_tokens if completion_tokens else tiktoken_total if tiktoken_total else chunk_count

    return _TokenCounts(
        completion_tokens=completion_tokens,
        per_chunk_tokens=per_chunk_tokens,
        tiktoken_total=tiktoken_total,
        answer_token_estimate=answer_token_estimate,
        reasoning_token_estimate=reasoning_token_estimate,
        chunk_token_ratio=chunk_token_ratio,
        chunk_token_cv=chunk_token_cv,
        chunk_token_max=chunk_token_max,
        token_count_for_tps=token_count_for_tps,
    )


@dataclass
class _ITLStats:
    stall_count: int
    hiccup_count: int
    raw_max_itl_ms: float | None
    raw_median_itl_ms: float
    raw_avg_itl_ms: float | None
    raw_p99_itl_ms: float | None
    effective_median_itl_ms: float | None
    effective_avg_itl_ms: float | None
    effective_p99_itl_ms: float | None
    effective_itl_tail_ratio: float | None
    effective_itl_tail_ratio_estimated: bool
    itl_reliable: bool
    burst_arrivals: int
    burst_arrival_pct: float
    shrinkage_factor: float | None
    stall_metrics: dict


def _compute_itl_statistics(
    model_label: str,
    token_times: list[float],
    tc: _TokenCounts,
    test_type: str,
    network_rtt_ms: float | None,
    network_jitter_ms: float | None,
    chunk_count: int,
) -> _ITLStats:
    _is_health = test_type == TEST_HEALTH

    raw_itls = [(token_times[i] - token_times[i - 1]) * 1000 for i in range(1, len(token_times))]

    # Local n_k division: divide each inter-chunk gap by the token count of the
    # SECOND chunk in the pair (the one arriving at the end of the gap). This
    # gives per-token ITL instead of per-chunk ITL - critical when providers
    # batch multiple tokens per SSE event. Without this, batched providers
    # would show artificially inflated ITL/stall counts.
    effective_itls = raw_itls
    if tc.per_chunk_tokens and raw_itls:
        _local_itls = []
        for k in range(len(raw_itls)):
            n_k = tc.per_chunk_tokens[k + 1]
            _local_itls.append(raw_itls[k] / n_k if n_k > 0 else raw_itls[k])
        effective_itls = _local_itls

    # Sub-millisecond ITLs indicate chunks arriving in the same event-loop tick
    # or coalesced by a proxy/CDN. A high burst rate strongly suggests proxy/CDN
    # buffering is distorting timing. Health checks have too few ITLs
    # (max_tokens=10) for burst analysis - skip.
    if _is_health or not raw_itls:
        burst_arrivals = 0
        burst_arrival_pct = 0.0
    else:
        _burst_count = sum(1 for itl in raw_itls if itl < 1.0)
        burst_arrivals = _burst_count
        burst_arrival_pct = round(_burst_count / len(raw_itls) * 100, 1)

    effective_stall_threshold = c.stall_visible_ms
    if network_jitter_ms and network_jitter_ms > 0:
        effective_stall_threshold = c.stall_visible_ms + round(network_jitter_ms)
    elif network_rtt_ms and network_rtt_ms > 0:
        effective_stall_threshold = c.stall_visible_ms + round(network_rtt_ms * 0.5)

    stall_count = 0
    raw_max_itl_ms = 0.0
    _raw_itls_pos: list[float] = []
    if raw_itls:
        for itl in raw_itls:
            if itl > effective_stall_threshold:
                stall_count += 1
            if itl > raw_max_itl_ms:
                raw_max_itl_ms = itl
            if itl > 0:
                _raw_itls_pos.append(itl)

    stall_metrics = compute_stall_metrics(raw_itls, effective_stall_threshold, chunk_count) if not _is_health else empty_stall_metrics()

    _raw_median = 0.0
    if _raw_itls_pos:
        _sraw = sorted(_raw_itls_pos)
        _rmid = len(_sraw) // 2
        _raw_median = _sraw[_rmid] if len(_sraw) % 2 else (_sraw[_rmid - 1] + _sraw[_rmid]) / 2
    # Adaptive hiccup threshold: hiccup_multiplier x raw median (default 3x).
    # Falls back to config stall_hiccup_ms when median is 0 (no ITLs or all zero).
    hiccup_threshold = c.hiccup_multiplier * _raw_median if _raw_median > 0 else c.stall_hiccup_ms
    hiccup_count = sum(1 for itl in _raw_itls_pos if itl > hiccup_threshold)

    # Filter out zero-ms ITLs - artifacts from the async event loop coalescing
    # multiple SSE chunks in one tick (not real latency events). Health checks
    # use 0 as the floor because they produce very few ITLs (max_tokens=10) and
    # sub-ms filtering would discard nearly all of them.
    _filter_min = 0 if _is_health else 1.0
    raw_stat_itls = [itl for itl in raw_itls if itl > _filter_min]
    stat_itls = [itl for itl in effective_itls if itl > _filter_min]

    raw_median_itl_ms = 0.0
    raw_p99_itl_ms = None
    raw_avg_itl_ms = None
    raw_max_itl_ms_out: float | None = None
    effective_itl_tail_ratio: float | None = None
    effective_itl_tail_ratio_estimated = False
    shrinkage_factor: float | None = None
    _raw_p99: float | None = None
    _eff_p99: float | None = None
    _eff_p99_adjusted: float | None = None
    _eff_median: float | None = None
    _eff_avg: float | None = None
    effective_median_itl_ms: float | None = None
    effective_avg_itl_ms: float | None = None
    effective_p99_itl_ms: float | None = None

    if raw_stat_itls:
        raw_avg_itl_ms = round(sum(raw_stat_itls) / len(raw_stat_itls), 2)
        sorted_raw = sorted(raw_stat_itls)
        mid = len(sorted_raw) // 2
        raw_median_itl_ms = sorted_raw[mid] if len(sorted_raw) % 2 else (sorted_raw[mid - 1] + sorted_raw[mid]) / 2

        _raw_p99 = round(percentile(sorted_raw, 99), 1)

        jitter_source = network_jitter_ms
        _raw_shrinkage: float | None = None
        if jitter_source and jitter_source > 0 and len(raw_stat_itls) >= 10 and raw_median_itl_ms > 0:
            var_observed = sum((x - raw_median_itl_ms) ** 2 for x in raw_stat_itls) / len(raw_stat_itls)
            var_noise = jitter_source ** 2
            var_signal = max(0, var_observed - var_noise)
            _raw_shrinkage = var_signal / (var_signal + var_noise) if (var_signal + var_noise) > 0 else 0.0
            if shrinkage_factor is None:
                shrinkage_factor = _raw_shrinkage

        if _raw_shrinkage is not None and raw_median_itl_ms > 0 and len(raw_stat_itls) >= 10:
            adjusted_raw = [raw_median_itl_ms + _raw_shrinkage * (itl - raw_median_itl_ms) for itl in raw_stat_itls]
            sorted_adjusted_raw = sorted(adjusted_raw)
            raw_p99_itl_ms = round(percentile(sorted_adjusted_raw, 99), 1)
            raw_avg_itl_ms = round(sum(adjusted_raw) / len(adjusted_raw), 2)
            raw_max_itl_ms_out = round(raw_median_itl_ms + _raw_shrinkage * (raw_max_itl_ms - raw_median_itl_ms), 1)
        else:
            raw_p99_itl_ms = _raw_p99
            raw_max_itl_ms_out = round(raw_max_itl_ms, 1)

    if stat_itls:
        _eff_sorted = sorted(stat_itls)
        _eff_mid = len(_eff_sorted) // 2
        _eff_median = _eff_sorted[_eff_mid] if len(_eff_sorted) % 2 else (_eff_sorted[_eff_mid - 1] + _eff_sorted[_eff_mid]) / 2
        _eff_avg = round(sum(stat_itls) / len(stat_itls), 2)
        _eff_p99 = round(percentile(_eff_sorted, 99), 1)

        jitter_source = network_jitter_ms
        _eff_shrinkage: float | None = None
        if jitter_source and jitter_source > 0 and len(stat_itls) >= 10 and _eff_median > 0:
            var_observed = sum((x - _eff_median) ** 2 for x in stat_itls) / len(stat_itls)
            var_noise = jitter_source ** 2
            var_signal = max(0, var_observed - var_noise)
            _eff_shrinkage = var_signal / (var_signal + var_noise) if (var_signal + var_noise) > 0 else 0.0

        if _eff_shrinkage is not None and _eff_median > 0 and len(stat_itls) >= 10:
            _adjusted_eff = [_eff_median + _eff_shrinkage * (itl - _eff_median) for itl in stat_itls]
            _sorted_adjusted_eff = sorted(_adjusted_eff)
            _eff_p99_adjusted = round(percentile(_sorted_adjusted_eff, 99), 1)
            if _eff_median >= 1.0:
                effective_itl_tail_ratio = round(_eff_p99_adjusted / _eff_median, 2)
            elif _eff_avg is not None and _eff_avg > 0:
                effective_itl_tail_ratio = round(_eff_p99_adjusted / _eff_avg, 2)
                effective_itl_tail_ratio_estimated = True
            else:
                effective_itl_tail_ratio = None
            if shrinkage_factor is None:
                shrinkage_factor = _eff_shrinkage
        else:
            if _eff_median >= 1.0:
                effective_itl_tail_ratio = round(_eff_p99 / _eff_median, 2)
            elif _eff_avg is not None and _eff_avg > 0:
                effective_itl_tail_ratio = round(_eff_p99 / _eff_avg, 2)
                effective_itl_tail_ratio_estimated = True
            else:
                effective_itl_tail_ratio = None

        if effective_itl_tail_ratio is not None:
            effective_itl_tail_ratio = min(effective_itl_tail_ratio, 100.0)

        if _eff_shrinkage is not None and _eff_median > 0 and len(stat_itls) >= 10:
            effective_median_itl_ms = round(_eff_median, 1)
            effective_avg_itl_ms = round(_eff_median + _eff_shrinkage * (_eff_avg - _eff_median), 1)
            effective_p99_itl_ms = _eff_p99_adjusted
        else:
            effective_median_itl_ms = round(_eff_median, 1) if _eff_median else None
            effective_avg_itl_ms = round(_eff_avg, 1) if _eff_avg else None
            effective_p99_itl_ms = round(_eff_p99, 1) if _eff_p99 else None

    if raw_max_itl_ms_out is None:
        raw_max_itl_ms_out = round(raw_max_itl_ms, 1) if raw_itls else None

    _SHRINKAGE_MIN = 0.01
    _shrinkage_ok = shrinkage_factor is None or shrinkage_factor > _SHRINKAGE_MIN
    itl_reliable = bool(raw_median_itl_ms >= 1.0 and len(raw_stat_itls) >= 10
                        and burst_arrival_pct < 30 and _shrinkage_ok)

    return _ITLStats(
        stall_count=stall_count,
        hiccup_count=hiccup_count,
        raw_max_itl_ms=raw_max_itl_ms_out,
        raw_median_itl_ms=round(raw_median_itl_ms, 1),
        raw_avg_itl_ms=raw_avg_itl_ms,
        raw_p99_itl_ms=raw_p99_itl_ms,
        effective_median_itl_ms=effective_median_itl_ms,
        effective_avg_itl_ms=effective_avg_itl_ms,
        effective_p99_itl_ms=effective_p99_itl_ms,
        effective_itl_tail_ratio=effective_itl_tail_ratio,
        effective_itl_tail_ratio_estimated=effective_itl_tail_ratio_estimated,
        itl_reliable=itl_reliable,
        burst_arrivals=burst_arrivals,
        burst_arrival_pct=burst_arrival_pct,
        shrinkage_factor=shrinkage_factor,
        stall_metrics=stall_metrics,
    )


def _infer_reasoning_tokens(
    model_label: str,
    is_anthropic: bool,
    usage_info: dict | None,
    answer_token_estimate: int,
    reasoning_token_estimate: int,
) -> int | None:
    reasoning_tokens: int | None = None
    if usage_info and not is_anthropic:
        details = usage_info.get("completion_tokens_details") or {}
        reasoning_tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
        if reasoning_tokens is None:
            reasoning_tokens = usage_info.get("reasoning_tokens")

    if is_anthropic and reasoning_tokens is None and usage_info is not None and answer_token_estimate > 0:
        output_tokens_total = usage_info.get("output_tokens")
        if output_tokens_total and output_tokens_total > answer_token_estimate:
            inferred = output_tokens_total - answer_token_estimate
            if inferred > 0:
                reasoning_tokens = inferred
                log.info(
                    "%s: inferred %d reasoning tokens from Anthropic usage (output_tokens=%d - answer_tokens=%d)",
                    model_label, inferred, output_tokens_total, answer_token_estimate,
                )
    if reasoning_tokens is None and reasoning_token_estimate > 0:
        reasoning_tokens = reasoning_token_estimate

    return reasoning_tokens


def _check_no_answer(
    model_label: str,
    answer_token_count: int,
    reasoning_token_count_observed: int,
    finish_reason: str | None,
    error: str | None,
    test_type: str,
    ttft_ms: float,
    network_rtt_ms: float | None,
    network_jitter_ms: float | None,
    thinking_duration_ms: float | None,
) -> dict | None:
    if answer_token_count == 0 and reasoning_token_count_observed > 0:
        if finish_reason in ("length", "max_tokens"):
            log.info(
                "%s: hit token ceiling while thinking (%d reasoning tokens, 0 answer) - recording as success",
                model_label, reasoning_token_count_observed,
            )
        elif test_type == TEST_HEALTH:
            log.info(
                "%s: health check - reasoning-only response (%d reasoning tokens, 0 answer, finish_reason=%s) - model is alive",
                model_label, reasoning_token_count_observed, finish_reason,
            )
        else:
            cause = "stream error during thinking" if error else "stopped without answering"
            log.warning(
                "%s: 0 answer tokens after %d reasoning tokens - %s",
                model_label, reasoning_token_count_observed, cause,
            )
            return make_result(
                error=f"No answer produced ({cause}; {reasoning_token_count_observed} reasoning tokens, 0 answer)",
                network_rtt_ms=network_rtt_ms,
                network_jitter_ms=network_jitter_ms,
                finish_reason=finish_reason,
                thinking_duration_ms=thinking_duration_ms,
                ttft_ms=ttft_ms,
            )
    return None


# ── Metrics computation ──────────────────────────────────────────────────────

def compute_stream_metrics(
    model_label: str,
    is_anthropic: bool,
    api_token_limit: int,
    start: float,
    end: float,
    first_token_time: float | None,
    last_token_time: float | None,
    token_times: list[float],
    tokens: list[str],
    answer_texts: list[str] | None,
    reasoning_texts: list[str] | None,
    answer_token_count: int,
    reasoning_token_count_observed: int,
    usage_info: dict | None,
    usage_source: str | None,
    finish_reason: str | None,
    error: str | None,
    error_trace: str | None,
    status_code: int | None,
    stream_error_after_tokens: bool,
    thinking_start_time: float | None,
    thinking_end_time: float | None,
    network_rtt_ms: float | None,
    raw_chunks_log: list[str],
    test_type: str = TEST_BENCHMARK,
    network_jitter_ms: float | None = None,
) -> dict:
    """Compute all benchmark metrics from streaming state.

    Returns a result dict via make_result(). TTFT is adjusted by subtracting
    network_rtt_ms (TCP+TLS) for cross-provider comparison. TPS uses
    token_count_for_tps (provider-reported, tiktoken, or chunk count fallback).
    No-answer detection: reasoning-only responses are success for health checks
    and length/max_tokens finish_reason, failure otherwise.
    """
    total_ms = (end - start) * 1000
    _is_health = test_type == TEST_HEALTH

    thinking_duration_ms = None
    if thinking_start_time is not None and thinking_end_time is not None:
        thinking_duration_ms = round((thinking_end_time - thinking_start_time) * 1000, 1)

    if (error and not stream_error_after_tokens) or first_token_time is None or last_token_time is None:
        log_func = log.warning if first_token_time is None else log.info
        if first_token_time is None:
            log_func(
                "No tokens received from %s - status=%s, finish_reason=%s, chunks_parsed=%d, "
                "total=%.1fms, last_raw_chunks=%s",
                model_label, status_code, finish_reason, len(tokens),
                total_ms,
                scrub_pii(str(raw_chunks_log[-5:])) if raw_chunks_log else "(none)",
            )
        else:
            log_func(
                "Incomplete timing data from %s - first_token=%s, last_token=%s, chunks=%d, error=%s",
                model_label, first_token_time, last_token_time, len(tokens), error,
            )
        return make_result(
            error=error or "No tokens received",
            error_trace=error_trace,
            network_rtt_ms=network_rtt_ms,
            network_jitter_ms=network_jitter_ms,
            finish_reason=finish_reason,
        )

    raw_ttft_ms = (first_token_time - start) * 1000
    gen_time_s = (last_token_time - first_token_time)

    # Near-zero gen_time with multiple chunks is a timestamping anomaly - treat
    # as zero to prevent absurdly high TPS.
    if gen_time_s < 0.001 and len(tokens) > 1:
        log.info(
            "%s: gen_time_s=%.6fs with %d chunks - possible timestamping anomaly, treating as zero",
            model_label, gen_time_s, len(tokens),
        )
        gen_time_s = 0.0

    tc = _validate_token_counts(
        model_label, is_anthropic, api_token_limit, tokens,
        answer_texts, reasoning_texts, answer_token_count,
        reasoning_token_count_observed, usage_info, usage_source,
    )

    if network_rtt_ms and network_rtt_ms > 0:
        ttft_ms = round(max(0, raw_ttft_ms - network_rtt_ms), 1)
    else:
        ttft_ms = round(raw_ttft_ms, 1)

    tps = None
    if gen_time_s > 0:
        tps = round(tc.token_count_for_tps / gen_time_s, 1)
    elif tc.token_count_for_tps and total_ms > 0:
        if network_rtt_ms and network_rtt_ms > 0:
            adjusted_total_ms = max(0, total_ms - network_rtt_ms)
            tps = round(tc.token_count_for_tps / (adjusted_total_ms / 1000), 2) if adjusted_total_ms > 0 else None
        else:
            tps = round(tc.token_count_for_tps / (total_ms / 1000), 2)
        log.info(
            "%s: batch response (%d chunks, %d tokens in %.1fms) - TPS from total latency",
            model_label, len(tokens), tc.completion_tokens or tc.tiktoken_total or len(tokens), total_ms,
        )

    tpot_ms = None
    if gen_time_s > 0 and tc.token_count_for_tps > 1:
        tpot_ms = round(gen_time_s / (tc.token_count_for_tps - 1) * 1000, 2)

    total_latency_ms = round(ttft_ms + gen_time_s * 1000, 1)

    itl = _compute_itl_statistics(
        model_label, token_times, tc, test_type,
        network_rtt_ms, network_jitter_ms, len(tokens),
    )

    reasoning_tokens = _infer_reasoning_tokens(
        model_label, is_anthropic, usage_info,
        tc.answer_token_estimate, tc.reasoning_token_estimate,
    )

    no_answer = _check_no_answer(
        model_label, answer_token_count, reasoning_token_count_observed,
        finish_reason, error, test_type, ttft_ms,
        network_rtt_ms, network_jitter_ms, thinking_duration_ms,
    )
    if no_answer is not None:
        return no_answer

    _result_partial = {
        "success": True,
        "error": error if stream_error_after_tokens else None,
        "error_trace": error_trace if stream_error_after_tokens else None,
        "ttft_ms": ttft_ms,
        "tps": tps,
        "tpot_ms": tpot_ms,
        "total_latency_ms": total_latency_ms,
        "token_count": len(tokens),
        "completion_tokens": tc.completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "finish_reason": finish_reason,
        "chunk_token_ratio": tc.chunk_token_ratio,
        "chunk_token_cv": tc.chunk_token_cv,
        "chunk_token_max": tc.chunk_token_max,
        "stall_count": itl.stall_count,
        "hiccup_count": itl.hiccup_count,
        "raw_max_itl_ms": itl.raw_max_itl_ms,
        "raw_median_itl_ms": itl.raw_median_itl_ms,
        "raw_avg_itl_ms": itl.raw_avg_itl_ms,
        "raw_p99_itl_ms": itl.raw_p99_itl_ms,
        "effective_median_itl_ms": itl.effective_median_itl_ms,
        "effective_avg_itl_ms": itl.effective_avg_itl_ms,
        "effective_p99_itl_ms": itl.effective_p99_itl_ms,
        "effective_itl_tail_ratio": itl.effective_itl_tail_ratio,
        "effective_itl_tail_ratio_estimated": itl.effective_itl_tail_ratio_estimated,
        "itl_reliable": itl.itl_reliable,
        "network_rtt_ms": network_rtt_ms,
        "network_jitter_ms": network_jitter_ms,
        "burst_arrivals": itl.burst_arrivals,
        "burst_arrival_pct": itl.burst_arrival_pct,
        "shrinkage_factor": itl.shrinkage_factor,
        "thinking_duration_ms": thinking_duration_ms,
        "degraded": stream_error_after_tokens,
        "degraded_reason": "stream_error" if stream_error_after_tokens else None,
        "stall_first_pct": itl.stall_metrics["stall_first_pct"],
        "stall_last_pct": itl.stall_metrics["stall_last_pct"],
        "stall_clusters": itl.stall_metrics["stall_clusters"],
        "stall_ratio": itl.stall_metrics["stall_ratio"],
    }
    if not _is_health:
        _result_partial["consistency_score"] = compute_consistency_score(_result_partial)
        _result_partial["speed_score"] = compute_speed_score(_result_partial)

    return make_result(**_result_partial)


# ── Request ID extraction ────────────────────────────────────────────────────

_REQUEST_ID_RE = re.compile(
    r'"(?:id|request_id|req_id|request-id)"\s*:\s*"([^"]{1,256})"',
    re.IGNORECASE,
)

# Strict variant for SSE chunks - excludes bare "id" (OpenAI completion ID,
# not a request ID) to avoid false positives.
_REQUEST_ID_RE_STRICT = re.compile(
    r'"(?:request_id|req_id|request-id)"\s*:\s*"([^"]{1,256})"',
    re.IGNORECASE,
)

def _extract_request_id(body: str) -> str | None:
    m = _REQUEST_ID_RE.search(body[:2000])
    return m.group(1) if m else None


def _extract_request_id_from_chunk(chunk: dict) -> str | None:
    """Extract request ID from a parsed SSE chunk (strict - no bare 'id')."""
    return _REQUEST_ID_RE_STRICT.search(json.dumps(chunk)[:2000]) if chunk else None


# ── Streaming test execution ─────────────────────────────────────────────────

@dataclass
class _StreamState:
    """Mutable streaming state accumulated across SSE events for one test run."""
    first_token_time: float | None = None
    last_token_time: float | None = None
    tokens: list[str] = field(default_factory=list)
    token_times: list[float] = field(default_factory=list)
    answer_texts: list[str] = field(default_factory=list)
    reasoning_texts: list[str] = field(default_factory=list)
    answer_token_count: int = 0
    reasoning_token_count_observed: int = 0
    thinking_start_time: float | None = None
    thinking_end_time: float | None = None
    _anthropic_current_thinking: bool = False
    _frame_tracker: dict | None = None

    def record_token(self, text: str, is_reasoning: bool):
        now = time.monotonic()
        if self.first_token_time is None:
            self.first_token_time = now
        self.last_token_time = now
        self.tokens.append(text)
        self.token_times.append(now)
        if self._frame_tracker is not None:
            self._frame_tracker["_pending_tokens"] += 1
        if is_reasoning:
            self.reasoning_texts.append(text)
            self.reasoning_token_count_observed += 1
            if self.thinking_start_time is None:
                self.thinking_start_time = now
        else:
            self.answer_texts.append(text)
            self.answer_token_count += 1
            if self._anthropic_current_thinking:
                self._anthropic_current_thinking = False
                if self.thinking_end_time is None:
                    self.thinking_end_time = now
            if self.thinking_start_time is not None and self.thinking_end_time is None:
                self.thinking_end_time = now


def _build_stream_request(
    provider: dict, prompt: str, test_type: str
) -> tuple[str, dict, dict, int, bool, int]:
    """Build URL, request body, and headers for a streaming test.

    Returns (url, body, headers, api_token_limit, is_anthropic, request_timeout).
    Anthropic uses x-api-key + /messages; OpenAI uses Bearer + /chat/completions.
    Health checks use health_max_tokens and skip thinking params and logprobs.
    """
    base_url = provider["api_url"].rstrip("/")
    # Defense-in-depth: bare hostnames are invalid but some providers ship
    # them - normalizing to https:// prevents httpx crashes.
    if base_url and "://" not in base_url:
        base_url = f"https://{base_url}"
    api_key = provider.get("api_key", "")
    model_id = provider.get("model_id", "?")
    is_health = test_type == TEST_HEALTH
    is_anthropic = "anthropic" in base_url.lower()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    for k, v in provider.get("headers", {}).items():
        headers[k] = v

    # Per-request timeout: health checks get the same timeout as benchmarks.
    request_timeout = c.test_timeout

    # Per-provider/per-model request options (deep-merged: model overrides provider).
    req_opts = {**provider.get("request_options", {})}
    model_opts = provider.get("_model_request_options")
    if model_opts:
        req_opts.update(model_opts)
    token_param = req_opts.get("token_param", "both")

    # Optional per-model/per-provider max_tokens override. Applied to BOTH
    # health and benchmark requests - reasoning models need a larger output
    # budget than the default health_max_tokens (10) to emit an answer, and
    # NanoGPT-style gateways refuse to run them with tiny ceilings
    # (empty_response). A positive override wins over the per-test-type default.
    req_max_tokens = req_opts.get("max_tokens")
    if is_health:
        max_tokens = req_max_tokens if req_max_tokens else c.health_max_tokens
        api_token_limit = max_tokens
    elif is_anthropic and c.anthropic_thinking_budget:
        max_tokens = req_max_tokens if req_max_tokens else c.benchmark_target_tokens
        # budget_tokens must be < max_tokens, hence min(target-1, budget)
        api_token_limit = max_tokens + min(max_tokens - 1, c.anthropic_thinking_budget)
    else:
        max_tokens = req_max_tokens if req_max_tokens else c.benchmark_target_tokens
        api_token_limit = max_tokens

    if is_anthropic:
        headers["x-api-key"] = api_key
        headers.pop("Authorization", None)
        url = f"{base_url}/messages"
        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        }
        if not is_health and c.anthropic_thinking_budget:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": min(max_tokens - 1, c.anthropic_thinking_budget),
            }
    else:
        url = f"{base_url}/chat/completions"
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        if token_param != "legacy":
            body["max_completion_tokens"] = max_tokens
        if token_param != "completion":
            body["max_tokens"] = max_tokens
        if req_opts.get("stream_options", True):
            body["stream_options"] = {"include_usage": True}
        if not is_health and req_opts.get("logprobs", True):
            body["logprobs"] = True

    return url, body, headers, api_token_limit, is_anthropic, request_timeout


async def stream_test(provider: dict, prompt: str, test_type: str = TEST_BENCHMARK):
    """Execute a streaming completion test against a provider and return raw metrics.

    test_type "benchmark" runs a full streaming test with all metrics;
    "health" runs a lightweight check (health_max_tokens, no thinking params,
    no logprobs) that only verifies reachability and measures TTFT. Uses a
    fresh TCP+TLS connection per request (http2=False, max_keepalive=0) so
    httpx trace events always fire for RTT measurement. Returns a result dict
    via make_result(); frame_batch_pct is added when frame tracking is active.
    """
    from backend.state import get_http_client

    provider_name = provider.get("name", "?")
    model_id = provider.get("model_id", "?")
    model_label = f"{provider_name}::{model_id}"
    is_health = test_type == TEST_HEALTH

    url, body, headers, api_token_limit, is_anthropic, request_timeout = _build_stream_request(provider, prompt, test_type)

    connect_start = None
    connect_end = None
    tls_start = None
    tls_end = None

    async def trace_handler(event_type, event_info):
        nonlocal connect_start, connect_end, tls_start, tls_end
        now = time.monotonic()
        if event_type == "connection.connect_tcp.started":
            connect_start = now
        elif event_type == "connection.connect_tcp.complete":
            connect_end = now
        elif event_type == "connection.start_tls.started":
            tls_start = now
        elif event_type == "connection.start_tls.complete":
            tls_end = now

    ss = _StreamState(_frame_tracker={} if not is_health else None)

    usage_info = None
    usage_source = None
    finish_reason = None
    error = None
    error_trace = None
    status_code = None
    _raw_chunks_log: list[str] = []
    _stream_error_after_tokens = False
    _thinking_ended = False
    # Per-request UUID sent as X-Request-ID so gateways/proxies can echo it
    # back. Used as fallback when the provider doesn't return its own.
    _our_request_id = str(uuid.uuid4())
    headers["X-Request-ID"] = _our_request_id
    _request_id = None  # provider's request ID, if returned in headers/body

    start = time.monotonic()

    try:
        req_ext = {"trace": trace_handler}
        client = get_http_client()
        stream_deadline = time.monotonic() + request_timeout
        # httpx read/write timeouts are large safety nets so they never fire
        # before our own deadline/activity_timeout logic - we manage timeouts ourselves.
        httpx_safety = max(request_timeout * 2, c.stream_activity_timeout * 3)
        async with client.stream("POST", url, json=body, headers=headers,
                                 extensions=req_ext,
                                 timeout=httpx.Timeout(connect=float(c.http_connect_timeout),
                                                       read=httpx_safety,
                                                       write=httpx_safety,
                                                       pool=30)) as resp:
                status_code = resp.status_code
                _request_id = (resp.headers.get("x-request-id")
                               or resp.headers.get("request-id")
                               or resp.headers.get("x-stainless-request-id"))
                if status_code != 200:
                    body_text = (await resp.aread()).decode()[:2000]
                    error, error_trace = format_api_error(status_code, body_text)
                    if not _request_id:
                        _request_id = _extract_request_id(body_text)
                    network_rtt = calc_network_rtt_ms(connect_start, connect_end, tls_start, tls_end)
                    update_provider_rtt(provider_name, network_rtt)
                    jitter = get_provider_jitter(provider_name)
                    return make_result(
                        error=error,
                        error_trace=error_trace,
                        network_rtt_ms=network_rtt,
                        network_jitter_ms=jitter,
                        test_type=test_type,
                        request_id=_request_id or _our_request_id,
                    )

                async for event_name, data_str in aiter_sse_events(resp, activity_timeout=c.stream_activity_timeout, frame_tracker=ss._frame_tracker, deadline=stream_deadline):
                    if event_name == "done":
                        break

                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        log.warning("Bad SSE chunk skipped from %s (len=%d)", model_label, len(data_str))
                        continue

                    if not isinstance(chunk, dict):
                        continue

                    _raw_chunks_log.append(data_str[:120])
                    if len(_raw_chunks_log) > 20:
                        _raw_chunks_log = _raw_chunks_log[-20:]

                    parsed = parse_anthropic_event(event_name, chunk) if is_anthropic else parse_openai_chunk(chunk)

                    if parsed["is_error"]:
                        error, error_trace = extract_stream_error(chunk, is_anthropic=is_anthropic)
                        if not _request_id:
                            _request_id = _extract_request_id_from_chunk(chunk)
                        break

                    if is_anthropic and parsed.get("block_type_start") is not None:
                        if parsed["block_type_start"] == "thinking":
                            ss._anthropic_current_thinking = True
                            if ss.thinking_start_time is None:
                                ss.thinking_start_time = time.monotonic()

                    content = parsed.get("content")
                    reasoning = parsed.get("reasoning")

                    if not is_anthropic:
                        content, reasoning, _thinking_ended = split_thinking(
                            content, reasoning, _thinking_ended,
                        )

                    if reasoning is not None and reasoning != "":
                        ss.record_token(reasoning, True)
                    if content is not None and content != "":
                        ss.record_token(content, False)

                    if parsed.get("usage_update") is not None:
                        src, usage_data = parsed["usage_update"]
                        if src in ("final_usage", "message_delta"):
                            usage_info = usage_data
                            usage_source = src
                        elif usage_info is None:
                            usage_info = usage_data
                            usage_source = src

                    if parsed.get("finish_reason"):
                        finish_reason = parsed["finish_reason"]

                    if parsed.get("is_stream_end"):
                        break

    except asyncio.TimeoutError:
        log.warning("%s: request timed out", model_label)
        error = "Request timed out"
    except StreamStalledError as e:
        log.warning("%s: stream stalled", model_label)
        error = safe_internal_error(e)
    except httpx.TimeoutException:
        log.warning("%s: request timed out (httpx safety)", model_label)
        error = "Request timed out"
    except asyncio.CancelledError:
        log.debug("stream_test cancelled for %s", model_label)
        raise
    except Exception as e:
        # During shutdown, httpx client closure causes exceptions that are
        # not provider errors - convert to CancelledError so run_test's cleanup
        # handles them instead of recording a spurious failure.
        if st._shutting_down:
            log.debug("stream_test aborted for %s during shutdown", model_label)
            raise asyncio.CancelledError() from None
        if is_internal_error(e):
            log.error("stream_test internal error for %s: %s", model_label, e)
            raise
        log.warning("stream_test failed for %s: %s", model_label, scrub_pii(str(e))[:200])
        error = safe_internal_error(e)

    if error and len(ss.tokens) > 0:
        _stream_error_after_tokens = True

    end = time.monotonic()

    if ss.thinking_start_time is not None and ss.thinking_end_time is None and ss.reasoning_token_count_observed > 0:
        ss.thinking_end_time = end
    network_rtt = calc_network_rtt_ms(connect_start, connect_end, tls_start, tls_end)

    update_provider_rtt(provider_name, network_rtt)
    jitter = get_provider_jitter(provider_name)

    result = compute_stream_metrics(
        model_label=model_label,
        is_anthropic=is_anthropic,
        api_token_limit=api_token_limit,
        start=start,
        end=end,
        first_token_time=ss.first_token_time,
        last_token_time=ss.last_token_time,
        token_times=ss.token_times,
        tokens=ss.tokens,
        answer_texts=ss.answer_texts,
        reasoning_texts=ss.reasoning_texts,
        answer_token_count=ss.answer_token_count,
        reasoning_token_count_observed=ss.reasoning_token_count_observed,
        usage_info=usage_info,
        usage_source=usage_source,
        finish_reason=finish_reason,
        error=error,
        error_trace=error_trace,
        status_code=status_code,
        stream_error_after_tokens=_stream_error_after_tokens,
        thinking_start_time=ss.thinking_start_time,
        thinking_end_time=ss.thinking_end_time,
        network_rtt_ms=network_rtt,
        raw_chunks_log=_raw_chunks_log,
        test_type=test_type,
        network_jitter_ms=jitter,
    )
    result["test_type"] = test_type

    if ss._frame_tracker is not None:
        total_tokens_recorded = len(ss.tokens)
        batched = ss._frame_tracker.get("tokens_in_multi_event_chunks", 0)
        if total_tokens_recorded > 0 and batched > 0:
            result["frame_batch_pct"] = round(batched / total_tokens_recorded * 100, 1)
        else:
            result["frame_batch_pct"] = 0.0

    result["request_id"] = _request_id or _our_request_id

    return result
