"""FastAPI application factory and wiring (M8, Phase 4).

This is the composition root for the marketing platform. It:

- opens the shared SQLite database (``ENGINE_DB`` or ``./engine.db``) and creates
  the platform tables (``init_db``), then seeds the bootstrap admin on first run;
- installs Starlette ``SessionMiddleware`` keyed on the *same* persisted secret
  the approve-by-email tokens use (``models.get_or_create_secret``), so sessions
  and tokens never drift apart;
- starts the single background job ``Worker`` at startup and stops it at
  shutdown, so extraction / verify / render / post run off the request path;
- mounts ``/static`` and registers one ``Jinja2Templates`` environment (brand
  tokens as globals; the SVG icon macro lives in ``_macros.html`` and each
  template imports it), exposed on ``app.state.templates`` for the routers;
- includes every route module defensively: a screen still under construction in
  a parallel build must not stop the app from booting, so a missing module is
  logged and skipped rather than raised.

Run with ``uvicorn webapp.main:app``.
"""

from __future__ import annotations

import importlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from webapp import auth, jobs, models

logger = logging.getLogger("webapp")

_HERE = Path(__file__).resolve().parent
_TEMPLATES_DIR = _HERE / "templates"
_STATIC_DIR = _HERE / "static"

# Brand tokens (mirror docs/DESIGN-SYSTEM.md + the demo-ad palette). Exposed to
# every template as ``brand`` so a template can reference a colour without
# hard-coding the hex twice.
BRAND_TOKENS = {
    "gold": "#B08D4A",
    "gold_deep": "#8C6D33",
    "gold_pale": "#F1E8D6",
    "ink": "#191613",
    "body": "#2B2620",
    "muted": "#877E70",
    "hairline": "#E5DFD4",
    "ground": "#EFEBE3",
    "sheet": "#FFFFFF",
    "block": "#9A3B2E",
    "note": "#A8792E",
    "ok": "#4F6B45",
    "info": "#4A6274",
}

# Route modules to include, in nav order. Missing modules (parallel build) are
# skipped so the app always boots.
ROUTE_MODULES = (
    "board",
    "intake",
    "gates",
    "artifacts",
    "post",
    "settings",
    "email_approve",
)


def _build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.globals["brand"] = BRAND_TOKENS
    # SA English niceties; templates may use these, never emojis / dashes.
    templates.env.globals["app_name"] = "Dynamic Auctioneers"
    return templates


def _include_routers(app: FastAPI) -> None:
    for name in ROUTE_MODULES:
        try:
            module = importlib.import_module(f"webapp.routes.{name}")
        except Exception as exc:  # a screen may not exist yet (parallel build)
            logger.warning("route module %r not loaded: %s", name, exc)
            continue
        router = getattr(module, "router", None)
        if router is None:
            logger.warning("route module %r has no 'router'; skipped", name)
            continue
        app.include_router(router)
        logger.info("included router: %s", name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure schema exists and a login account is available on first run.
    db_path = models.init_db()
    app.state.db_path = db_path
    auth.seed_admin(db_path)

    # One background worker drains the job queue for the life of the process.
    worker = jobs.Worker(db_path).start()
    app.state.worker = worker
    logger.info("job worker started (db=%s)", db_path)
    try:
        yield
    finally:
        worker.stop()
        logger.info("job worker stopped")


def create_app() -> FastAPI:
    app = FastAPI(title="Dynamic Auctioneers Marketing Platform", lifespan=lifespan)

    # Ensure the schema exists before we read the shared secret: the middleware is
    # constructed at import time, ahead of the lifespan startup that also inits the
    # DB. init_db is idempotent, so calling it here and in lifespan is safe.
    models.init_db()

    # Sessions + tokens share one persisted secret. Behind HTTPS (the Caddy
    # deployment), set ENGINE_HTTPS=1 so the session cookie is marked Secure and
    # cannot be sniffed on plain HTTP; local dev over HTTP leaves it unset.
    _https_only = os.getenv("ENGINE_HTTPS", "").lower() in ("1", "true", "yes")
    app.add_middleware(
        SessionMiddleware,
        secret_key=models.get_or_create_secret(),
        https_only=_https_only,
        same_site="lax",
        max_age=14 * 24 * 3600,
    )

    app.state.templates = _build_templates()
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    _include_routers(app)
    return app


app = create_app()
