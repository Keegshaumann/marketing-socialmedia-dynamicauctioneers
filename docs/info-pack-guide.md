# Dynamic Auctioneers: Property Info Pack Automation Guide

Version: 1.0  
Prepared: 25 November 2026  
Owner: Dynamic Auctioneers marketing team  
Purpose: Reusable instructions for an automation service that prepares property information packs.

## 1. What this guide covers

Create accurate, branded, buyer-facing property information packs for residential, sectional-title, commercial, industrial, agricultural, vacant-land and multi-property sales. Adapt to auctions, private-treaty sales, business rescue, liquidation, insolvency and deceased estates only where the instruction and documents establish that sale type.

This guide combines the supplied DP3107 final pack, the source documents and corrections in that project, and relevant available history of earlier property work. It is not an audit of every historical pack. Do not assume a previous assistant draft was approved merely because it exists.

Distinguish three kinds of instruction:

- **Established requirements:** accuracy, relevant property features, investment highlights, OTP-derived terms, separate property/portion details, unchanged approved disclaimers and no invented facts.
- **Reference design:** the attached DP3107 pack demonstrates the current black, gold and white layout, numbered sections, tables, photographs and closing page. Use as the default when no newer brand template is supplied.
- **Automation recommendations:** the data model, evidence ledger, validation gates and workflow below make those requirements repeatable. They are implementation recommendations, not claims about existing software.

The deliverable is an information pack, not a social-media caption. Do not add hashtags, social-ad emojis, audience targeting or unrelated sales scripts unless separately requested.

## 2. Non-negotiable rules

1. Never invent a feature, date, price, registration fee, commission, deadline, approval, capacity, income, link or contact.
2. Read the full current source documents, including OTP schedules, special conditions, annexures and relevant photographic pages. Text extraction alone can miss scanned pages and labels.
3. Keep a source reference for every material fact and every contractual summary.
4. Reuse the process and layout; never reuse another property's data as defaults.
5. Always include **Investment Highlights** in the completed pack, using supported facts.
6. Keep separate properties, portions, sectional units and lots distinct. Combine only where the sale instruction permits, and label totals clearly.
7. Preserve conditions and exceptions in the OTP. A shorter summary must not change the agreement's meaning.
8. Preserve the approved disclaimer exactly unless the user explicitly requests a change.
9. Differentiate confirmed absence from missing information. "Not specified" does not mean "does not exist."
10. Use plain, professional language. Avoid exaggerated claims, filler, jargon and unrelated features on the same line.
11. Do not claim the pack is publication-ready while material facts remain contradictory or unresolved.
12. Carry the property reference consistently across the cover, content, contact page and filenames.

## 3. Inputs to collect

### 3.1 Core inputs

- Current instruction from the marketing team or property manager.
- Property reference and intended lot structure.
- Current approved auction or sale artwork.
- Current OTP / Agreement and Conditions of Sale, including schedules and amendments.
- Lightstone / EVM report, where available.
- Valuation and its annexures, where available.
- Current photographs, aerial images and supplied maps.
- Approved brand assets and current contact details.

### 3.2 Conditional inputs

Request only those relevant to the property and proposed claims:

- Title deed, survey diagram, sectional plan and exclusive-use records.
- Zoning certificate, land-use scheme extracts, consent uses and approvals.
- Building plans and accommodation schedule.
- Current municipal account, levy statement and special levies.
- Lease agreements, rent roll, occupancy schedule and income/expense records.
- Water-use documents, borehole tests, irrigation schedules and storage specifications.
- Electrical supply details, solar specifications and compliance certificates.
- Crop schedules, cultivars, tree counts, yields and production records.
- Inventory, equipment condition report and inclusion/exclusion schedule.
- Business rescue, liquidation, insolvency or estate particulars.
- Employee information required for the transaction, handled separately where confidential.
- Environmental/planning reports and development approvals.

Do not hold up a useful draft because a nonessential document is missing. Record gaps and prepare the supported sections.

## 4. Establish evidence before writing

### 4.1 Use a field-specific source hierarchy

There is no single document that overrides every other document for every field.

