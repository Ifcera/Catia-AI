# Changelog

All versions are **alpha** (pre-beta).

## v0.2.0-alpha — 2026-11-17

### Added
- **Helical features:** one-call compression springs (closed/ground ends), helix + sweep (rib/slot),
  straight / cross / diagonal knurling.
- **Threads:** ISO metric cosmetic threaded holes, plus the new `model_thread` tool for real (modelled) ISO thread
  geometry — internal and external, also on inclined axes, visible in STL exports.
- **Materials** from the CATIA catalogue (brass, steel, aluminium…) and mass calculation.
- Feature tools can check results in the same call (`expect`), take an inline sketch (`profile`),
  drill several holes at once (`positions`), and export STL/STEP while saving.
- Example outputs: compression spring, M2 brass heat-set insert, 45° elbow hose fitting
  (real M20×1.5 thread, softened hexagon).

### Changed
- Token optimisation: the fixed part sent with every request is ~22% smaller; for the same job,
  cache writes dropped ~73% and cache reads ~22%.
- 51 tools in total; 100 tests (including real CATIA R20 tests) pass.

### Fixed
- Blind hole depth is now applied correctly (it used to stay at 10 mm).
- Edge length units.
- Bounding-box measurement on curved bodies.

## v0.1.0-alpha — 2026-11-13

- First public alpha: Part Design + Assembly tools (44), local MCP server, Claude Code bridge,
  Claude / OpenAI / Gemini API providers, PySide6 UI (Türkçe / English), Windows `.exe` build.
