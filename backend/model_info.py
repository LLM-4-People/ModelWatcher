"""Fetch and cache LLM model metadata from provider APIs and HuggingFace.

Provider-agnostic design: each model object from any provider API is flattened
into dotted field paths (e.g. ``metadata.pricing.input_per_million``), then
matched against a registry of known patterns that map to canonical field names
with optional transforms (unit conversion, type coercion, list-contains).

This eliminates per-provider parser functions and ``detect_provider_type()``.
New providers work automatically as long as their field paths match known
patterns; unknown paths are silently ignored.
"""

import asyncio
import re

import backend.state as st
from backend.state import MODEL_INFO_FIELDS as _METADATA_FIELDS, log, log_error

_HF_SEARCH_URL = "https://huggingface.co/api/models"
_HF_MODEL_URL = "https://huggingface.co/api/models/{model_id}"
_HF_README_URL = "https://huggingface.co/{model_id}/raw/main/README.md"

_SERVED_BY_NAMES = frozenset({
    "vllm", "tgi", "triton", "ollama", "llamacpp", "llama.cpp",
    "text-generation-inference", "deepspeed", "tensorflow-serving",
    "torchserve", "kserve", "sagemaker", "modal", "lepton", "sglang",
})

_HF_FIELDS = frozenset((
    "description", "license", "owner", "context_window", "architecture",
    "quantization", "param_count", "tokenizer", "created",
))

_HF_ORG_PREFIXES = (
    "mistralai", "meta-llama", "google", "openai", "anthropic",
    "deepseek-ai", "Qwen", "CohereForAI", "mistral",
    "facebook", "microsoft", "stabilityai", "bigscience",
    "THUDM", "xiaomi", "minimaxai", "moonshotai", "zai-org",
    "deepseek", "nvidia", "NousResearch", "tencent", "Salesforce",
    "ibm-granite", "arcee-ai", "stepfun-ai", "amazon", "baidu",
    "upstage", "allenai", "Alibaba-NLP", "baseten",
)

_hf_canonical_cache: dict[str, dict] = {}
_hf_prefix_hint: dict[str, str] = {}

_FETCH_CONCURRENCY = 6


def _fmt_params(n: int) -> str:
    if n >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.1f}T"
    if n >= 1_000_000_000:
        b = n / 1_000_000_000
        return f"{b:.0f}B" if b == int(b) else f"{b:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    return str(n)


def _as_int(v) -> int | None:
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _as_float(v) -> float | None:
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _as_bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return None


def _list_contains(haystack, needle: str) -> bool | None:
    if not isinstance(haystack, list):
        return None
    return needle in haystack


def _list_join(v) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return None


def _per_token_to_per_m(v) -> float | None:
    f = _as_float(v)
    if f is not None:
        return f * 1_000_000
    return None


def _cents_to_dollar(v) -> float | None:
    f = _as_float(v)
    if f is not None:
        return f / 100
    return None


def _per_1k_to_per_m(v) -> float | None:
    f = _as_float(v)
    if f is not None:
        return f * 1000
    return None


def _owned_by_to_served_by_or_owner(v) -> dict | None:
    if not isinstance(v, str) or not v:
        return None
    if v.lower() in _SERVED_BY_NAMES:
        return {"served_by": v}
    return {"owner": v}


def _list_contains_tools(v) -> bool | None:
    result = _list_contains(v, "tools")
    return result if result is True else None


def _input_modalities_contains_image(v) -> bool | None:
    result = _list_contains(v, "image")
    return result if result is True else None


def _capabilities_list_has_vision(v) -> bool | None:
    if isinstance(v, list) and "vision" in v:
        return True
    return None


def _capabilities_list_has_tools(v) -> bool | None:
    if isinstance(v, list) and "tools" in v:
        return True
    return None


def _capabilities_list_has_thinking(v) -> str | None:
    if isinstance(v, list) and "thinking" in v:
        return "enabled"
    return None


def _extract_modality_str(v) -> str | None:
    return _list_join(v)


def _thinking_from_bool(v) -> str | None:
    b = _as_bool(v)
    if b is True:
        return "enabled"
    return None


_QUANT_ALIASES = {
    "compressed-tensors": "CT",
    "compressed_tensors": "CT",
    "gptq": "GPTQ",
    "awq": "AWQ",
    "gguf": "GGUF",
    "bitsandbytes": "BnB",
    "bnb": "BnB",
    "fp8": "FP8",
    "int8": "INT8",
    "int4": "INT4",
    "nf4": "NF4",
    "marlin": "Marlin",
    "moe": "MoE",
    "aqlm": "AQLM",
    "hqq": "HQQ",
    "eetq": "EETQ",
    "quanto": "Quanto",
    "fbgemm": "FBGEMM",
    "bitblas": "BitBLAS",
}


def _quantization_from_str(v) -> str | None:
    if not isinstance(v, str) or not v:
        return None
    if v.lower() in ("none", "fp32", "fp16", "bf16", ""):
        return None
    return _QUANT_ALIASES.get(v.lower(), v)


def _param_count_from_str(v) -> str | None:
    if not isinstance(v, str) or not v:
        return None
    n = _as_int(v.replace(",", ""))
    if n is not None and n > 0:
        return _fmt_params(n)
    try:
        val = float(v)
        if val > 0:
            return _fmt_params(int(val))
    except (ValueError, TypeError):
        pass
    return v if v else None


def _flatten(obj, prefix: str = "") -> dict[str, object]:
    result = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            child_prefix = f"{prefix}{k}." if prefix else f"{k}."
            if isinstance(v, dict):
                result.update(_flatten(v, child_prefix))
            elif isinstance(v, list):
                for i, item in enumerate(v):
                    idx_prefix = f"{prefix}{k}[{i}]."
                    if isinstance(item, dict):
                        result.update(_flatten(item, idx_prefix))
                    else:
                        result[f"{prefix}{k}[{i}]"] = item
                result[f"{prefix}{k}"] = v
            else:
                result[f"{prefix}{k}"] = v
    return result


class FieldRule:
    __slots__ = ("pattern", "canonical", "transform", "priority")

    def __init__(self, pattern: str, canonical: str, transform=None, priority: int = 0):
        self.pattern = pattern
        self.canonical = canonical
        self.transform = transform
        self.priority = priority


_SUFFIX_RULES: list[FieldRule] = [
    FieldRule("context_length", "context_window", _as_int, 1),
    FieldRule("max_context_length", "context_window", _as_int, 1),
    FieldRule("max_output_tokens", "output_context", _as_int, 1),
    FieldRule("general.architecture", "architecture", None, 1),
    FieldRule("general.parameter_count", "param_count",
              lambda v: _fmt_params(int(v)) if isinstance(v, (int, float)) and v > 0 else None, 1),
    FieldRule("embedding_length", "_skip", None, 0),
]


