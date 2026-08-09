"""Capability probe for LLM models.

Sends lightweight non-streaming completion requests to detect model
features: tool calling, vision/image input, JSON structured output,
reasoning/thinking, and prompt caching. Also extracts the inference
engine, version, tensor parallelism (from system_fingerprint), and
the actual served model name (from the response model field).

Probes run concurrently (tools, vision, json_mode in parallel) and are
informational only - they do not affect model status.
"""

import asyncio
import json
import time

import backend.state as st
from backend.state import log_error, parse_fingerprint

_VISION_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAADIAAAAyCAIAAACRXR/mAAACh0lEQVR4nO2WPWvyUBSAb8TFoVWKtGNHhYJCSmoMWCxqBd0cRIqCP8AWRUQcXEs7ZOwg/gJRQXAKloKD34KKdLFUEBdXP7CI2vMOF0KhCl544X2H+0znnHvPzZPkBMIAAPr/UPxrgd1QLRKoFglUiwSqRQLVImGHlkql8nq9cur3+1UqFUIoGAxms1lcNBqNkUgEx+FwOJfL+Xw+q9VqtVoFQdBqtQghSZI8Hg/e8/7+fnV1td1uD/WCX6jVaoPBsNlsAOD7+5vnebVaDQCpVCoWiwHAbDZjWdZsNuP9PM9PJhO5PZ1OJ5NJHLvd7nK5DAAOh6NWq/2+1j52v0SWZVutFkKo2+0aDAZcFASh3W4jhOr1usvlWi6Xq9VqvV4vl8uzszP5Jl9eXkKhEE5FUYzH4/l8/vz8nOf5Qx/VvtlyOp2SJCGEJElyOp24eHFxMRwOAaBarVoslsvLy06n0+12OY6TG4vFIsdxp6enONXpdCaTKRKJPD09He60V+v29vb19RUh9Pb2ZrfbcZFhGL1ePxgMms2m2WwWBKFerzcajevra7lRFMVoNPrzqNlsplQqF4vFX9A6OTlRKBTj8RghdHx8LNcFQWg2m19fX0dHR7KWxWLBq41GQ6PR6HQ6eX+lUplOp6lU6uHhgUhr98gDwOPjYyAQeH5+lisAUCqVbDbb/f09/hpYlr25uZEbPR4PHnDMer3mOO7z8xMvFQqFw0d+r1av12MYpt/v/9Saz+dKpTKTyeDU7Xbf3d3h+OPjg+f5n+eIophIJHA8Go30ev1isThQiwH6L384VIsEqkUC1SKBapFAtUigWiRQLRKoFglUiwSqRQLVIoFqkUC1SKBaJPwBQwUAFsynccUAAAAASUVORK5CYII="

_VISION_SECRET = "MW7X"  # embedded in _VISION_PNG_B64; the model must read this to pass
_VISION_PNG_URI = f"data:image/png;base64,{_VISION_PNG_B64}"

_BASIC_TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Return the current time",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

