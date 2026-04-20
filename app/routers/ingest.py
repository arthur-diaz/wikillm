"""Ingest — upload de fichier + stream SSE de progression."""
from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, UploadFile
from sse_starlette.sse import EventSourceResponse

import wiki as w
from app import state
from app.wiki_bridge import ingest_stream

router = APIRouter(tags=["ingest"])


@router.post("/ingest")
async def ingest_file(file: UploadFile):
    ctx = state.get_ctx()
    dest = ctx.raw_dir / file.filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    async def event_gen():
        async for event in ingest_stream(dest):
            yield {"data": json.dumps(event, ensure_ascii=False)}

    return EventSourceResponse(event_gen())


@router.get("/raw-files")
async def list_raw_files():
    raw_dir = state.get_ctx().raw_dir
    if not raw_dir.exists():
        return []
    return [
        {"name": f.name, "size": f.stat().st_size, "suffix": f.suffix}
        for f in sorted(raw_dir.iterdir()) if f.is_file()
    ]
