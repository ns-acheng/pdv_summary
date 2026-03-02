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

2. **Releases**: Release versions and their day mappings are defined in `data/releases.json`. Add new versions there as needed.

3. **DC Mapping**: Manual datacenter GUID → name mappings live in `data/dc_mapping.json`. Add entries as you discover them.


## Usage

All arguments are optional. If omitted, an interactive menu is shown.

```
python pdv_parser.py [version] [env] [day_number] [--show-all-comp]
```

### Examples

```bash
# Interactive menu (pick version, then pick days)
python pdv_parser.py

# Pick version, then interactive day selection
python pdv_parser.py 135.0

# Specific day
python pdv_parser.py 135.0 staging
python pdv_parser.py 135.0 preprod 1
python pdv_parser.py 135.0 prod 4

# All prod days
python pdv_parser.py 135.0 prod

# All days for a version
python pdv_parser.py 135.0 all

# Show all components (not just client/nsclient)
python pdv_parser.py 135.0 prod --show-all-comp
```

### Arguments

| Arg | Values | Description |
|-----|--------|-------------|
| `version` | `134.1`, `135.0`, ... | Release version (from `releases.json`) |
| `env` | `staging`, `preprod`, `prod`, `all` | Environment / day filter |
| `day_number` | `1`, `2`, `3`, `4` | Day number (for `preprod` or `prod`) |
| `--show-all-comp` | (flag) | Show all components (default: only client/nsclient) |

## Output example
<img width="1567" height="821" alt="image" src="https://github.com/user-attachments/assets/f69afb2e-db78-4726-a950-a7318ee9c5aa" />


## Target Components

| Name | GUID |
|------|------|
| NSClient d5f1 | `d5f1a252-05e9-4679-9be1-aaecd106de1a` |
| NSClient 0d05 | `0d055ea2-fcaa-4c60-94b0-c3165a8956b8` |
| Client 3380 | `33809b17-a76b-4531-b8fd-272e5a90680b` |
