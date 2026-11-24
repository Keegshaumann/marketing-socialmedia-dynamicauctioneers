"""The Ad Studio: Nikki's ads and info packs, made from the property's own folder.

It replaces the old intake -> gates -> artifacts -> post flow (scrapped 24 Nov 2026,
see SPEC D127). What changed, and why:

- Nothing is uploaded. Every open property, its photos and its documents are read
  from SharePoint, where the team already keeps them. The property list and folder
  listings come from the director platform's hourly mirror (``catalog``); the bytes
  come from Microsoft Graph, or a synced OneDrive folder in development
  (``sharepoint``).
- The layouts are the team's own Canva designs, rebuilt as HTML at their exact
  sizes (``templates``), not designs the system invented.
- One screen: pick the photos, check the words, download the whole set named the
  way the team names its files.
"""
