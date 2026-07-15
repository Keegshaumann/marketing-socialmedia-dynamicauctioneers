# Dynamic Auctioneers — Marketing Engine (Prototype)

One property record per DP number; every marketing artifact rendered from it. Change the price or photos once, regenerate everything. This kills the "repost the ad 4+ times" problem at the root.

## What this prototype demonstrates (DP3060)

Built 2026-07-08 from the real matched pair for DP3060 (Unit 1 SS Kyalami, 40 Topham Road, Pelham North):

```
DP3060/
├── record.json            The single source of truth, merged from both source docs
├── verification-memo.md   Lightstone vs Property Report cross-check, flags raised
├── photos/                26 images auto-extracted from the Property Report PDF
└── ads/
    ├── portal-listing.md  Property24-ready copy
    ├── facebook-post.md   FB post + boost notes
    ├── email-blast.md     Subject A/B + body
    └── demo-ad.html       Branded one-pager (real DA letterhead, Montserrat, photos)
```

## The pipeline

1. **Intake (paired upload).** Lightstone EVM + Dynamic's Property Report uploaded together, keyed by DP number (`DP3035`, sub-properties `DP3035.1`, `.2`, ...). Filenames/folders already carry the DP number, so routing is automatic.
2. **Extraction.** Both PDFs parsed into `record.json`. Lightstone wins on deeds/market/comps data; the Property Report wins on physical features and terms of sale. Photos pulled from the PDF (production: from the property's Media folder on OneDrive).
3. **Verification.** Cross-check both sources plus live market research; discrepancies land in `verification-memo.md` with actions. A human signs off before anything publishes. (DP3060 proved the point: Lightstone claims 3 garages, the inspection found none; the flatlet only exists in the inspection.)
4. **Rendering.** Every ad format generated from the record by template. POPIA redaction is structural: owner name/ID and occupant contact never leave the internal layer.
5. **Distribution (channel rules).** All properties → Property24, own site, Facebook, email. R10m+ → add JamesEdition. Private Property → excluded. A price/date change updates the record and re-renders/pushes all channels; a price drop also triggers a "reduced" re-engagement burst.

## Roadmap

- [ ] Automate extraction (Claude API: PDF pair in → record.json + memo out)
- [ ] Watch OneDrive property folders via Microsoft Graph (files live in the
      "Master Training Solutions" SharePoint library)
- [ ] Prop Data feed access for Property24 auto-syndication + auto-updates
      (email api-support@propdata.net; fallback: Entegral/Fusion)
- [ ] Industrial/commercial parser path (need one sample Lightstone report)
- [ ] Auction-date/venue fields from Auction Prep folder or ze.NOTES
- [ ] Generate Dynamic's Property Report itself from a short form
      (today Gerrie assembles it by hand, embedding Lightstone screenshots)
- [ ] Buyer CRM: tag every enquiry by DP + category, notify matched buyers
      on new listings

## Open questions for the team

1. Garages on DP3060: Lightstone says 3, inspection says none. Which is it?
2. Do sub-properties (.1, .2) always share the parent's auction event, or can
   lots go to auction on different dates?
3. Property Report cover prints the owner's full name + ID number. If that
   document goes to buyers as the info pack, it should be the redacted variant.
