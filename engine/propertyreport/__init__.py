"""Property reports (M11, D116, D117).

The inspection report the properties team sends the trustee, liquidator or bank
that instructed a sale, before the instruction to market it. It names the owner,
so like the proposals (M9) and OTPs (M10) it has its own screen and records and
never goes near ``public_view``. (The buyer-facing "Property Report" in the
marketing pipeline is the info pack, a different document.)

    engine.propertyreport.model      -- the record, the viewing checklist and what the template prints
    engine.propertyreport.store      -- SQLite persistence, one row per DP
    engine.propertyreport.prefill    -- a Lightstone report into blank fields
    engine.propertyreport.checks     -- the rules that must pass before generating
    engine.propertyreport.docx_build -- fills templates/property-report.docx
"""
