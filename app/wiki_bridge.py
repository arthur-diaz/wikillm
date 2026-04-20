"""Bridge entre wiki.py et FastAPI.

- Wrappers async pour les appels LLM bloquants (thread pool)
- Streaming token-par-token via générateur async
- Pipeline ingest avec événements de progression
"""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import wiki as w

# Le LLM n'est pas thread-safe : un seul worker
_executor = ThreadPoolExecutor(max_workers=1)
_llm_lock = asyncio.Lock()


async def reload_model_async() -> None:
    """Recharge le modèle LLM (bloque les requêtes pendant le rechargement)."""
    from app import state
    async with _llm_lock:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, w.reset_llm)
            await loop.run_in_executor(_executor, w._get_llm)
            state.set_model_status(state.ModelStatus.loaded)
        except Exception as e:
            state.set_model_status(state.ModelStatus.error, str(e))


async def run_blocking(fn, *args, **kwargs):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, lambda: fn(*args, **kwargs))


# ---------------------------------------------------------------------------
# Streaming token-par-token
# ---------------------------------------------------------------------------

def _llm_stream_sync(system: str, user: str):
    llm = w._get_llm()
    inf = w.CFG["inference"]
    for chunk in llm.create_chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=inf["temperature"],
        top_p=inf["top_p"],
        max_tokens=inf["max_tokens"],
        stream=True,
    ):
        delta = chunk["choices"][0]["delta"].get("content", "")
        if delta:
            yield delta


async def llm_stream(system: str, user: str) -> AsyncGenerator[str, None]:
    """Génère des tokens en async depuis le thread LLM."""
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _produce():
        try:
            for token in _llm_stream_sync(system, user):
                asyncio.run_coroutine_threadsafe(queue.put(token), loop).result()
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    async with _llm_lock:
        loop.run_in_executor(_executor, _produce)
        while True:
            token = await queue.get()
            if token is None:
                break
            yield token


# ---------------------------------------------------------------------------
# Pipeline ingest avec progression
# ---------------------------------------------------------------------------

