"""FastAPI server — LLM Wiki GUI."""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager

# Force UTF-8 on stdout/stderr — Windows defaults to cp1252 or ascii in
# non-interactive mode, which breaks click.echo() calls with accented chars.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import logging

from pathlib import Path

from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.routers import ingest, query, search, pages, lint_stats, suggest, corpus, settings
from app import state


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Charge le LLM une seule fois au démarrage, le garde en mémoire."""
    import wiki as w
    # Restaure le corpus actif depuis workspaces.json
    ws = state.load_workspaces()
    active = ws.get("active", "default")
    if active in ws.get("corpora", {}):
        state.set_ctx(state.ctx_from_entry(ws["corpora"][active]))

    provider_cfg = w.CFG.get("llm_provider", {})
    if provider_cfg.get("mode") == "api":
        print(f"[startup] mode API ({provider_cfg.get('api_provider', '?')}) — pas de chargement GGUF.")
        state.set_model_status(state.ModelStatus.loaded)
    else:
        print("[startup] chargement du modèle LLM…")
        state.set_model_status(state.ModelStatus.loading)
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, w._get_llm)
            state.set_model_status(state.ModelStatus.loaded)
            print("[startup] modèle prêt.")
        except Exception as e:
            state.set_model_status(state.ModelStatus.error, str(e))
            print(f"[startup] erreur modèle : {e}")
    yield


app = FastAPI(title="LLM Wiki", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(pages.router, prefix="/api")
app.include_router(lint_stats.router, prefix="/api")
app.include_router(suggest.router, prefix="/api")
app.include_router(corpus.router, prefix="/api")
app.include_router(settings.router, prefix="/api")

# Servir le frontend buildé en production
_static = Path(__file__).parent / "static"
if _static.exists() and any(_static.iterdir()):
    app.mount("/", StaticFiles(directory=_static, html=True), name="static")
