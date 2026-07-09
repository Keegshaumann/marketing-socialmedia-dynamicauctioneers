"""Route modules for the Dynamic Auctioneers platform (M8, Phase 4).

Each module here exposes a module-level ``router = APIRouter()`` that
``webapp.main`` includes at startup. Keeping the routers in their own package
lets the app compose from independently built screens.
"""