_RULES: list[FieldRule] = [
    FieldRule("context_length", "context_window", _as_int),
    FieldRule("context_window", "context_window", _as_int),
    FieldRule("context_size", "context_window", _as_int),
    FieldRule("max_model_len", "context_window", _as_int),
    FieldRule("top_provider.context_length", "context_window", _as_int, 1),
    FieldRule("metadata.limits.max_context_length", "context_window", _as_int, 1),
    FieldRule("wafer.context_length", "context_window", _as_int, 1),
    FieldRule("max_completion_tokens", "output_context", _as_int),
    FieldRule("max_output_tokens", "output_context", _as_int),
    FieldRule("top_provider.max_completion_tokens", "output_context", _as_int, 1),
    FieldRule("metadata.limits.max_output_tokens", "output_context", _as_int, 1),

    FieldRule("supports_vision", "supports_vision", _as_bool),
    FieldRule("supportsImageInput", "supports_vision", _as_bool),
    FieldRule("capabilities.vision", "supports_vision", _as_bool, 1),
    FieldRule("metadata.capabilities.vision", "supports_vision", _as_bool, 1),
    FieldRule("wafer.capabilities.vision", "supports_vision", _as_bool, 1),
    FieldRule("architecture.input_modalities", "supports_vision", _input_modalities_contains_image, 1),
    FieldRule("capabilities", "supports_vision", _capabilities_list_has_vision, 2),

    FieldRule("supports_tools", "supports_tools", _as_bool),
    FieldRule("supportsTools", "supports_tools", _as_bool),
    FieldRule("capabilities.function_calling", "supports_tools", _as_bool, 1),
    FieldRule("capabilities.tool_calling", "supports_tools", _as_bool, 1),
    FieldRule("capabilities.tools", "supports_tools", _as_bool, 1),
    FieldRule("capabilities.parallel_tool_calls", "supports_tools", _as_bool, 1),
    FieldRule("metadata.capabilities.tools", "supports_tools", _as_bool, 1),
    FieldRule("wafer.capabilities.tools", "supports_tools", _as_bool, 1),
    FieldRule("supported_features", "supports_tools", _list_contains_tools, 2),
    FieldRule("supported_parameters", "supports_tools", _list_contains_tools, 1),
    FieldRule("capabilities", "supports_tools", _capabilities_list_has_tools, 2),

    FieldRule("supports_cache", "supports_cache", _as_bool),
    FieldRule("supportsCache", "supports_cache", _as_bool),

    FieldRule("supports_structured_output", "supports_structured_output", _as_bool),
    FieldRule("capabilities.structured_output", "supports_structured_output", _as_bool, 1),
    FieldRule("metadata.capabilities.json_mode", "supports_structured_output", _as_bool, 1),
    FieldRule("supported_parameters", "supports_structured_output",
              lambda v: True if isinstance(v, list) and "response_format" in v else None, 1),

    FieldRule("pricing.prompt", "input_price", _per_token_to_per_m),
    FieldRule("pricing.input", "input_price", _per_token_to_per_m),
    FieldRule("metadata.pricing.input_per_million", "input_price", _as_float, 1),
    FieldRule("wafer.pricing.input_cents_per_million", "input_price", _cents_to_dollar, 1),

    FieldRule("pricing.completion", "output_price", _per_token_to_per_m),
    FieldRule("pricing.output", "output_price", _per_token_to_per_m),
    FieldRule("metadata.pricing.output_per_million", "output_price", _as_float, 1),
    FieldRule("wafer.pricing.output_cents_per_million", "output_price", _cents_to_dollar, 1),

    FieldRule("pricing.input_cache_read", "cache_price", _per_token_to_per_m),
    FieldRule("pricing.cached_input", "cache_price", _per_token_to_per_m),
    FieldRule("pricing.cacheReadInputPer1kTokens", "cache_price", _per_1k_to_per_m),
    FieldRule("wafer.pricing.cache_read_cents_per_million", "cache_price", _cents_to_dollar, 1),
    FieldRule("metadata.pricing.cached_input_per_million", "cache_price", _as_float, 1),

    FieldRule("pricing.internal_reasoning", "reasoning_price", _per_token_to_per_m),
    FieldRule("pricing.image", "image_price", _as_float),

    FieldRule("display_name", "display_name", None),
    FieldRule("displayName", "display_name", None),
    FieldRule("name", "display_name", None, 1),
    FieldRule("metadata.display_name", "display_name", None, 1),
    FieldRule("wafer.display_name", "display_name", None, 1),

    FieldRule("description", "description", None),
    FieldRule("metadata.description", "description", None, 1),
    FieldRule("wafer.description", "description", None, 1),

    FieldRule("created", "created", _as_float),
    FieldRule("owned_by", "owned_by", None),

    FieldRule("architecture.modality", "modalities", _extract_modality_str, 1),
    FieldRule("architecture.output_modalities", "modalities", _extract_modality_str),
    FieldRule("modalities", "modalities", _list_join),
    FieldRule("modality", "modalities", _list_join),

    FieldRule("architecture.tokenizer", "tokenizer", None),
    FieldRule("tokenizer", "tokenizer", None),

    FieldRule("license", "license", None),

    FieldRule("capabilities.reasoning", "thinking", _thinking_from_bool, 1),
    FieldRule("metadata.capabilities.reasoning", "thinking", _thinking_from_bool, 1),
    FieldRule("metadata.capabilities.reasoning_effort", "thinking",
              lambda v: "effort" if _as_bool(v) is True else None, 1),
    FieldRule("wafer.capabilities.reasoning", "thinking", _thinking_from_bool, 1),
    FieldRule("thinking", "thinking", _thinking_from_bool),
    FieldRule("supports_reasoning", "thinking", _thinking_from_bool),
    FieldRule("reasoning", "thinking", _thinking_from_bool),
    FieldRule("capabilities", "thinking", _capabilities_list_has_thinking, 2),

    FieldRule("details.quantization_level", "quantization", _quantization_from_str, 1),
    FieldRule("quantization", "quantization", _quantization_from_str),
    FieldRule("quant", "quantization", _quantization_from_str),
    FieldRule("precision", "quantization", _quantization_from_str),
    FieldRule("weight_format", "quantization", _quantization_from_str),

    FieldRule("details.family", "architecture", None, 1),
    FieldRule("model_type", "architecture", None),
    FieldRule("wafer.provider", "owner", None, 1),
    FieldRule("metadata.provider", "owner", None, 1),

    FieldRule("details.parameter_size", "param_count", _param_count_from_str, 1),
    FieldRule("total_params", "param_count", lambda v: _fmt_params(int(v)) if isinstance(v, (int, float)) and v > 0 else None),
    FieldRule("num_params", "param_count", lambda v: _fmt_params(int(v)) if isinstance(v, (int, float)) and v > 0 else None),
    FieldRule("parameter_count", "param_count", lambda v: _fmt_params(int(v)) if isinstance(v, (int, float)) and v > 0 else None),
    FieldRule("params", "param_count", lambda v: _fmt_params(int(v)) if isinstance(v, (int, float)) and v > 0 else None),

    FieldRule("served_by", "served_by", None),
    FieldRule("server", "served_by", None),
    FieldRule("backend", "served_by", None),
    FieldRule("engine", "served_by", None),
    FieldRule("inference_engine", "served_by", None),

    FieldRule("pricing.unit", "_pricing_unit", None),
    FieldRule("metadata.pricing.unit", "_pricing_unit", None),
    FieldRule("pricing.currency", "_skip", None),
    FieldRule("metadata.pricing.currency", "_skip", None),
    FieldRule("wafer.pricing.currency", "_skip", None),

    FieldRule("hugging_face_id", "_huggingface_id", None),
    FieldRule("metadata.huggingface_id", "_huggingface_id", None),

    FieldRule("num_experts", "num_experts", _as_int),
    FieldRule("n_routed_experts", "num_experts", _as_int),
    FieldRule("num_local_experts", "num_experts", _as_int),
    FieldRule("num_experts_per_tok", "num_experts_per_tok", _as_int),
    FieldRule("n_shared_experts", "num_shared_experts", _as_int),
    FieldRule("moe_intermediate_size", "moe_intermediate_size", _as_int),
]

