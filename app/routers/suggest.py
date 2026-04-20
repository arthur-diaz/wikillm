"""Suggest — analyse LLM streamée du wiki."""
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

import wiki as w
from app import state
from app.wiki_bridge import llm_stream

router = APIRouter(tags=["suggest"])

_SYSTEM = """\
Tu es un assistant d'analyse de wiki. On te donne l'index d'un wiki personnel.
Identifie :
1. **Questions à explorer** : 3-5 questions intéressantes à poser au wiki.
2. **Sources manquantes** : 2-3 types de sources qui enrichiraient le wiki.
3. **Pages à créer** : 2-4 concepts ou entités mentionnés sans page dédiée.
4. **Connexions sous-exploitées** : liens entre pages à renforcer.
Réponds en markdown concis avec des liens [[slug]].
"""


@router.get("/suggest")
async def suggest():
    index = w.read_index(state.get_ctx())

    async def event_gen():
        if not index.strip():
            yield {"event": "error", "data": "Wiki vide."}
            return
        async for token in llm_stream(_SYSTEM, f"Index du wiki :\n\n{index}"):
            yield {"event": "token", "data": token}
        yield {"event": "done", "data": ""}

    return EventSourceResponse(event_gen())