_ANTHROPIC_BASIC_TOOL = {
    "name": "get_time",
    "description": "Return the current time",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def _is_anthropic(api_url: str) -> bool:
    return "anthropic" in api_url.lower()


def _build_headers(api_key: str, is_anthropic: bool, custom_headers: dict | None = None) -> dict:
    if is_anthropic:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    else:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
    if custom_headers:
        headers.update(custom_headers)
    return headers


def _resolve_token_param(provider_cfg: dict) -> str:
    req_opts = {**provider_cfg.get("request_options", {})}
    model_opts = provider_cfg.get("_model_request_options")
    if model_opts:
        req_opts.update(model_opts)
    return req_opts.get("token_param", "both")


def _resolve_max_tokens(provider_cfg: dict, default: int) -> int:
    """Resolve the output-token budget, honoring a per-model/per-provider
    `request_options.max_tokens` override. Used by probes so reasoning models
    with a larger budget requirement (NanoGPT-style gateways refuse tiny
    ceilings with `empty_response`) don't fail detection probes.
    """
    req_opts = {**provider_cfg.get("request_options", {})}
    model_opts = provider_cfg.get("_model_request_options")
    if model_opts:
        req_opts.update(model_opts)
    override = req_opts.get("max_tokens")
    return override if override else default


def _apply_token_params(body: dict, max_tokens: int, token_param: str) -> None:
    if token_param != "legacy":
        body["max_completion_tokens"] = max_tokens
    if token_param != "completion":
        body["max_tokens"] = max_tokens


def _base_url(api_url: str) -> str:
    return api_url.rstrip("/")


def _provider_for(model_key: str) -> dict | None:
    import backend.models as models
    return models.get_provider_for(model_key)


async def _probe_request(client, url: str, headers: dict, body: dict, timeout: float) -> tuple[dict | None, int | None]:
    try:
        resp = await client.post(url, headers=headers, json=body, timeout=timeout)
        if resp.status_code == 200:
            return resp.json(), 200
        return None, resp.status_code
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log_error(f"Probe request failed for {url}", e)
        return None, None


async def _probe_tools(client, base_url: str, headers: dict, model_id: str,
                       timeout: float, max_tokens: int, is_anthropic: bool,
                       token_param: str) -> tuple[bool | None, dict | None]:
    if is_anthropic:
        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "tools": [_ANTHROPIC_BASIC_TOOL],
            "messages": [{"role": "user", "content": "What time is it?"}],
        }
        resp, _ = await _probe_request(client, f"{base_url}/messages", headers, body, timeout)
    else:
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": "What time is it?"}],
            "tools": [_BASIC_TOOL],
        }
        _apply_token_params(body, max_tokens, token_param)
        resp, _ = await _probe_request(client, f"{base_url}/chat/completions", headers, body, timeout)
    if resp is None:
        return None, None
    if is_anthropic:
        content = resp.get("content", [])
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return True, resp
        return False, resp
    tool_calls = None
    choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        tool_calls = msg.get("tool_calls")
    return bool(tool_calls), resp


def _vision_response_text(resp: dict, is_anthropic: bool) -> str:
    if not isinstance(resp, dict):
        return ""
    if is_anthropic:
        parts = []
        for block in resp.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text", "")
                if isinstance(t, str):
                    parts.append(t)
        return " ".join(parts).lower()
    choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        c0 = choices[0]
        if isinstance(c0, dict):
            msg = c0.get("message", {})
            if isinstance(msg, dict):
                c = msg.get("content", "")
                rc = msg.get("reasoning_content", msg.get("reasoning", ""))
                parts = []
                if isinstance(c, str):
                    parts.append(c)
                if isinstance(rc, str):
                    parts.append(rc)
                return " ".join(parts).lower()
    return ""


async def _probe_vision(client, base_url: str, headers: dict, model_id: str,
                        timeout: float, max_tokens: int, is_anthropic: bool,
                        token_param: str) -> tuple[bool | None, dict | None]:
    prompt = "Read the text in this image. Output only the text, nothing else."
    if is_anthropic:
        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _VISION_PNG_B64}},
                ],
            }],
        }
        resp, _ = await _probe_request(client, f"{base_url}/messages", headers, body, timeout)
    else:
        body = {
            "model": model_id,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _VISION_PNG_URI}},
                ],
            }],
        }
        _apply_token_params(body, max_tokens, token_param)
        resp, _ = await _probe_request(client, f"{base_url}/chat/completions", headers, body, timeout)
    if resp is None:
        return None, None
    text = _vision_response_text(resp, is_anthropic)
    if _VISION_SECRET.lower() in text:
        return True, resp
    return False, resp


def _looks_like_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, TypeError, ValueError):
        return False


async def _probe_json_mode(client, base_url: str, headers: dict, model_id: str,
                           timeout: float, max_tokens: int, is_anthropic: bool,
                           token_param: str) -> tuple[bool | None, dict | None]:
    if is_anthropic:
        body = {
            "model": model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": "Return a JSON object with key ok set to true. Output ONLY valid JSON."}],
            "tools": [{
                "name": "json_output",
                "description": "Return structured JSON output",
                "input_schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                },
            }],
        }
        resp, status = await _probe_request(client, f"{base_url}/messages", headers, body, timeout)
    else:
        body = {
            "model": model_id,
            "messages": [{"role": "user", "content": "Return a JSON object with key 'ok' set to true."}],
            "response_format": {"type": "json_object"},
        }
        _apply_token_params(body, max_tokens, token_param)
        resp, status = await _probe_request(client, f"{base_url}/chat/completions", headers, body, timeout)
    if resp is None:
        if status is not None and 400 <= status < 500:
            return False, None
        return None, None
    if is_anthropic:
        blocks = resp.get("content", [])
        has_tool = any(b.get("type") == "tool_use" for b in blocks if isinstance(b, dict))
        has_json = any(b.get("type") == "text" and _looks_like_json(b.get("text", "")) for b in blocks if isinstance(b, dict))
        return has_tool or has_json, resp
    try:
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        return _looks_like_json(content), resp
    except (IndexError, KeyError, TypeError):
        return False, resp


