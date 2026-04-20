"""Gestion des corpus (multi-corpus)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import wiki as w
from app import state

router = APIRouter(tags=["corpus"])


_OBSIDIAN_APP = "{}"
_OBSIDIAN_APPEARANCE = "{}"
_OBSIDIAN_CORE_PLUGINS = """{
  "file-explorer": true,
  "global-search": true,
  "switcher": true,
  "graph": true,
  "backlink": true,
  "canvas": true,
  "outgoing-link": true,
  "tag-pane": true,
  "properties": true,
  "page-preview": true,
  "templates": true,
  "note-composer": true,
  "command-palette": true,
  "editor-status": true,
  "bookmarks": true,
  "outline": true,
  "word-count": true,
  "file-recovery": true
}"""
_OBSIDIAN_GRAPH = """{
  "collapse-filter": true,
  "showTags": false,
  "showAttachments": false,
  "hideUnresolved": false,
  "showOrphans": true,
  "collapse-color-groups": true,
  "colorGroups": [],
  "collapse-display": true,
  "showArrow": false,
  "textFadeMultiplier": 0,
  "nodeSizeMultiplier": 1,
  "lineSizeMultiplier": 1,
  "collapse-forces": true,
  "centerStrength": 0.518713248970312,
  "repelStrength": 10,
  "linkStrength": 1,
  "linkDistance": 250,
  "scale": 1,
  "close": true
}"""


def _init_corpus_structure(base_path) -> None:
    """Crée la structure de dossiers, fichiers wiki et vault Obsidian."""
    from pathlib import Path
    base = Path(base_path)
    for d in ["raw", "wiki/sources", "wiki/entities", "wiki/concepts", "wiki/.obsidian"]:
        (base / d).mkdir(parents=True, exist_ok=True)
    index = base / "wiki" / "index.md"
    if not index.exists():
        index.write_text("# Index du wiki\n\n", encoding="utf-8")
    log = base / "wiki" / "log.md"
    if not log.exists():
        log.write_text("", encoding="utf-8")
    obsidian = base / "wiki" / ".obsidian"
    (obsidian / "app.json").write_text(_OBSIDIAN_APP, encoding="utf-8")
    (obsidian / "appearance.json").write_text(_OBSIDIAN_APPEARANCE, encoding="utf-8")
    (obsidian / "core-plugins.json").write_text(_OBSIDIAN_CORE_PLUGINS, encoding="utf-8")
    (obsidian / "graph.json").write_text(_OBSIDIAN_GRAPH, encoding="utf-8")


@router.get("/corpus")
async def list_corpus():
    ws = state.load_workspaces()
    return {
        "active": ws["active"],
        "corpora": [
            {
                "id": name,
                "name": entry.get("name") or entry.get("label") or name,
            }
            for name, entry in ws["corpora"].items()
        ],
    }


class CreateCorpusBody(BaseModel):
    name: str


@router.post("/corpus")
async def create_corpus(body: CreateCorpusBody):
    label = body.name.strip()
    if not label:
        raise HTTPException(400, "Le nom ne peut pas être vide")

    slug = w.slugify(label)
    ws = state.load_workspaces()

    # Dédoublonnage du slug
    candidate = slug
    counter = 2
    while candidate in ws["corpora"]:
        candidate = f"{slug}-{counter}"
        counter += 1
    slug = candidate

    corpus_path = f"corpora/{slug}"
    _init_corpus_structure(w.PROJECT_ROOT / corpus_path)

    ws["corpora"][slug] = {"name": label, "path": corpus_path}
    state.save_workspaces(ws)
    return {"ok": True, "id": slug, "name": label}


class ActivateBody(BaseModel):
    name: str


@router.put("/corpus/active")
async def activate_corpus(body: ActivateBody):
    try:
        state.activate_corpus(body.name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "active": body.name}


@router.delete("/corpus/{name}")
async def delete_corpus(name: str):
    ws = state.load_workspaces()
    if name not in ws["corpora"]:
        raise HTTPException(404, f"Corpus inconnu : {name!r}")
    if name == "default":
        raise HTTPException(400, "Le corpus par défaut ne peut pas être supprimé")
    if ws["active"] == name:
        raise HTTPException(400, "Impossible de supprimer le corpus actif — active un autre corpus d'abord")
    del ws["corpora"][name]
    state.save_workspaces(ws)
    return {"ok": True}
