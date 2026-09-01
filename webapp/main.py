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

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from webapp import auth, jobs, models

# Load .env at import so the app's process sees the same GHL config the engine
# uses (GHL_USER_ID, GHL_LOCATION_ID, GHL_ACCOUNT_MAP and the GHL_POST_STATUS
# draft guard rail). This runs once at import; the test suite's autouse
# ``_hermetic_env`` fixture strips these per-test, so tests stay offline.
load_dotenv()

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


def _static_version(filename: str) -> str:
    """Cache-buster for a static asset: its mtime as an int string.

    Templates append this as ``?v=...`` so browsers refetch app.css / app.js
    automatically after every deploy (git pull rewrites the file mtime) without
    anyone needing a hard refresh. Falls back to ``"0"`` if the file is missing.
    """
    try:
        return str(int((_STATIC_DIR / filename).stat().st_mtime))
    except OSError:
        return "0"


def _build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.globals["brand"] = BRAND_TOKENS
    # SA English niceties; templates may use these, never emojis / dashes.
    templates.env.globals["app_name"] = "Dynamic Auctioneers"
    templates.env.globals["static_v"] = _static_version
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

    # Sessions + tokens share one persisted secret. The session cookie is marked
    # Secure BY DEFAULT so it is never sent over plain HTTP on the internet-facing
    # deployment (nginx terminates TLS in front of us). Local HTTP dev opts out
    # with ENGINE_ALLOW_INSECURE_COOKIE=1, and a loud warning fires whenever it is
    # off so a misconfigured production box is obvious in the logs.
    _allow_insecure = os.getenv("ENGINE_ALLOW_INSECURE_COOKIE", "").lower() in ("1", "true", "yes")
    _https_only = not _allow_insecure
    if not _https_only:
        logger.warning(
            "session cookie is NOT marked Secure (ENGINE_ALLOW_INSECURE_COOKIE set) - "
            "use this only for local HTTP development, never behind a public URL."
        )
    app.add_middleware(
        SessionMiddleware,
        secret_key=models.get_or_create_secret(),
        https_only=_https_only,
        same_site="lax",
        max_age=14 * 24 * 3600,
    )

    # Security headers on every response: block framing (clickjacking of the
    # gate-action pages), stop MIME sniffing, and trim referrer leakage. HSTS is
    # left to the nginx TLS front so it is not duplicated / wrong on local HTTP.
    @app.middleware("http")
    async def _security_headers(request, call_next):
        response = await call_next(request)
        # SAMEORIGIN (not DENY): the gate-2 ad preview embeds the rendered
        # artifact HTML in a same-origin <iframe>. DENY blocked that (the
        # preview showed a broken frame); SAMEORIGIN still stops cross-site
        # framing / clickjacking.
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # HTML is never cached (D96). The stylesheet is cache-busted by mtime,
        # but that only helps if the browser re-reads the PAGE: a cached page
        # carries the OLD ?v= and keeps using the old stylesheet with it. That
        # is how a deploy reached a marketer as new markup wearing old styling -
        # the icon picker arrived unstyled, at full glyph size. The pages are
        # small, dynamic and behind a login, so there is nothing to gain by
        # caching them; the versioned static assets still cache hard.
        ctype = response.headers.get("content-type", "")
        if ctype.startswith("text/html"):
            response.headers.setdefault("Cache-Control", "no-store, must-revalidate")
        return response

    app.state.templates = _build_templates()
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    _include_routers(app)
    return app


app = create_app()
