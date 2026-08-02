"""Audit test runner - executes third-party compliance test suites.

Runs external CLI tools (currently SynBad) against model API endpoints
and records pass/fail results. Audit results are informational only -
they do not affect model status or trigger notifications.

Each suite is configured under ``suites`` in audits.yaml. The suite
runner is selected by suite name; new suites are added by extending
``_SUITE_RUNNERS`` and ``_SUITE_CHECKS``.

Reasoning evals: skip_reasoning is auto-detected from model_info_cache
(the ``thinking`` field) - models with thinking=true run reasoning
evals, others skip them. Set skip_reasoning explicitly in audits.yaml
to override auto-detection.
"""

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import backend.state as st
from backend.models import get_provider_for
from backend.state import parse_model_key

_SUITE_RUNNERS: dict[str, Callable[[dict, str, str], Awaitable[dict]]] = {}
_SUITE_CHECKS: dict[str, Callable[[], bool]] = {}

_MAX_ERROR_LEN = 300
_MAX_RESPONSE_LEN = 4000


def _find_synbad_bin() -> str:
    """Resolve the SynBad binary path.

    Priority:
    1. SYNBAD_BIN environment variable
    2. 'synbad' on PATH (shutil.which)
    3. Local node_modules/.bin/synbad relative to project root
    """
    explicit = os.environ.get("SYNBAD_BIN")
    if explicit:
        return explicit
    import shutil
    on_path = shutil.which("synbad")
    if on_path:
        return on_path
    return str(Path(__file__).resolve().parent.parent / "node_modules" / ".bin" / "synbad")


_SYNBAD_BIN = _find_synbad_bin()

# SynBad CLI output format (stdout/stderr) that _parse_synbad_output parses:
#   stdout: "Running <name>... ✅ passed\n"           (pass, single line)
#   stderr: "Response:\n{...JSON...}\n<ErrorType>: <msg>\n<continuation>\n    at ...\n❌ <name> failed"  (fail)
#   stdout: "✅ All evals passed!"                   (all-pass summary)
#   stdout: "N/M evals passed. Failures:\n- <name>" (partial summary)
_EVAL_PASSED_RE = re.compile(r"Running\s+([\w/\-]+)\.\.\..*✅\s*passed")
_EVAL_FAILED_RE = re.compile(r"❌\s+([\w/\-]+)\s+failed")
_ERR_MSG_RE = re.compile(r"^(\w+Error(?:\s+\[\w+\])?)\s*:\s*(.+)")
_SUMMARY_RE = re.compile(r"(\d+)/(\d+)\s+evals\s+passed")
_RESPONSE_RE = re.compile(r"^Response:\s*$")

_ERR_BASE = {"suite": "synbad", "passed": 0, "total": 0, "pass_rate": 0.0, "evals": [], "success": False}


def _model_has_thinking(model_key: str) -> bool:
    """Check model_info_cache for reasoning/thinking capability."""
    info = st.model_info_cache.get(model_key)
    if not info:
        return False
    val = info.get("thinking")
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "1", "yes", "enabled")


def _synbad_skip_reasoning(model_key: str) -> bool:
    """Decide whether to skip reasoning evals for a model.

    Explicit config override takes priority; otherwise auto-detect from
    model_info. Returns True to skip reasoning evals.
    """
    suite_cfg = st.c.audit_suites.get("synbad", {})
    explicit = suite_cfg.get("skip_reasoning")
    if explicit is not None:
        return explicit
    return not _model_has_thinking(model_key)


def _get_synbad_version() -> str | None:
    """Read synbad version from its package.json.

    Handles both nvm global installs (bin → lib/node_modules/@syntheticlab/synbad/)
    and local node_modules installs. Falls back to the version recorded in
    audits.yaml config if the package.json cannot be located.
    """
    try:
        bin_path = Path(_SYNBAD_BIN).resolve()
        for parent in bin_path.parents:
            pkg = parent / "@syntheticlab" / "synbad" / "package.json"
            if pkg.is_file():
                with open(pkg) as f:
                    return json.load(f).get("version")
        pkg = bin_path.parent.parent / "package.json"
        if pkg.is_file():
            with open(pkg) as f:
                d = json.load(f)
                if d.get("name") == "@syntheticlab/synbad":
                    return d.get("version")
    except Exception:
        st.log_error("Failed to read synbad version from package.json")
    return st.c.audit_suites.get("synbad", {}).get("version")


