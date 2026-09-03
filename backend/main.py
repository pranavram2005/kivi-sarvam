"""Kivi Semantic Memory - FastAPI application.

Run from the repository root:

    uvicorn backend.main:app --reload --reload-dir backend

Interactive API docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend.api import analytics, heykivi, memories, system, transcripts
from backend.config import REPO_ROOT, get_settings
from backend.database.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Make sure the schema exists before the first request touches it."""
    settings = get_settings()
    init_db()
    print(f"[kivi] database   : {settings.db_path}")
    print(f"[kivi] llm        : {settings.llm_provider} ({settings.llm_model})")
    print(f"[kivi] embeddings : {settings.embedding_provider} ({settings.embedding_model})")
    yield


app = FastAPI(
    title="Kivi Semantic Memory",
    version=__version__,
    lifespan=lifespan,
    description=(
        "Kivi learns durable facts, preferences, commitments and work context from a "
        "user's past dictations, keeps every memory traceable to the transcript that "
        "produced it, retrieves only what a question needs, and abstains rather than "
        "inventing an answer."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transcripts.router)
app.include_router(memories.router)
app.include_router(memories.process_router)
app.include_router(heykivi.router)
app.include_router(system.router)
app.include_router(system.eval_router)
app.include_router(analytics.router)


# ---------------------------------------------------------------------------
# Serving the built frontend
# ---------------------------------------------------------------------------
# In development Vite serves the UI on :5173 and proxies /api here, so this
# does nothing. In a container there is one process and one port: if a built
# frontend is present it is served from the same origin as the API, which also
# means no CORS to configure on the host.
_FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
_INDEX = _FRONTEND_DIST / "index.html"


if _INDEX.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=_FRONTEND_DIST / "assets"),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(_INDEX)

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        """Serve a real file when one exists, and index.html otherwise.

        The app routes on the URL hash, so this mostly catches favicons and a
        reload of a deep link. Anything under /api never reaches here - those
        routes are registered first and win.
        """
        candidate = (_FRONTEND_DIST / path).resolve()
        # Containment check: a crafted path must not escape the dist directory.
        if candidate.is_file() and _FRONTEND_DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_INDEX)

else:

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("backend.main:app", host=settings.api_host, port=settings.api_port, reload=True)
