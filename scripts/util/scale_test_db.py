#!/usr/bin/env python3
"""
Generate a scale-test SQLite database for ModelWatcher.

Creates `data/metrics-scale-test.db` with 6 months of realistic
benchmark + health check history for configurable numbers of
providers and models.

Defaults: 100 providers × 50 models = 5000 models
          Benchmarks every 5h, health checks every 15m
          6 months of history (~876 benchmarks + ~17520 health per model)

Usage:
    python scripts/scale_test_db.py                    # 100×50 = 5000 models, 6mo
    python scripts/scale_test_db.py --providers 10 --models-per 5   # 50 models (fast)
    python scripts/scale_test_db.py --months 1          # 1 month only

Then:
    MW_DB_NAME=metrics-scale-test.db MW_MODELS_YAML=models-scale-test.yaml \
    MW_APP_YAML=app-scale-test.yaml MW_SCALE_TEST_KEY=dummy MW_DISABLE_TESTS=1 \
    uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload --reload-dir backend
"""

import argparse
import json
import math
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BENCH_INTERVAL = 5 * 3600   # 5 hours
HEALTH_INTERVAL = 15 * 60   # 15 minutes
SIX_MONTHS_S = 180 * 86400  # ~6 months in seconds

PROVIDER_NAMES = [
    "AlphaAI", "BetaLLM", "CloudMind", "DeltaGPT", "EchoNet",
    "FluxAI", "GridLLM", "HyperGPT", "IotaMind", "JadeAI",
    "KappaLLM", "LambdaAI", "MicroGPT", "NovaLLM", "OmegaAI",
    "PrimeGPT", "QuantaAI", "RhoLLM", "SigmaGPT", "ThetaAI",
    "UltraLLM", "VectorGPT", "WaveAI", "XenonLLM", "YieldGPT",
    "ZetaAI", "ArcLLM", "BlazeGPT", "CrestAI", "DuneLLM",
    "EdgeGPT", "ForgeAI", "GlowLLM", "HazeGPT", "IonAI",
    "JunctionLLM", "KineticGPT", "LumenAI", "MachLLM", "NeuralGPT",
    "OrbitAI", "PulseLLM", "QuillGPT", "RadiantAI", "SparkLLM",
    "TuringGPT", "UnityAI", "VortexLLM", "WarpGPT", "XenithAI",
    "AetherAI", "BinaryLLM", "CyberGPT", "DynamoAI", "EmberLLM",
    "FusionGPT", "GlacierAI", "HorizonLLM", "InfinityGPT", "JetAI",
    "KineticAI", "LunarLLM", "MysticGPT", "NebulaAI", "OnyxLLM",
    "PrismGPT", "QuasarAI", "RippleLLM", "SolarGPT", "TempestAI",
    "UmbraLLM", "VertexGPT", "WhisperAI", "XrayLLM", "ZenithGPT",
    "ApexAI", "BoltLLM", "CascadeGPT", "DriftAI", "EclipseLLM",
    "FlareGPT", "GraniteAI", "HelixLLM", "ImpulseGPT", "JoltAI",
    "KnotLLM", "LatticeGPT", "MirageAI", "NexusLLM", "OasisGPT",
    "PinnacleAI", "QuantumLLM", "RidgeGPT", "SummitAI", "TideLLM",
    "UpliftGPT", "VaporAI", "WellspringLLM", "XyloGPT", "ZephyrAI",
]

