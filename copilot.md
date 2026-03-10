# Copilot Background Context (pdv_summary)

Last updated: 2026-03-05

## Project overview

This workspace contains Python utilities for:
- Querying PDV release-day data from Netskope release management APIs
- Extracting and printing component/datacenter summaries
- Caching API responses and processed outputs
- Fetching Jenkins (XPAS) build console outputs for POC/debug workflows

Primary workspace root:
- `c:\mycode\pdv_summary`

## Key files and responsibilities

- `pdv_summary.py`
  - Main CLI app for PDV data retrieval, release sync, extraction, and cache output.
  - Uses `util_browser.py` for browser token retrieval.
  - Uses `util_output.py` for console/table rendering.

- `util_output.py`
  - Houses output/table formatting logic extracted from `pdv_summary.py`.
  - PDV Reason column was intentionally removed from display output.

- `util_browser.py`
  - Shared browser helpers for token retrieval + cookie extraction.
  - Includes token persistence helper to `data/token.txt`.

- `util_xpas.py`
  - Fetches Jenkins `consoleFull` HTML.
  - Parses `consoleText` link from HTML and fetches plain text logs.
  - Stores outputs under `cache-xpas/`.
  - Supports parsing from existing HTML (`--from-html-file`).

## Important recent changes

1. Output refactor:
- Output/table helpers were moved out of `pdv_summary.py` into `util_output.py` to keep the main script cleaner.

2. Output columns:
- `PDV Reason` was removed from the rendered output table.

3. Release header line:
- Changed from:
  - `Release <version>  --  <N> day(s) selected`
- To:
  - `Release <version>  --  <current timestamp>`

4. Jenkins XPAS POC flow:
- Initial raw Jenkins HTML response saved from `/consoleFull`.
- Correct log source identified via HTML anchor:
  - `<a download="#20268.txt" href="consoleText" class="jenkins-button">`
- Real plain-text Jenkins log fetched from `/consoleText`.
- Existing log moved from `cache/` to `cache-xpas/`.

## Known paths and output conventions

- PDV cache files:
  - `cache/full_response_<version>_<label>.json`
  - `cache/component_data_<version>_<label>.json`

- XPAS cache files:
  - `cache-xpas/xpas_console_<job>_<build>_<timestamp>.log` (HTML)
  - `cache-xpas/xpas_consoleText_<job>_<build>_<timestamp>.txt` (plain text)

## Working style and implementation rules

1. Keep Python lines readable and generally under 110 chars.
2. Prefer small, focused changes that preserve current behavior.
3. Keep utility modules separated by concern (token, output, XPAS, main logic).
4. Prefer explicit file outputs in cache folders for debugging traceability.
5. When Jenkins `consoleFull` is HTML, parse and follow `consoleText` for real plain logs.

## Environment and package management rule (explicit)

- **NEVER create a python virtual environment.**
- **Just run/install the python modules directly in the current environment.**

## Example XPAS usage

From existing saved HTML:
- `python util_xpas.py --from-html-file "cache-xpas/xpas_console_MPAS_20268_20260305_110650.log" --cookie "<cookie>" --url "https://cqejenkins-xpas-nonprod.netskope.io/job/MPAS/20268/consoleFull"`

Direct fetch flow:
- `python util_xpas.py --url "https://cqejenkins-xpas-nonprod.netskope.io/job/MPAS/20268/consoleFull" --cookie "<cookie>"`