| Field | Preferred evidence | Handling rule |
|---|---|---|
| Contractual obligations | Current transaction OTP, signed amendments and applicable special conditions | Preserve scope, deadlines, exceptions and named approving parties; flag internal contradictions |
| Auction date, format, viewing and registration | Latest explicitly approved instruction and event material | Compare all supplied versions; never use an old artwork year silently |
| Legal identity and registered extent | Relevant title/registration records and transaction documents | Compare with Lightstone; flag differences |
| Cadastral extent | Cadastral/survey records or clearly attributed report | Keep separate from registered extent |
| Improvements and condition | Current inspection, property report, dated valuation and photographs | Attribute historical facts and distinguish visible appearance from tested operation |
| Zoning and permitted uses | Current planning/zoning evidence | Lightstone usage category is not conclusive zoning proof |
| Rent, occupancy and returns | Current leases and financial schedules | State dates, gross/net basis, assumptions and exclusions |
| Water rights and capacities | Actual entitlement and technical documentation | A pump or irrigated orchard does not prove lawful abstraction volume or guaranteed supply |
| Later owner/manager detail | Explicit dated instruction or statement | Record the source; reconcile contradictions before promoting it to a verified fact |

A current explicit correction can resolve an old error, but do not erase a conflict with a contract or official record without recording the resolution.

### 4.2 Maintain an internal evidence ledger

For each fact store:

- Field name and property/portion/lot ID.
- Exact source wording.
- Normalised value and unit.
- Filename, document date and page or clause.
- Evidence status: `documented`, `user_supplied`, `calculated`, `unconfirmed`, `conflicting` or `not_applicable`.
- Calculation, if any, and its inputs.
- Intended buyer-facing wording.
- Resolution needed and person responsible, if unresolved.

Do not put this internal ledger, extraction logs or automation field names into the buyer's pack.

### 4.3 Resolve conflicts explicitly

Examples:

- Registered area and cadastral area can differ; show their distinct labels.
- A valuation's land-use categories may not add up to the registered extent. Do not invent an allocation for the difference.
- A newer approved info pack may correct an earlier artwork date. Record the newer source and update every occurrence.
- Two OTP clauses may allocate charges across overlapping dates. Do not choose a convenient interpretation; request conveyancer clarification.
- A municipal use category, marketing description and confirmed zoning may describe different things. Never collapse them into one field.

## 5. End-to-end workflow

1. Inventory the inputs and record their versions and dates.
2. Extract text, tables and schedules; inspect scanned and visual pages as required.
3. Build the property/portion/lot records and evidence ledger.
4. Resolve material conflicts and prepare a missing-information list.
5. Select the relevant content modules for this property type.
6. Draft property facts and OTP-based sale conditions.
7. Draft investment highlights from the evidence, not from generic marketing promises.
8. Build the branded layout, tables, photographs, map and contact page.
9. Generate the table of contents from the actual final sections and pagination.
10. Run factual, contractual, numeric and visual checks.
11. Produce a complete draft with clearly separated internal review notes.
12. Apply the authorised corrections, update pagination and validate the final export.

Use the user's existing production environment. This guide does not prescribe a specific automation platform or design application.

## 6. Default information-pack structure

Use the following order as a flexible baseline. Omit irrelevant optional modules and renumber the actual contents. Do not create empty pages just to preserve a template.

| Order | Section | What it contains |
|---|---|---|
| Cover | Branded cover | Property type, key size, locality/province, sale type, auction format/date, reference, feature strapline and relevant image |
| Front matter | Table of Contents | Actual section titles and correct page numbers |
| 1 | Auction / Sale Information | Event facts, viewing, registration, main payment terms and bidding route |
| 2 | Property Overview | Concise description of what is being sold and the strongest factual features |
| 3 | Property Particulars & Location | Identity, title, extent, locality, municipality, coordinates and map |
| 4 | Property Features / Property Breakdown | Relevant residential, commercial, industrial or agricultural details; separate each property/portion |
| Optional | Land-Use / Accommodation / Unit Breakdown | Tables appropriate to the asset |
| Optional | Water, Electricity & Infrastructure | Supported supply, capacity, installations and restrictions |
| Optional | Tenancy / Income / Operations | Verified current occupancy, leases and financial or operating information |
| Optional | Movable Assets | Included inventory and condition limitations |
| Optional | Going-Concern Sale, Crops & Employees | Exact scope and transfer provisions from the OTP |
| Next | Summary of Sale Conditions | Transaction-specific OTP summary |
| Next | Registration & FICA Requirements | Current transaction checklist and authority requirements |
| Next | Photographic Overview | Relevant photographs, plans and properly labelled visuals |
| Next | Investment Highlights | Strong supported facts and their buyer relevance; never omit |
| Optional | Supporting Documents | Relevant annexures or index of documents supplied separately |
| Closing | Contact / CTA / Disclaimer | Approved contacts, reference, next step and unchanged approved disclaimer |

