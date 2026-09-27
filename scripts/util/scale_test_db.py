#!/usr/bin/env python3
"""Generate a scale-test database and matching configs for ModelWatcher.

Seeds synthetic benchmark and health history for a configurable number of
providers and models. Paths, schema and row encoding come from the backend
(backend.state, backend.db, backend.favicons), so the output always matches
what the server reads. Status, uptime, trends and reliability scores are left
for the server to derive at startup, exactly as it does for real data.

Run from the project root, in module form so `backend` is importable:
    python3 -m scripts.util.scale_test_db                                 # 100 x 50 = 5000 models, 6 months
    python3 -m scripts.util.scale_test_db --providers 10 --models-per 5   # 50 models (fast)
    python3 -m scripts.util.scale_test_db --help                          # every option and default

It finishes by logging the env vars that start the server on the generated files.
"""

import argparse
import colorsys
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

import backend.db as db
import backend.state as st
from backend.favicons import FAVICON_DIR, provider_slug

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

# models.yaml references this env var for every provider's api_key
API_KEY_ENV = "MW_SCALE_TEST_KEY"

# Default benchmark interval written to the generated app.yaml: with stagger off,
# leave about this many seconds per model so one pass fits in one interval.
_APP_BENCH_SECONDS_PER_MODEL = 150
_APP_BENCH_HEADROOM_S = 100

_BENCH_ERRORS = ("Connection timeout", "HTTP 500 Internal server error", "HTTP 429 Rate limit exceeded", "Stream interrupted")
_HEALTH_ERRORS = ("Connection timeout", "HTTP 500 Internal server error")
_DEGRADED_REASONS = ("stream_error", "insufficient_output", "critical_tier")
_CRITICAL_METRICS = ("tps", "stall_count", "raw_p99_itl_ms", "effective_itl_tail_ratio", "chunk_token_ratio")
_FONT_CANDIDATES = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Generate a scale-test DB and configs for ModelWatcher",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--providers", type=int, default=100, help="Number of providers")
    ap.add_argument("--models-per", type=int, default=50, help="Models per provider")
    ap.add_argument("--months", type=float, default=6, help="Months of history (30-day months)")
    ap.add_argument("--bench-interval", type=int, default=5 * 3600, help="Seconds between seeded benchmarks")
    ap.add_argument("--health-interval", type=int, default=15 * 60, help="Seconds between seeded health checks")
    ap.add_argument("--error-rate", type=float, default=0.05, help="Share of failed benchmarks")
    ap.add_argument("--health-error-rate", type=float, default=0.02, help="Share of failed health checks")
    ap.add_argument("--degraded-rate", type=float, default=0.08, help="Share of successful benchmarks marked degraded")
    ap.add_argument("--seed", type=int, default=42, help="Random seed, for reproducible output")
    ap.add_argument("--flush-rows", type=int, default=50_000, help="Rows buffered before each SQLite write")
    ap.add_argument("--data-dir", type=Path, default=st.DATA_DIR, help="Where the DB and favicons go")
    ap.add_argument("--config-dir", type=Path, default=st.CONFIG_DIR, help="Where the generated YAML files go")
    ap.add_argument("--db-name", default="metrics-scale-test.db", help="Server reads it via MW_DB_NAME")
    ap.add_argument("--models-yaml", default="models-scale-test.yaml", help="Server reads it via MW_MODELS_YAML")
    ap.add_argument("--app-yaml", default="app-scale-test.yaml", help="Server reads it via MW_APP_YAML")
    ap.add_argument("--app-template", type=Path, default=st.CONFIG_DIR / "app.yaml",
                    help="app.yaml the generated one is derived from")
    ap.add_argument("--app-bench-interval", type=int, default=None,
                    help=f"Benchmark interval in the generated app.yaml (default: models x "
                         f"{_APP_BENCH_SECONDS_PER_MODEL}s + {_APP_BENCH_HEADROOM_S}s)")
    ap.add_argument("--app-set", type=_app_override, action="append", default=[], metavar="PATH=VALUE",
                    help="Override a template key in the generated app.yaml (dotted path, YAML value, "
                         "repeatable), e.g. websocket.heartbeat_interval=1")
    return ap.parse_args(argv)


