"""Query — réponse streamée via SSE."""
from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

import wiki as w
from app import state
from app.wiki_bridge import llm_stream, _llm_lock, run_blocking

router = APIRouter(tags=["query"])

_SELECT_SYSTEM = (
    "Tu es un assistant de recherche. Étant donné l'index d'un wiki et une question, "
    "sélectionne les 3 à 8 pages les plus pertinentes. "
    "Réponds en JSON strict avec les slugs tels qu'ils apparaissent dans l'index."
)
_SELECT_USER = """Index :\n\n{index}\n\nQuestion : {question}\n\nJSON attendu :\n{{"pages": ["slug-1"], "reasoning": "..."}}"""

_ANSWER_SYSTEM = (
    "Tu es un assistant qui répond en s'appuyant STRICTEMENT sur les pages wiki fournies. "
    "Si l'information n'est pas dans les pages, dis-le. "
    "Cite avec des liens [[slug]]. Pas d'invention ni d'extrapolation."
)
_ANSWER_USER = "Question : {question}\n\nPages :\n\n{pages}\n\nRéponds en markdown concis avec des liens [[page]]."

_SUGGEST_SYSTEM = (
    "Tu es un expert en gestion de connaissances. "
    "Une question a été posée mais le wiki ne contient aucune information pertinente. "
    "Propose 2-3 types de sources ou documents concrets à ingérer pour pouvoir répondre. "
    "Sois bref et pratique. Réponds en français avec des tirets."
)


@router.get("/query")
async def query(question: str, file_back: bool = False):
    ctx = state.get_ctx()
    index = w.read_index(ctx)

    async def event_gen():
        if not index.strip():
            yield {"event": "error", "data": "Index vide — ingère d'abord des sources."}
            return

        yield {"event": "status", "data": "Sélection des pages pertinentes…"}
        async with _llm_lock:
            selection = await run_blocking(
                w.llm_json, _SELECT_SYSTEM,
                _SELECT_USER.format(index=index, question=question),
            )

        selected_slugs = selection.get("pages", [])
        if not selected_slugs:
            yield {"event": "status", "data": "Aucune page pertinente — suggestions de sources…"}
            async with _llm_lock:
                suggestions = await run_blocking(
                    w.llm_chat,
                    _SUGGEST_SYSTEM,
                    f"Question : {question}\n\nPropose 2-3 types de sources à ingérer.",
                    max_tokens=300,
                )
            w.append_log(
                "query", question[:80],
                "- Pages : (aucune)\n- Suggestions de sources : oui\n",
                ctx,
            )
            yield {"event": "done", "data": json.dumps({"pages": [], "suggestions": suggestions})}
            return

        all_pages = w.list_wiki_pages(ctx)
        slug_to_path = {p.stem: p for p in all_pages}
        loaded: list[tuple[str, str]] = []
        budget = w.token_budget() - 500

        for raw_slug in selected_slugs:
            clean = raw_slug.strip().strip("[]")
            p = slug_to_path.get(clean)
            if p is None:
                continue
            content = p.read_text(encoding="utf-8")
            if sum(w.estimate_tokens(c) for _, c in loaded) + w.estimate_tokens(content) > budget:
                break
            loaded.append((clean, content))

        yield {"event": "pages", "data": json.dumps([s for s, _ in loaded])}

        pages_blob = "\n\n---\n\n".join(f"### [[{slug}]]\n\n{content}" for slug, content in loaded)
        yield {"event": "status", "data": f"Synthèse depuis {len(loaded)} page(s)…"}

        full_answer: list[str] = []
        async for token in llm_stream(_ANSWER_SYSTEM, _ANSWER_USER.format(question=question, pages=pages_blob)):
            full_answer.append(token)
            yield {"event": "token", "data": token}

        answer = "".join(full_answer)

        if file_back:
            today = datetime.now().strftime("%Y-%m-%d")
            slug = w.slugify(question[:60])
            page_path = ctx.wiki_dir / "concepts" / f"{slug}.md"
            refs = w.LINK_RE.findall(answer)
            w.WikiPage(page_path, {
                "type": "concept", "name": question, "slug": slug,
                "sources": [f"[[{r}]]" for r in refs],
                "origin_query": question, "origin_date": today,
                "last_updated": today, "tags": ["file-back"],
            }, f"# {question}\n\n{answer}\n").save()
            w._update_index_entry("Concepts", f"- [[{slug}]] — file-back _(créé {today})_", ctx)

        w.append_log("query", question[:80],
            f"- Pages : {', '.join(f'[[{s}]]' for s, _ in loaded)}\n"
            f"- File-back : {'oui' if file_back else 'non'}\n",
            ctx,
        )
        yield {"event": "done", "data": json.dumps({"pages": [s for s, _ in loaded], "file_back": file_back})}

    return EventSourceResponse(event_gen())
