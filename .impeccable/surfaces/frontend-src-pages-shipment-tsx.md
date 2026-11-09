---
version: 1
slug: "frontend-src-pages-shipment-tsx"
primary_target: "frontend/src/pages/Shipment.tsx"
related_targets: ["frontend/src/components/PackagingComposition.tsx"]
---

# Packaging composition tab

Mode: Operate. Extend the existing warehouse document workspace; read-only composition lookup.

## Direction contract

THESIS: Make real packaging relationships inspectable as a collapsible tree, without conflating assembly counts or guessing missing levels.

OWN-WORLD: Keep the existing cobalt, white, flat MoySklad-inspired tabs, compact rows, functional status colors and monospace marking codes. No identity changes.

STORY: The warehouse employee selects «Состав блоков», sees known boxes and blocks, expands actual children and searches a code while keeping its ancestor path. Missing structure offers a background CZ lookup.

FIRST VIEWPORT: Third tab beside «Сборка» and «Марки». A compact search and «Обновить из ЧЗ» toolbar precedes a dated, indented tree. Root details show package type, code and unit count. Errors keep the previous snapshot visible.

FORM: Established-world, code-led local extension; no concept seed required. Native disclosures and bounded rendering fit large documents and narrow screens.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Completion evidence — 2026-11-09

Ordinary extension of the incumbent system; documentation complete for this scope. Compared PRODUCT.md and DESIGN.md with PackagingComposition.tsx, FlowWorkspaceTabs.tsx, their Shipment/Writeoff integrations and packaging styles in app.css. The third «Состав блоков» tab preserves the existing cobalt/white flat UI, shared buttons/fields, monospace codes and existing SVG icons. The tree displays recorded box → block → pack relationships, preserves ancestor paths during search and identifies missing/unconfirmed structure; lookup does not add assembly quantities.

Inspected `packaging-desktop.png` (1280×900), `packaging-mobile.png` (390×844) and `packaging-browser-results.json` in `C:/Users/sanch/.codex/visualizations/2026/11/07/01a1b5c6-130b-7ef2-abc2-ef8899ec471a/`. The Chromium fixture used real components/styles with mocked API data. Recorded checks passed: third tab, hierarchy expansion, ancestor search, keyboard navigation, background refresh, persisted tab, narrow-screen overflow, empty state, previous snapshot on failure and no console errors.

Finish-reviewer disposition: **ship**, within the reviewed local component-fixture scope. The named P2 hint/placeholder contrast finding was scored resolved after a scoped override to the existing `--ms-text-muted` token. This is not live ЧЗ or production QA. No generated images or raster assets ship with this change.

Pre-existing documentation drift remains: DESIGN.md uses the earlier prose schema without token-bearing YAML/canonical section headings, and `.impeccable/design.json` is absent. No visual identity/system change was requested, so PRODUCT.md, DESIGN.md and the existing system files are preserved; no sidecar was created.