A short property can combine compatible sections. A complex portfolio may require more pages. There is no universal page-count target.

## 7. Cover, titles and feature straplines

The cover must answer: what is it, where is it, how big is it, and when/how is it sold?

- Use the strongest accurate property description.
- Include the actual legal sale context only when established.
- Show online versus onsite correctly. Onsite packs require the verified venue.
- Include the property reference and current auction year.
- Use one short factual strapline with related features separated by ` | `.
- Use actual property imagery for factual representation. Decorative crop/asset imagery must not imply those exact items were photographed on the property.
- Boundary overlays are illustrative unless verified from appropriate survey evidence; do not present a hand-drawn outline as a surveyed boundary.

Style examples, only where supported:

- `MACADAMIA & AVOCADO ORCHARDS | IRRIGATION | NURSERY INFRASTRUCTURE`
- `GREENHOUSE TUNNELS | PACKHOUSE | WATER INFRASTRUCTURE`
- `INDUSTRIAL BUILDINGS | YARD SPACE | ACCESS`

Do not use "water security" simply because a tank or borehole is mentioned. Do not invent a higher-value use to improve the headline.

## 8. Core content rules

### 8.1 Auction / sale information

Capture the sale type, format, full date, start/end time, time zone where useful, venue if applicable, viewing arrangements, registration fee, deposit, commission, VAT treatment, confirmation period and guarantee deadline.

- A refundable registration fee is not automatically the deposit.
- Do not assume whether the registration fee is credited to the deposit.
- Separate commission from the purchase price and deposit.
- Keep auction-day obligations distinct from obligations triggered by written acceptance.
- Preserve `calendar days`, `business days` or the exact exclusions in the source.
- Validate weekday/date combinations using a calendar, and flag a past date in a forthcoming sale.
- Do not infer viewing arrangements as "by arrangement" unless authorised.
- For private treaty, replace auction-specific fields with the actual offer process.

### 8.2 Property particulars and location

Use a clear two-column table for legal description, erf/farm number, portion/unit, registration division, scheme, title deed, registered owner, extent, local municipality, province and relevant matter details.

Distinguish:

- Marketing locality from the legal description.
- Town, suburb, farm/locality and municipal area.
- Registered land area from cadastral area.
- Erf area, sectional unit area, exclusive-use area and building floor area.
- Administrative usage category from confirmed zoning.

Use verified coordinates for a map pin. Do not invent travel times, distances, landmarks or nearby amenities. Do not include unrelated personal information from a source document.

### 8.3 Property features

Use concrete, relevant details. Include under-roof areas when available. State what each area measures and its source/date.

Keep related facts together. Bedrooms and bathrooms can share a line. A study and a double garage should not be combined merely to save a bullet.

Replace vague terms such as "outbuildings" with their actual descriptions when known. If unknown, retain only what the source supports.

Do not infer accommodation from photographs of buildings. Do not infer room counts, safety, security or operating condition from appearance.

## 9. Property-type modules

### 9.1 Residential and sectional title

Capture supported details for bedrooms, bathrooms, guest toilets, living areas, kitchens, studies, balconies/patios, entertainment areas, gardens, pools, garages and parking.

For sectional title, keep unit number, scheme, registered unit size, exclusive-use rights, levies, special levies and scheme rules distinct. Do not use the full scheme's erf area as the unit's private land.

Record occupation, leases and vacant-possession limitations accurately. Student accommodation, guesthouse, medical rooms or other uses require appropriate qualification; do not advertise them as approved uses without evidence.

### 9.2 Commercial and industrial

Capture separate building areas, factory/warehouse/office components, yard, access, loading facilities, height, power, security and zoning only where documented.

- Separate multiple factories and ancillary areas.
- Specify power capacity and phase only when supplied.
- Do not claim truck access from the existence of a road alone.
- Distinguish month-to-month leases, fixed-term leases and vacant space.
- Distinguish gross rent from net operating income and actual figures from projections.
- Never invent yield, occupancy, profitability or return estimates.

