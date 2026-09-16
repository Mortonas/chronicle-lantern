# Chronicle Lantern Architecture

## Brand and implementation namespace

Chronicle Lantern is the external product, repository, window-title, documentation, screenshot, and release-metadata name. The application directory and Python imports remain `scenesmith/` for the preview release. This is intentional compatibility debt, not an invitation for opportunistic cleanup. A namespace rename requires a separate architecture plan, an audit of every import consumer and entry point, and a documented migration.

No PyPI package or supported external Python API is published. Launch and development documentation therefore always tells contributors to enter `scenesmith/` first.

## Configuration authority and setup mode

`src/config_io.py` owns startup selection and validation. `CHRONICLE_LANTERN_CONFIG`, when present, is authoritative. Otherwise `config/app.local.yaml` is selected when present; only the absence of both selects `config/app.example.yaml`. Invalid selected input never falls through.

Validation completes before vault, provider, rewrite, search-worker, or Club-cache initialization. Diagnostics expose only source class and error category. The example has no vault, endpoint, credential, or enabled provider and produces a safe setup shell with affected actions disabled.

Public character vocabulary is configured under `character_schema`: affiliation labels, faction labels, relationship headings, and default character tags. Defaults are `Affiliation`, `Faction`, `Relationships`, `npc`, and `character`.

## Club cache ownership

Production cache data lives under `%LOCALAPPDATA%\Chronicle Lantern\cache\generic-v1\<schema-hash>\`. `cache_manifest.json` binds that directory to product, namespace, descriptor version, every schema/prompt/request/key owner, and the lowercase SHA-256 descriptor hash. Readers recompute and verify the descriptor before access. A changed descriptor gets a fresh sibling and never shares payloads.

All identity, index, event, panel, and prep cache access goes through `ClubCacheService`. Keys include the generic cache identity. Invalid entries are misses and rebuild atomically. An unusable manifest or identity registry stops Club generation before a provider call while other tabs remain available.

The former application-local `.club-cache` is never read by the public build. Recognition is limited to known cache markers directly under that exact root. Optional cleanup is best-effort, does not recurse through or remove the root, does not follow reparse points, and preserves unknown files.

## Canonical data and provider boundary

Character identity, membership, ordering, provenance, affiliation, relationships, and other indexed facts are computed locally. Provider output is presentation-only, validated against admitted source identifiers, and fails closed. Provider calls remain behind the model adapter boundary.

Canonical `affiliation` replaces the former setting-specific identity field in normal JSON, debug projections, provider skeletons, UI views, exports, and cache schemas. Independent prep owners change only when their serialized inputs or outputs change.

## Campaign state

The Campaign tab owns local YAML persistence, backup-on-save, Markdown note export, and Recent-to-Past rollover. Its system-neutral obligation model is schema version 2. Invalid loads do not replace in-memory state.

## Public release boundary

The private repository remains authoritative. Public releases are allow-listed one-commit snapshots built from a clean private commit. Private policy, inventory, export manifest, and signed clearance records bind content review to the exact source commit, public tree, and tag. GitHub visibility changes only after repository-generated logs, artifacts, metadata, settings, tag resolution, and retained objects are reviewed against the same immutable SHA.

The preview may include only source files and the two inventory-approved, metadata-sanitized documentation screenshots rendered from the generic example vault. It does not include an executable distribution, bundled dependency, font, stock image, or other binary product asset. Screenshot capture tooling and detailed visual/OCR evidence remain private.
