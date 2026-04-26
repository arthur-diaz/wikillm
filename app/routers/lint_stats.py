"""Lint et Stats — vérifications structurelles + lint sémantique SSE."""
from __future__ import annotations

import json
import re
from collections import Counter

import frontmatter
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

import wiki as w
from app import state
from app.wiki_bridge import _llm_lock, run_blocking

router = APIRouter(tags=["lint", "stats"])


# ---------------------------------------------------------------------------
# Lint structurel (rapide, sans LLM)
# ---------------------------------------------------------------------------

@router.get("/lint")
async def lint(auto_fix: bool = False):
    ctx = state.get_ctx()
    pages = w.list_wiki_pages(ctx)
    if not pages:
        return {
            "pages": 0, "total_tokens": 0,
            "broken_links": [], "invalid_frontmatter": [],
            "orphans": [], "in_wiki_not_index": [], "in_index_not_wiki": [],
            "date_inconsistencies": [],
        }

    known_slugs = {p.stem for p in pages}
    broken_links, invalid_fm = [], []
    inbound = {s: 0 for s in known_slugs}
    total_tokens = 0

    for page_path in pages:
        rel = str(page_path.relative_to(w.PROJECT_ROOT))
        raw = page_path.read_text(encoding="utf-8", errors="replace")
        total_tokens += w.estimate_tokens(raw)
        try:
            post = frontmatter.load(page_path)
        except Exception as e:
            invalid_fm.append({"page": rel, "reason": f"parse error: {e}"})
            continue

        meta = post.metadata
        if not meta:
            invalid_fm.append({"page": rel, "reason": "frontmatter absent"})
        else:
            ptype = meta.get("type")
            if ptype not in {"source", "entity", "concept"}:
                invalid_fm.append({"page": rel, "reason": f"type invalide: {ptype!r}"})
            required = {"source": ["title", "ingested"], "entity": ["name"], "concept": ["name"]}
            for field in required.get(ptype, []):
                if field not in meta:
                    invalid_fm.append({"page": rel, "reason": f"champ manquant: {field}"})

        for m in w.LINK_RE.finditer(frontmatter.dumps(post)):
            target = m.group(1).strip()
            if target not in known_slugs:
                broken_links.append({"page": rel, "target": target})
            else:
                inbound[target] += 1

    orphans = [
        s for s, c in inbound.items()
        if c == 0 and not any(p.stem == s and "sources" in p.parts for p in pages)
    ]
    index_text = w.read_index(ctx)
    index_slugs = set(w.LINK_RE.findall(index_text))
    in_wiki_not_index = sorted(known_slugs - index_slugs)
    in_index_not_wiki = sorted(index_slugs - known_slugs)

    # Cohérence des dates (structurelle, sans LLM — SCHEMA §9)
    date_inconsistencies = w._lint_date_consistency(pages, ctx)

    if auto_fix:
        for s in in_wiki_not_index:
            p = next((pg for pg in pages if pg.stem == s), None)
            if not p:
                continue
            desc = w._extract_page_description(p)
            if "entities" in p.parts:
                section = "Entités"
            elif "analyses" in p.parts:
                section = "Analyses"
            elif "concepts" in p.parts:
                section = "Concepts"
            else:
                section = "Sources"
            w._update_index_entry(section, f"- [[{s}]] — {desc}", ctx)

    w.append_log(
        "lint", "health check",
        f"- Pages: {len(pages)} | tokens: {total_tokens:,} | "
        f"liens cassés: {len(broken_links)} | frontmatter: {len(invalid_fm)} | "
        f"orphelines: {len(orphans)} | désync: {len(in_wiki_not_index) + len(in_index_not_wiki)} | "
        f"dates: {len(date_inconsistencies)}\n",
        ctx,
    )

    return {
        "pages": len(pages), "total_tokens": total_tokens,
        "broken_links": broken_links, "invalid_frontmatter": invalid_fm,
        "orphans": orphans, "in_wiki_not_index": in_wiki_not_index,
        "in_index_not_wiki": in_index_not_wiki, "auto_fixed": auto_fix,
        "date_inconsistencies": date_inconsistencies,
    }


# ---------------------------------------------------------------------------
# Lint sémantique — endpoint SSE (résultats streamés au fur et à mesure)
# ---------------------------------------------------------------------------

