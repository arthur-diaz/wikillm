"""Lint et Stats."""
from __future__ import annotations

import re
from collections import Counter

import frontmatter
from fastapi import APIRouter

import wiki as w
from app import state

router = APIRouter(tags=["lint", "stats"])


@router.get("/lint")
async def lint(auto_fix: bool = False):
    ctx = state.get_ctx()
    pages = w.list_wiki_pages(ctx)
    if not pages:
        return {"pages": 0, "total_tokens": 0, "broken_links": [], "invalid_frontmatter": [],
                "orphans": [], "in_wiki_not_index": [], "in_index_not_wiki": []}

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

    orphans = [s for s, c in inbound.items() if c == 0 and not any(p.stem == s and "sources" in p.parts for p in pages)]
    index_text = w.read_index(ctx)
    index_slugs = set(w.LINK_RE.findall(index_text))
    in_wiki_not_index = sorted(known_slugs - index_slugs)
    in_index_not_wiki = sorted(index_slugs - known_slugs)

    if auto_fix:
        for s in in_wiki_not_index:
            p = next((pg for pg in pages if pg.stem == s), None)
            if not p:
                continue
            section = "Entités" if "entities" in p.parts else "Concepts" if "concepts" in p.parts else "Sources"
            w._update_index_entry(section, f"- [[{s}]] — (ajouté par lint)", ctx)

    w.append_log("lint", "health check",
        f"- Pages: {len(pages)} | tokens: {total_tokens:,} | "
        f"liens cassés: {len(broken_links)} | frontmatter: {len(invalid_fm)} | "
        f"orphelines: {len(orphans)} | désync: {len(in_wiki_not_index) + len(in_index_not_wiki)}\n",
        ctx,
    )

    return {
        "pages": len(pages), "total_tokens": total_tokens,
        "broken_links": broken_links, "invalid_frontmatter": invalid_fm,
        "orphans": orphans, "in_wiki_not_index": in_wiki_not_index,
        "in_index_not_wiki": in_index_not_wiki, "auto_fixed": auto_fix,
    }


@router.get("/stats")
async def stats():
    ctx = state.get_ctx()
    pages = w.list_wiki_pages(ctx)
    sources = [p for p in pages if "sources" in p.parts]
    entities = [p for p in pages if "entities" in p.parts]
    concepts = [p for p in pages if "concepts" in p.parts]
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
        "pages": {"total": len(pages), "sources": len(sources), "entities": len(entities), "concepts": len(concepts)},
        "size": {"chars": total_chars, "tokens": total_tokens},
        "budget": w.token_budget(),
        "top_tags": tags_counter.most_common(10),
        "log_entries": len(log_entries),
        "last_log": log_entries[-1] if log_entries else None,
    }
