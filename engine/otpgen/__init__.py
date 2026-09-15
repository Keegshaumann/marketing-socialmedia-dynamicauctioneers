"""Offers to purchase (OTPs) for properties (M10, D114).

Generated from the team's master OTP with the per-property parts filled in and
the master's errors corrected (D115). A separate place from the auction
proposals (M9): its own records, its own screen, reached through the switcher
at the top of both. A proposal still takes an uploaded OTP.

    engine.otpgen.model      -- the Otp record and what the template prints
    engine.otpgen.store      -- SQLite persistence, one row per DP
    engine.otpgen.checks     -- the rules that must pass before generating
    engine.otpgen.docx_build -- fills templates/otp.docx
"""