_RULE_INDEX: dict[str, list[FieldRule]] = {}
for _r in _RULES:
    _RULE_INDEX.setdefault(_r.pattern, []).append(_r)


_PER_TOKEN_PRICING_PATHS = frozenset({
    "pricing.prompt", "pricing.input", "pricing.completion", "pricing.output",
    "pricing.input_cache_read", "pricing.cached_input",
})

_CACHE_PRICING_PATHS = frozenset({
    "pricing.input_cache_read", "pricing.cached_input",
    "pricing.cacheReadInputPer1kTokens",
    "wafer.pricing.cache_read_cents_per_million",
    "metadata.pricing.cached_input_per_million",
})


_SUFFIX_INDEX: dict[str, list[FieldRule]] = {}
for _sr in _SUFFIX_RULES:
    _SUFFIX_INDEX.setdefault(_sr.pattern, []).append(_sr)


def _apply_rule(rule: FieldRule, path: str, value, best: dict, per_token_pricing_used: set,
                pricing_unit_holder: list) -> str | None:
    canonical = rule.canonical

    if canonical == "_skip":
        return None
    if canonical == "_pricing_unit":
        pricing_unit_holder[0] = value
        return None
    if canonical == "_huggingface_id":
        return None
    if canonical == "owned_by":
        mapped = _owned_by_to_served_by_or_owner(value)
        if mapped:
            for k, v in mapped.items():
                existing = best.get(k)
                if existing is None or 0 > existing[0]:
                    best[k] = (0, v)
        return None

    if rule.transform:
        transformed = rule.transform(value)
        if transformed is None:
            return None
    else:
        transformed = value

    existing = best.get(canonical)
    if existing is None or rule.priority > existing[0]:
        best[canonical] = (rule.priority, transformed)
        if path in _PER_TOKEN_PRICING_PATHS:
            per_token_pricing_used.add(canonical)
    return canonical


def extract_model_info(flat: dict[str, object]) -> dict:
    """Match flattened provider-API field paths against the rule registry and return canonical model info.

    Resolves competing field sources by priority, applies unit-conversion
    transforms, and infers supports_cache from cache pricing presence.
    """
    info: dict[str, object] = {}
    best: dict[str, tuple[int, object]] = {}
    pricing_unit_holder = [None]
    per_token_pricing_used: set[str] = set()
    cache_pricing_present_as_null = False

    for path, value in flat.items():
        if value is None:
            if path in _CACHE_PRICING_PATHS:
                cache_pricing_present_as_null = True
            continue
        if isinstance(value, str) and value == "":
            base = path.rsplit(".", 1)[-1] if "." in path else path
            if base not in ("details.family", "details.parameter_size", "details.quantization_level",
                           "details.format", "details.parent_model"):
                continue

        matched = False
        rules = _RULE_INDEX.get(path)
        if rules:
            for rule in rules:
                if rule.pattern == path:
                    _apply_rule(rule, path, value, best, per_token_pricing_used, pricing_unit_holder)
                    matched = True

        if not matched:
            if "." in path:
                parts = path.rsplit(".", 1)
                tail = parts[-1]

                suffix_rules = _SUFFIX_INDEX.get(tail)
                if suffix_rules:
                    for sr in suffix_rules:
                        if sr.pattern == tail:
                            _apply_rule(sr, path, value, best, per_token_pricing_used, pricing_unit_holder)
                            matched = True
                            break

                if not matched and path.count(".") >= 2:
                    rparts = path.rsplit(".", 2)
                    compound = ".".join(rparts[-2:])
                    suffix_rules2 = _SUFFIX_INDEX.get(compound)
                    if suffix_rules2:
                        for sr in suffix_rules2:
                            if sr.pattern == compound:
                                _apply_rule(sr, path, value, best, per_token_pricing_used, pricing_unit_holder)
                                break

    pricing_unit = pricing_unit_holder[0]

    for canonical, (_, value) in best.items():
        info[canonical] = value

    if pricing_unit == "per_million_tokens":
        for key in per_token_pricing_used:
            if key in info and isinstance(info[key], (int, float)):
                info[key] = info[key] / 1_000_000

    _PRICE_KEYS = ("input_price", "output_price", "cache_price", "reasoning_price", "image_price")
    for key in _PRICE_KEYS:
        if key in info and isinstance(info[key], float):
            rounded = round(info[key], 6)
            if rounded == int(rounded):
                info[key] = int(rounded)
            else:
                info[key] = rounded

    if "cache_price" in info and "supports_cache" not in info:
        info["supports_cache"] = 1

    if "cache_price" not in info and cache_pricing_present_as_null and "input_price" in info:
        info["cache_price"] = info["input_price"]
        if "supports_cache" not in info:
            info["supports_cache"] = 1

    return {k: v for k, v in info.items() if k in _METADATA_FIELDS and v is not None}


