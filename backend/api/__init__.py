"""Aurora - FastAPI application.

Routes are organized into modules under api/routes/:
  chat      - /chat, /chat/stream, /ws, /soul, /health
  files     - /files, /rag
  sessions  - /sessions, /goal, /context, /checkpoint
  settings  - /config, /settings, /models, /llm, /providers, /presets, /prompts
  system    - /tools, /plugins, /agents, /mcp, /browser, /approval, /threads,
              /heartbeat, /sentry, /agents-md, /processes, /storage, /observability
  memory    - /memory
  marketplace - /marketplace, /skins
  auth      - /auth
  re        - /re
  detective - /detective
  tasks     - /tasks, /cron
  intgr     - /magic-docs, /im, /search, /worktree, /context/collapse
  remote    - /remote
"""
from __future__ import annotations
import os
import time
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import HTTPException

app = FastAPI(
    title="Aurora AI Agent",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("AURORA_CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    import traceback, os
    traceback.print_exc()
    # Never leak error details in production; only in dev
    is_dev = os.environ.get("AURORA_ENV", "") == "dev"
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            **({"detail": str(exc)[:200]} if is_dev else {}),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "HTTPException", "detail": exc.detail},
    )


@app.middleware("http")
async def add_timing(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    response.headers["X-Process-Time"] = f"{(time.time() - start) * 1000:.0f}ms"
    return response


# ---- Register route modules ----

from backend.api.routes.chat import router as chat_router
from backend.api.routes.files import router as files_router
from backend.api.routes.sessions import router as sessions_router
from backend.api.routes.settings import router as settings_router
from backend.api.routes.system import router as system_router
from backend.api.routes.memory import router as memory_router
from backend.api.routes.marketplace import router as marketplace_router
from backend.api.routes.auth import router as auth_router
from backend.api.routes.re import router as re_router
from backend.api.routes.detective import router as detective_router
from backend.api.routes.tasks import router as tasks_router
from backend.api.routes.connectors import router as connectors_router
from backend.api.routes.integrations import router as integrations_router
from backend.api.routes.remote import router as remote_router
from backend.api.routes.chronicle import router as chronicle_router
from backend.api.routes.i18n import router as i18n_router

app.include_router(chat_router)
app.include_router(files_router)
app.include_router(sessions_router)
app.include_router(settings_router)
app.include_router(system_router)
app.include_router(memory_router)
app.include_router(marketplace_router)
app.include_router(auth_router)
app.include_router(re_router)
app.include_router(detective_router)
app.include_router(tasks_router)
app.include_router(connectors_router)
app.include_router(integrations_router)
app.include_router(remote_router, prefix="/remote")
