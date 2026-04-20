"""Search — BM25 full-text."""
from fastapi import APIRouter
import wiki as w
from app import state

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(q: str, n: int = 10):
    pages = w.list_wiki_pages(state.get_ctx())
    if not pages or not q.strip():
        return {"results": [], "total": 0}

    query_terms = w._tokenize_simple(q)
    docs, idf = w._build_bm25_index(pages)
    avg_dl = sum(len(w._tokenize_simple(d[2])) for d in docs) / len(docs)

    scored = []
    for slug, path, text in docs:
        score = w._bm25_score(query_terms, text, idf, avg_dl)
        if score > 0:
            scored.append({
                "slug": slug,
                "score": round(score, 3),
                "snippet": w._extract_snippet(text, query_terms),
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"results": scored[:n], "total": len(scored)}