def _app_override(text: str) -> tuple[list[str], object]:
    path, sep, value = text.partition("=")
    if not sep or not path:
        raise argparse.ArgumentTypeError(f"expected PATH=VALUE, got {text!r}")
    return path.split("."), yaml.safe_load(value)


def _build_providers(n_providers: int, models_per: int) -> list[tuple[str, str, list[str]]]:
    """(name, api_url, model_ids) per provider - the one catalog every output is built from."""
    providers = []
    for pi in range(n_providers):
        name = PROVIDER_NAMES[pi] if pi < len(PROVIDER_NAMES) else f"Provider{pi}"
        model_ids = []
        for mj in range(models_per):
            mid = MODEL_IDS[mj % len(MODEL_IDS)]
            if mj >= len(MODEL_IDS):
                mid = f"{mid}-v{mj // len(MODEL_IDS) + 1}"
            model_ids.append(mid)
        providers.append((name, f"https://api.provider{pi}.example.com/v1", model_ids))
    return providers


def _iso(ts_epoch: float) -> str:
    return datetime.fromtimestamp(ts_epoch, tz=timezone.utc).isoformat()


def _bench(ts_epoch: float, is_error: bool, degraded_rate: float) -> dict:
    record = {"ts_epoch": ts_epoch, "timestamp": _iso(ts_epoch), "test_type": st.TEST_BENCHMARK,
              "available": not is_error, "success": not is_error}
    if is_error:
        record["error"] = random.choice(_BENCH_ERRORS)
        return record
    tps = round(random.uniform(15, 140), 2)
    median_itl = round(random.uniform(5, 50), 1)
    p99_itl = round(median_itl * random.uniform(2, 6), 1)
    tail_ratio = round(random.uniform(1.5, 8.0), 2)
    tokens = random.randint(800, 4000)
    stall_count = random.randint(0, max(0, int((tail_ratio - 2) * 2)))
    record.update({
        "ttft_ms": round(random.uniform(150, 6000), 1),
        "tps": tps,
        "itl_reliable": False,
        "tpot_ms": round(1000.0 / max(tps, 1), 2),
        "total_latency_ms": round(random.uniform(5, 40) * 1000, 0),
        "token_count": tokens,
        "completion_tokens": tokens,
        "chunk_token_ratio": round(random.uniform(1.0, 4.0), 2),
        "finish_reason": "stop",
        "stall_count": stall_count,
        "hiccup_count": max(0, stall_count - random.randint(0, stall_count)),
        "raw_max_itl_ms": round(p99_itl * random.uniform(1, 3), 1),
        "raw_median_itl_ms": median_itl,
        "raw_avg_itl_ms": round(median_itl * random.uniform(0.9, 1.3), 1),
        "raw_p99_itl_ms": p99_itl,
        "effective_itl_tail_ratio": tail_ratio,
        "effective_itl_tail_ratio_estimated": False,
        "network_rtt_ms": round(random.uniform(30, 2000), 1),
        "stall_clusters": 0,
        "consistency_score": round(random.uniform(30, 95), 1),
        "speed_score": round(random.uniform(30, 95), 1),
    })
    if random.random() < degraded_rate:
        record["degraded"] = True
        record["degraded_reason"] = random.choice(_DEGRADED_REASONS)
        if record["degraded_reason"] == "critical_tier":
            record["critical_metrics"] = random.sample(_CRITICAL_METRICS, k=random.randint(2, 3))
    return record