### 9.3 Agricultural

Use the actual farming activity: orchard/fruit/nut, greenhouse, crops, grazing, livestock or other supported type. Viticulture applies to grapes, not avocado/macadamia orchards.

Capture relevant land allocations, orchards/crops, cultivar, tree age with valuation date, irrigation/dryland area, production infrastructure, nursery, packhouse, cold rooms, accommodation, staff housing, roads, fencing, power and water.

- Show orchard breakdowns in tables.
- Do not count an irrigated subset twice in total planted area.
- An area labelled "buildings and nursery" is not the buildings' under-roof area.
- Keep dryland and irrigated cultivation distinct.
- Separate borehole count, tested yield, water quality, pumping equipment, tanks, dams and water entitlements.
- "Liquid storage tank" does not establish water storage or potable use.
- Water entitlements included "to the extent transferable" must retain that qualifier.
- Cultivar diversity can support an extended harvesting-season highlight when supplied; do not turn that into guaranteed uninterrupted production or revenue.
- Do not assert guaranteed water, turnkey operation, full production or profitability without evidence.

### 9.4 Vacant land and development opportunities

Capture extent, access, terrain, existing structures, supplied services, zoning and approval status separately.

Distinguish proposed potential, approved plans, consent use, rezoning and township establishment. Never call a property development-ready solely because an agent suggests a future use.

Environmental approval requirements, timelines, bulk-service capacities and development costs are project-specific. Do not reuse a prior project's timeline or make definitive legal/planning claims without relevant current evidence.

### 9.5 Multiple properties, portions and unit portfolios

Create a separate record and feature section for each property, portion or unit group. This is essential where, for example, the instruction requests separate PT27 and PT28 features.

- Do not assign one portion's residence, boreholes, infrastructure or land area to another.
- Label every photograph and table by the relevant asset.
- State individual versus combined sale options only if authorised.
- Check whether deposits, fees and commission apply per lot or to a combined transaction.
- Clearly label portfolio totals and show the constituent figures.
- Do not imply a portfolio purchaser can choose individual units if the sale is one lot only.

## 10. Investment highlights are mandatory

Include a distinct Investment Highlights section in every completed property pack. The user specifically requested this after its omission.

Use a short bold heading plus one factual sentence, or a two-column table. Aim for the strongest supported points, usually about five to eight; fewer is better than padding.

Good themes, where supported:

- Existing productive or usable infrastructure.
- Established orchards and supported crop diversity.
- Verified location/access benefits.
- Documented rental income or tenant profile.
- Sale flexibility across separate and combined lots.
- Included processing equipment or movable assets.
- Documented standing-crop treatment at transfer.
- Nursery, packhouse, cold storage or other specific facilities.
- Additional land, with future use qualified by suitability and approvals.

Avoid generic promises such as "guaranteed returns," "exceptional profits," "risk-free," "fully operational," "unlimited development" or "secure water supply" without evidence.

Example based on user-supplied information:

> **Extended Avocado Harvesting Season:** Different avocado cultivars planted to support harvesting across the year.

Keep the statement's provenance internally. The phrase does not establish cultivar names, yields or twelve months of guaranteed harvests.

## 11. Summarising the OTP

Read the complete OTP, not just its first page. Read schedules and special conditions alongside the standard clauses. Maintain the distinction between the property's marketing description and contractual sale scope.

For every applicable topic below, capture the source clause and a concise buyer-facing summary:

| Topic | Required detail |
|---|---|
| Sale scope | Immovable property, business, movables, exclusions and lot configuration |
| Confirmation | Required approving parties, deadline, extension mechanism and bid-binding period |
| Higher offers | Whether allowed, matching right, notification, response window and exclusions |
| Deposit | Percentage, trigger, account designation, treatment and refund conditions |
| Commission | Payer, rate, VAT, timing, earning event and refund provisions |
| Balance / guarantees | Balance trigger, acceptable guarantee, deadline and stated default consequences |
| VAT | Excluded/included basis, conditional going-concern treatment and alternative treatment |
| Transfer | Appointed conveyancer, contact and responsibility for costs |
| Occupation | Date, existing tenancies, written alternatives and vacant-possession limitations |
| Risk / insurance | Trigger date and purchaser obligations |
| Occupational interest | Rate, calculation base, period, payment timing and exceptions |
| Rates / taxes / levies | Specific categories, payer and allocation dates |
| Condition | Voetstoots provisions, warranties/exclusions, plans and services |
| Certificates | Who obtains and pays for each required certificate |
| Title / land claims | Applicable restrictions, servitudes and stated limitations |
| Crops | Crop on trees versus harvested crop/proceeds and the relevant cutoff |
| Employees | Whether and when the contract provides for transfer |
| Special conditions | Creditor consent, liabilities exceeding price, suspensive conditions and refunds |
| FICA / authority | Required documents and entity authorisation |

