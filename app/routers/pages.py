"""Wiki browser — liste et lecture des pages + graphe."""
from __future__ import annotations

import re
from datetime import date

import frontmatter
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import wiki as w
from app import state

router = APIRouter(tags=["pages"])


@router.get("/pages")
async def list_pages(type: str | None = None, tag: str | None = None):
    ctx = state.get_ctx()
    result = []
    for p in w.list_wiki_pages(ctx):
        try:
            post = frontmatter.load(p)
            meta = dict(post.metadata)
        except Exception:
            meta = {}
        if type and meta.get("type") != type:
            continue
        if tag and tag not in meta.get("tags", []):
            continue
        result.append({
            "slug": p.stem,
            "type": meta.get("type", ""),
            "name": meta.get("name") or meta.get("title") or p.stem,
            "tags": meta.get("tags", []),
            "last_updated": meta.get("last_updated", ""),
            "sources_count": len(meta.get("sources", [])),
        })
    result.sort(key=lambda x: (x["type"], x["name"].lower()))
    return result


@router.get("/pages/{slug}")
async def get_page(slug: str):
    ctx = state.get_ctx()
    slug_map = {p.stem: p for p in w.list_wiki_pages(ctx)}
    if slug not in slug_map:
        raise HTTPException(404, f"[[{slug}]] introuvable")
    post = frontmatter.load(slug_map[slug])
    return {"slug": slug, "meta": dict(post.metadata), "content": post.content}


# ---------------------------------------------------------------------------
# PATCH — mise à jour du contenu markdown
# ---------------------------------------------------------------------------

class PageUpdate(BaseModel):
    content: str


@router.patch("/pages/{slug}")
async def update_page(slug: str, body: PageUpdate):
    ctx = state.get_ctx()
    slug_map = {p.stem: p for p in w.list_wiki_pages(ctx)}
    if slug not in slug_map:
        raise HTTPException(404, f"[[{slug}]] introuvable")
    post = frontmatter.load(slug_map[slug])
    meta = dict(post.metadata)
    meta["last_updated"] = date.today().isoformat()
    page = w.WikiPage(path=slug_map[slug], meta=meta, content=body.content)
    page.save()
    return {"ok": True, "last_updated": meta["last_updated"]}


# ---------------------------------------------------------------------------
# DELETE — suppression d'une page
# ---------------------------------------------------------------------------

@router.delete("/pages/{slug}")
async def delete_page(slug: str):
    ctx = state.get_ctx()
    slug_map = {p.stem: p for p in w.list_wiki_pages(ctx)}
    if slug not in slug_map:
        raise HTTPException(404, f"[[{slug}]] introuvable")
    path = slug_map[slug]
    path.unlink()
    _remove_from_index(slug, ctx)
    w.append_log("DELETE", slug, f"Page [[{slug}]] supprimée manuellement.", ctx)
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST — création manuelle d'une page
# ---------------------------------------------------------------------------

class PageCreate(BaseModel):
    type: str
    name: str
    tags: list[str] = []
    slug: str | None = None


@router.post("/pages")
async def create_page(body: PageCreate):
    ctx = state.get_ctx()
    if body.type not in ("source", "entity", "concept"):
        raise HTTPException(400, "type doit être source, entity ou concept")
    slug = body.slug or re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")
    if not slug:
        raise HTTPException(400, "nom invalide")
    slug_map = {p.stem: p for p in w.list_wiki_pages(ctx)}
    if slug in slug_map:
        raise HTTPException(409, f"[[{slug}]] existe déjà")
    subdir = {"source": "sources", "entity": "entities", "concept": "concepts"}[body.type]
    path = ctx.wiki_dir / subdir / f"{slug}.md"
    today = date.today().isoformat()
    meta = {
        "type": body.type,
        "name": body.name,
        "slug": slug,
        "aliases": [],
        "sources": [],
        "tags": body.tags,
        "last_updated": today,
    }
    page = w.WikiPage(path=path, meta=meta, content=f"# {body.name}\n\n")
    page.save()
    section = {"source": "Sources", "entity": "Entités", "concept": "Concepts"}[body.type]
    w._update_index_entry(section, f"- [[{slug}]] — {body.name}", ctx)
    w.append_log("CREATE", slug, f"Page [[{slug}]] créée manuellement.", ctx)
    return {"slug": slug, "meta": meta, "content": page.content}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _remove_from_index(slug: str, ctx: w.WikiContext) -> None:
    index_file = ctx.index_file
    if not index_file.exists():
        return
    text = index_file.read_text(encoding="utf-8")
    text = re.sub(
        rf"^- \[\[{re.escape(slug)}\]\][^\n]*\n?",
        "",
        text,
        flags=re.MULTILINE,
    )
    index_file.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Graphe
# ---------------------------------------------------------------------------

@router.get("/graph-data")
async def graph_data():
    ctx = state.get_ctx()
    pages = w.list_wiki_pages(ctx)
    nodes, links = [], []
    slug_set = set()

    for p in pages:
        slug = p.stem
        slug_set.add(slug)
        try:
            post = frontmatter.load(p)
            meta = post.metadata
            full_text = frontmatter.dumps(post)
        except Exception:
            meta = {}
            full_text = p.read_text("utf-8", errors="replace")

        nodes.append({
            "id": slug,
            "label": meta.get("name") or meta.get("title") or slug,
            "type": meta.get("type", "concept"),
            "src": len(meta.get("sources", [])),
        })
        for m in w.LINK_RE.finditer(full_text):
            target = m.group(1).strip()
            if target != slug:
                links.append({"source": slug, "target": target})

    # Nœuds fantômes
    all_targets = {lnk["target"] for lnk in links}
    for t in all_targets - slug_set:
        nodes.append({"id": t, "label": t.replace("-", " ").title(), "type": "concept", "src": 0})
        slug_set.add(t)

    # Dédoublonnage
    seen: set[tuple[str, str]] = set()
    deduped = []
    for lnk in links:
        key = (lnk["source"], lnk["target"])
        if key not in seen and lnk["target"] in slug_set:
            seen.add(key)
            deduped.append(lnk)

    return {"nodes": nodes, "links": deduped}
