"""Dynamic Auctioneers marketing engine (Phase 1: ingest).

Public surface:
    engine.schema  -- PropertyRecord and the record schema (M2/M4)
    engine.intake  -- pair + classify source PDFs by DP number (M1)
    engine.extract -- Claude extraction of a PDF pair into a record (M2)
    engine.photos  -- PyMuPDF photo extraction (M2)
    engine.store   -- SQLite record store + lifecycle state machine (M4)
    engine.cli     -- the `engine` command-line entry point
"""

__version__ = "0.1.0"

MODEL = "claude-opus-4-8"
