# Changelog

## Maintenance update

- Close failed ZIP uploads before cleanup so Windows preserves the intended API error.
- Fix Windows type checking and byte-exact patch test output.
- Exclude environment overrides and generated build artifacts from Git and Docker contexts.
- Require usable baseline and post-migration evidence before marking a job fully validated.
- Preserve project-defined v2 method dispatch and flag ambiguous dependency ranges for review.
- Preserve source bytes in unified diffs and report dry-run validation metrics as unmeasured.

## 0.1.0

- Added Pydantic v1 → v2 repository intake, planning, deterministic rewrites, scoped model assistance and bounded repairs.
- Added durable jobs, an offline Docker validation runner, regression evidence, and diff and report artifacts.
- Added the CLI, API, Streamlit dashboard, optional explicit GitHub pull requests and a benchmark manifest.
