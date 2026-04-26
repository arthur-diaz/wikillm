"""Paramètres du modèle, inférence et fournisseur LLM."""
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
    provider_cfg = w.CFG.get("llm_provider", {})
    api_keys = provider_cfg.get("api_keys", {})
    current_provider = provider_cfg.get("api_provider", "openai")
    return {
        "model": w.CFG.get("model", {}),
        "inference": w.CFG.get("inference", {}),
        "provider": {
            "mode": provider_cfg.get("mode", "local"),
            "api_provider": current_provider,
            "api_model": provider_cfg.get("api_model", "gpt-4o"),
            # Indique si une clé est définie (sans l'exposer)
            "has_key": bool(api_keys.get(current_provider, "")),
        },
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

    # Persist to config.yaml (sans les clés API — stockées dans secrets.yaml)
    provider_cfg = w.CFG.get("llm_provider", {})
    provider_cfg_to_save = {k: v for k, v in provider_cfg.items() if k != "api_keys"}
    provider_cfg_to_save["api_keys"] = {p: "" for p in ("openai", "anthropic", "mistral")}
    cfg_copy = {
        "model": dict(w.CFG["model"]),
        "inference": dict(w.CFG["inference"]),
        "paths": dict(w.CFG.get("paths", {})),
        "llm_provider": provider_cfg_to_save,
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


# ---------------------------------------------------------------------------
# Fournisseur LLM externe
# ---------------------------------------------------------------------------

class LLMProviderBody(BaseModel):
    mode: str  # "local" | "api"
    api_provider: Optional[str] = None
    api_model: Optional[str] = None
    api_key: Optional[str] = None  # clé pour le fournisseur sélectionné


@router.put("/settings/llm-provider")
async def update_llm_provider(body: LLMProviderBody):
    if body.mode not in ("local", "api"):
        raise HTTPException(400, "mode doit être 'local' ou 'api'")

    provider_cfg = w.CFG.setdefault("llm_provider", {})
    previous_mode = provider_cfg.get("mode", "local")
    provider_cfg["mode"] = body.mode

    if body.api_provider is not None:
        provider_cfg["api_provider"] = body.api_provider
    if body.api_model is not None:
        provider_cfg["api_model"] = body.api_model
    target_provider = body.api_provider or provider_cfg.get("api_provider", "openai")

    if body.api_key is not None:
        # Clé écrite dans secrets.yaml (gitignored), jamais dans config.yaml
        secrets_path = w.PROJECT_ROOT / "secrets.yaml"
        if secrets_path.exists():
            with secrets_path.open("r", encoding="utf-8") as f:
                secrets = yaml.safe_load(f) or {}
        else:
            secrets = {}
        secrets.setdefault("api_keys", {})[target_provider] = body.api_key
        secrets_path.write_text(
            yaml.dump(secrets, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        # Met aussi à jour le CFG en mémoire
        provider_cfg.setdefault("api_keys", {})[target_provider] = body.api_key

    # config.yaml sans les clés API
    provider_cfg_to_save = {k: v for k, v in provider_cfg.items() if k != "api_keys"}
    provider_cfg_to_save["api_keys"] = {
        p: "" for p in ("openai", "anthropic", "mistral")
    }
    cfg_copy = {
        "model": dict(w.CFG["model"]),
        "inference": dict(w.CFG["inference"]),
        "paths": dict(w.CFG.get("paths", {})),
        "llm_provider": provider_cfg_to_save,
    }
    w.CONFIG_PATH.write_text(
        yaml.dump(cfg_copy, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )

    if body.mode == "local" and previous_mode != "local":
        # Passage de API → local : charger le modèle GGUF
        state.set_model_status(state.ModelStatus.loading)
        asyncio.create_task(reload_model_async())
    elif body.mode == "api":
        # En mode API pas de modèle à charger
        state.set_model_status(state.ModelStatus.loaded)

    return {"ok": True}


class TestConnectionBody(BaseModel):
    provider: str
    api_key: Optional[str] = None  # si absent, utilise la clé sauvegardée
    model: str


@router.post("/settings/test-connection")
async def test_connection(body: TestConnectionBody):
    """Effectue un appel minimal pour vérifier que la clé API fonctionne."""
    import asyncio as _asyncio
    import logging
    import traceback

    log = logging.getLogger("wiki.test_connection")

    provider_cfg = w.CFG.get("llm_provider", {})
    api_keys = provider_cfg.get("api_keys", {})
    api_key = body.api_key or api_keys.get(body.provider, "")

    log.info("[test-connection] provider=%s model=%s key_len=%d",
             body.provider, body.model, len(api_key))

    if not api_key:
        raise HTTPException(400, f"Clé API {body.provider} manquante.")

    def _test() -> str:
        import sys
        log.info("[test-connection] thread stdout encoding=%s stderr encoding=%s default=%s",
                 getattr(sys.stdout, "encoding", "?"),
                 getattr(sys.stderr, "encoding", "?"),
                 sys.getdefaultencoding())

        # Valider la clé avant tout appel réseau
        try:
            api_key.encode("ascii")
        except UnicodeEncodeError as ue:
            bad_char = api_key[ue.start]
            raise ValueError(
                f"La cle API contient un caractere non-ASCII a la position {ue.start} "
                f"(U+{ord(bad_char):04X}). Verifiez que vous avez copie la bonne cle "
                f"(longueur actuelle : {len(api_key)} caracteres)."
            )
        if len(api_key) > 500:
            raise ValueError(
                f"La cle API semble invalide : {len(api_key)} caracteres "
                f"(une cle OpenAI/Anthropic/Mistral fait moins de 200 caracteres). "
                f"Collez uniquement la cle, pas un JSON ou une page entiere."
            )

        if body.provider == "openai":
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError("pip install openai>=1.0")
            log.info("[test-connection] openai import OK, creating client")
            client = OpenAI(api_key=api_key)
            log.info("[test-connection] client created, sending request")
            resp = client.chat.completions.create(
                model=body.model,
                messages=[{"role": "user", "content": "Say ok."}],
                max_tokens=5,
            )
            log.info("[test-connection] response received")
            return resp.choices[0].message.content.strip()

        elif body.provider == "anthropic":
            try:
                import anthropic as _ant
            except ImportError:
                raise RuntimeError("pip install anthropic>=0.20")
            log.info("[test-connection] anthropic import OK, creating client")
            client = _ant.Anthropic(api_key=api_key)
            log.info("[test-connection] client created, sending request")
            resp = client.messages.create(
                model=body.model,
                messages=[{"role": "user", "content": "Say ok."}],
                max_tokens=5,
            )
            log.info("[test-connection] response received")
            return resp.content[0].text.strip()

        elif body.provider == "mistral":
            try:
                from mistralai import Mistral
            except ImportError:
                raise RuntimeError("pip install mistralai>=0.4")
            log.info("[test-connection] mistral import OK, creating client")
            client = Mistral(api_key=api_key)
            log.info("[test-connection] client created, sending request")
            resp = client.chat.complete(
                model=body.model,
                messages=[{"role": "user", "content": "Say ok."}],
                max_tokens=5,
            )
            log.info("[test-connection] response received")
            return resp.choices[0].message.content.strip()

        else:
            raise ValueError(f"Fournisseur inconnu : {body.provider}")

    loop = _asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _test)
        log.info("[test-connection] success: %r", result)
        return {"ok": True, "message": f"Connexion reussie - reponse : {result!r}"}
    except RuntimeError as e:
        log.warning("[test-connection] RuntimeError: %s", e)
        raise HTTPException(400, str(e))
    except Exception as e:
        tb = traceback.format_exc()
        log.error("[test-connection] EXCEPTION type=%s\n%s", type(e).__name__, tb)
        try:
            msg = str(e)
        except Exception:
            msg = repr(e)
        # Ne pas exposer la clé dans le message d'erreur
        if api_key and api_key in msg:
            msg = msg.replace(api_key, "***")
            tb = tb.replace(api_key, "***")
        log.error("[test-connection] msg (raw): %r", msg)
        # Encode en ASCII safe pour éviter tout problème Windows dans la réponse JSON
        msg = msg.encode("ascii", errors="replace").decode("ascii")
        raise HTTPException(502, f"Erreur {body.provider} : {msg}")
