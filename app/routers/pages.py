"""Wiki browser — liste et lecture des pages + graphe."""
from __future__ import annotations

import json

import frontmatter
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

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
