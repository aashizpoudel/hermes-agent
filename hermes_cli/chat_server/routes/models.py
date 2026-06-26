"""Provider and model selection routes."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from hermes_cli.config import load_config, save_config

from ..runner import get_runner
from ..state import _check_bearer

router = APIRouter()

logger = logging.getLogger(__name__)


class _ModelSwitchBody(BaseModel):
    provider: str
    model: str


def _trim_provider(p: Dict[str, Any]) -> Dict[str, Any]:
    """Strip large fields from a list_authenticated_providers entry."""
    return {
        "slug": p.get("slug", ""),
        "name": p.get("name", ""),
        "is_current": bool(p.get("is_current")),
        "is_user_defined": bool(p.get("is_user_defined")),
        "total_models": int(p.get("total_models") or 0),
        "api_url": p.get("api_url") or "",
    }


def _load_provider_args(cfg: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
    """Pull the kwargs list_authenticated_providers expects out of cfg."""
    user_provs = cfg.get("providers")
    if not isinstance(user_provs, dict):
        user_provs = None
    custom_provs: Optional[List[Dict[str, Any]]] = None
    try:
        from hermes_cli.config import get_compatible_custom_providers
        custom_provs = get_compatible_custom_providers(cfg) or None
    except Exception:
        raw = cfg.get("custom_providers")
        custom_provs = raw if isinstance(raw, list) else None
    return user_provs, custom_provs


@router.get("/api/providers")
async def list_providers(request: Request) -> Dict[str, Any]:
    """Return authenticated providers (slug/name/is_current/...)."""
    _check_bearer(request)
    runner = get_runner()
    try:
        from hermes_cli.model_switch import list_authenticated_providers
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"providers unavailable: {e}")

    cfg = load_config()
    user_provs, custom_provs = _load_provider_args(cfg)
    current_provider = ""
    if runner.agent is not None:
        current_provider = getattr(runner.agent, "provider", "") or ""
    if not current_provider:
        mc = cfg.get("model") or {}
        if isinstance(mc, dict):
            current_provider = (mc.get("provider") or "").strip()

    try:
        provs = list_authenticated_providers(
            current_provider=current_provider,
            user_providers=user_provs,
            custom_providers=custom_provs,
            max_models=50,
        ) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("list_authenticated_providers failed: %s", e)
        provs = []
    return {"items": [_trim_provider(p) for p in provs]}


@router.get("/api/models")
async def list_models(request: Request, slug: str = Query(...)) -> Dict[str, Any]:
    """Return available models for a provider — live-probe for user-defined."""
    _check_bearer(request)
    runner = get_runner()
    try:
        from hermes_cli.model_switch import list_authenticated_providers
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"providers unavailable: {e}")

    cfg = load_config()
    user_provs, custom_provs = _load_provider_args(cfg)
    try:
        provs = list_authenticated_providers(
            user_providers=user_provs,
            custom_providers=custom_provs,
            max_models=200,
        ) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("list_authenticated_providers failed: %s", e)
        provs = []

    target = next((p for p in provs if p.get("slug") == slug), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"unknown provider: {slug}")

    saved_models: List[str] = list(target.get("models") or [])

    if not target.get("is_user_defined"):
        return {"slug": slug, "models": saved_models, "source": "static"}

    # Live probe for user-defined / custom providers.
    api_url = (target.get("api_url") or "").strip().rstrip("/")
    api_key = ""
    # Try to find a matching custom_providers entry by base_url.
    if isinstance(custom_provs, list):
        for entry in custom_provs:
            if not isinstance(entry, dict):
                continue
            entry_url = (
                entry.get("base_url") or entry.get("url") or entry.get("api") or ""
            ).strip().rstrip("/")
            if entry_url and entry_url == api_url:
                api_key = (entry.get("api_key") or "").strip()
                if api_key:
                    break
    # Fallback: if URL matches the active session, use its key.
    if not api_key and runner.agent is not None:
        agent_url = (getattr(runner.agent, "base_url", "") or "").strip().rstrip("/")
        if agent_url and agent_url == api_url:
            api_key = getattr(runner.agent, "api_key", "") or ""
    if not api_key:
        mc = cfg.get("model") or {}
        if isinstance(mc, dict):
            mc_url = (mc.get("base_url") or "").strip().rstrip("/")
            if mc_url and mc_url == api_url:
                api_key = (mc.get("api_key") or "").strip()

    live: List[str] = []
    try:
        from hermes_cli.models import fetch_api_models
        probed = fetch_api_models(api_key or None, api_url or None, timeout=8.0)
        if probed:
            live = list(probed)
    except Exception as e:  # noqa: BLE001
        logger.warning("fetch_api_models failed for %s: %s", slug, e)
        live = []

    # Merge — keep saved (current) models even if not in the live list.
    seen_lower = {m.lower() for m in live}
    merged = list(live)
    for m in saved_models:
        if m and m.lower() not in seen_lower:
            merged.append(m)
            seen_lower.add(m.lower())
    return {"slug": slug, "models": merged, "source": "live"}


@router.get("/api/model/current")
async def current_model(request: Request) -> Dict[str, Any]:
    """Return the currently active model + provider + base_url + label."""
    _check_bearer(request)
    runner = get_runner()
    cfg = load_config()
    mc = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    provider = ""
    model = ""
    base_url = ""
    if runner.agent is not None:
        provider = getattr(runner.agent, "provider", "") or ""
        model = getattr(runner.agent, "model", "") or ""
        base_url = getattr(runner.agent, "base_url", "") or ""
    if not provider:
        provider = (mc.get("provider") or "").strip()
    if not model:
        model = (mc.get("default") or "").strip()
    if not base_url:
        base_url = (mc.get("base_url") or "").strip()

    label = provider
    try:
        from hermes_cli.providers import get_label
        label = get_label(provider) or provider
    except Exception:
        pass
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "label": label,
    }


@router.post("/api/model/switch")
async def switch_model_endpoint(body: _ModelSwitchBody, request: Request) -> Dict[str, Any]:
    """Switch the active model and persist to ~/.hermes/config.yaml."""
    _check_bearer(request)
    runner = get_runner()
    target_provider = (body.provider or "").strip()
    target_model = (body.model or "").strip()
    if not target_model:
        raise HTTPException(status_code=400, detail="model is required")

    try:
        from hermes_cli.model_switch import switch_model
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"model_switch unavailable: {e}")

    cfg = load_config()
    user_provs, custom_provs = _load_provider_args(cfg)
    mc = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    cur_provider = ""
    cur_model = ""
    cur_base_url = ""
    cur_api_key = ""
    if runner.agent is not None:
        cur_provider = getattr(runner.agent, "provider", "") or ""
        cur_model = getattr(runner.agent, "model", "") or ""
        cur_base_url = getattr(runner.agent, "base_url", "") or ""
        cur_api_key = getattr(runner.agent, "api_key", "") or ""
    cur_provider = cur_provider or (mc.get("provider") or "").strip()
    cur_model = cur_model or (mc.get("default") or "").strip()
    cur_base_url = cur_base_url or (mc.get("base_url") or "").strip()
    cur_api_key = cur_api_key or (mc.get("api_key") or "").strip()

    try:
        result = switch_model(
            raw_input=target_model,
            current_provider=cur_provider,
            current_model=cur_model,
            current_base_url=cur_base_url,
            current_api_key=cur_api_key,
            is_global=True,
            explicit_provider=target_provider,
            user_providers=user_provs,
            custom_providers=custom_provs,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("switch_model crashed")
        raise HTTPException(status_code=400, detail=str(e))

    if not getattr(result, "success", False):
        raise HTTPException(
            status_code=400,
            detail=getattr(result, "error_message", "") or "model switch failed",
        )

    # Persist to ~/.hermes/config.yaml. switch_model() does not write the
    # config itself — the CLI normally calls save_config_value().  Use
    # save_config() with a re-loaded dict so we don't stomp on unrelated
    # keys that may have changed since.
    try:
        new_cfg = load_config()
        m = new_cfg.get("model")
        if not isinstance(m, dict):
            m = {}
        m["default"] = result.new_model
        if getattr(result, "provider_changed", False) and result.target_provider:
            m["provider"] = result.target_provider
        if result.base_url:
            m["base_url"] = result.base_url
        if result.api_key:
            m["api_key"] = result.api_key
        new_cfg["model"] = m
        save_config(new_cfg)
    except Exception as e:  # noqa: BLE001
        logger.warning("save_config after model switch failed: %s", e)

    # Drop the cached agent so the next /api/message rebuilds with the new
    # model/provider/base_url.
    runner.agent = None
    return {
        "ok": True,
        "provider": result.target_provider or target_provider,
        "model": result.new_model,
    }
