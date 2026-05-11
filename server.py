"""
Deep Agent — FastAPI server.

Endpoints:
  POST /threads                          create thread
  GET  /threads/{id}                     get thread
  POST /threads/{id}/run                 run agent (SSE streaming)
  GET  /threads/{id}/files               list VFS files
  POST /threads/{id}/files               upload file to MinIO
  GET  /threads/{id}/files/{path:path}   presigned download URL
  POST /threads/{id}/interrupt/{run_id}  resume after request_options

SSE event format (newline-delimited, text/event-stream):
  data: {"type": "token",    "content": "..."}
  data: {"type": "tool_start", "name": "...", "input": {...}}
  data: {"type": "tool_end",   "name": "...", "output": "..."}
  data: {"type": "done",     "thread_id": "..."}
  data: {"type": "error",    "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from agent import storage
from agent.db import (
    init_schema,
    thread_create,
    thread_get,
    thread_update_status,
    run_create,
    run_update_status,
)
from agent.graph import build_agent

_agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    # Initialize DB tables (idempotent — safe to run on every startup)
    init_schema()
    _agent = await build_agent()
    yield


app = FastAPI(title="Deep Agent API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    message: str
    thread_id: str | None = None


class InterruptReply(BaseModel):
    choice: str


# ── Thread CRUD ───────────────────────────────────────────────────────────────

@app.post("/threads", status_code=201)
def create_thread():
    tid = str(uuid.uuid4())
    return thread_create(tid)


@app.get("/threads/{thread_id}")
def get_thread(thread_id: str):
    thread = thread_get(thread_id)
    if not thread:
        raise HTTPException(404, "Thread not found")
    return thread


# ── Run (SSE streaming) ───────────────────────────────────────────────────────

@app.post("/threads/{thread_id}/run")
async def run_thread(thread_id: str, req: RunRequest):
    # Ensure thread exists
    if not thread_get(thread_id):
        thread_create(thread_id)

    run_id = str(uuid.uuid4())
    run_create(run_id, thread_id, assistant_id="deep-agent")

    return StreamingResponse(
        _stream_agent(thread_id, run_id, req.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_agent(
    thread_id: str, run_id: str, message: str
) -> AsyncIterator[str]:
    config = {"configurable": {"thread_id": thread_id}}
    inputs = {"messages": [HumanMessage(content=message)]}

    thread_update_status(thread_id, "running")
    run_update_status(run_id, "running")

    try:
        async for event in _agent.astream_events(inputs, config=config, version="v2"):
            etype = event.get("event", "")
            chunk = _format_event(etype, event, None)
            if chunk:
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0)  # flush

        run_update_status(run_id, "completed")
        thread_update_status(thread_id, "idle")
        yield f"data: {json.dumps({'type': 'done', 'thread_id': thread_id})}\n\n"

    except Exception as exc:
        run_update_status(run_id, "failed")
        thread_update_status(thread_id, "idle")
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"


def _format_event(etype: str, event: dict[str, Any], _: Any) -> dict | None:
    if etype == "on_chat_model_stream":
        # Only emit tokens from the orchestrator agent node, not from sub-LLMs
        # inside skill tools. LangGraph v2 sets metadata.langgraph_node on each event.
        if event.get("metadata", {}).get("langgraph_node") != "agent":
            return None
        chunk = event.get("data", {}).get("chunk")
        if chunk and hasattr(chunk, "content") and chunk.content:
            return {"type": "token", "content": chunk.content}

    elif etype == "on_tool_start":
        return {
            "type": "tool_start",
            "name": event.get("name", ""),
            "input": event.get("data", {}).get("input", {}),
        }

    elif etype == "on_tool_end":
        output = event.get("data", {}).get("output", "")
        return {
            "type": "tool_end",
            "name": event.get("name", ""),
            "output": str(output)[:500],
        }

    return None


# ── Interrupt / resume ────────────────────────────────────────────────────────

@app.post("/threads/{thread_id}/interrupt/{run_id}")
async def resume_interrupt(thread_id: str, run_id: str, reply: InterruptReply):
    """Resume an agent paused at request_options by providing the human's choice."""
    config = {"configurable": {"thread_id": thread_id}}
    await _agent.aupdate_state(config, None, as_node="__interrupt__")
    return {"status": "resumed", "choice": reply.choice}


# ── File management ───────────────────────────────────────────────────────────

@app.get("/threads/{thread_id}/files")
def list_files(thread_id: str):
    paths = storage.vfs_list(thread_id)
    return {"files": paths}


@app.post("/threads/{thread_id}/files", status_code=201)
async def upload_file(thread_id: str, file: UploadFile = File(...)):
    content = await file.read()
    filename = file.filename or "upload"
    storage.vfs_write(thread_id, filename, content)
    # Embed text files for RAG
    try:
        storage.vfs_embed(thread_id, filename, content.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    return {"path": filename, "size": len(content)}


@app.get("/threads/{thread_id}/files/{path:path}")
def download_file(thread_id: str, path: str):
    try:
        url = storage.vfs_presigned_url(thread_id, path, expires_seconds=3600)
    except Exception as exc:
        raise HTTPException(404, str(exc))
    return {"url": url}


@app.get("/threads/{thread_id}/content/{path:path}")
def download_file_content(thread_id: str, path: str):
    from fastapi.responses import Response as FastAPIResponse
    data = storage.vfs_read(thread_id, path)
    if data is None:
        raise HTTPException(404, "File not found")
    filename = path.split("/")[-1]
    return FastAPIResponse(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/threads/{thread_id}/files/{path:path}", status_code=204)
def delete_file(thread_id: str, path: str):
    storage.vfs_delete(thread_id, path)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}