MODEL_IDS = [
    "llama-4-70b", "llama-4-8b", "gpt-5.4-mini", "gpt-5.4", "claude-4-sonnet",
    "claude-4-haiku", "gemini-3-pro", "gemini-3-flash", "mistral-8x22b", "mistral-7b",
    "qwen3-235b", "qwen3-30b", "deepseek-v4", "deepseek-v4-lite", "command-r-plus",
    "yi-1.5-34b", "phi-4", "starcoder-3", "codellama-70b", "falcon-180b",
    "mpt-30b", "vicuna-33b", "wizardlm-70b", "airoboros-70b", "zephyr-7b",
    "mixtral-8x7b", "internlm-20b", "solar-10.7b", "openhermes-2.5", "nous-hermes-2",
    "gemma-3-27b", "gemma-3-9b", "cohere-r2", "dbrx-132b", "stablelm-2-12b",
    "pythia-12b", "opt-66b", "bloom-176b", "xgen-7b", "mpt-7b",
    "redpajama-7b", "falcon-40b", "llama-3.3-70b", "qwen2.5-72b", "yi-1.5-6b",
    "phi-3.5-moe", "gemma-2-27b", "command-r", "dbrx-instruct", "hermes-3-70b",
    "granite-34b", "arctic-instruct", "llama-3.1-405b", "qwen-2-72b", "mistral-nemo",
    "pixtral-12b", "mathstral-7b", "codestral-22b", "deepseek-coder-v2", "yi-coder-9b",
    "phi-3-mini", "gemma-2-9b", "llama-3.2-3b", "qwen2.5-3b", "mistral-tiny",
    "claude-3.5-haiku", "gpt-4o", "gpt-4o-mini", "o1-mini", "o3-mini",
    "gemini-1.5-pro", "gemini-1.5-flash", "grok-2", "grok-2-mini", "perplexity-sonar",
    "anthill-70b", "bee-13b", "cricket-7b", "dragonfly-34b", "earwig-3b",
    "firefly-12b", "grasshopper-70b", "hornet-7b", "junebug-13b", "katydid-34b",
    "ladybug-3b", "mosquito-7b", "nit-12b", "orbweaver-70b", "prayingmantis-13b",
    "queenbee-34b", "rolypoly-3b", "stickbug-7b", "termit-12b", "underwing-70b",
    "velvetant-13b", "wasp-34b", "xerces-3b", "yellowjacket-7b", "zephyr-12b",
]

RESULT_COLUMNS = [
    "model_key", "provider", "ts_epoch", "timestamp",
    "available", "success",
    "ttft_ms", "tps", "itl_reliable",
    "tpot_ms", "total_latency_ms",
    "token_count", "completion_tokens", "reasoning_tokens",
    "chunk_token_ratio", "chunk_token_cv", "chunk_token_max", "finish_reason",
    "stall_count", "hiccup_count",
    "raw_max_itl_ms", "raw_median_itl_ms", "raw_avg_itl_ms",
    "raw_p99_itl_ms", "effective_median_itl_ms", "effective_avg_itl_ms", "effective_p99_itl_ms",
    "effective_itl_tail_ratio", "effective_itl_tail_ratio_estimated",
    "network_rtt_ms", "thinking_duration_ms",
    "degraded", "degraded_reason", "critical_metrics",
    "retry_attempt", "retry_total", "retry_count",
    "error", "error_trace",
    "test_type",
    "consistency_score", "speed_score",
    "stall_first_pct", "stall_last_pct", "stall_clusters", "stall_ratio",
    "network_jitter_ms", "burst_arrivals", "burst_arrival_pct",
    "shrinkage_factor",
    "frame_batch_pct",
    "request_id",
]