### 11.1 Rates and outstanding charges: preserve the actual allocation

Do not insert "All outstanding fees to be settled by the Seller" as a universal rule. Check the property's OTP first.

In the Rhede OTP, clause 20 addresses seller liability up to registration and purchaser liability from occupation to transfer. If early occupation creates an overlap, retain the clause's distinctions and obtain clarification rather than silently rewriting it.

Do not suggest the purchaser pays historic arrears unless the transaction documents actually say so. Do not promise the seller pays every possible fee when the clause covers specified charges only.

### 11.2 Going-concern and VAT qualifications

Do not advertise unconditional zero-rated VAT. Record the contract's requirements, qualifying purchaser status and alternative treatment. Do not substitute tax advice for a faithful summary.

Similarly, do not assume an asset is included merely because it appears in a valuation or photograph. Check Schedule A and the final sale configuration.

### 11.3 Preserve governing documents

Use a short note explaining that the summary does not replace the full Agreement and Conditions of Sale, Rules of Auction and applicable amendments.

The automation must not rewrite the OTP itself when asked only to prepare a pack. Unresolved legal interpretation belongs with the transaction's authorised adviser.

## 12. Registration and FICA

Use the current transaction's supplied checklist. Do not assume that historical form names or requirements remain universally applicable.

Where required by that checklist, cover identification, address evidence, tax details, marital-status documents, representative authority, entity registration documents and resolutions.

- Do not publish completed identity documents, tax numbers, banking information or employee records in a broadly circulated buyer pack.
- A blank checklist/template is different from a purchaser's completed records.
- Do not invent bank details. Use a separately verified payment instruction process.
- A registration fee's refund and crediting rules must be sourced, not assumed.

## 13. Photographs, maps and design

The current reference pack uses a portrait format, black/gold/white branding, the supplied logo, readable tables, numbered sections, page numbers, substantial property photography and a closing CTA page.

Use those as defaults unless a newer template is supplied. Exact font names, colour codes, dimensions and margins are not established by this guide; use the actual brand/template assets rather than inventing them as official specifications.

- White body pages with restrained gold accents and clear black text.
- Strong, short headings and consistent table styling.
- Suitable photographs with enough detail to inspect.
- Photos assigned to the correct property/portion.
- Legible maps and clearly labelled illustrative boundaries.
- No stretched photos, clipped text, broken rows or headings stranded from their content.
- No dense blocks copied from an OCR extraction without editing.
- No image-based substitution for accessible body text where avoidable.
- Do not use generated imagery as evidence of actual property condition.

Generate contents-page numbers only after layout. The reference uses unnumbered cover/contents and body numbering beginning at Auction Information; follow the chosen template consistently rather than hardcoding page numbers.

## 14. Writing, numbers and typography

- Plain, professional, factual wording; concise paragraphs and related bullet groups.
- Describe the property for its likely buyers, while keeping proposed uses qualified.
- Include the best features early and the detailed breakdown later.
- Use tables for exact areas, inventories, legal particulars and sale terms.
- Retain source precision in legal details; use clearly approximate rounded figures in marketing summaries.
- Distinguish hectares, square metres, litres, flow rates and currency.
- Do not silently convert tree age at a historical valuation into present age.
- Distinguish registered area from measured/estimated area.
- Use South African currency and readable spacing; keep notation consistent.
- Preserve unit symbols correctly in the rendered PDF. Use `ha`, `m2` and `approximately` as plain-text fallbacks when needed.
- Use standard hyphens and visible spaces. Avoid zero-width characters, nonbreaking-space artifacts, HTML entities such as `&#x20;`, stray OCR glyphs and hidden formatting.
- Keep approved contractual wording and the approved disclaimer intact where exact reproduction is required.

## 15. Brand, contacts and disclaimer

