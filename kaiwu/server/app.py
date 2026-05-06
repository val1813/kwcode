"""
KwCode HTTP Server: FastAPI + SSE event streaming.
Wraps the orchestrator pipeline, serves TUI and VSCode plugin.
Port 7355 by default.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy imports to keep module importable without fastapi installed
_app_instance = None


def create_app(
    model_path: Optional[str] = None,
    ollama_url: str = "http://localhost:11434",
    ollama_model: str = "qwen3-8b",
    project_root: str = ".",
    verbose: bool = False,
    api_key: str = "",
):
    """Create and configure the FastAPI application."""
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse

    from kaiwu.server.models import (
        TaskRequest, TaskResponse, HealthResponse,
        StatusResponse, FileContent,
    )
    from kaiwu.server.pipeline_factory import build_pipeline

    app = FastAPI(
        title="KwCode Server",
        version="1.3.0",
        description="KwCode coding agent HTTP API with SSE streaming",
    )

    # CORS for localhost (TUI and VSCode plugin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:*", "http://127.0.0.1:*", "*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Pipeline singleton ──────────────────────────────────────
    project_root = os.path.abspath(project_root)
    start_time = time.time()

    gate, orchestrator, memory, registry = build_pipeline(
        model_path=model_path,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        project_root=project_root,
        verbose=verbose,
        api_key=api_key,
    )

    # ── Task state ──────────────────────────────────────────────
    # task_id -> asyncio.Queue for SSE events
    _task_queues: dict[str, asyncio.Queue] = {}
    # task_id -> result dict
    _task_results: dict[str, dict] = {}

    # ── Endpoints ───────────────────────────────────────────────

    @app.get("/api/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            status="ok",
            version="1.3.0",
            model=ollama_model or "local",
            project_root=project_root,
        )

    @app.get("/api/status", response_model=StatusResponse)
    async def status():
        from kaiwu.search.duckduckgo import _is_search_enabled
        return StatusResponse(
            model=ollama_model or "local",
            project_root=project_root,
            experts_loaded=len(registry.list_experts()) if hasattr(registry, 'list_experts') else 0,
            search_enabled=_is_search_enabled(),
            uptime_seconds=time.time() - start_time,
        )

    @app.post("/api/task", response_model=TaskResponse)
    async def submit_task(request: TaskRequest):
        """Submit a task for execution. Returns task_id for SSE streaming."""
        task_id = str(uuid.uuid4())[:8]
        queue = asyncio.Queue()
        _task_queues[task_id] = queue

        # Run orchestrator in background thread
        async def _execute():
            try:
                # EventBus handler to push events to queue
                def _event_handler(event: str, payload: dict):
                    try:
                        asyncio.get_event_loop().call_soon_threadsafe(
                            queue.put_nowait,
                            {"event": event, **payload}
                        )
                    except Exception:
                        pass

                orchestrator.bus.on("*", _event_handler)

                # Gate classification
                await queue.put({"event": "gate_start", "msg": "分析任务..."})
                gate_result = await asyncio.to_thread(
                    gate.classify,
                    request.input,
                    memory_context=memory.load(request.project_root or project_root),
                )
                await queue.put({"event": "gate_done", "msg": gate_result.get("expert_type", "chat")})

                # Run orchestrator
                result = await asyncio.to_thread(
                    orchestrator.run,
                    user_input=request.input,
                    gate_result=gate_result,
                    project_root=request.project_root or project_root,
                    no_search=request.no_search,
                    image_paths=request.image_paths or None,
                )

                # Store result
                _task_results[task_id] = result

                # Send completion event
                ctx = result.get("context")
                files_modified = []
                if ctx and ctx.generator_output and ctx.generator_output.get("patches"):
                    files_modified = [p["file"] for p in ctx.generator_output["patches"]]

                await queue.put({
                    "event": "task_completed",
                    "success": result.get("success", False),
                    "error": result.get("error", ""),
                    "elapsed": result.get("elapsed", 0),
                    "files_modified": files_modified,
                })

                orchestrator.bus.off("*", _event_handler)

            except Exception as e:
                logger.error("Task %s failed: %s", task_id, e)
                await queue.put({
                    "event": "task_error",
                    "error": str(e),
                })

        asyncio.create_task(_execute())
        return TaskResponse(task_id=task_id, status="accepted")

    @app.get("/api/task/{task_id}/events")
    async def task_events(task_id: str):
        """SSE stream of events for a running task."""
        queue = _task_queues.get(task_id)
        if not queue:
            raise HTTPException(status_code=404, detail="Task not found")

        async def event_stream():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=300)
                    except asyncio.TimeoutError:
                        # Send keepalive
                        yield f"data: {json.dumps({'event': 'keepalive'})}\n\n"
                        continue

                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

                    # Terminal events
                    if event.get("event") in ("task_completed", "task_error", "circuit_break"):
                        break
            finally:
                # Cleanup queue after stream ends
                _task_queues.pop(task_id, None)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/task/{task_id}/result")
    async def task_result(task_id: str):
        """Get the final result of a completed task."""
        result = _task_results.get(task_id)
        if not result:
            raise HTTPException(status_code=404, detail="Task result not found")
        return {
            "task_id": task_id,
            "success": result.get("success", False),
            "error": result.get("error"),
            "elapsed": result.get("elapsed", 0),
        }

    @app.get("/api/files")
    async def list_files(path: str = ".", max_depth: int = 3):
        """List project files as a tree."""
        from kaiwu.ast_engine.graph_builder import SKIP_DIRS

        base = os.path.join(project_root, path) if path != "." else project_root
        if not os.path.isdir(base):
            raise HTTPException(status_code=404, detail="Directory not found")

        def _scan(dir_path: str, depth: int) -> list[dict]:
            if depth > max_depth:
                return []
            items = []
            try:
                entries = sorted(os.listdir(dir_path))
            except OSError:
                return []
            for entry in entries:
                if entry.startswith(".") or entry in SKIP_DIRS:
                    continue
                full = os.path.join(dir_path, entry)
                rel = os.path.relpath(full, project_root).replace("\\", "/")
                if os.path.isdir(full):
                    children = _scan(full, depth + 1)
                    items.append({"name": entry, "path": rel, "is_dir": True, "children": children})
                else:
                    items.append({"name": entry, "path": rel, "is_dir": False})
            return items

        tree = await asyncio.to_thread(_scan, base, 0)
        return {"root": path, "items": tree}

    @app.get("/api/file")
    async def read_file(path: str):
        """Read a single file's content."""
        full_path = os.path.join(project_root, path)
        if not os.path.isfile(full_path):
            raise HTTPException(status_code=404, detail="File not found")

        # Security: prevent path traversal
        resolved = os.path.realpath(full_path)
        if not resolved.startswith(os.path.realpath(project_root)):
            raise HTTPException(status_code=403, detail="Access denied")

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

        ext = os.path.splitext(path)[1].lower()
        from kaiwu.ast_engine.language_detector import _EXT_TO_LANG
        language = _EXT_TO_LANG.get(ext, "")

        return FileContent(
            path=path,
            content=content,
            language=language,
            lines=content.count("\n") + 1,
        )

    @app.post("/api/rig/refresh")
    async def refresh_rig():
        """Rebuild rig.json for the project."""
        from kaiwu.ast_engine.graph_builder import GraphBuilder

        try:
            gb = GraphBuilder(project_root)
            rig = await asyncio.to_thread(gb.export_rig)
            return {"status": "ok", "files": len(rig.get("files", {}))}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return app
