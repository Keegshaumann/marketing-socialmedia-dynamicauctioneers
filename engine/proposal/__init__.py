"""Auction proposals for properties (M9, D103).

A proposal is what goes to the liquidator, trustee or executor before an auction
is approved: cover facts, the Lightstone deeds page, the draft advert, the
marketing examples, the budget the liquidator set, that property's own OTP word
for word, and the introduction pages. It is a client document, not public
marketing, so it lives outside the record's gate flow and ``public_view``.

    engine.proposal.model      -- the Proposal record and its formatting helpers
    engine.proposal.store      -- SQLite persistence, one row per DP
    engine.proposal.otp_docx   -- the sale terms read out of the OTP .docx
    engine.proposal.checks     -- the rules that must pass before generating
    engine.proposal.docx_build -- fills the Word template and joins the OTP
    engine.proposal.lightstone -- prefill and the deeds page from an EVM report
    engine.proposal.sharepoint -- saves to the property folder and converts to PDF
"""