The legal trading name established in the available history is:

`Dynamic Solutions 1068 (Pty) Ltd t/a Dynamic Auctioneers`

Use `Dynamic Auctioneers`, not `Dynamic Auctioneer`, for the brand.

Common contact details in the reference material are examples to verify for each job, not a permanently approved routing list:

- Property enquiries: `properties.admin@dynamicauctioneers.co.za`
- Main telephone: `0861 55 22 88` / `086 155 2288`
- Online bidding: `https://online.dynamicauctioneers.co.za`
- Office: `187 Gouws Avenue, Raslouw AH, Centurion`

The reference back page uses `administration@dynamicauctioneers.co.za`; enquiry and footer addresses may serve different purposes. Use the approved contacts for the current pack. Do not automatically list every staff email address on marketing pages.

Default to the exact disclaimer supplied for the current job. The following wording has been used in this workflow:

> All prices indicated exclude VAT. VAT will be added where applicable. Information provided as received from available records and the Seller. Neither the Agent nor the Seller warrants or guarantees the correctness of any information provided. T's & C's apply.

If the approved source uses different punctuation or typography, preserve that approved version. Do not replace it with this transcription without reason. A disclaimer does not permit unsupported claims elsewhere.

## 16. Missing information and release gates

Prepare a separate internal review list with field, source conflict/gap, required resolution and publication impact.

### Draft handling

- Use explicit placeholders such as `[CONFIRM AUCTION DATE]` in a working draft.
- Never fill an empty amount with a typical amount from another auction.
- Optional unknown amenities can be omitted.
- A material uncertainty may need a concise buyer-facing qualification if relevant to the decision.
- Do not cover the pack with repetitive "not confirmed" statements when the unknown detail is immaterial.

### Publication blockers

Require resolution of missing or conflicting property identity, sale date/format, material payment terms, inclusion/exclusion scope and any material marketing claim without support.

This is an automation release-control recommendation. It is not a requirement to ask the user for permission at every stage. Continue drafting and validating all independent work while specific facts are being resolved.

## 17. Suggested internal data contract

The following JSON is a schema sketch, not populated property data. `null` means unknown, never zero or confirmed absence. Add fields as needed; retain evidence at field level.

```json
{
  "pack": {
    "reference": null,
    "revision": null,
    "status": "draft",
    "sources": [],
    "review_issues": []
  },
  "sale": {
    "type": null,
    "format": null,
    "date": null,
    "start_time": null,
    "end_time": null,
    "timezone": "Africa/Johannesburg",
    "venue": null,
    "viewing": null,
    "registration_fee": null,
    "deposit": null,
    "commission": null,
    "vat_treatment": null,
    "confirmation": null,
    "guarantees": null,
    "conveyancer": null,
    "occupation": null,
    "rates_allocation": null,
    "lot_configuration": null
  },
  "properties": [
    {
      "property_id": null,
      "lot_id": null,
      "legal_description": null,
      "portion_or_unit": null,
      "locality": null,
      "registered_extent": null,
      "cadastral_extent": null,
      "building_areas": [],
      "features": [],
      "land_use": [],
      "infrastructure": [],
      "tenancies": [],
      "photographs": []
    }
  ],
  "movable_assets": [],
  "investment_highlights": [],
  "contact_details": {},
  "approved_disclaimer": null,
  "evidence_records": [
    {
      "field_path": null,
      "property_id": null,
      "value": null,
      "unit": null,
      "status": "unconfirmed",
      "source_filename": null,
      "source_date": null,
      "page_or_clause": null,
      "source_wording": null,
      "calculation": null
    }
  ]
}
```

Store rates, amounts, dates and triggers as distinct values; do not reduce all sale terms to one unstructured paragraph. Keep evidence and internal review metadata separate from the rendered buyer document.

## 18. Validation checklist

### Facts and scope

- [ ] Correct property reference everywhere.
- [ ] Correct legal property, owner and sale context.
- [ ] Current source versions recorded.
- [ ] Each property/portion/lot remains distinct.
- [ ] Registered, cadastral, building and sectional areas correctly labelled.
- [ ] All totals recomputed; discrepancies flagged and not disguised.
- [ ] No double counting of irrigated/planted subsets.
- [ ] No invented accommodation, water, security, sustainability or service-capacity claims.
- [ ] Zoning, usage and proposed potential remain distinct.
- [ ] Photographs and map labels match the correct property.