@router.get("/lint/semantic")
async def lint_semantic():
    """Lint sémantique SSE. Événements émis :
    estimate, progress, date_inconsistencies, missing_concepts,
    contradictions, source_gaps, stale_claims, done.
    """
    ctx = state.get_ctx()
    pages = w.list_wiki_pages(ctx)

    async def event_gen():
        if not pages:
            yield {"event": "done", "data": "{}"}
            return

        cache = await run_blocking(w._lint_cache_load, ctx)

        # Estimation du coût LLM
        estimate = await run_blocking(w._semantic_lint_estimate, pages, ctx, cache)
        yield {"event": "estimate", "data": json.dumps({"llm_calls": estimate}, ensure_ascii=False)}

        # ── 1. Cohérence des dates (structurelle, sans LLM) ──────────────
        yield {"event": "progress", "data": json.dumps({"step": "date_consistency", "msg": "Vérification des dates…"})}
        date_issues = await run_blocking(w._lint_date_consistency, pages, ctx)
        yield {"event": "date_inconsistencies", "data": json.dumps(date_issues, ensure_ascii=False)}

        # ── 2. Concepts manquants ─────────────────────────────────────────
        yield {"event": "progress", "data": json.dumps({"step": "missing_concepts", "msg": "Recherche des concepts sans page…"})}
        async with _llm_lock:
            missing = await run_blocking(w._lint_missing_concepts, pages, ctx, cache)
        yield {"event": "missing_concepts", "data": json.dumps(missing, ensure_ascii=False)}

        # ── 3. Contradictions ─────────────────────────────────────────────
        yield {"event": "progress", "data": json.dumps({"step": "contradictions", "msg": "Détection des contradictions inter-pages…"})}
        async with _llm_lock:
            contradictions = await run_blocking(w._lint_contradictions, pages, ctx, cache)
        yield {"event": "contradictions", "data": json.dumps(contradictions, ensure_ascii=False)}

        # ── 4. Lacunes de sources ─────────────────────────────────────────
        yield {"event": "progress", "data": json.dumps({"step": "source_gaps", "msg": "Analyse des lacunes thématiques…"})}
        async with _llm_lock:
            gaps = await run_blocking(w._lint_source_gaps, ctx, cache)
        yield {"event": "source_gaps", "data": json.dumps(gaps, ensure_ascii=False)}

        # ── 5. Claims obsolètes ───────────────────────────────────────────
        yield {"event": "progress", "data": json.dumps({"step": "stale_claims", "msg": "Détection des affirmations obsolètes…"})}
        async with _llm_lock:
            stale = await run_blocking(w._lint_stale_claims, pages, ctx, cache)
        yield {"event": "stale_claims", "data": json.dumps(stale, ensure_ascii=False)}

        await run_blocking(w._lint_cache_save, cache, ctx)
        w.append_log(
            "lint", "lint sémantique",
            f"- Concepts manquants: {len(missing)} | Contradictions: {len(contradictions)} | "
            f"Lacunes: {len(gaps)} | Obsolètes: {len(stale)}\n",
            ctx,
        )
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_gen())


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def stats():
    ctx = state.get_ctx()
    pages = w.list_wiki_pages(ctx)
    sources = [p for p in pages if "sources" in p.parts]
    entities = [p for p in pages if "entities" in p.parts]
    concepts = [p for p in pages if "concepts" in p.parts]
    analyses = [p for p in pages if "analyses" in p.parts]
    total_chars = total_tokens = 0
    tags_counter: Counter = Counter()

    for p in pages:
        text = p.read_text(encoding="utf-8", errors="replace")
        total_chars += len(text)
        total_tokens += w.estimate_tokens(text)
        try:
            post = frontmatter.load(p)
            for tag in post.metadata.get("tags", []):
                tags_counter[tag] += 1
        except Exception:
            pass

    log_entries = []
    if ctx.log_file.exists():
        log_entries = re.findall(r"^## \[.+$", ctx.log_file.read_text(encoding="utf-8"), re.MULTILINE)

    raw_count = sum(1 for f in ctx.raw_dir.rglob("*") if f.is_file()) if ctx.raw_dir.exists() else 0

    return {
        "raw_files": raw_count,
        "pages": {
            "total": len(pages), "sources": len(sources),
            "entities": len(entities), "concepts": len(concepts),
            "analyses": len(analyses),
        },
        "size": {"chars": total_chars, "tokens": total_tokens},
        "budget": w.token_budget(),
        "top_tags": tags_counter.most_common(10),
        "log_entries": len(log_entries),
        "last_log": log_entries[-1] if log_entries else None,
    }