def _detect_reasoning(resp: dict, is_anthropic: bool = False) -> str | None:
    if not isinstance(resp, dict):
        return None
    if is_anthropic:
        content = resp.get("content", [])
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                return "thinking_block"
        return None
    choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        if isinstance(msg, dict):
            if msg.get("reasoning") is not None:
                return "reasoning"
            if msg.get("reasoning_content") is not None:
                return "reasoning_content"
    usage = resp.get("usage", {})
    if isinstance(usage, dict):
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict) and details.get("reasoning_tokens"):
            return "hidden"
    return None


def _detect_cache(resp: dict, is_anthropic: bool = False) -> bool | None:
    usage = resp.get("usage", {})
    if not isinstance(usage, dict):
        return None
    if is_anthropic:
        for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
            v = usage.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return True
        return None
    ptd = usage.get("prompt_tokens_details")
    if isinstance(ptd, dict):
        ct = ptd.get("cached_tokens")
        if isinstance(ct, (int, float)) and ct > 0:
            return True
    cr = usage.get("cache_read_input_tokens")
    if isinstance(cr, (int, float)) and cr > 0:
        return True
    ph = usage.get("prompt_cache_hit_tokens")
    if isinstance(ph, (int, float)) and ph > 0:
        return True
    return None


def _extract_response_meta(resp: dict, is_anthropic: bool = False) -> dict:
    meta = {}
    if not isinstance(resp, dict):
        return meta
    if is_anthropic:
        usage = resp.get("usage", {})
        if isinstance(usage, dict):
            for key in ("cache_read_input_tokens", "cache_creation_input_tokens"):
                v = usage.get(key)
                if isinstance(v, (int, float)) and v > 0:
                    meta[key] = v
        model = resp.get("model")
        if isinstance(model, str) and model:
            meta["response_model"] = model
        return meta
    sf = resp.get("system_fingerprint")
    if isinstance(sf, str) and sf:
        meta["system_fingerprint"] = sf
    model = resp.get("model")
    if isinstance(model, str) and model:
        meta["response_model"] = model
    choices = resp.get("choices")
    if isinstance(choices, list) and choices:
        c0 = choices[0]
        if isinstance(c0, dict):
            fr = c0.get("finish_reason")
            if isinstance(fr, str):
                meta["finish_reason"] = fr
            msg = c0.get("message", {})
            if isinstance(msg, dict):
                if "reasoning" in msg:
                    meta["reasoning_field"] = "reasoning"
                elif "reasoning_content" in msg:
                    meta["reasoning_field"] = "reasoning_content"
    usage = resp.get("usage", {})
    if isinstance(usage, dict):
        ptd = usage.get("prompt_tokens_details")
        if isinstance(ptd, dict):
            ct = ptd.get("cached_tokens")
            if isinstance(ct, (int, float)) and ct > 0:
                meta["cached_tokens"] = ct
        cr = usage.get("cache_read_input_tokens")
        if isinstance(cr, (int, float)) and cr > 0:
            meta["cache_read_input_tokens"] = cr
        ph = usage.get("prompt_cache_hit_tokens")
        if isinstance(ph, (int, float)) and ph > 0:
            meta["prompt_cache_hit_tokens"] = ph
    return meta