def _health(ts_epoch: float, is_error: bool) -> dict:
    record = {"ts_epoch": ts_epoch, "timestamp": _iso(ts_epoch), "test_type": st.TEST_HEALTH,
              "available": not is_error, "success": not is_error}
    if is_error:
        record["error"] = random.choice(_HEALTH_ERRORS)
    else:
        record["ttft_ms"] = round(random.uniform(150, 6000), 1)
    return record


def _model_records(now: float, n_bench: int, n_health: int, args: argparse.Namespace) -> list[dict]:
    records = [
        _bench(now - (n_bench - i) * args.bench_interval + random.uniform(-60, 60),
               random.random() < args.error_rate, args.degraded_rate)
        for i in range(n_bench)
    ]
    records += [
        _health(now - (n_health - i) * args.health_interval + random.uniform(-5, 5),
                random.random() < args.health_error_rate)
        for i in range(n_health)
    ]
    return records


def _remove_db(db_path: Path):
    """Delete a previous DB together with its WAL sidecars so no stale pages survive."""
    for path in (db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
        if path.exists():
            path.unlink()
            st.log.info("Removed existing %s", path)


def _seed_db(db_path: Path, providers: list, favicon_ext: str, args: argparse.Namespace) -> int:
    """Create the DB through backend.db.init() and fill it. Returns the number of test_results rows."""
    history_s = args.months * 30 * 86400
    n_bench = max(1, int(history_s / args.bench_interval))
    n_health = max(1, int(history_s / args.health_interval))
    total_models = sum(len(model_ids) for _, _, model_ids in providers)
    st.log.info("Generating %d providers x %d models = %d models", len(providers), args.models_per, total_models)
    st.log.info("  %s months = %d benchmarks + %d health checks per model (~%d rows)",
                args.months, n_bench, n_health, total_models * (n_bench + n_health))

    _remove_db(db_path)
    db.init(db_path)
    now = time.time()
    total_rows = 0
    try:
        for name, api_url, _ in providers:
            db.upsert_provider(name, api_url=api_url, page_title=f"{name} - LLM API",
                               logo_path=f"{provider_slug(name)}.{favicon_ext}", last_fetched_at=now)
        batch: list[tuple[str, dict]] = []
        for name, _, model_ids in providers:
            for mid in model_ids:
                model_key = st.make_model_key(name, mid)
                records = _model_records(now, n_bench, n_health, args)
                db.upsert_model_state(model_key, {
                    "status": "unknown",
                    "total_tests": len(records),
                    "total_success": sum(1 for r in records if r["success"]),
                    "first_ts_epoch": min(r["ts_epoch"] for r in records),
                })
                batch.extend((model_key, r) for r in records)
                total_rows += len(records)
                if len(batch) >= args.flush_rows:
                    db.insert_results(batch)
                    db.commit()
                    batch.clear()
                    st.log.info("  %d rows written", total_rows)
        db.insert_results(batch)
        db.commit()
    finally:
        db.close()
    return total_rows


def _write_models_yaml(path: Path, providers: list):
    cfg = {"providers": [
        {"name": name, "api_url": api_url, "api_key": f"${{{API_KEY_ENV}}}",
         "models": [{"id": mid, "name": mid} for mid in model_ids]}
        for name, api_url, model_ids in providers
    ]}
    path.write_text("# Auto-generated scale test config\n" + yaml.safe_dump(cfg, sort_keys=False))


def _write_app_yaml(path: Path, template: Path, bench_interval: int, total_models: int, overrides: list):
    """Copy the template with a scaled benchmark interval, stagger off, and the --app-set overrides."""
    cfg = yaml.safe_load(template.read_text())
    bench = cfg["testing"]["benchmark"]
    bench["interval"] = bench_interval
    bench["stagger"] = False
    for keys, value in overrides:
        node = cfg
        for key in keys[:-1]:
            node = node[key]
        # Only existing keys: a typo would otherwise add a key the server ignores or rejects later
        if keys[-1] not in node:
            raise KeyError(f"--app-set {'.'.join(keys)}: no such key in {template.name}")
        node[keys[-1]] = value
    header = f"# Auto-generated from {template.name} - {total_models} models, stagger disabled\n"
    path.write_text(header + yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))