def _parse_tool_args(response: dict) -> dict:
    """Parse tool_calls.function.arguments from JSON strings to objects.

    Adds explicit None/[] for missing content/tool_calls keys so downstream
    consumers can rely on the fields always being present.
    """
    if "content" not in response:
        response["content"] = None
    tool_calls = response.get("tool_calls")
    if tool_calls is None:
        response["tool_calls"] = []
        return response
    if not isinstance(tool_calls, list):
        return response
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            continue
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                fn["arguments"] = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                pass
    return response


def _parse_synbad_output(stdout: str, stderr: str) -> dict:
    evals = []
    passed = 0
    total = 0

    for line in stdout.splitlines():
        m = _EVAL_PASSED_RE.match(line.strip())
        if m:
            evals.append({"name": m.group(1), "passed": True})
            passed += 1
            total += 1

    # Parse failures from stderr. SynBad emits per-failure blocks where a
    # "Response:" marker starts a JSON blob, followed by an error line and
    # optional continuation, then a "❌ <name> failed" terminator.
    current_error = None
    current_response = None
    in_response = False
    in_error_cont = False
    response_brace_depth = 0
    response_buf = []

    for line in stderr.splitlines():
        stripped = line.strip()
        m = _EVAL_FAILED_RE.match(stripped)
        if m:
            in_error_cont = False
            eval_entry = {"name": m.group(1), "passed": False, "error": current_error}
            if current_response is not None:
                eval_entry["response"] = current_response
            evals.append(eval_entry)
            total += 1
            current_error = None
            current_response = None
            in_response = False
            response_brace_depth = 0
            response_buf = []
            continue
        if _RESPONSE_RE.match(stripped):
            in_error_cont = False
            in_response = True
            response_buf = []
            response_brace_depth = 0
            continue
        if in_response:
            response_buf.append(line)
            response_brace_depth += line.count("{") - line.count("}")
            if response_brace_depth <= 0:
                raw = "\n".join(response_buf)
                try:
                    current_response = _parse_tool_args(json.loads(raw))
                except (json.JSONDecodeError, ValueError):
                    current_response = raw[:_MAX_RESPONSE_LEN]
                in_response = False
                response_buf = []
                response_brace_depth = 0
            continue
        em = _ERR_MSG_RE.match(stripped)
        if em:
            current_error = f"{em.group(1)}: {em.group(2)}"[:_MAX_ERROR_LEN]
            in_error_cont = True
            continue
        if in_error_cont:
            if not stripped:
                continue
            if stripped.startswith("at "):
                in_error_cont = False
                continue
            if current_error and len(current_error) < _MAX_ERROR_LEN - 10:
                current_error = f"{current_error} {stripped}"[:_MAX_ERROR_LEN]

    m = _SUMMARY_RE.search(stdout)
    if m:
        passed = int(m.group(1))
        total = int(m.group(2))

    evals.sort(key=lambda e: e["name"])

    suite_error = None
    if passed < total:
        failed_names = [e["name"] for e in evals if not e.get("passed")]
        suite_error = "; ".join(failed_names) if failed_names else None

    return {
        "suite": "synbad",
        "passed": passed,
        "total": total,
        "pass_rate": round(passed / total, 4) if total > 0 else 0.0,
        "evals": evals,
        "success": passed == total and total > 0,
        "error": suite_error,
    }


