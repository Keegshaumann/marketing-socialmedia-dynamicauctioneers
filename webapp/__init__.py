"""Dynamic Auctioneers marketing platform (M8, Phase 4).

A FastAPI + Jinja2 + HTMX web app that drives the SPEC section 12 workflow end to
end over the ``engine`` package. Backend backbone modules:

    webapp.models  -- webapp tables (users, jobs, settings, channel_status) + helpers
    webapp.auth    -- bcrypt users, sessions, require_role, seed_admin
    webapp.tokens  -- signed single-use expiring approve-by-email tokens
    webapp.jobs    -- the background worker thread (key-gated steps degrade cleanly)

Run with ``uvicorn webapp.main:app``.
"""