RESULT_INSERT_SQL = f"INSERT INTO test_results ({', '.join(RESULT_COLUMNS)}) VALUES ({', '.join(['?'] * len(RESULT_COLUMNS))})"

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS test_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key               TEXT NOT NULL,
    provider                TEXT NOT NULL,
    ts_epoch                REAL NOT NULL,
    timestamp               TEXT NOT NULL,
    available               INTEGER NOT NULL,
    success                 INTEGER NOT NULL,
    ttft_ms                 REAL,
    tps                     REAL,
    itl_reliable            INTEGER DEFAULT 0,
    tpot_ms                 REAL,
    total_latency_ms        REAL,
    token_count             INTEGER,
    completion_tokens       INTEGER,
    reasoning_tokens        INTEGER,
    chunk_token_ratio       REAL,
    chunk_token_cv          REAL,
    chunk_token_max         INTEGER,
    finish_reason           TEXT,
    stall_count             INTEGER,
    hiccup_count            INTEGER,
    raw_max_itl_ms          REAL,
    raw_median_itl_ms       REAL,
    raw_avg_itl_ms          REAL,
    raw_p99_itl_ms          REAL,
    effective_median_itl_ms  REAL,
    effective_avg_itl_ms    REAL,
    effective_p99_itl_ms    REAL,
    effective_itl_tail_ratio REAL,
    effective_itl_tail_ratio_estimated INTEGER DEFAULT 0,
    network_rtt_ms          REAL,
    thinking_duration_ms    REAL,
    degraded                INTEGER DEFAULT 0,
    degraded_reason         TEXT,
    critical_metrics        TEXT,
    retry_attempt           INTEGER,
    retry_total             INTEGER,
    retry_count             INTEGER,
    error                   TEXT,
    error_trace             TEXT,
    test_type               TEXT DEFAULT 'benchmark',
    consistency_score       REAL,
    speed_score             REAL,
    stall_first_pct         REAL,
    stall_last_pct          REAL,
    stall_clusters          INTEGER DEFAULT 0,
    stall_ratio             REAL,
    network_jitter_ms       REAL,
    burst_arrivals          INTEGER,
    burst_arrival_pct       REAL,
    shrinkage_factor        REAL,
    frame_batch_pct          REAL,
    request_id              TEXT
);