def parse_model_object(model_obj: dict) -> dict:
    """Flatten a raw provider API model object and extract canonical model info."""
    flat = _flatten(model_obj)
    return extract_model_info(flat)


def _build_headers(api_key: str, custom_headers: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    if custom_headers:
        headers.update(custom_headers)
    return headers


_ALT_LIST_ENDPOINTS = {
    "ollama": ["/api/tags"],
}

_DETAILED_QUERY_PARAMS = {
    "nanogpt": "?detailed=true",
}


def _detect_provider_tag(api_url: str) -> str:
    url = api_url.lower()
    for tag in ("ollama", "nano-gpt", "nanogpt"):
        if tag in url:
            return "nanogpt" if tag.startswith("nano") else tag
    return ""


async def _fetch_list_endpoint(client, url: str, headers: dict, timeout: float) -> list[dict]:
    try:
        resp = await client.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if isinstance(data, list):
            return [m for m in data if isinstance(m, dict)]
        models_list = data.get("data") or data.get("models") or []
        if isinstance(models_list, dict):
            models_list = list(models_list.values())
        return [m for m in models_list if isinstance(m, dict)]
    except Exception as e:
        log_error(f"Model info: list endpoint fetch failed for {url}", e)
        return []


def _model_id_from_obj(m: dict) -> str:
    model_id = m.get("id") or m.get("name", "")
    if model_id.startswith("models/"):
        model_id = model_id[len("models/"):]
    return model_id


async def fetch_provider_models(provider_name: str, provider_cfg: dict) -> dict[str, dict]:
    """Fetch model metadata for a provider from its /models API and per-model detail endpoints.

    Tries the provider's /models endpoint (or a configured models_url), then
    per-model detail fetches, Ollama's /api/show, and finally each model's
    own api_url + /models as a fallback. Returns a dict of {model_id: parsed_info}
    for models registered under this provider.
    """
    api_url = provider_cfg.get("api_url", "")
    api_key = provider_cfg.get("api_key", "")
    if not api_url:
        return {}

    custom_models_url = provider_cfg.get("models_url")
    base_url = api_url.rstrip("/")
    root_url = base_url.rsplit("/v1", 1)[0] if "/v1" in base_url else base_url
    headers = _build_headers(api_key, provider_cfg.get("headers"))
    client = st.get_http_client()
    timeout = st.c.http_connect_timeout

    urls_to_try = []
    if custom_models_url:
        urls_to_try.append(custom_models_url)
    else:
        urls_to_try.append(f"{base_url}/models")

    ptag = _detect_provider_tag(api_url)

    detailed_param = _DETAILED_QUERY_PARAMS.get(ptag)
    if detailed_param and not custom_models_url:
        urls_to_try.insert(0, f"{base_url}/models{detailed_param}")

    alt_paths = _ALT_LIST_ENDPOINTS.get(ptag, [])
    for alt in alt_paths:
        urls_to_try.append(f"{root_url}{alt}")

    all_raw: dict[str, dict] = {}
    seen_ids: set[str] = set()

    for url in urls_to_try:
        models_list = await _fetch_list_endpoint(client, url, headers, timeout)
        for m in models_list:
            model_id = _model_id_from_obj(m)
            if not model_id or model_id in seen_ids:
                continue
            seen_ids.add(model_id)
            all_raw[model_id] = m

    registered_ids = {entry["model_id"] for entry in st.model_registry if entry["provider"] == provider_name}

    result = {}
    for model_id, raw in all_raw.items():
        if model_id not in registered_ids:
            continue
        parsed = parse_model_object(raw)
        if parsed:
            result[model_id] = parsed

    detail_url_template = provider_cfg.get("model_info_url")
    if detail_url_template:
        for entry in st.model_registry:
            if entry["provider"] != provider_name:
                continue
            model_id = entry["model_id"]
            if model_id not in result and model_id not in seen_ids:
                continue
            detail = await _fetch_model_detail(provider_cfg, model_id)
            if detail:
                existing = result.get(model_id, {})
                for k, v in detail.items():
                    if k not in existing:
                        existing[k] = v
                result[model_id] = existing

    if ptag == "ollama" and not detail_url_template:
        ollama_show_url = f"{root_url}/api/show"
        for entry in st.model_registry:
            if entry["provider"] != provider_name:
                continue
            model_id = entry["model_id"]
            if model_id not in seen_ids:
                continue
            try:
                resp = await client.post(ollama_show_url, headers=headers, timeout=10,
                                         json={"model": model_id, "verbose": True})
                if resp.status_code == 200:
                    show_data = resp.json()
                    if isinstance(show_data, dict):
                        detail = parse_model_object(show_data)
                        if detail:
                            existing = result.get(model_id, {})
                            for k, v in detail.items():
                                if k not in existing:
                                    existing[k] = v
                            result[model_id] = existing
            except Exception as e:
                log_error(f"Model info: Ollama show endpoint failed for {provider_name}/{model_id}", e)

    await _fetch_per_model_details(client, base_url, headers, timeout,
                                    provider_name, result)

    # Fallback: try each model's own api_url + /models. Some providers expose
    # only per-model sub-path endpoints, so the base /v1/models is inaccessible.
    for entry in st.model_registry:
        if entry['provider'] != provider_name:
            continue
        model_id = entry['model_id']
        if model_id in result:
            continue  # already got info from provider listing
        model_api_url = entry.get('api_url')
        if not model_api_url:
            continue  # no per-model URL to try
        model_base = model_api_url.rstrip('/')
        models_url = f'{model_base}/models'
        fetched = await _fetch_list_endpoint(client, models_url, headers, timeout)
        if not fetched:
            continue
        # Match by ID, or accept a single-model listing (sub-path endpoints
        # often serve one model under a different local ID)
        match = None
        for m in fetched:
            mid = _model_id_from_obj(m)
            if mid == model_id:
                match = m
                break
        if match is None and len(fetched) == 1:
            match = fetched[0]
            log.debug('Per-model fallback %s/%s: accepting single-model listing (id=%s)',
                      provider_name, model_id, _model_id_from_obj(match))
        if match:
            parsed = parse_model_object(match)
            if parsed:
                result[model_id] = parsed

    log.info("Model info fetch %s: %d/%d models", provider_name, len(result), len(registered_ids))
    return result


async def _fetch_per_model_details(client, base_url: str, headers: dict,
                                    timeout: float, provider_name: str,
                                    result: dict[str, dict]) -> None:
    registered_ids = set()
    for entry in st.model_registry:
        if entry["provider"] == provider_name:
            registered_ids.add(entry["model_id"])

    if not registered_ids:
        return

    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def _fetch_one(model_id):
        existing = result.get(model_id)
        if existing:
            missing_fields = _METADATA_FIELDS - set(existing.keys())
            if not missing_fields:
                return
        try:
            async with sem:
                resp = await client.get(f"{base_url}/models/{model_id}",
                                         headers=headers, timeout=10)
            if resp.status_code != 200:
                return
            data = resp.json()
            if not isinstance(data, dict):
                return
            detail = parse_model_object(data)
            if not detail:
                return
            if existing:
                for k, v in detail.items():
                    if k not in existing:
                        existing[k] = v
            else:
                result[model_id] = detail
        except Exception as e:
            log_error(f"Model info: per-model detail fetch failed for {provider_name}/{model_id}", e)

    await asyncio.gather(*[_fetch_one(mid) for mid in registered_ids])


async def _fetch_model_detail(provider_cfg: dict, model_id: str) -> dict | None:
    detail_url_template = provider_cfg.get("model_info_url")
    if not detail_url_template:
        return None

    api_key = provider_cfg.get("api_key", "")
    url = detail_url_template.replace("{model_id}", model_id)
    headers = _build_headers(api_key, provider_cfg.get("headers"))

    try:
        client = st.get_http_client()
        resp = await client.post(url, headers=headers, timeout=10,
                                 json={"model": model_id})
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        return parse_model_object(data)
    except Exception as e:
        log_error(f"Model info: model detail fetch failed for {model_id}", e)
        return None


def _tokenize(s: str) -> list[str]:
    return s.lower().replace("/", " ").replace("-", " ").replace(".", " ").replace("_", " ").split()


_NOTICE_PATTERNS = (
    "have been updated", "has been updated", "please re-pull", "please re-download",
    "apologize for any inconvenience", "please update", "please re-clone",
    "tokenizer_config.json", "since the initial release",
    "before this commit", "after this commit", "may lead to degraded",
    "ensure correct model behavior", "outdated config",
)


def _is_notice_paragraph(text: str) -> bool:
    lower = text.lower()
    matches = sum(1 for p in _NOTICE_PATTERNS if p in lower)
    return matches >= 2


def _extract_readme_description(md: str) -> str | None:
    import re as _re
    lines = md.split("\n")
    past_frontmatter = False
    dash_count = 0
    paragraphs = []
    current = []

    for line in lines:
        stripped = line.strip()
        if not past_frontmatter:
            if stripped == "---":
                dash_count += 1
                if dash_count >= 2:
                    past_frontmatter = True
            continue
        if stripped.startswith("#"):
            if current:
                text = " ".join(current).strip()
                if len(text) > 50:
                    paragraphs.append(text)
                current = []
            continue
        if stripped.startswith(">") or stripped.startswith("<"):
            if current:
                text = " ".join(current).strip()
                if len(text) > 50:
                    paragraphs.append(text)
                current = []
            continue
        if stripped == "":
            if current:
                text = " ".join(current).strip()
                if len(text) > 50:
                    paragraphs.append(text)
                current = []
            continue
        clean = _re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', stripped)
        clean = _re.sub(r'<[^>]+>', '', clean)
        clean = _re.sub(r'&\w+;', ' ', clean)
        clean = _re.sub(r'\*\*([^*]*)\*\*', r'\1', clean)
        clean = _re.sub(r'\*([^*]*)\*', r'\1', clean)
        clean = _re.sub(r'`([^`]*)`', r'\1', clean)
        clean = clean.strip()
        if clean and not clean.startswith("![") and len(clean) > 10:
            current.append(clean)

    if current:
        text = " ".join(current).strip()
        if len(text) > 50:
            paragraphs.append(text)

    if not paragraphs:
        return None

    desc = None
    for p in paragraphs:
        if not _is_notice_paragraph(p):
            desc = p
            break
    if desc is None:
        desc = paragraphs[0]

    if len(desc) > 600:
        desc = desc[:597] + "..."
    return desc


def _extract_moe_fields(cfg: dict) -> dict:
    src = cfg.get("text_config") if isinstance(cfg.get("text_config"), dict) else cfg
    result = {}
    for src_key, canon_key in (
        ("n_routed_experts", "num_experts"),
        ("num_local_experts", "num_experts"),
        ("num_experts", "num_experts"),
        ("num_experts_per_tok", "num_experts_per_tok"),
        ("n_shared_experts", "num_shared_experts"),
        ("moe_intermediate_size", "moe_intermediate_size"),
    ):
        val = src.get(src_key)
        if isinstance(val, int) and val > 0:
            result[canon_key] = val
    if "num_shared_experts" not in result:
        sei = src.get("shared_expert_intermediate_size")
        if isinstance(sei, int) and sei > 0:
            result["num_shared_experts"] = 1
    return result


def _strip_colon_suffixes(model_id: str) -> str:
    while ":" in model_id:
        model_id = model_id[: model_id.rindex(":")]
    return model_id


def _normalize_hf_key(model_id: str) -> str:
    clean = _strip_colon_suffixes(model_id)
    lower = clean.lower()
    base_name = lower.rsplit("/", 1)[-1] if "/" in lower else lower
    for prefix in _HF_ORG_PREFIXES:
        candidate = f"{prefix.lower()}/{base_name}"
        if lower == candidate or lower.startswith(candidate + "/"):
            return candidate
    return lower


def _hf_candidates(model_id: str) -> list[str]:
    clean = _strip_colon_suffixes(model_id)
    if "/" in clean:
        org, name = clean.rsplit("/", 1)
    else:
        org, name = None, clean
    segments = name.split("-")
    bare_candidates = ["-".join(segments[:i]) for i in range(len(segments), 0, -1)]
    candidates = []
    if org:
        for c in bare_candidates:
            candidates.append(f"{org}/{c}")
        for prefix in _HF_ORG_PREFIXES:
            if prefix == org:
                continue
            if prefix.startswith(org) or org.startswith(prefix):
                for c in bare_candidates:
                    candidates.append(f"{prefix}/{c}")
    else:
        candidates.extend(bare_candidates)
        seen = set(candidates)
        hinted = _hf_prefix_hint.get(segments[0].lower())
        prefix_order = list(_HF_ORG_PREFIXES)
        if hinted and hinted in prefix_order:
            prefix_order.remove(hinted)
            prefix_order.insert(0, hinted)
        for prefix in prefix_order:
            for c in bare_candidates:
                full = f"{prefix}/{c}"
                if full not in seen:
                    candidates.append(full)
                    seen.add(full)
    return candidates


def clear_hf_cache():
    """Clear the HuggingFace canonical-id cache and prefix hint map."""
    _hf_canonical_cache.clear()
    _hf_prefix_hint.clear()


async def _fetch_hf_model(model_id: str) -> dict | None:
    url = _HF_MODEL_URL.format(model_id=model_id)
    try:
        client = st.get_http_client()
        resp = await client.get(url, timeout=10, headers={"Accept": "application/json"}, follow_redirects=True)
        if resp.status_code != 200:
            return None
        data = resp.json()
        canonical_id = data.get("id")
        result = {}
        card_data = data.get("card_data") or data.get("card_info") or data.get("model_info") or {}
        if isinstance(card_data, dict):
            lic = card_data.get("license") or data.get("license")
            if lic and isinstance(lic, str):
                result["license"] = lic.strip()
        author = data.get("author")
        if author and isinstance(author, str):
            result["owner"] = author
        elif "/" in model_id:
            result["owner"] = model_id.split("/")[0]

        config = data.get("config")
        if isinstance(config, dict):
            mpe = config.get("max_position_embeddings")
            if mpe and isinstance(mpe, int) and "context_window" not in result:
                result["context_window"] = mpe
            qcfg = config.get("quantization_config")
            if isinstance(qcfg, dict) and "quantization" not in result:
                qm = qcfg.get("quant_method")
                if qm:
                    result["quantization"] = str(qm)
            model_type = config.get("model_type")
            if model_type and isinstance(model_type, str) and "architecture" not in result:
                result["architecture"] = model_type
            for k, v in _extract_moe_fields(config).items():
                if k not in result:
                    result[k] = v

        safetensors = data.get("safetensors")
        if isinstance(safetensors, dict):
            total = safetensors.get("total")
            if isinstance(total, (int, float)) and total > 0 and "param_count" not in result:
                result["param_count"] = _fmt_params(int(total))

        created_at = data.get("createdAt")
        if created_at and isinstance(created_at, str) and "created" not in result:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                result["created"] = dt.timestamp()
            except (ValueError, TypeError):
                pass

        siblings = data.get("siblings") or []
        has_config = any(
            isinstance(s, dict) and s.get("rfilename") == "config.json"
            for s in siblings
        )

        async def _fetch_config():
            if not has_config:
                return None
            try:
                resp = await client.get(
                    f"https://huggingface.co/{model_id}/raw/main/config.json",
                    timeout=10, follow_redirects=True)
                if resp.status_code != 200:
                    return None
                cfg = resp.json()
                return cfg if isinstance(cfg, dict) else None
            except Exception as e:
                log_error(f"Model info: HF config.json fetch/parse failed for {model_id}", e)
                return None

        async def _fetch_tokenizer():
            if "tokenizer" in result:
                return None
            try:
                resp = await client.get(
                    f"https://huggingface.co/{model_id}/raw/main/tokenizer_config.json",
                    timeout=10, follow_redirects=True)
                if resp.status_code != 200:
                    return None
                tok_cfg = resp.json()
                return tok_cfg if isinstance(tok_cfg, dict) else None
            except Exception as e:
                log_error(f"Model info: HF tokenizer_config.json fetch/parse failed for {model_id}", e)
                return None

        async def _fetch_readme():
            try:
                resp = await client.get(
                    _HF_README_URL.format(model_id=model_id),
                    timeout=10, follow_redirects=True)
                if resp.status_code != 200:
                    return None
                return _extract_readme_description(resp.text)
            except Exception as e:
                log_error(f"Model info: HF README fetch/parse failed for {model_id}", e)
                return None

        cfg, tok_cfg, readme_desc = await asyncio.gather(
            _fetch_config(), _fetch_tokenizer(), _fetch_readme())

        if cfg:
            if "context_window" not in result:
                mpe = cfg.get("max_position_embeddings")
                if mpe and isinstance(mpe, int):
                    result["context_window"] = mpe
            if "architecture" not in result:
                mt = cfg.get("model_type")
                if mt and isinstance(mt, str):
                    result["architecture"] = mt
            if "quantization" not in result:
                qc = cfg.get("quantization_config")
                if isinstance(qc, dict):
                    qm = qc.get("quant_method")
                    if qm:
                        result["quantization"] = str(qm)
            for k, v in _extract_moe_fields(cfg).items():
                if k not in result:
                    result[k] = v

        if tok_cfg:
            tc = tok_cfg.get("tokenizer_class")
            if tc and isinstance(tc, str):
                result["tokenizer"] = tc

        if readme_desc:
            result["description"] = readme_desc

        if canonical_id:
            ck = _normalize_hf_key(canonical_id)
            if ck not in _hf_canonical_cache:
                _hf_canonical_cache[ck] = result
            if "/" in canonical_id:
                prefix = canonical_id.split("/")[0]
                base = canonical_id.rsplit("/", 1)[-1].split("-")[0].lower()
                if base:
                    _hf_prefix_hint[base] = prefix
        return result if result else None
    except Exception as e:
        log_error(f"Model info: HF model fetch failed for {model_id}", e)
        return None


async def _search_hf_models(model_id: str) -> str | None:
    search_name = model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id
    mid_tokens = {t for t in _tokenize(search_name) if len(t) >= 3}
    if not mid_tokens:
        return None

    try:
        client = st.get_http_client()
        params = {"search": search_name, "sort": "downloads", "direction": "-1", "limit": "10"}
        resp = await client.get(_HF_SEARCH_URL, params=params, timeout=10,
                                headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return None
        data = resp.json()
        models = data if isinstance(data, list) else data.get("data") or []

        best_id = None
        best_score = 0.0
        for m in models:
            hf_id = m.get("id", "") if isinstance(m, dict) else ""
            if not hf_id:
                continue
            hf_name = hf_id.rsplit("/", 1)[-1] if "/" in hf_id else hf_id
            hf_tokens = {t for t in _tokenize(hf_name) if len(t) >= 3}
            if not hf_tokens:
                continue
            intersection = mid_tokens & hf_tokens
            union = mid_tokens | hf_tokens
            score = len(intersection) / len(union)
            if score > best_score and score >= 0.5:
                best_score = score
                best_id = hf_id

        return best_id
    except Exception as e:
        log_error(f"Model info: HF model search failed for {model_id}", e)
        return None


async def _fetch_hf_for_model(model_id: str, existing: dict | None = None,
                              hf_id_override: str | None = None) -> dict | None:
    existing = existing or {}
    needs_any = any(k not in existing or existing.get(k) is None for k in _HF_FIELDS)
    if not needs_any:
        return None

    lookup = hf_id_override or model_id
    cache_key = _normalize_hf_key(lookup)
    cached = _hf_canonical_cache.get(cache_key)
    if cached:
        return dict(cached)

    clean_id = _strip_colon_suffixes(model_id)
    for prefix in _HF_ORG_PREFIXES:
        base = _strip_colon_suffixes(lookup).rsplit("/", 1)[-1] if "/" in _strip_colon_suffixes(lookup) else _strip_colon_suffixes(lookup)
        alt_key = _normalize_hf_key(f"{prefix}/{base}")
        alt_cached = _hf_canonical_cache.get(alt_key)
        if alt_cached:
            _hf_canonical_cache[cache_key] = alt_cached
            return dict(alt_cached)

    result = None
    if hf_id_override:
        result = await _fetch_hf_model(hf_id_override)

    if not result:
        candidates = _hf_candidates(model_id)
        quant_result = None
        for candidate in candidates:
            candidate_result = await _fetch_hf_model(candidate)
            if not candidate_result:
                continue
            if candidate_result.get("quantization") and len(candidates) > 1:
                if not quant_result:
                    quant_result = candidate_result
                continue
            result = candidate_result
            break
        if not result and quant_result:
            result = quant_result
        if result and quant_result and result is not quant_result:
            for k, v in quant_result.items():
                if k not in result or result[k] is None:
                    result[k] = v

    if not result:
        hf_id = await _search_hf_models(_strip_colon_suffixes(model_id))
        if hf_id:
            result = await _fetch_hf_model(hf_id)

    if result:
        _hf_canonical_cache[cache_key] = result
    return result


def _info_changed(new_info: dict, cached: dict) -> bool:
    for k, v in new_info.items():
        old = cached.get(k)
        if old is None and v is not None:
            return True
        if old is not None and old != v:
            return True
    return False


async def _write_model_info(model_id: str, data: dict, overwrite: bool):
    """Persist model info to SQLite and update the in-memory cache."""
    import backend.db as db
    await asyncio.to_thread(db.update_model_info, model_id, data, overwrite)
    st.model_info_cache.setdefault(model_id, {}).update(data)


async def _enrich_hf(entries: list[dict], fields: frozenset) -> int:
    """Run HuggingFace enrichment for registry entries, filling only missing fields.

    Fetches HF model/config/tokenizer/README data concurrently and persists
    only fields that are absent from the existing cache entry. Returns the
    count of entries that received new data.
    """
    import backend.db as db
    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def _fetch_one(entry):
        cached = st.model_info_cache.get(entry["id"], {})
        async with sem:
            hf_result = await _fetch_hf_for_model(
                entry["model_id"],
                existing=cached,
                hf_id_override=entry.get("hf_id"),
            )
        return entry, cached, hf_result

    results = await asyncio.gather(*[_fetch_one(e) for e in entries], return_exceptions=True)

    updated = 0
    for r in results:
        if isinstance(r, Exception):
            log_error("Model info: HF enrichment fetch failed", r)
            continue
        entry, cached, hf_result = r
        if not hf_result:
            continue
        hf_info = {k: v for k, v in hf_result.items() if k in fields and v is not None}
        if not hf_info:
            continue
        fill_only = {k: v for k, v in hf_info.items() if cached.get(k) is None}
        if not fill_only:
            continue
        await asyncio.to_thread(db.update_model_info, entry["id"], fill_only, False)
        st.model_info_cache.setdefault(entry["id"], {}).update(fill_only)
        updated += 1
    return updated


_HF_SHARABLE_FIELDS = frozenset(("description", "architecture", "param_count",
                                  "tokenizer", "license", "owner", "created"))

_HF_PLACEHOLDER_RE = re.compile(r"^[📖📄📚📑🔗💡]\s*(Check out|Read|See|Refer to)", re.IGNORECASE)


async def _share_hf_descriptions(entries: list[dict]) -> int:
    """Share descriptions across providers for models resolving to the same base HF model.

    After provider API fetch and HF enrichment, some models may still lack a
    description (or have a useless blog-link one) because their provider didn't
    return one and their HF README is minimal. If another provider's model has
    the same base model ID (e.g. zai-org/glm-5.1 when this model is
    zai-org/glm-5.1-fp8), borrow its description and other sharable fields.

    If the target already has a description but it's significantly shorter than
    the source (< 40% of source length), replace it - handles quant variants
    that get a useless blog-link description from HF while the base model has a
    real description from a provider.
    """
    import backend.db as db
    base_to_entries: dict[str, list[dict]] = {}
    for entry in entries:
        mid = entry.get("model_id", "")
        base = _normalize_hf_key(_strip_colon_suffixes(mid))
        base_to_entries.setdefault(base, []).append(entry)
    updated = 0
    for base, group in base_to_entries.items():
        if len(group) < 2:
            continue
        best_desc = None
        best_source_key = None
        best_source_cache = None
        for entry in group:
            cached = st.model_info_cache.get(entry["id"], {})
            desc = cached.get("description")
            if desc and (best_desc is None or len(desc) > len(best_desc)):
                best_desc = desc
                best_source_key = entry["id"]
                best_source_cache = cached
        if not best_source_cache:
            continue
        for entry in group:
            if entry["id"] == best_source_key:
                continue
            cached = st.model_info_cache.get(entry["id"], {})
            fill = {}
            for k in _HF_SHARABLE_FIELDS:
                if k == "description":
                    existing_desc = cached.get("description")
                    if not existing_desc:
                        fill[k] = best_source_cache[k]
                    elif _HF_PLACEHOLDER_RE.match(existing_desc) and len(best_desc) > len(existing_desc):
                        fill[k] = best_source_cache[k]
                elif cached.get(k) is None and best_source_cache.get(k) is not None:
                    fill[k] = best_source_cache[k]
            if not fill:
                continue
            await asyncio.to_thread(db.update_model_info, entry["id"], fill, False)
            st.model_info_cache.setdefault(entry["id"], {}).update(fill)
            updated += 1
    for entry in entries:
        cached = st.model_info_cache.get(entry["id"], {})
        mid = entry.get("model_id", "")
        candidates = _hf_candidates(mid)
        for candidate in candidates[1:]:
            ck = _normalize_hf_key(_strip_colon_suffixes(candidate))
            group = base_to_entries.get(ck)
            if not group:
                continue
            source = None
            source_desc = None
            for g_entry in group:
                g_cached = st.model_info_cache.get(g_entry["id"], {})
                g_desc = g_cached.get("description")
                if g_desc and (source_desc is None or len(g_desc) > len(source_desc)):
                    source = g_cached
                    source_desc = g_desc
            if not source:
                continue
            fill = {}
            for k in _HF_SHARABLE_FIELDS:
                if k == "description":
                    existing_desc = cached.get("description")
                    if not existing_desc:
                        fill[k] = source_desc
                    elif _HF_PLACEHOLDER_RE.match(existing_desc) and len(source_desc) > len(existing_desc):
                        fill[k] = source_desc
                elif cached.get(k) is None and source.get(k) is not None:
                    fill[k] = source[k]
            if not fill:
                continue
            await asyncio.to_thread(db.update_model_info, entry["id"], fill, False)
            st.model_info_cache.setdefault(entry["id"], {}).update(fill)
            updated += 1
            break
    return updated


async def fetch_model_info_for_keys(model_keys: list[str]) -> int:
    """Fetch model info for specific model keys (e.g. newly added models on config reload).

    Unlike fetch_all_model_info(), this only hits provider APIs for providers
    that have new models, and skips models that already have cached info.
    """
    if not model_keys:
        return 0

    # Build lookup: model_key -> registry entry (single pass)
    key_set = set(model_keys)
    key_to_entry: dict[str, dict] = {}
    for entry in st.model_registry:
        if entry["id"] in key_set:
            key_to_entry[entry["id"]] = entry

    if not key_to_entry:
        return 0

    # Group by provider
    provider_keys: dict[str, list[str]] = {}
    for key, entry in key_to_entry.items():
        provider_keys.setdefault(entry["provider"], []).append(key)

    # Build provider config lookup
    provider_cfg_map: dict[str, dict] = {
        p.get("name", ""): p for p in st.models_cfg.get("providers", [])
    }

    total_updated = 0

    # Pre-filter providers that need fetching
    providers_to_fetch = []
    for provider_name, keys in provider_keys.items():
        provider_cfg = provider_cfg_map.get(provider_name)
        if not provider_cfg:
            continue
        # Skip models that already have cached info with actual data
        keys_needing_fetch = []
        for key in keys:
            cached = st.model_info_cache.get(key)
            if not cached:
                keys_needing_fetch.append(key)
            elif not any(v is not None for k, v in cached.items()
                         if k not in st.MODEL_INFO_BOOL_FIELDS):
                keys_needing_fetch.append(key)
        if keys_needing_fetch:
            providers_to_fetch.append((provider_name, provider_cfg, keys_needing_fetch))

    if providers_to_fetch:
        sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def _limited_fetch(name, provider_cfg):
            async with sem:
                return await fetch_provider_models(name, provider_cfg)

        fetch_tasks = [_limited_fetch(name, cfg) for name, cfg, _ in providers_to_fetch]
        fetched_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        for (provider_name, _, keys_needing_fetch), fetched in zip(providers_to_fetch, fetched_results):
            if isinstance(fetched, Exception) or not fetched:
                continue
            for key in keys_needing_fetch:
                entry = key_to_entry[key]
                info = fetched.get(entry["model_id"])
                if not info:
                    continue
                info_to_write = {k: v for k, v in info.items() if k in _METADATA_FIELDS and v is not None}
                if not info_to_write:
                    continue
                await _write_model_info(entry["id"], info_to_write, True)
                total_updated += 1

    # HuggingFace enrichment for new models only
    hf_entries = [key_to_entry[k] for k in model_keys if key_to_entry.get(k)]
    hf_updated = await _enrich_hf(hf_entries, _HF_FIELDS)

    if hf_updated:
        total_updated += hf_updated
        log.info("HuggingFace: enriched %d new models", hf_updated)

    shared = await _share_hf_descriptions(hf_entries)
    if shared:
        total_updated += shared
        log.info("HuggingFace: shared descriptions for %d new models", shared)

    if total_updated:
        st.invalidate_metrics_cache()
        st.invalidate_providers_cache()
        st.invalidate_model_info_response_cache()
        log.info("Model info: fetched info for %d new models", total_updated)

    return total_updated


async def fetch_all_model_info() -> int:
    """Fetch and persist model metadata for all providers, then enrich via HuggingFace.

    Fetches provider /models APIs concurrently, then runs HF enrichment and
    cross-provider description sharing. Returns the total count of updated
    model entries. Invalidates caches if any updates occurred.
    """
    providers = st.models_cfg.get("providers", [])
    total_updated = 0

    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def _limited_fetch(name, provider):
        async with sem:
            return await fetch_provider_models(name, provider)

    fetch_tasks = [_limited_fetch(p.get("name", ""), p) for p in providers]
    fetched_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    for provider, fetched in zip(providers, fetched_results):
        if isinstance(fetched, Exception) or not fetched:
            continue
        name = provider.get("name", "")
        for entry in st.model_registry:
            if entry["provider"] != name:
                continue
            info = fetched.get(entry["model_id"])
            if not info:
                continue
            info_to_write = {k: v for k, v in info.items() if k in _METADATA_FIELDS and v is not None}
            if not info_to_write:
                continue
            cached = st.model_info_cache.get(entry["id"])
            if cached and not _info_changed(info_to_write, cached):
                continue
            await _write_model_info(entry["id"], info_to_write, True)
            total_updated += 1

    hf_updated = await _enrich_hf(st.model_registry, _HF_FIELDS)
    if hf_updated:
        total_updated += hf_updated
        log.info("HuggingFace: enriched %d models", hf_updated)

    shared = await _share_hf_descriptions(st.model_registry)
    if shared:
        total_updated += shared
        log.info("HuggingFace: shared descriptions for %d models", shared)

    if total_updated:
        st.invalidate_metrics_cache()
        st.invalidate_providers_cache()
        st.invalidate_model_info_response_cache()
        log.info("Model info: updated %d models total", total_updated)
    else:
        log.debug("Model info: no changes")

    return total_updated


async def model_info_fetch_loop():
    """Deprecated no-op stub - kept for backward compatibility during the transition.

    The scheduler's probe test dispatching now handles periodic model_info
    fetching. Callers should use start_model_info_fetch() or the probe system.
    """
    log.warning("model_info_fetch_loop() is deprecated - use probe test type instead")


_model_info_task: asyncio.Task | None = None


def start_model_info_fetch():
    """Start model info fetch as a fire-and-forget background task.

    Fetches provider metadata (context_window, pricing, architecture, etc.)
    from provider /v1/models APIs and Ollama /api/show. Probes only write
    capability booleans - this fetches the full structural metadata.
    """
    global _model_info_task
    if _model_info_task and not _model_info_task.done():
        _model_info_task.cancel()
    _model_info_task = st.create_task(fetch_all_model_info(), name="fetch_all_model_info")