def _favicon_font():
    from PIL import ImageFont
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, 26)
    return ImageFont.load_default()


def _write_favicons(favicon_dir: Path, providers: list, favicon_ext: str):
    """Placeholder logo per provider: a coloured disc with the provider's initial."""
    favicon_dir.mkdir(parents=True, exist_ok=True)
    font = _favicon_font() if st.pillow_available else None
    for pi, (name, _, _) in enumerate(providers):
        hue = (pi * 137) % 360
        target = favicon_dir / f"{provider_slug(name)}.{favicon_ext}"
        letter = name[0].upper()
        if font is None:
            target.write_text(
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
                f'<circle cx="24" cy="24" r="20" fill="hsl({hue},60%,85%)"/>'
                f'<text x="24" y="24" text-anchor="middle" dominant-baseline="central"'
                f' font-family="sans-serif" font-weight="bold" font-size="26" fill="white">{letter}</text></svg>'
            )
            continue
        from PIL import ImageDraw
        img = st.Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r, g, b = colorsys.hsv_to_rgb(hue / 360, 0.6, 0.85)
        draw.ellipse([4, 4, 44, 44], fill=(int(r * 255), int(g * 255), int(b * 255)))
        bbox = draw.textbbox((0, 0), letter, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((24 - tw / 2, 24 - th / 2 - bbox[1]), letter, fill=(255, 255, 255), font=font)
        img.save(target, "PNG")


def main(argv: list[str] | None = None):
    args = _parse_args(argv)
    random.seed(args.seed)
    providers = _build_providers(args.providers, args.models_per)
    total_models = sum(len(model_ids) for _, _, model_ids in providers)
    favicon_ext = "png" if st.pillow_available else "svg"
    if not st.pillow_available:
        st.log.warning("Pillow not installed - generating SVG favicon placeholders")

    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.config_dir.mkdir(parents=True, exist_ok=True)
    db_path = args.data_dir / args.db_name
    models_yaml = args.config_dir / args.models_yaml
    app_yaml = args.config_dir / args.app_yaml
    favicon_dir = args.data_dir / FAVICON_DIR.name
    bench_interval = args.app_bench_interval or total_models * _APP_BENCH_SECONDS_PER_MODEL + _APP_BENCH_HEADROOM_S

    rows = _seed_db(db_path, providers, favicon_ext, args)
    _write_models_yaml(models_yaml, providers)
    _write_app_yaml(app_yaml, args.app_template, bench_interval, total_models, args.app_set)
    _write_favicons(favicon_dir, providers, favicon_ext)

    st.log.info("Done: %d providers, %d models, %d results", len(providers), total_models, rows)
    st.log.info("  Database:    %s (%.1f MB)", db_path, db_path.stat().st_size / 1024 / 1024)
    st.log.info("  Models YAML: %s", models_yaml)
    st.log.info("  App YAML:    %s", app_yaml)
    st.log.info("  Favicons:    %s", favicon_dir)
    # MW_DB_NAME and MW_*_YAML take a name inside data/ and config/, or an absolute path
    in_place = (args.data_dir.resolve(), args.config_dir.resolve()) == (st.DATA_DIR, st.CONFIG_DIR)
    st.log.info(
        "Start the server with: MW_DB_NAME=%s MW_MODELS_YAML=%s MW_APP_YAML=%s %s=dummy MW_DISABLE_TESTS=1 "
        "PORT=8080 python3 -m backend.main",
        *((args.db_name, args.models_yaml, args.app_yaml) if in_place else (db_path.resolve(), models_yaml.resolve(), app_yaml.resolve())),
        API_KEY_ENV,
    )


if __name__ == "__main__":
    main()
