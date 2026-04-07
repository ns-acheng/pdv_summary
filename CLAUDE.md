# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python CLI tool that fetches and displays release PDV (Post-Deployment Validation) data from the Netskope Insights Platform API. Presents datacenter-level component status in colorized ASCII tables.

## Commands

```bash
# Install dependencies (no virtual environment — use global Python directly)
pip install -r requirements.txt

# Run interactively
python pdv_summary.py

# Specific version/env/day
python pdv_summary.py 135.0 prod 4

# Sync release days from API (version required)
python pdv_summary.py 136.0 --sync-releases

# Cache-only datacenter lookup
python pdv_summary.py 135.0 --dc DFW3

# Show all components (default filters to client/nsclient only)
python pdv_summary.py 135.0 prod --show-all-comp

# XPAS Jenkins log fetch from saved HTML
python util_xpas.py --from-html-file "cache-xpas/<file>.log" --cookie "<cookie>" --url "<url>"

# Direct XPAS fetch
python util_xpas.py --url "<consoleFull_url>" --cookie "<cookie>"
```

There are no tests or linting commands configured.

## Architecture

**Module responsibilities:**

- **pdv_summary.py** — Main CLI entry point. Handles argument parsing, API orchestration, token management, release syncing, interactive menus, and failure log analysis. Uses `argparse` for CLI.
- **util_browser.py** — Chrome remote debugging (CDP via WebSocket on port 9222) for automatic JWT token extraction from `localStorage`. Launches Chrome, discovers tabs, extracts tokens/cookies.
- **util_output.py** — ASCII table rendering with ANSI color codes. Status colorization: FAILURE=red, SUCCESS=green, RUNNING=yellow, TODO=blue. PDV Reason column was intentionally removed.
- **util_xpas.py** — Jenkins console log fetching and caching. Downloads `consoleFull` HTML, follows `consoleText` link for plain-text logs. Stores under `cache-xpas/`.
- **util_log.py** — Jenkins log parsing. Extracts test summaries, failed/error cases, and repeating patterns from log text.

**Data flow:**

1. Token loaded from `data/token.txt` (auto-refreshes on 403 via Chrome CDP)
2. Release metadata fetched from `insights-platform.netskope.io/releasemgmtserv/v1` API
3. Component/DC status fetched per release day, cached in `cache/component_data_<version>_<label>.json`
4. Failure cases trigger Jenkins log download → parse → display
5. Concurrent downloading via `ThreadPoolExecutor`

**Data files (under `data/`):**

- `releases.json` — Version → release day mappings (auto-synced or manual)
- `component_mapping.json` — UUID → component name (50+ entries)
- `dc_mapping.json` — UUID → datacenter name (~200+ entries, auto-discovered)
- `token.txt` — Cached JWT bearer token (gitignored)

## Code Style & Rules

- Keep lines under 110 characters
- Prefer small, focused changes that preserve current behavior
- Keep utility modules separated by concern
- Prefer explicit file outputs in cache folders for debugging traceability
- When Jenkins `consoleFull` is HTML, parse and follow `consoleText` for real plain logs
- **Never create a Python virtual environment** — install/run directly in the current environment