async def run_probe_test(model_key: str) -> dict:
    """Run all capability probes for a model and return the aggregated result.

    Returns a dict with success flag, detected capabilities (supports_tools,
    supports_vision, supports_structured_output, supports_cache, thinking),
    response metadata (system_fingerprint, served_model), and duration_ms.
    Returns early with an error dict if no provider config or api_url exists.
    """
    provider_cfg = _provider_for(model_key)
    if not provider_cfg:
        return {"error": "no provider config", "success": False}

    api_url = provider_cfg.get("api_url", "")
    if not api_url:
        return {"error": "no api_url", "success": False}

    api_key = provider_cfg.get("api_key", "")
    model_id = provider_cfg.get("model_id", "")
    custom_headers = provider_cfg.get("headers")
    base = _base_url(api_url)
    is_a = _is_anthropic(api_url)
    headers = _build_headers(api_key, is_a, custom_headers)
    client = st.get_http_client()
    timeout = st.c.test_timeout
    max_tokens = _resolve_max_tokens(provider_cfg, st.c.probe_max_tokens)
    token_param = _resolve_token_param(provider_cfg)

    t0 = time.monotonic()
    result = {
        "model_key": model_key,
        "provider": provider_cfg.get("name", ""),
        "success": True,
        "error": None,
    }

    supports_tools = None
    supports_vision = None
    supports_json = None
    thinking = None
    reasoning_field = None
    supports_cache = None
    system_fingerprint = None
    response_meta = {}
    served_model = None
    probes_attempted = 3
    tools_r, vision_r, json_r = await asyncio.gather(
        _probe_tools(client, base, headers, model_id, timeout, max_tokens, is_a, token_param),
        _probe_vision(client, base, headers, model_id, timeout, max_tokens, is_a, token_param),
        _probe_json_mode(client, base, headers, model_id, timeout, max_tokens, is_a, token_param),
        return_exceptions=True,
    )
    probes_succeeded = sum(1 for r in (tools_r, vision_r, json_r) if not isinstance(r, Exception))

    _names = ("tools", "vision", "json_mode")
    _unwrapped = []
    for _name, _r in zip(_names, (tools_r, vision_r, json_r)):
        if isinstance(_r, Exception):
            log_error(f"Probe {_name} failed for {model_key}", _r)
            _unwrapped.append((None, None))
        elif isinstance(_r, BaseException):
            raise _r
        else:
            _unwrapped.append(_r)
    (tools_ok, tools_resp), (vision_ok, vision_resp), (json_ok, json_resp) = _unwrapped

    supports_tools = tools_ok
    if tools_resp:
        thinking = _detect_reasoning(tools_resp, is_a)
        cache_detected = _detect_cache(tools_resp, is_a)
        if cache_detected is not None:
            supports_cache = cache_detected
        meta = _extract_response_meta(tools_resp, is_a)
        response_meta.update(meta)
        sf = meta.get("system_fingerprint")
        if sf:
            system_fingerprint = sf
        rf = meta.get("reasoning_field")
        if rf:
            reasoning_field = rf
        rm = meta.get("response_model")
        if rm:
            served_model = rm

    supports_json = json_ok
    if json_resp:
        if thinking is None:
            thinking = _detect_reasoning(json_resp, is_a)
        if supports_cache is None:
            cache_detected = _detect_cache(json_resp, is_a)
            if cache_detected is not None:
                supports_cache = cache_detected
        meta = _extract_response_meta(json_resp, is_a)
        for k, v in meta.items():
            if k not in response_meta:
                response_meta[k] = v
        if not served_model and meta.get("response_model"):
            served_model = meta["response_model"]

    supports_vision = vision_ok
    if vision_resp:
        meta = _extract_response_meta(vision_resp, is_a)
        for k, v in meta.items():
            if k not in response_meta:
                response_meta[k] = v
        if not served_model and meta.get("response_model"):
            served_model = meta["response_model"]

    elapsed = time.monotonic() - t0

    if probes_attempted > 0 and probes_succeeded == 0:
        result["success"] = False

    result["supports_tools"] = supports_tools
    result["supports_vision"] = supports_vision
    result["supports_structured_output"] = supports_json
    result["supports_cache"] = supports_cache
    result["thinking"] = bool(thinking) or reasoning_field is not None
    result["reasoning_field"] = reasoning_field or thinking
    result["system_fingerprint"] = system_fingerprint
    result["response_meta"] = response_meta or None
    result["duration_ms"] = round(elapsed * 1000, 1)

    fp = parse_fingerprint(system_fingerprint)
    if fp:
        result["served_by"] = fp.get("engine")
        if "engine_version" in fp:
            result["engine_version"] = fp["engine_version"]
        if "tensor_parallel" in fp:
            result["tensor_parallel"] = fp["tensor_parallel"]
        if "quantization" in fp:
            result["quantization"] = fp["quantization"]
        if "fp_server" in fp:
            result["fp_server"] = fp["fp_server"]
        if "fp_features" in fp:
            result["fp_features"] = fp["fp_features"]

    if served_model and served_model != model_id:
        result["served_model"] = served_model

    return result

