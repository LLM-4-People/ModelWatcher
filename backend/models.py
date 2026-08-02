"""Model registry, provider lookup, and grouping.

Builds the flat model registry from models.yaml and provides provider
config lookup by model key. The registry is the single source of truth
for which models exist and which provider they belong to.
"""

from backend.state import models_cfg, model_registry, MODEL_INFO_FIELDS, ensure_scheme, _archived_model_keys

_provider_by_name: dict[str, dict] = {}


def _rebuild_provider_index():
    _provider_by_name.clear()
    for p in models_cfg.get("providers", []):
        name = p.get("name")
        if name:
            normalized = dict(p)
            normalized["api_url"] = ensure_scheme(p.get("api_url", ""))
            purl = p.get("provider_url")
            if purl:
                normalized["provider_url"] = ensure_scheme(purl)
            _provider_by_name[name] = normalized


def _find_provider(name: str) -> dict | None:
    return _provider_by_name.get(name)


def build_model_registry() -> list:
    """Build the flat model registry from models.yaml, sorted by provider then model name.

    Extracts optional metadata fields (context_window, supports_vision, etc.)
    from YAML so they can be persisted to model_info during config sync.
    """
    _rebuild_provider_index()
    result = []
    _META_KEYS = tuple(sorted(MODEL_INFO_FIELDS & {
        "context_window", "output_context", "supports_cache",
        "supports_vision", "supports_tools", "supports_structured_output",
        "input_price", "output_price", "cache_price",
    }))
    for provider in sorted(models_cfg.get("providers", []), key=lambda p: p.get("name", "Unknown").lower()):
        provider_name = provider.get("name", "Unknown")
        provider_archived = provider.get("archived")  # True/False/None
        provider_auto_archive = provider.get("auto_archive", True)
        for m in sorted(provider.get("models", []), key=lambda m: m.get("name", m["id"]).lower()):
            entry = {
                "id": f"{provider_name}::{m['id']}",
                "provider": provider_name,
                "model_id": m["id"],
                "name": m.get("name", m["id"]),
            }
            # Model-level archived overrides provider-level; absent defers to DB state.
            if m.get("archived") is not None:
                entry["archived"] = bool(m["archived"])
            elif provider_archived is not None:
                entry["archived"] = bool(provider_archived)
            if provider_auto_archive is False or m.get("auto_archive") is False:
                entry["auto_archive"] = False
            hf_id = m.get("hf_id")
            if hf_id:
                entry["hf_id"] = hf_id
            model_api_url = m.get("api_url")
            if model_api_url:
                entry["api_url"] = model_api_url
            model_req_opts = m.get("request_options")
            if model_req_opts:
                entry["request_options"] = model_req_opts
            for k in _META_KEYS:
                v = m.get(k)
                if v is not None:
                    if k.startswith("supports_"):
                        if v:
                            entry[k] = 1
                    else:
                        entry[k] = v
            result.append(entry)
    return result


_registry_by_id: dict[str, dict] = {}

def _rebuild_registry_index():
    _registry_by_id.clear()
    for entry in model_registry:
        _registry_by_id[entry["id"]] = entry


def get_provider_for(model_key: str) -> dict | None:
    """Look up the full provider config for a model key, merging in model_id.

    Inherits provider-level ``request_options`` and applies per-model overrides.
    Per-model ``api_url`` replaces the provider-level URL.
    """
    entry = _registry_by_id.get(model_key)
    if not entry:
        return None
    provider = _find_provider(entry["provider"])
    if not provider:
        return None
    result = {**provider, "model_id": entry["model_id"]}
    model_api_url = entry.get("api_url")
    if model_api_url:
        result["api_url"] = ensure_scheme(model_api_url)
    model_req_opts = entry.get("request_options")
    if model_req_opts:
        result["_model_request_options"] = model_req_opts
    return result


def get_providers_grouped(providers: set[str] | None = None) -> dict:
    """Group model registry entries by provider with derived website URL, logo, and title.

    Args:
        providers: If set, only include these provider names. None = all.
    """
    from backend.db import get_providers_batch
    from backend.favicons import provider_logo_data_uri, root_url

    groups: dict[str, list] = {}
    for entry in model_registry:
        groups.setdefault(entry["provider"], []).append(entry)
    sorted_names = sorted(groups)
    prov_rows = get_providers_batch(sorted_names) if sorted_names else {}
    result = {}
    for name in sorted_names:
        if providers is not None and name not in providers:
            continue
        cfg = _find_provider(name)
        base = cfg.get("provider_url") or cfg.get("api_url", "") if cfg else ""
        prov = prov_rows.get(name)
        title = prov.get("page_title") if prov else None
        model_entries = []
        for entry in groups[name]:
            m = {"id": entry["id"], "provider": entry["provider"],
                 "model_id": entry["model_id"], "name": entry["name"]}
            if entry["id"] in _archived_model_keys:
                m["archived"] = True
            model_entries.append(m)
        result[name] = {
            "models": model_entries,
            "api_url": root_url(base) if base else None,
            "logo": provider_logo_data_uri(name),
            "title": title,
        }
    return result


def get_provider_concurrency(provider_name: str) -> int:
    """Return the concurrent_models limit for a provider (default 1 = sequential)."""
    provider = _find_provider(provider_name)
    return provider.get("concurrent_models", 1) if provider else 1