async def ingest_stream(source_path: Path) -> AsyncGenerator[dict, None]:
    from app import state
    ctx = state.get_ctx()
    today = datetime.now().strftime("%Y-%m-%d")

    yield {"step": "reading", "msg": f"Lecture de {source_path.name}"}
    raw_text = source_path.read_text(encoding="utf-8", errors="replace")

    source_content = await run_blocking(w.summarize_long_source, raw_text)
    index = w.read_index(ctx) or "(index vide)"

    yield {"step": "extracting", "msg": "Extraction structurée via LLM…"}
    try:
        async with _llm_lock:
            data = await run_blocking(
                w.llm_json,
                w.INGEST_SYSTEM,
                w.INGEST_USER_TEMPLATE.format(
                    index=index,
                    source_path=source_path.name,
                    source_content=source_content,
                ),
            )
    except Exception as e:
        yield {"step": "error", "msg": str(e)}
        return

    slug = w.slugify(data.get("slug") or data.get("title", "source"))
    source_page_slug = f"{today}-{slug}"
    source_page_path = ctx.wiki_dir / "sources" / f"{source_page_slug}.md"

    counter = 2
    while source_page_path.exists():
        source_page_slug = f"{today}-{slug}-{counter}"
        source_page_path = ctx.wiki_dir / "sources" / f"{source_page_slug}.md"
        counter += 1

    yield {"step": "source_page", "msg": f"Création de [[{source_page_slug}]]"}

    entities_links = [f"[[{w.slugify(e.get('slug') or e['name'])}]]" for e in data.get("entities", [])]
    concepts_links = [f"[[{w.slugify(c.get('slug') or c['name'])}]]" for c in data.get("concepts", [])]

    source_meta = {
        "type": "source",
        "title": data.get("title", "Sans titre"),
        "slug": slug,
        "ingested": today,
        "source_path": source_path.name,
        "source_kind": "article",
        "tags": data.get("tags", []),
        "related_entities": entities_links,
        "related_concepts": concepts_links,
    }
    key_points_md = "\n".join(f"- {kp}" for kp in data.get("key_points", []))
    contradictions = data.get("contradictions") or []
    contradictions_md = ""
    if contradictions:
        contradictions_md = "\n\n## Contradictions\n\n" + "\n".join(f"- {c}" for c in contradictions)

    source_content_md = (
        f"# {data.get('title', 'Sans titre')}\n\n"
        f"{data.get('summary_one_line', '')}\n\n"
        f"## Points clés\n\n{key_points_md}"
        f"{contradictions_md}\n\n"
        f"## Entités\n\n{', '.join(entities_links) or '_aucune_'}\n\n"
        f"## Concepts\n\n{', '.join(concepts_links) or '_aucun_'}\n"
    )
    w.WikiPage(source_page_path, source_meta, source_content_md).save()

    created_stubs: list[str] = []
    updated_stubs: list[str] = []

    for ent in data.get("entities", []):
        ent_slug = w.slugify(ent.get("slug") or ent["name"])
        ent_path = ctx.wiki_dir / "entities" / f"{ent_slug}.md"
        yield {"step": "entity", "msg": f"Entité : {ent['name']}"}
        if ent.get("new") or not ent_path.exists():
            w.WikiPage(ent_path, {
                "type": "entity", "name": ent["name"], "slug": ent_slug,
                "kind": ent.get("kind", "other"), "aliases": [],
                "sources": [f"[[{source_page_slug}]]"],
                "last_updated": today, "tags": data.get("tags", []),
            }, f"# {ent['name']}\n\n{ent.get('note', '')}\n").save()
            created_stubs.append(f"[[{ent_slug}]] (entité)")
        else:
            async with _llm_lock:
                await run_blocking(w._enrich_existing_page, ent_path, source_page_slug, ent.get("note", ""), today, updated_stubs, "entité")

    for cpt in data.get("concepts", []):
        cpt_slug = w.slugify(cpt.get("slug") or cpt["name"])
        cpt_path = ctx.wiki_dir / "concepts" / f"{cpt_slug}.md"
        yield {"step": "concept", "msg": f"Concept : {cpt['name']}"}
        if cpt.get("new") or not cpt_path.exists():
            w.WikiPage(cpt_path, {
                "type": "concept", "name": cpt["name"], "slug": cpt_slug,
                "aliases": [], "sources": [f"[[{source_page_slug}]]"],
                "last_updated": today, "tags": data.get("tags", []),
            }, f"# {cpt['name']}\n\n{cpt.get('note', '')}\n").save()
            created_stubs.append(f"[[{cpt_slug}]] (concept)")
        else:
            async with _llm_lock:
                await run_blocking(w._enrich_existing_page, cpt_path, source_page_slug, cpt.get("note", ""), today, updated_stubs, "concept")

    w._update_index_entry("Sources", f"- [[{source_page_slug}]] — {data.get('summary_one_line', '')} _(ingéré {today})_", ctx)
    for ent in data.get("entities", []):
        if ent.get("new"):
            es = w.slugify(ent.get("slug") or ent["name"])
            w._update_index_entry("Entités", f"- [[{es}]] — {(ent.get('note', '') or ent['name']).splitlines()[0]}", ctx)
    for cpt in data.get("concepts", []):
        if cpt.get("new"):
            cs = w.slugify(cpt.get("slug") or cpt["name"])
            w._update_index_entry("Concepts", f"- [[{cs}]] — {(cpt.get('note', '') or cpt['name']).splitlines()[0]}", ctx)

    w.append_log("ingest", data.get("title", slug),
        f"- Source : `{source_path.name}`\n"
        f"- Page créée : [[{source_page_slug}]]\n"
        f"- Stubs créés : {', '.join(created_stubs) or '_aucun_'}\n"
        f"- Pages enrichies : {', '.join(updated_stubs) or '_aucune_'}\n"
        f"- Contradictions : {len(contradictions)}\n",
        ctx,
    )

    yield {
        "step": "done",
        "msg": "Ingest terminé",
        "data": {
            "source_slug": source_page_slug,
            "title": data.get("title", "Sans titre"),
            "summary": data.get("summary_one_line", ""),
            "created": 1 + len(created_stubs),
            "enriched": len(updated_stubs),
            "contradictions": contradictions,
            "entities": [e["name"] for e in data.get("entities", [])],
            "concepts": [c["name"] for c in data.get("concepts", [])],
        },
    }