### Sale conditions

- [ ] Correct auction year, date, time, format and viewing details.
- [ ] Calendar and weekday checked where stated.
- [ ] Registration fee distinct from deposit and commission.
- [ ] Every rate and deadline traced to the current source.
- [ ] Required approving parties and matching-right exceptions preserved.
- [ ] VAT qualifications preserved.
- [ ] Rates/taxes/levies allocation accurately summarised.
- [ ] Occupation, risk, interest and vacant-possession limitations preserved.
- [ ] Movables, crop and employee provisions included only where applicable.
- [ ] Material special conditions included.

### Content and presentation

- [ ] Investment Highlights included and evidence-backed.
- [ ] Strong, relevant features and available under-roof areas included.
- [ ] No unrelated features combined merely to shorten copy.
- [ ] Current enquiry contact and reference visible.
- [ ] Approved disclaimer unchanged.
- [ ] No private FICA, banking or employee information exposed.
- [ ] Contents titles and page numbers match the final document.
- [ ] No placeholders or internal review comments in a released pack.
- [ ] No hidden characters, HTML residue or OCR artifacts.
- [ ] All rendered pages visually inspected after final edits.
- [ ] File opens correctly; links and map pin checked against supplied destinations.

## 19. Regression examples: mistakes the automation must avoid

| Unsafe output | Correct handling |
|---|---|
| Copying DP3107's R100,000 registration fee to another sale | Extract the current property's approved fee |
| Reusing 10% deposit / 7.5% commission / 14-day confirmation universally | Treat each as a transaction-specific value |
| Keeping 28 January 2026 because it appears on old artwork | Compare the newer approved pack; DP3107's attached pack states 28 January 2027 |
| Repeating 36.2515 ha as Rhede's full registered size | Keep 38.1515 ha registered extent separate from the valuation's land-use sum |
| Calling 3 ha of buildings/nursery 30,000 m2 under roof | State that it is a land allocation, not measured floor area |
| Calling two 500-litre liquid tanks water tanks | Retain the inventory description until water use is confirmed |
| Claiming a borehole because irrigation and a pump exist | Report irrigation/pump only; the source of water needs evidence |
| Adding a farmhouse or staff quarters because it is a farm | Include only documented accommodation |
| Promising year-round income from multiple avocado cultivars | Use the supported extended-harvesting-season statement |
| "All outstanding fees paid by Seller" without checking the OTP | Summarise the actual categories and allocation dates |
| Dropping non-working/non-runner labels from inventory | Preserve condition labels and sale scope |
| Merging PT27 and PT28 features into one undifferentiated list | Separate features and photos by portion |
| Omitting investment highlights | Include the distinct section every time |
| Adding hashtags and emojis from a social caption template | Keep the info pack in the professional document format |

These examples demonstrate rules; their property-specific figures are not reusable defaults.

## 20. Reference and provenance notes

Primary finished-pack reference: `DP3107 - AUCTION INFO PACK.pdf`, supplied 25 November 2026. Its finished content establishes the example's updated date, fee, layout, section order, photographic overview and investment-highlights treatment. It is a reference design and project record, not a universal contract template.

Underlying Rhede sources reviewed in this workflow include:

- `3107 - OTP.pdf`.
- `Desktop Valuation - Rhede Boerdery CC (Property)(3).pdf`, dated 12 May 2026.
- `EVM_Report_MT__Limpopo(1).pdf`, dated 23 September 2026.

The guide also incorporates explicit user corrections visible in the project and relevant available earlier work: separate portion features, include building areas where supplied, avoid vague unsupported outbuildings, distinguish zoning from usage, keep features relevant, and always include investment highlights.

Earlier draft text may be superseded by later instructions or approved packs. For every new job, repeat source intake rather than treating this guide or a past property's finished pack as evidence of the new property's facts.

## Studio decisions (where the Ad Studio differs from this guide)

- **No names (25 November 2026).** The studio's packs leave out the registered owner, the company or VAT registration number and the business rescue practitioner or other representative, although section 8.2 and the DP3107 reference pack include them. Keegan decided this; the read's instructions (`studio/facts.py`) and the privacy check enforce it.
- **Registration fees** come from the instruction, not the documents. When no document states one, the studio leaves it out rather than guess (rule 1); the team adds it in the studio.