async def _run_synbad_suite(provider: dict, model_id: str, model_key: str) -> dict:
    """Run SynBad eval suite via CLI subprocess."""
    if not os.path.isfile(_SYNBAD_BIN):
        return {**_ERR_BASE, "error": "synbad not installed"}

    api_key = provider.get("api_key", "")
    base_url = provider.get("api_url", "").rstrip("/")

    env = os.environ.copy()
    env["MW_AUDIT_API_KEY"] = api_key

    suite_cfg = st.c.audit_suites.get("synbad", {})
    stream = suite_cfg.get("stream", True)
    skip_reasoning = _synbad_skip_reasoning(model_key)
    count = suite_cfg.get("count", 1)
    reasoning_effort = suite_cfg.get("reasoning_effort")
    only = suite_cfg.get("only")

    params = {
        "stream": stream,
        "skip_reasoning": skip_reasoning,
        "count": count,
    }
    if reasoning_effort:
        params["reasoning_effort"] = reasoning_effort
    if only:
        params["only"] = only

    cmd = [_SYNBAD_BIN, "eval",
           "--env-var", "MW_AUDIT_API_KEY",
           "--base-url", base_url,
           "--model", model_id]
    if stream:
        cmd.append("--stream")
    if skip_reasoning:
        cmd.append("--skip-reasoning")
    if count and count > 1:
        cmd.extend(["--count", str(count)])
    if reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])
    if only:
        cmd.extend(["--only", only])

    version = _get_synbad_version()

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=st.c.test_timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        duration_ms = round((time.monotonic() - start) * 1000)
        return {**_ERR_BASE, "duration_ms": duration_ms, "error": "timeout",
                "suite_version": version, "params": params}
    except (FileNotFoundError, PermissionError) as exc:
        duration_ms = round((time.monotonic() - start) * 1000)
        return {**_ERR_BASE, "duration_ms": duration_ms, "error": f"synbad: {exc}",
                "suite_version": version, "params": params}

    duration_ms = round((time.monotonic() - start) * 1000)
    result = _parse_synbad_output(
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )
    result["duration_ms"] = duration_ms
    result["suite_version"] = version
    result["params"] = params
    if result["evals"]:
        failed = [e for e in result["evals"] if not e.get("passed")]
        if failed:
            for e in failed:
                st.log.warning("SynBad %s: %s failed - %s", model_id, e["name"], e.get("error", "unknown"))
        else:
            st.log.debug("SynBad %s: %d/%d passed", model_id, result["passed"], result["total"])
    elif result.get("error"):
        st.log.warning("SynBad %s: %s", model_id, result["error"])
    return result


_SUITE_RUNNERS["synbad"] = _run_synbad_suite


def _synbad_available() -> bool:
    return os.path.isfile(_SYNBAD_BIN)


_SUITE_CHECKS["synbad"] = _synbad_available

_audit_unavailable_logged = False


def audit_available() -> bool:
    """Check whether at least one enabled audit suite has its dependency installed.

    Returns False when audit is disabled or no suite runner is ready. Logs a
    warning once when audit is enabled but no suite is available, then suppresses
    repeated logs to avoid log spam.
    """
    global _audit_unavailable_logged
    if not st.c.audit_enabled:
        return False
    any_enabled = False
    for suite_name, suite_cfg in st.c.audit_suites.items():
        if not suite_cfg.get("enabled", False):
            continue
        any_enabled = True
        check = _SUITE_CHECKS.get(suite_name)
        if check and check():
            return True
    if any_enabled and not _audit_unavailable_logged:
        suites = [s for s, c in st.c.audit_suites.items() if c.get("enabled", False)]
        st.log.warning("Audit enabled but no suite runner is available (suites: %s). "
                       "Install missing dependencies (e.g., npm install).", suites)
        _audit_unavailable_logged = True
    return False


async def run_audit_test(model_key: str) -> dict | None:
    """Run enabled audit suites for a model. Returns per-suite results or None on error."""
    if not st.c.audit_enabled:
        return None
    provider = get_provider_for(model_key)
    if not provider:
        return None

    _, model_id = parse_model_key(model_key)
    suite_results = []

    for suite_name, suite_cfg in st.c.audit_suites.items():
        if not suite_cfg.get("enabled", False):
            continue
        runner = _SUITE_RUNNERS.get(suite_name)
        if not runner:
            continue
        result = await runner(provider, model_id, model_key)
        if result is None:
            continue
        url = suite_cfg.get("url")
        if url:
            result["url"] = url
        suite_results.append(result)

    if not suite_results:
        return None

    total_passed = 0
    total_count = 0
    errors = []
    for r in suite_results:
        total_passed += r.get("passed", 0)
        total_count += r.get("total", 0)
        if r.get("error"):
            errors.append(r["error"])

    ts_epoch = time.time()
    return {
        "suites": suite_results,
        "passed": total_passed,
        "total": total_count,
        "pass_rate": round(total_passed / total_count, 4) if total_count > 0 else 0.0,
        "success": all(r.get("success", False) for r in suite_results),
        "error": "; ".join(errors) if errors else None,
        "duration_ms": sum(r.get("duration_ms", 0) for r in suite_results),
        "ts_epoch": ts_epoch,
    }
