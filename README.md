# PDV Parser

Fetches and displays release PDV (Post-Deployment Validation) data from the Netskope Insights Platform API. Targets specific component GUIDs (NSClient, NSClient2, Client) and presents datacenter-level status in ASCII tables.

## Requirements

```
pip install -r requirements.txt
```

## Setup

1. **Token**: On first run you'll be prompted for a JWT Bearer token (grab it from browser DevTools → Network → `authorization` header). It's cached in `data/token.txt`. If the token expires (403), you'll be prompted again automatically.

### Automatic Token Retrieval (Chrome)

To avoid manual copy-paste, the tool auto-fetches the token from an open Chrome tab via remote debugging. If Chrome is not already running with debugging enabled, it will be launched automatically.

> **Note**: If Chrome is already open **without** `--remote-debugging-port=9222`, close all Chrome windows first — Chrome only allows one instance per profile.

Just run `pdv_parser.py` as normal. On a missing/expired token it will:
1. Automatically launch Chrome with `--remote-debugging-port=9222` and open the dashboard
2. Wait for the page to load and read the token from `localStorage`
3. Fall back to a manual paste prompt only if the browser fetch fails

2. **Releases**: Release versions and their day mappings are defined in `data/releases.json`. 
   - Release days are auto-synced from the API when a requested version or env/day is not found locally.
   - Alternatively, manually add entries to `releases.json` if needed.

3. **DC Mapping**: Manual datacenter GUID → name mappings live in `data/dc_mapping.json`. Add entries as you discover them.

---

## Auto-Syncing Releases

Release day mappings (`release_day_id` per version/day) are maintained in `data/releases.json`. When you request a version or env/day that isn't in the local file, the tool automatically syncs from the Insights Platform API.

**Auto-sync triggers:**
- Version not in `releases.json` → syncs that version's release days
- Version exists but requested env/day not found (e.g. `prod` days not yet available) → re-syncs that version

**How it works:**
- Queries the release-management API to list all release versions
- For each version, fetches the release days on dashboards 1 (prod/preprod) and 16 (staging)
- Maps API `dayId` → day name and `typeId` → environment (prod/preprod)
- Builds the standard release label (e.g. "prod day 4", "staging") and release_day_id
- Merges results into `releases.json` (existing entries are preserved)

---


## Usage & Example Sessions

All arguments are optional. If omitted, an interactive menu is shown.

```
python pdv_summary.py [version] [env] [day_number] [--show-all-comp] [--dc DATACENTER]
```

### Common runs

```bash
# Interactive menu (pick version, then pick days)
python pdv_summary.py

# Pick version, then interactive day selection
python pdv_summary.py 135.0

# Specific day
python pdv_summary.py 135.0 staging
python pdv_summary.py 135.0 preprod 1
python pdv_summary.py 135.0 prod 4

# All prod days
python pdv_summary.py 135.0 prod

# All days for staging, preprod and prod
python pdv_summary.py 135.0 all

# Show all components (not just client/nsclient)
python pdv_summary.py 135.0 prod --show-all-comp

# Cache-only datacenter lookup
python pdv_summary.py 135.0 --dc DFW3

# New version — auto-syncs release days from API
python pdv_summary.py 136.0 prod
```

`--dc` mode reads cached `cache/component_data_<version>_*.json` files (no live API fetch),
filters by datacenter name, prints the same colorized table style, and checks whether
corresponding logs exist in `cache-xpas/`.

### Arguments

| Arg | Values | Description |
|-----|--------|-------------|
| `version` | `134.1`, `135.0`, ... | Release version (from `releases.json`) |
| `env` | `staging`, `preprod`, `prod`, `all` | Environment / day filter |
| `day_number` | `1`, `2`, `3`, `4` | Day number (for `preprod` or `prod`) |
| `--show-all-comp` | (flag) | Show all components (default: only client/nsclient) |
| `--dc` | e.g. `DFW3` | Cache-only datacenter query for a release version; outputs client/nsclient rows and checks `cache-xpas` logs |

## Output example
<img width="1567" height="821" alt="image" src="https://github.com/user-attachments/assets/f69afb2e-db78-4726-a950-a7318ee9c5aa" />


## Target Components

| Name | GUID |
|------|------|
| NSClient d5f1 | `d5f1a252-05e9-4679-9be1-aaecd106de1a` |
| NSClient 0d05 | `0d055ea2-fcaa-4c60-94b0-c3165a8956b8` |
| Client 3380 | `33809b17-a76b-4531-b8fd-272e5a90680b` |

---

## Jenkins Log Analyzer

`jenkins_log_analyze.py` is a standalone tool to fetch and analyze a Jenkins job log directly by URL, without needing a PDV release context.

### Usage

```bash
python jenkins_log_analyze.py --url "<jenkins_job_url>"
```

### Examples

```bash
# Analyze a specific MPAS job
python jenkins_log_analyze.py --url "https://cqejenkins-xpas-prod03.netskope.io/job/MPAS/11934/"

# Disable TLS verification (debug/internal CAs)
python jenkins_log_analyze.py --url "https://cqejenkins-xpas-prod03.netskope.io/job/MPAS/11934/" --insecure

# Custom HTTP timeout
python jenkins_log_analyze.py --url "https://cqejenkins-xpas-prod03.netskope.io/job/MPAS/11934/" --timeout 120
```

The URL can be the base job URL (`.../job/MPAS/11934/`), or include any console suffix (`/console`, `/consoleFull`, `/consoleText`) — it normalizes automatically.

### What it does

1. Auto-extracts a browser cookie from the Chrome debug session (port 9222) for the Jenkins hostname
2. Fetches the `consoleFull` HTML page, parses the `consoleText` link, and downloads the plain-text log
3. Saves the log to `cache-xpas/xpas_console_<JOB>_<BUILD>.txt`
4. Parses and displays failed test cases from the pytest short summary section, or detects repeating failure patterns as a fallback

### Prerequisites

Chrome must be running with remote debugging enabled and have the target Jenkins site open and logged in:

```bash
# Windows — close all existing Chrome windows first, then:
start chrome --user-data-dir="local_profile" --remote-debugging-port=9222 "https://cqejenkins-xpas-prod03.netskope.io/"
```

### Example output

```
[jenkins] Fetching Jenkins log for MPAS #11934 ...
[jenkins] Saved plain-text log to: cache-xpas\xpas_console_MPAS_11934.txt
[jenkins] Retrieved 85234 characters of plain text.
[jenkins] Found 9 failed case(s):
[jenkins] 1. nsclient_tests/tests/api/test_client_mpas.py::TestGeneralRegression::test_02_client_status_events
[jenkins]    - TypeError: '>' not supported between instances of 'NoneType' and 'int'
[jenkins] 2. nsclient_tests/tests/api/test_client_mpas.py::TestGeneralRegression::test_07_exception_domains
[jenkins]    - Exception: Config update did not succeed
...
```
