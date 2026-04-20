"""État global partagé entre les routers FastAPI.

- Corpus actif (WikiContext)
- Statut du modèle LLM
"""
from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional

import wiki as w

_WORKSPACES_FILE = w.PROJECT_ROOT / "workspaces.json"


# ---------------------------------------------------------------------------
# Corpus actif
# ---------------------------------------------------------------------------

_active_ctx: w.WikiContext = w.DEFAULT_CTX


def get_ctx() -> w.WikiContext:
    return _active_ctx


def set_ctx(ctx: w.WikiContext) -> None:
    global _active_ctx
    _active_ctx = ctx


def load_workspaces() -> dict:
    if not _WORKSPACES_FILE.exists():
        default = {
            "active": "default",
            "corpora": {
                "default": {
                    "name": "Default",
                    "wiki_dir": str(w.WIKI_DIR.relative_to(w.PROJECT_ROOT)).replace("\\", "/"),
                    "raw_dir": str(w.RAW_DIR.relative_to(w.PROJECT_ROOT)).replace("\\", "/"),
                }
            },
        }
        _WORKSPACES_FILE.write_text(json.dumps(default, indent=2, ensure_ascii=False), encoding="utf-8")
        return default
    return json.loads(_WORKSPACES_FILE.read_text(encoding="utf-8"))


def save_workspaces(ws: dict) -> None:
    _WORKSPACES_FILE.write_text(json.dumps(ws, indent=2, ensure_ascii=False), encoding="utf-8")


def ctx_from_entry(entry: dict) -> w.WikiContext:
    if "path" in entry:
        base = w.PROJECT_ROOT / entry["path"]
        return w.WikiContext.from_paths(base / "wiki", base / "raw")
    wiki_dir = w.PROJECT_ROOT / entry["wiki_dir"]
    raw_dir = w.PROJECT_ROOT / entry["raw_dir"]
    return w.WikiContext.from_paths(wiki_dir, raw_dir)


def activate_corpus(name: str) -> w.WikiContext:
    ws = load_workspaces()
    if name not in ws["corpora"]:
        raise KeyError(f"Corpus inconnu : {name!r}")
    ws["active"] = name
    save_workspaces(ws)
    ctx = ctx_from_entry(ws["corpora"][name])
    set_ctx(ctx)
    return ctx


# ---------------------------------------------------------------------------
# Statut du modèle
# ---------------------------------------------------------------------------

class ModelStatus(str, Enum):
    loaded = "loaded"
    loading = "loading"
    error = "error"


_model_status: ModelStatus = ModelStatus.loaded
_model_error: Optional[str] = None


def get_model_status() -> dict:
    return {"status": _model_status.value, "error": _model_error}


def set_model_status(status: ModelStatus, error: Optional[str] = None) -> None:
    global _model_status, _model_error
    _model_status = status
    _model_error = error