CREATE INDEX IF NOT EXISTS idx_results_model_ts ON test_results (model_key, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_results_epoch ON test_results (ts_epoch);
CREATE INDEX IF NOT EXISTS idx_results_provider_ts ON test_results (provider, ts_epoch DESC);

CREATE TABLE IF NOT EXISTS model_state (
    model_key       TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'unknown',
    degraded_source TEXT,
    uptime_pct      REAL,
    total_tests     INTEGER NOT NULL DEFAULT 0,
    total_success   INTEGER NOT NULL DEFAULT 0,
    first_ts_epoch  REAL,
    reliability_score REAL,
    trends_json     TEXT,
    updated_at      REAL
);

CREATE TABLE IF NOT EXISTS providers (
    name            TEXT PRIMARY KEY,
    api_url         TEXT,
    page_title      TEXT,
    logo_path       TEXT,
    last_fetched_at REAL,
    extra           TEXT
);

CREATE TABLE IF NOT EXISTS model_info (
    model_key       TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    display_name    TEXT,
    context_window  INTEGER,
    output_context  INTEGER,
    supports_cache  INTEGER,
    supports_vision INTEGER,
    supports_tools  INTEGER,
    supports_structured_output INTEGER,
    input_price     REAL,
    output_price    REAL,
    cache_price     REAL,
    description     TEXT,
    modalities      TEXT,
    tokenizer       TEXT,
    reasoning_price REAL,
    image_price     REAL,
    created         REAL,
    owner           TEXT,
    license         TEXT,
    thinking        TEXT,
    quantization    TEXT,
    served_by       TEXT,
    architecture    TEXT,
    param_count     TEXT,
    num_experts     INTEGER,
    num_experts_per_tok INTEGER,
    num_shared_experts  INTEGER,
    moe_intermediate_size INTEGER,
    last_fetched_at REAL,
    updated_at      REAL
);

CREATE INDEX IF NOT EXISTS idx_model_info_provider ON model_info (provider);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint        TEXT PRIMARY KEY,
    p256dh          TEXT NOT NULL,
    auth            TEXT NOT NULL,
    client_id       TEXT NOT NULL,
    prefs           TEXT NOT NULL,
    created_at      REAL NOT NULL,
    last_active     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_push_client ON push_subscriptions (client_id);
"""


def _tuple(d):
    return tuple(d.get(c) for c in RESULT_COLUMNS)


def _bench(model_key, provider, ts_epoch, is_error=False):
    ts_iso = datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat()
    tps = round(random.uniform(15, 140), 2)
    ttft = round(random.uniform(150, 6000), 1)
    median_itl = round(random.uniform(5, 50), 1)
    avg_itl = round(median_itl * random.uniform(0.9, 1.3), 1)
    p99_itl = round(median_itl * random.uniform(2, 6), 1)
    max_itl = round(p99_itl * random.uniform(1, 3), 1)
    tail_ratio = round(random.uniform(1.5, 8.0), 2)
    chunk_ratio = round(random.uniform(1.0, 4.0), 2)
    tokens = random.randint(800, 4000)
    latency = round(random.uniform(5, 40) * 1000, 0)
    stall_count = random.randint(0, max(0, int((tail_ratio - 2) * 2)))
    hiccup_count = max(0, stall_count - random.randint(0, stall_count))
    network_rtt = round(random.uniform(30, 2000), 1)
    is_degraded = random.random() < 0.08 and not is_error
    degraded_reason = None
    critical_metrics_val = None
    if is_degraded:
        degraded_reason = random.choice(["stream_error", "insufficient_output", "critical_tier"])
        if degraded_reason == "critical_tier":
            critical_metrics_val = json.dumps(random.sample(
                ["tps", "stall_count", "raw_p99_itl_ms", "effective_itl_tail_ratio", "chunk_token_ratio"],
                k=random.randint(2, 3),
            ))

    return _tuple({
        "model_key": model_key, "provider": provider,
        "ts_epoch": ts_epoch, "timestamp": ts_iso,
        "available": 0 if is_error else 1, "success": 0 if is_error else 1,
        "ttft_ms": None if is_error else ttft,
        "tps": None if is_error else tps,
        "itl_reliable": 0,
        "tpot_ms": None if is_error else round(1000.0 / max(tps, 1), 2),
        "total_latency_ms": None if is_error else latency,
        "token_count": None if is_error else tokens,
        "completion_tokens": None if is_error else tokens,
        "reasoning_tokens": None,
        "chunk_token_ratio": None if is_error else chunk_ratio,
        "chunk_token_cv": None,
        "chunk_token_max": None,
        "finish_reason": None if is_error else "stop",
        "stall_count": None if is_error else stall_count,
        "hiccup_count": None if is_error else hiccup_count,
        "raw_max_itl_ms": None if is_error else max_itl,
        "raw_median_itl_ms": None if is_error else median_itl,
        "raw_avg_itl_ms": None if is_error else avg_itl,
        "raw_p99_itl_ms": None if is_error else p99_itl,
        "effective_median_itl_ms": None,
        "effective_avg_itl_ms": None,
        "effective_p99_itl_ms": None,
        "effective_itl_tail_ratio": None if is_error else tail_ratio,
        "effective_itl_tail_ratio_estimated": 0,
        "network_rtt_ms": None if is_error else network_rtt,
        "thinking_duration_ms": None,
        "degraded": 1 if is_degraded else 0,
        "degraded_reason": degraded_reason,
        "critical_metrics": critical_metrics_val,
        "retry_attempt": None, "retry_total": None, "retry_count": None,
        "error": random.choice([
            "Connection timeout", "HTTP 500 Internal server error",
            "HTTP 429 Rate limit exceeded", "Stream interrupted",
        ]) if is_error else None,
        "error_trace": None,
        "test_type": "benchmark",
        "consistency_score": None if is_error else round(random.uniform(30, 95), 1),
        "speed_score": None if is_error else round(random.uniform(30, 95), 1),
        "stall_first_pct": None, "stall_last_pct": None, "stall_clusters": 0, "stall_ratio": None,
        "network_jitter_ms": None,
        "burst_arrivals": None, "burst_arrival_pct": None,
        "shrinkage_factor": None,
        "frame_batch_pct": None,
        "request_id": None,
    })


def _health(model_key, provider, ts_epoch, is_error=False):
    ts_iso = datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat()
    ttft = round(random.uniform(150, 6000), 1) if not is_error else None

    return _tuple({
        "model_key": model_key, "provider": provider,
        "ts_epoch": ts_epoch, "timestamp": ts_iso,
        "available": 0 if is_error else 1, "success": 0 if is_error else 1,
        "ttft_ms": ttft,
        "tps": None, "itl_reliable": 0,
        "tpot_ms": None, "total_latency_ms": None,
        "token_count": None, "completion_tokens": None, "reasoning_tokens": None,
        "chunk_token_ratio": None, "chunk_token_cv": None, "chunk_token_max": None,
        "finish_reason": None,
        "stall_count": None, "hiccup_count": None,
        "raw_max_itl_ms": None, "raw_median_itl_ms": None, "raw_avg_itl_ms": None,
        "raw_p99_itl_ms": None,
        "effective_median_itl_ms": None, "effective_avg_itl_ms": None, "effective_p99_itl_ms": None,
        "effective_itl_tail_ratio": None, "effective_itl_tail_ratio_estimated": 0,
        "network_rtt_ms": None, "thinking_duration_ms": None,
        "degraded": 0, "degraded_reason": None, "critical_metrics": None,
        "retry_attempt": None, "retry_total": None, "retry_count": None,
        "error": random.choice(["Connection timeout", "HTTP 500 Internal server error"]) if is_error else None,
        "error_trace": None,
        "test_type": "health",
        "consistency_score": None, "speed_score": None,
        "stall_first_pct": None, "stall_last_pct": None, "stall_clusters": 0, "stall_ratio": None,
        "network_jitter_ms": None, "burst_arrivals": None, "burst_arrival_pct": None,
        "shrinkage_factor": None, "frame_batch_pct": None,
        "request_id": None,
    })


def _trends():
    metrics = [
        "tps", "ttft_ms", "stall_count", "raw_p99_itl_ms",
        "effective_itl_tail_ratio", "chunk_token_ratio",
        "consistency_score", "speed_score", "available", "reliability_score",
    ]
    trends = {"since_ts": time.time() - 86400 * 7}
    for m in metrics:
        direction = random.choice(["improving", "stable", "stable", "degrading"])
        change = round(random.uniform(0, 30), 2)
        unit = (
            "t/s" if m == "tps" else
            "ms" if "ms" in m else
            "pts" if "score" in m else
            "pp" if m == "available" else
            "×"
        )
        trends[m] = {
            "direction": direction, "change": change, "unit": unit,
            "data_points": random.randint(5, 20),
        }
    return json.dumps(trends)


def main():
    ap = argparse.ArgumentParser(description="Generate scale-test DB for ModelWatcher")
    ap.add_argument("--providers", type=int, default=100)
    ap.add_argument("--models-per", type=int, default=50, help="Models per provider")
    ap.add_argument("--months", type=float, default=6, help="Months of history")
    ap.add_argument("--bench-interval", type=int, default=BENCH_INTERVAL)
    ap.add_argument("--health-interval", type=int, default=HEALTH_INTERVAL)
    ap.add_argument("--error-rate", type=float, default=0.05)
    ap.add_argument("--health-error-rate", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)

    try:
        from PIL import Image, ImageDraw, ImageFont
        _has_pil = True
    except ImportError:
        _has_pil = False

    project_root = Path(__file__).resolve().parent.parent
    db_path = project_root / "data" / "metrics-scale-test.db"

    if db_path.exists():
        db_path.unlink()
        print(f"Removed existing {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)

    for p in [
        "PRAGMA journal_mode=WAL",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA mmap_size=0",
        "PRAGMA cache_size=-131072",
        "PRAGMA busy_timeout=5000",
        "PRAGMA temp_store=MEMORY",
        "PRAGMA foreign_keys=ON",
        "PRAGMA wal_autocheckpoint=50000",
    ]:
        conn.execute(p)

    total_models = args.providers * args.models_per
    history_s = args.months * 30 * 86400
    now = time.time()

    n_bench = max(1, int(history_s / args.bench_interval))
    n_health = max(1, int(history_s / args.health_interval))

    print(f"Generating {args.providers} providers × {args.models_per} models = {total_models} models")
    print(f"  {args.months} months history = {n_bench:,} benchmarks + {n_health:,} health per model")
    est_rows = total_models * (n_bench + n_health)
    print(f"  ≈ {est_rows:,} total test_results rows")

    provider_rows = []
    favicon_ext = "png" if _has_pil else "svg"
    for pi in range(args.providers):
        pname = PROVIDER_NAMES[pi] if pi < len(PROVIDER_NAMES) else f"Provider{pi}"
        slug = pname.replace(" ", "_")
        provider_rows.append((
            pname,
            f"https://api.provider{pi}.example.com/v1",
            f"{pname} - LLM API",
            f"{slug}.{favicon_ext}",
            now,
            None,
        ))

    print("Inserting providers...")
    conn.executemany(
        "INSERT OR IGNORE INTO providers (name, api_url, page_title, logo_path, last_fetched_at, extra) VALUES (?,?,?,?,?,?)",
        provider_rows,
    )
    conn.execute("COMMIT")
    conn.execute("BEGIN")

    print("Generating test_results + model_state...")
    result_batch = []
    state_batch = []
    FLUSH = 50000
    total_rows = 0

    for pi in range(args.providers):
        pname = PROVIDER_NAMES[pi] if pi < len(PROVIDER_NAMES) else f"Provider{pi}"
        for mj in range(args.models_per):
            mid = MODEL_IDS[mj % len(MODEL_IDS)]
            if mj >= len(MODEL_IDS):
                mid = f"{mid}-v{(mj // len(MODEL_IDS)) + 1}"
            model_key = f"{pname}::{mid}"

            first_epoch = None
            total_success = 0
            total_tests = 0

            for ri in range(n_bench):
                is_error = random.random() < args.error_rate
                ts = now - (n_bench - ri) * args.bench_interval + random.uniform(-60, 60)
                if first_epoch is None or ts < first_epoch:
                    first_epoch = ts
                result_batch.append(_bench(model_key, pname, ts, is_error))
                if not is_error:
                    total_success += 1
                total_tests += 1

            for ri in range(n_health):
                is_error = random.random() < args.health_error_rate
                ts = now - (n_health - ri) * args.health_interval + random.uniform(-5, 5)
                if first_epoch is None or ts < first_epoch:
                    first_epoch = ts
                result_batch.append(_health(model_key, pname, ts, is_error))
                if not is_error:
                    total_success += 1
                total_tests += 1

            total_rows += total_tests
            uptime = round(total_success / max(total_tests, 1) * 100, 1)
            status = (
                "online" if uptime > 80 else
                "degraded" if uptime > 50 else
                "error"
            )

            state_batch.append((
                model_key, status, None, uptime, total_tests, total_success,
                first_epoch, round(50 + random.random() * 48, 1), _trends(), now,
            ))

            if len(result_batch) >= FLUSH:
                conn.executemany(RESULT_INSERT_SQL, result_batch)
                result_batch.clear()
                conn.execute("COMMIT")
                conn.execute("BEGIN")
                model_idx = pi * args.models_per + mj + 1
                print(f"  {total_rows:,} rows... ({model_idx}/{total_models} models)")

        if result_batch:
            conn.executemany(RESULT_INSERT_SQL, result_batch)
            result_batch.clear()

    print("Inserting model_state...")
    conn.executemany(
        "INSERT OR IGNORE INTO model_state "
        "(model_key, status, degraded_source, uptime_pct, total_tests, total_success, "
        "first_ts_epoch, reliability_score, trends_json, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        state_batch,
    )

    conn.execute("COMMIT")

    count = conn.execute("SELECT COUNT(*) FROM test_results").fetchone()[0]
    ms_count = conn.execute("SELECT COUNT(*) FROM model_state").fetchone()[0]
    p_count = conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
    db_size_mb = db_path.stat().st_size / 1024 / 1024

    conn.close()

    yaml_path = project_root / "config" / "models-scale-test.yaml"
    with open(yaml_path, "w") as f:
        f.write("# Auto-generated scale test config\n")
        f.write("providers:\n")
        for pi in range(args.providers):
            pname = PROVIDER_NAMES[pi] if pi < len(PROVIDER_NAMES) else f"Provider{pi}"
            f.write(f"  - name: \"{pname}\"\n")
            f.write(f"    api_url: \"https://api.provider{pi}.example.com/v1\"\n")
            f.write(f"    api_key: \"${{MW_SCALE_TEST_KEY}}\"\n")
            f.write(f"    models:\n")
            for mj in range(args.models_per):
                mid = MODEL_IDS[mj % len(MODEL_IDS)]
                if mj >= len(MODEL_IDS):
                    mid = f"{mid}-v{(mj // len(MODEL_IDS)) + 1}"
                f.write(f"      - id: \"{mid}\"\n")
                f.write(f"        name: \"{mid}\"\n")

    yaml_size_kb = yaml_path.stat().st_size / 1024

    app_src = project_root / "config" / "app.yaml"
    app_dst = project_root / "config" / "app-scale-test.yaml"
    stagger_interval = total_models * 150 + 100
    import re
    with open(app_src) as src:
        app_text = src.read()
    app_text = re.sub(r'(\s+interval:\s+)\d+', rf'\g<1>{stagger_interval}', app_text, count=1)
    app_text = re.sub(r'(\s+stagger:\s+)\w+', r'\g<1>false', app_text)
    with open(app_dst, "w") as dst:
        dst.write(f"# Auto-generated - {total_models} models, stagger disabled\n")
        dst.write(app_text)
    app_size_kb = app_dst.stat().st_size / 1024

    favicon_dir = project_root / "data" / "favicons"
    favicon_dir.mkdir(parents=True, exist_ok=True)
    print("Generating placeholder favicons...")
    if not _has_pil:
        print("  Warning: Pillow not installed, generating SVG placeholders instead")
    if _has_pil:
        import colorsys
        from PIL import Image, ImageDraw, ImageFont

    for pi in range(args.providers):
        pname = PROVIDER_NAMES[pi] if pi < len(PROVIDER_NAMES) else f"Provider{pi}"
        slug = pname.replace(" ", "_")
        if _has_pil:
            img = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            hue = (pi * 137) % 360
            r, g, b = colorsys.hsv_to_rgb(hue / 360, 0.6, 0.85)
            fill = (int(r * 255), int(g * 255), int(b * 255))
            draw.ellipse([4, 4, 44, 44], fill=fill)
            letter = pname[0].upper()
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
            except (OSError, IOError):
                try:
                    font = ImageFont.truetype("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", 26)
                except (OSError, IOError):
                    font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), letter, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            draw.text((24 - tw / 2, 24 - th / 2 - bbox[1]), letter, fill=(255, 255, 255), font=font)
            img.save(favicon_dir / f"{slug}.png", "PNG")
        else:
            hue = (pi * 137) % 360
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
                f'<circle cx="24" cy="24" r="20" fill="hsl({hue},60%,85%)"/>'
                f'<text x="24" y="24" text-anchor="middle" dominant-baseline="central"'
                f' font-family="sans-serif" font-weight="bold" font-size="26" fill="white">'
                f'{pname[0]}</text></svg>'
            )
            with open(favicon_dir / f"{slug}.svg", "w") as f:
                f.write(svg)

    print()
    print(f"Done!")
    print(f"  Database:    {db_path} ({db_size_mb:.1f} MB)")
    print(f"  Models YAML: {yaml_path} ({yaml_size_kb:.0f} KB)")
    print(f"  App YAML:    {app_dst} ({app_size_kb:.0f} KB)")
    print(f"  Favicons:    {favicon_dir} ({args.providers} files)")
    print(f"  Providers:   {p_count}")
    print(f"  Models:      {ms_count:,}")
    print(f"  Results:     {count:,}")
    print()
    print("To use:")
    print(f"  MW_DB_NAME=metrics-scale-test.db MW_MODELS_YAML=models-scale-test.yaml \\")
    print(f"  MW_APP_YAML=app-scale-test.yaml MW_SCALE_TEST_KEY=dummy MW_DISABLE_TESTS=1 \\")
    print(f"  uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload --reload-dir backend")


if __name__ == "__main__":
    main()
