"""Paramètres du modèle et inférence."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import wiki as w
from app import state
from app.wiki_bridge import reload_model_async

router = APIRouter(tags=["settings"])

_MODELS_DIR = w.PROJECT_ROOT / "models"


@router.get("/settings")
async def get_settings():
    return {
        "model": w.CFG.get("model", {}),
        "inference": w.CFG.get("inference", {}),
    }


@router.get("/settings/models")
async def list_models():
    if not _MODELS_DIR.exists():
        return []
    files = []
    for f in _MODELS_DIR.glob("*.gguf"):
        size_gb = round(f.stat().st_size / 1024**3, 2)
        files.append({
            "filename": f.name,
            "path": str(f.relative_to(w.PROJECT_ROOT)),
            "size_gb": size_gb,
            "active": w.CFG["model"]["path"] == str(f.relative_to(w.PROJECT_ROOT)).replace("\\", "/"),
        })
    files.sort(key=lambda x: x["filename"])
    return files


class SettingsBody(BaseModel):
    model_path: Optional[str] = None
    n_ctx: Optional[int] = None
    n_gpu_layers: Optional[int] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None


@router.put("/settings")
async def update_settings(body: SettingsBody):
    needs_reload = False
    old_n_ctx = w.CFG["model"]["n_ctx"]
    old_n_gpu = w.CFG["model"]["n_gpu_layers"]
    old_path = w.CFG["model"]["path"]

    if body.model_path is not None:
        full = w.PROJECT_ROOT / body.model_path
        if not full.exists():
            raise HTTPException(404, f"Modèle introuvable : {body.model_path}")
        w.CFG["model"]["path"] = body.model_path
        needs_reload = True
    if body.n_ctx is not None:
        w.CFG["model"]["n_ctx"] = body.n_ctx
        needs_reload = needs_reload or (body.n_ctx != old_n_ctx)
    if body.n_gpu_layers is not None:
        w.CFG["model"]["n_gpu_layers"] = body.n_gpu_layers
        needs_reload = needs_reload or (body.n_gpu_layers != old_n_gpu)
    if body.temperature is not None:
        w.CFG["inference"]["temperature"] = body.temperature
    if body.max_tokens is not None:
        w.CFG["inference"]["max_tokens"] = body.max_tokens
    if body.top_p is not None:
        w.CFG["inference"]["top_p"] = body.top_p

    # Persist to config.yaml
    cfg_copy = {
        "model": dict(w.CFG["model"]),
        "inference": dict(w.CFG["inference"]),
        "paths": dict(w.CFG.get("paths", {})),
    }
    w.CONFIG_PATH.write_text(
        yaml.dump(cfg_copy, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )

    if needs_reload:
        state.set_model_status(state.ModelStatus.loading)
        asyncio.create_task(reload_model_async())

    return {"ok": True, "reload_triggered": needs_reload}


@router.get("/settings/status")
async def model_status():
    return state.get_model_status()
