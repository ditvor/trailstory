# Changelog

All notable changes to this project will be documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- Initial project structure, CI pipeline, and developer tooling.
- HTML renderer producing a self-contained, bilingual memory page with
  photos embedded as base64 data URIs and an inline elevation-profile SVG
  (`trailstory.renderers.html.render_html`, `templates/memory.html.j2`).
- `trailstory generate` CLI: orchestrates the full pipeline (GPX → photos
  → narrative → HTML) with Rich progress output, `--photos`, `--gpx`,
  `--seed`, `--name`, `--age`, `--out`, and `--location` options.
- Instagram carousel renderer: `trailstory generate --instagram` writes
  1080×1350 portrait JPEG slides under `output/{slug}/carousel/` (title +
  N photos center-cropped to 4:5 + closing pull-quote).
- Content-addressed narrative cache (`trailstory.llm.cache`): the LLM
  call is keyed on the SHA-256 of the GPX bytes, the per-photo bytes,
  the parent's seed/baby/location inputs, and the model identifier, then
  stored under `~/.cache/trailstory/narratives/<key>.json`. Iterating on
  the HTML or Instagram renderer no longer re-spends an Opus call per
  run. New `--no-cache` flag on `trailstory generate` forces a fresh
  call. `NarrativeOutput.schema_version` (defaults to `1`) lets the
  cache auto-invalidate stale shapes after a model evolution.

### Fixed
- **Privacy:** `trailstory.photos.load_photos` now strips the GPS sub-IFD
  (EXIF tag `0x8825`) from every resized JPEG and bakes EXIF orientation
  into pixels via `PIL.ImageOps.exif_transpose`. Previously the resized
  photos preserved EXIF wholesale, and the HTML renderer base64-embeds
  those JPEGs verbatim — meaning every shareable `.html` produced before
  this fix carries the precise GPS coordinates of every selected photo.
  Camera make/model, lens, and timestamp tags are kept; only GPS is
  removed. **Previously-rendered `.html` files still contain the leaked
  GPS data** — if location privacy matters, re-render those memories from
  source and re-share the new files; the originals already in recipients'
  inboxes cannot be recalled.

---

<!-- Template for new releases:

## [X.Y.Z] - YYYY-MM-DD

### Added
- New features.

### Changed
- Changes to existing behaviour.

### Deprecated
- Soon-to-be removed features.

### Removed
- Features removed this release.

### Fixed
- Bug fixes.

### Security
- Security fixes.

-->
