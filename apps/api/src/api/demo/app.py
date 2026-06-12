"""DEMO ASGI app — wraps the real app and mounts the demo router.

Production runs ``api.main:app``; the demo runs ``api.demo.app:app``. This keeps
``api/main.py`` pristine — no demo wiring leaks into the production entrypoint.

    DEMO_MODE=1 uv run uvicorn api.demo.app:app --reload --port 8000
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.main import create_app


def create_demo_app() -> FastAPI:
    if os.getenv("DEMO_MODE") != "1":
        # Fail loudly: this module must never be the production entrypoint.
        raise RuntimeError(
            "api.demo.app loaded without DEMO_MODE=1. This app is demo-only; "
            "production must run api.main:app."
        )

    app = create_app()
    app.title = "Sizeify API (DEMO)"

    # The web client (apps/web) is served from a dev origin during the demo.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],  # Vite dev server
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Imported lazily so a stray import of this module can't pull demo routes
    # into a production process.
    from api.demo.router import router as demo_router

    app.include_router(demo_router)
    return app


app = create_demo_app()
