import argparse
import re
import requests
import json
import os
import sys
from datetime import datetime
from util_token import get_token_from_browser
from util_output import print_all_components
from util_xpas import fetch_and_analyze

if sys.platform == "win32":
    os.system("")

BASE_API = "https://insights-platform.netskope.io/releasemgmtserv/v1"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
SERVICE_MAPPING_FILE = os.path.join(DATA_DIR, "service_mpapping.json")
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache")
TOKEN_FILE = os.path.join(DATA_DIR, "token.txt")
DC_CACHE_FILE = os.path.join(CACHE_DIR, "dc_names.json")
RELEASES_FILE = os.path.join(DATA_DIR, "releases.json")
DC_MAPPING_FILE = os.path.join(DATA_DIR, "dc_mapping.json")

# Dashboard IDs we care about for release syncing
#   1  = "release"         (prod + preprod days for the main release)
#  16  = "staging-release"  (staging day)
SYNC_DASHBOARD_IDS = {1: "release", 16: "staging-release"}

def load_target_components():
    try:
        with open(SERVICE_MAPPING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Warning: {SERVICE_MAPPING_FILE} not found. No components will be targeted.")
        return {}
    except Exception as e:
        print(f"Warning: Failed to load {SERVICE_MAPPING_FILE}: {e}")
        return {}

TARGET_COMPONENTS = load_target_components()

# x-auth-params templates by env
_AUTH_PARAMS = {
    "preprod": "page=/pdv/staging-release/staging/dashboard; dashboard=staging-release; env=preprod",
    "prod":    "page=/pdv/release/prod/dashboard; dashboard=release; env=prod",
}
_LIGHT_BROWN = "\033[38;5;180m"
_RESET = "\033[0m"


def _colorize_prompt_label(label: str) -> str:
    """Color only the datacenter part (right side of ' / ') in light brown."""
    if " / " not in label:
        return label
    left, right = label.rsplit(" / ", 1)
    return f"{left} / {_LIGHT_BROWN}{right}{_RESET}"


def load_releases() -> dict:
    """Load release version -> days mapping from releases.json."""
    if not os.path.isfile(RELEASES_FILE):
        raise SystemExit(f"Missing {RELEASES_FILE}. Create it first.")
    with open(RELEASES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def day_to_profile(version: str, day: dict) -> dict:
    """Convert a day entry from releases.json into a profile dict
    that the rest of the code can consume."""
    env = day["env"]
    label = day.get("label", "")  # e.g. "prod day 2" or "staging"
    # Derive a human-readable "Day N" string for the releaseday x-auth-params field
    m = re.search(r"day\s+(\d+)", label, re.IGNORECASE)
    if m:
        release_day_label = f"Day {m.group(1)}"
    elif label.lower() == "staging":
        release_day_label = "Staging"
    else:
        release_day_label = label.title()
    return {
        "release_day_id": day["release_day_id"],
        "x_auth_params":  _AUTH_PARAMS[env],
        "dashboard":      day["dashboard"],
        "releaseType":    env,
        "releaseVersion": version,
        "releaseDay":     release_day_label,
    }


# ── Release sync from API ─────────────────────────────────────────────────────

def _sync_headers(token: str, dashboard: str = "release", env: str = "prod") -> dict:
    """Build HTTP headers for release-sync API calls."""
    if dashboard == "staging-release":
        page = "/pdv/staging-release/staging/dashboard"
    else:
        page = f"/pdv/{dashboard}/{env}/dashboard"
    x_auth = f"page={page}; dashboard={dashboard}; env={env}"
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": f"Bearer {token}",
        "origin": "https://insights.netskope.io",
        "referer": "https://insights.netskope.io/",
        "x-auth-params": x_auth,
    }


def _api_get(token: str, path: str, dashboard: str = "release", env: str = "prod") -> dict:
    """GET a releasemgmtserv endpoint, return parsed JSON."""
    url = f"{BASE_API}/{path}"
    resp = requests.get(url, headers=_sync_headers(token, dashboard, env), timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_api_metadata(token: str) -> tuple:
    """Fetch the three reference tables needed for release syncing.
    Returns (dashboards, days, release_types) dicts keyed by id."""
    raw_dash = _api_get(token, "dashboards")["dashboards"]
    raw_days = _api_get(token, "days")["days"]
    raw_types = _api_get(token, "release_types")["releaseTypes"]
    dashboards = {d["id"]: d for d in raw_dash}
    days = {d["id"]: d for d in raw_days}
    release_types = {t["id"]: t for t in raw_types}
    return dashboards, days, release_types


def _build_day_label(day_name: str, env_name: str) -> str:
    """Build a human-readable label like 'prod day 2' or 'staging'.

    day_name comes from the /days API (e.g. 'Day 1', 'Day 4', 'Staging').
    env_name comes from the /release_types API (e.g. 'prod', 'preprod').
    """
    lower = day_name.strip().lower()
    if lower == "staging":
        return "staging"
    # Extract the number from "Day 3" → "3"
    m = re.match(r"day\s+(\d+)", lower)
    if m:
        return f"{env_name} day {m.group(1)}"
    # Fallback: just combine env + day_name
    return f"{env_name} {lower}"


def _dashboard_name_for_entry(dashboard_id: int, env_name: str,
                              dashboards: dict) -> str:
    """Return the 'dashboard' value used in releases.json entries."""
    dash_obj = dashboards.get(dashboard_id, {})
    name = dash_obj.get("name", "release")
    # For staging-release dashboard, use 'staging-release'
    if name == "staging-release":
        return "staging-release"
    return "release"


def sync_releases(token: str, version_filter: str = None) -> dict:
    """Call the release-management API to discover all release versions
    and their days, then return a dict matching the releases.json schema.

    Only includes releases on dashboard IDs listed in SYNC_DASHBOARD_IDS.
    If *version_filter* is given (e.g. '135.0'), only that version is synced.
    """
    print("[sync] Fetching metadata (dashboards, days, release_types) ...")
    dashboards, days_map, type_map = fetch_api_metadata(token)

    # Fetch the full releases list
    print("[sync] Fetching releases list ...")
    all_releases = _api_get(token, "releases?type=prod")["releases"]

    # Filter to dashboards we care about
    target_releases = [
        r for r in all_releases
        if r["dashboardId"] in SYNC_DASHBOARD_IDS and r.get("enabled", True)
    ]
    if version_filter:
        target_releases = [r for r in target_releases if r["name"] == version_filter]

    if not target_releases:
        print("[sync] No matching releases found.")
        return {}

    # Group releases by version name — a version can have entries on
    # both dashboard 1 (release) and dashboard 16 (staging-release)
    from collections import defaultdict
    by_version = defaultdict(list)
    for r in target_releases:
        by_version[r["name"]].append(r)

    result = {}
    for ver_name, release_objs in sorted(by_version.items()):
        print(f"[sync] Processing {ver_name} ({len(release_objs)} dashboard(s)) ...")
        day_entries = []
        for rel in release_objs:
            rid = rel["id"]
            did = rel["dashboardId"]
            try:
                rd_list = _api_get(token, f"release_days?releaseId={rid}")["releaseDays"]
            except Exception as e:
                print(f"  [warn] Failed to fetch release_days for releaseId={rid}: {e}")
                continue

            for rd in rd_list:
                if not rd.get("enabled", True):
                    continue
                day_obj = days_map.get(rd["dayId"], {})
                type_obj = type_map.get(rd["typeId"], {})
                day_name = day_obj.get("name", f"dayId={rd['dayId']}")
                env_name = type_obj.get("name", "unknown")
                label = _build_day_label(day_name, env_name)
                dashboard_str = _dashboard_name_for_entry(did, env_name, dashboards)
                # Map env_name to the env field used in releases.json
                if env_name == "prod":
                    env_field = "prod"
                else:
                    env_field = "preprod"
                day_entries.append({
                    "label": label,
                    "release_day_id": rd["id"],
                    "env": env_field,
                    "dashboard": dashboard_str,
                })

        # Sort: staging first, then preprod days, then prod days
        def sort_key(d):
            lab = d["label"].lower()
            if lab == "staging":
                return (0, 0)
            if lab.startswith("preprod"):
                # extract day number for ordering
                m = re.search(r"(\d+)$", lab)
                return (1, int(m.group(1)) if m else 0)
            if lab.startswith("prod"):
                m = re.search(r"(\d+)$", lab)
                return (2, int(m.group(1)) if m else 0)
            return (3, 0)

        day_entries.sort(key=sort_key)
        if day_entries:
            result[ver_name] = {"days": day_entries}

    return result


def do_sync_releases(token: str, version_filter: str = None):
    """Sync releases from API and merge into releases.json."""
    new_data = sync_releases(token, version_filter)
    if not new_data:
        print("[sync] Nothing to sync.")
        return

    # Load existing
    existing = {}
    if os.path.isfile(RELEASES_FILE):
        with open(RELEASES_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)

    # Merge: new data replaces existing entries per version
    for ver, data in new_data.items():
        existing[ver] = data

    # Write back
    with open(RELEASES_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
        f.write("\n")

    versions_str = ", ".join(sorted(new_data.keys()))
    print(f"[sync] Updated {RELEASES_FILE}")
    print(f"[sync] Synced versions: {versions_str}")
    # Print summary
    for ver in sorted(new_data.keys()):
        days = new_data[ver]["days"]
        labels = ", ".join(d["label"] for d in days)
        print(f"  {ver}: {labels}")


# ── Token management ──────────────────────────────────────────────────────────

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)


def load_token() -> str:
    if os.path.isfile(TOKEN_FILE):
        token = open(TOKEN_FILE, "r", encoding="utf-8").read().strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        # A valid JWT starts with "eyJ" and must not be 'dummy'
        if token and token.startswith("eyJ") and token != "dummy":
            return token
    return refresh_token("No valid token found.")


def save_token(token: str):
    ensure_data_dir()
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token.strip())
    print(f"Token saved to {TOKEN_FILE}")


def prompt_and_save_token(reason: str) -> str:
    print(f"\n{reason}")
    print("Paste your Bearer token (from browser DevTools -> Network -> authorization header).")
    print("You can paste the full 'Bearer eyJ...' or just the 'eyJ...' part:")
    token = input("> ").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise SystemExit("No token provided. Exiting.")
    save_token(token)
    return token


def refresh_token(reason: str) -> str:
    """Try to get a fresh token from Chrome first; fall back to manual prompt."""
    print(f"\n{reason}")
    print("[token] Trying to fetch token from Chrome (remote debug)...")
    token = get_token_from_browser()
    if token and token.startswith("eyJ"):
        save_token(token)
        print("[token] Token refreshed from browser.")
        return token
    print("[token] Browser fetch failed or Chrome not running with --remote-debugging-port=9222.")
    return prompt_and_save_token("Please paste your Bearer token manually.")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _headers(token: str, x_auth_params: str) -> dict:
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9,zh-TW;q=0.8,zh;q=0.7",
        "authorization": f"Bearer {token}",
        "origin": "https://insights.netskope.io",
        "referer": "https://insights.netskope.io/",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "x-auth-params": x_auth_params,
    }


def fetch_events(token: str, profile: dict) -> dict:
    url = f"{BASE_API}/release_days/{profile['release_day_id']}/events"
    resp = requests.get(url, headers=_headers(token, profile["x_auth_params"]), timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_pdv_runs(token: str, release_component_id: int, profile: dict) -> list[dict]:
    """Call /pdv_runs?releaseComponentIds=<id> and return the list of run dicts."""
    # Build x-auth-params that includes releaseday, mirroring the browser request
    day_label = profile.get("releaseDay", "")  # e.g. "Day 2"
    env = profile.get("releaseType", "prod")
    dash = profile.get("dashboard", "release")
    x_auth = (
        f"page=/pdv/{dash}/{env}/dashboard; "
        f"dashboard={dash}; "
        f"releaseday={day_label}; "
        f"env={env}"
    )
    url = f"{BASE_API}/pdv_runs?releaseComponentIds={release_component_id}"
    hdrs = _headers(token, x_auth)
    resp = requests.get(url, headers=hdrs, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # API returns {"pdvRuns": [...]} or a bare list
    if isinstance(data, dict):
        return data.get("pdvRuns", [])
    return data if isinstance(data, list) else []


def get_latest_failure_run(pdv_runs: list[dict]) -> dict | None:
    """Return the run with the latest createdAt among FAILURE runs."""
    failures = [
        r for r in pdv_runs
        if r.get("status", "").upper() == "FAILURE" and r.get("createdBy", "").strip()
    ]
    if not failures:
        return None
    return max(failures, key=lambda r: r.get("createdAt", ""))


def job_url_to_console_full(job_url: str) -> str:
    """Convert a base Jenkins job URL to its consoleFull URL."""
    base = job_url.rstrip("/")
    # Strip any existing console suffix before appending
    for suffix in ("/consoleFull", "/consoleText", "/console"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base + "/consoleFull"


# Statuses that warrant Jenkins log analysis
_AUTO_ANALYZE_STATUSES = {"FAILURE"}             # fetch logs automatically
_PROMPT_ANALYZE_STATUSES = {"APPROVED"}          # ask user first
_ALL_ANALYZE_STATUSES = _AUTO_ANALYZE_STATUSES | _PROMPT_ANALYZE_STATUSES


def collect_analyzable_component_ids(
    all_apps: dict,
    dc_names: dict | None = None,
    target_components: dict | None = None,
) -> tuple[dict[int, str], dict[int, str]]:
    """Walk all_apps and return two dicts {releaseComponentId: label}:
      - auto_ids  : FAILURE components (analyzed automatically)
      - prompt_ids: APPROVED components (user is prompted first)
    Only considers Client/NSClient components when target_components is given.
    Deduplicates by releaseComponentId.
    """
    if dc_names is None:
        dc_names = {}
    auto_ids: dict[int, str] = {}
    prompt_ids: dict[int, str] = {}
    for app_name, components in all_apps.items():
        for comp_id, comp_data in components.items():
            # Only consider Client / NSClient components
            if target_components:
                friendly = target_components.get(comp_id, "").lower()
                if "client" not in friendly and "nsclient" not in friendly:
                    continue
            for dc_id, dc_info in comp_data.get("datacenters", {}).items():
                status = dc_info.get("pdvRun", {}).get("status", "").upper()
                rcid = dc_info.get("releaseComponentId")
                if not rcid:
                    continue
                dc_name = (
                    dc_info.get("datacenterName")
                    or dc_names.get(dc_id)
                    or dc_id[:8]
                )
                label = f"{app_name} / {dc_name}"
                if status in _AUTO_ANALYZE_STATUSES and rcid not in auto_ids:
                    auto_ids[rcid] = label
                elif status in _PROMPT_ANALYZE_STATUSES and rcid not in prompt_ids:
                    prompt_ids[rcid] = label
    return auto_ids, prompt_ids


def _analyze_component(token: str, rcid: int, label: str, profile: dict,
                       cookie: str | None = None) -> None:
    """Fetch pdv_runs for a single releaseComponentId, find the latest
    FAILURE run, download its Jenkins log, and print failed cases."""
    pretty_label = _colorize_prompt_label(label)
    print(f"\n[pdv] ── {pretty_label}  (releaseComponentId={rcid}) ──")
    try:
        runs = fetch_pdv_runs(token, rcid, profile)
    except Exception as exc:
        print(f"[pdv]   Could not fetch pdv_runs: {exc}")
        return

    if not runs:
        print("[pdv]   No pdv_runs returned.")
        return

    latest = get_latest_failure_run(runs)
    if not latest:
        print("[pdv]   No FAILURE run with a Jenkins URL found in pdv_runs.")
        return

    jenkins_job_url = latest["createdBy"].strip()
    console_url = job_url_to_console_full(jenkins_job_url)
    created_at = latest.get("createdAt", "unknown")
    print(f"[pdv]   Latest FAILURE run: createdAt={created_at}")
    print(f"[pdv]   Jenkins URL: {console_url}")

    fetch_and_analyze(
        console_url,
        cookie=cookie,
        prefix="[pdv]",
        concise_output=True,
    )


def analyze_failure_jenkins_logs(
    token: str,
    all_apps: dict,
    profile: dict,
    cookie: str | None = None,
    dc_names: dict | None = None,
    target_components: dict | None = None,
) -> None:
    """For FAILURE components, auto-fetch and analyze Jenkins logs.
    For APPROVED components, prompt the user first."""
    auto_ids, prompt_ids = collect_analyzable_component_ids(
        all_apps, dc_names, target_components
    )

    # ── Auto-analyze FAILURE components ──────────────────────────────────
    if auto_ids:
        print(f"\n[pdv] Found {len(auto_ids)} FAILURE component(s). "
              "Fetching Jenkins logs...")
        for rcid, label in auto_ids.items():
            _analyze_component(token, rcid, label, profile, cookie)

    # ── Prompt for APPROVED components ──────────────────────────────────
    if prompt_ids:
        print(f"\n[pdv] Found {len(prompt_ids)} APPROVED component(s) "
              "(manually approved after failure):")
        for rcid, label in prompt_ids.items():
            pretty_label = _colorize_prompt_label(label)
            print(f"  - {pretty_label}  (releaseComponentId={rcid})")
        try:
            answer = input(
                "\n[pdv] Collect Jenkins logs for APPROVED components? [y/N] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer in ("y", "yes"):
            for rcid, label in prompt_ids.items():
                _analyze_component(token, rcid, label, profile, cookie)
        else:
            print("[pdv] Skipped APPROVED component analysis.")


def fetch_with_retry(token: str, profile: dict):
    """Fetch events; on 403, keep asking for a new token until it works."""
    while True:
        try:
            return fetch_events(token, profile), token
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 403:
                token = refresh_token("Token is expired or invalid (403).")
                print("Retrying with new token ...")
                continue
            raise


# ── Datacenter name resolution ────────────────────────────────────────────────

def fetch_dc_names(token: str, profile: dict, verbose: bool = True) -> dict:
    """
    Try several API endpoints to discover datacenter GUID -> name mapping.
    Uses both release-management and PDV endpoints from the API spec.
    Returns {guid: name} dict.  Falls back to empty dict.
    """
    rid = profile["release_day_id"]
    dash = profile.get("dashboard", "release")
    rtype = profile.get("releaseType", "prod")
    rver = profile.get("releaseVersion", "")
    rday = profile.get("releaseDay", "day1")

    # Build a list of (label, url) pairs to probe
    candidate_urls = [
        # Release-management service endpoints
        ("release_days/{id}",
         f"{BASE_API}/release_days/{rid}"),
        ("release_days/{id}/datacenters",
         f"{BASE_API}/release_days/{rid}/datacenters"),
        ("datacenters",
         f"{BASE_API}/datacenters"),
        ("components",
         f"{BASE_API}/components"),
        # PDV endpoints (from API spec)
        ("pdv/serviceDc",
         f"{BASE_API}/pdv/serviceDc/{dash}/{rtype}/{rver}/{rday}"),
        ("pdv/applications",
         f"{BASE_API}/pdv/applications/{dash}/{rtype}/{rver}/{rday}"),
        ("pdv/releases",
         f"{BASE_API}/pdv/releases/{dash}?releaseType={rtype}"),
        ("pdv/summary (no app)",
         f"{BASE_API}/pdv/summary/{dash}/{rtype}/{rver}/{rday}"),
        ("pdv/signoff",
         f"{BASE_API}/pdv/signoff/{dash}/{rtype}/{rver}/{rday}"),
    ]

    hdrs = _headers(token, profile["x_auth_params"])
    combined = {}
    got_403 = False

    for label, url in candidate_urls:
        try:
            resp = requests.get(url, headers=hdrs, timeout=15)
            if verbose:
                print(f"    [{resp.status_code}] {label}: {url}")
            if resp.status_code == 403:
                got_403 = True
            if resp.status_code != 200:
                continue
            data = resp.json()
            mapping = _extract_dc_mapping(data)
            if mapping:
                combined.update(mapping)
                if verbose:
                    print(f"           -> found {len(mapping)} GUID-name pair(s)")
            else:
                if verbose:
                    # Print response body for 200s that yielded no mapping
                    text = json.dumps(data, indent=2)
                    if len(text) > 2000:
                        text = text[:2000] + "\n... (truncated)"
                    print(f"           -> response (no GUID-name pairs):\n{text}")
            # Also save the raw probe response for debugging
            probe_file = os.path.join(CACHE_DIR, f"probe_{label.replace('/', '_')}.json")
            with open(probe_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            if verbose:
                print(f"    [ERR]  {label}: {exc}")
            continue

    if got_403 and not combined:
        raise requests.exceptions.HTTPError(
            "403 Forbidden during DC name resolution",
            response=type('R', (), {'status_code': 403})()
        )
    return combined


def _extract_dc_mapping(data, mapping=None) -> dict:
    """Recursively search JSON for objects that have both an 'id' (UUID-like)
    and a 'name' / 'dcName' / 'datacenterName' / 'label' field."""
    if mapping is None:
        mapping = {}
    if isinstance(data, dict):
        # Try multiple key combinations for id
        obj_id = (
            data.get("id") or data.get("datacenterId")
            or data.get("uuid") or data.get("dc_id")
        )
        # Try multiple key combinations for name
        obj_name = (
            data.get("name") or data.get("datacenterName")
            or data.get("dcName") or data.get("dc_name")
            or data.get("label") or data.get("dcname")
        )
        if (
            isinstance(obj_id, str)
            and len(obj_id) == 36
            and "-" in obj_id
            and isinstance(obj_name, str)
            and obj_name
        ):
            mapping[obj_id] = obj_name
        for v in data.values():
            _extract_dc_mapping(v, mapping)
    elif isinstance(data, list):
        for item in data:
            _extract_dc_mapping(item, mapping)
    return mapping


def load_dc_cache() -> dict:
    """Load DC names: start from auto-discovered cache, then overlay
    the manual mapping from data/dc_mapping.json (takes priority)."""
    result = {}
    # Auto-discovered cache (purgeable)
    if os.path.isfile(DC_CACHE_FILE):
        try:
            result.update(json.load(open(DC_CACHE_FILE, "r", encoding="utf-8")))
        except Exception:
            pass
    # Manual mapping (authoritative, in data/)
    if os.path.isfile(DC_MAPPING_FILE):
        try:
            result.update(json.load(open(DC_MAPPING_FILE, "r", encoding="utf-8")))
        except Exception:
            pass
    return result


def save_dc_cache(mapping: dict):
    ensure_data_dir()
    with open(DC_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)


# ── Data extraction & display ─────────────────────────────────────────────────

def extract_all_components(data: dict) -> dict:
    """Extract target components from ALL applications.
    Returns {app_name: {component_id: component_data, ...}, ...}
    Only includes components listed in TARGET_COMPONENTS.
    """
    results = {}
    for app_name, app_data in data.get("applications", {}).items():
        components = app_data.get("components", {})
        filtered = {cid: cdata for cid, cdata in components.items()
                    if cid in TARGET_COMPONENTS}
        if filtered:
            results[app_name] = filtered
    return results


# Output functions moved to util_output.py



# ── Main ──────────────────────────────────────────────────────────────────────

def _match_days(days: list, env_arg: str, num_arg: str = None) -> list:
    """Filter days list by env keyword and optional day number.
    Examples:
        _match_days(days, "staging")       -> [staging day]
        _match_days(days, "prod", "4")     -> [prod day 4]
        _match_days(days, "prod")          -> [all prod days]
        _match_days(days, "preprod", "1")  -> [preprod day 1]
    """
    env_arg = env_arg.lower()
    matched = []
    for d in days:
        label = d["label"].lower()
        if env_arg == "staging" and label == "staging":
            matched.append(d)
        elif env_arg in ("prod", "preprod") and label.startswith(env_arg):
            if num_arg:
                # "prod day 4" matches num_arg="4"
                if label.endswith(f"day {num_arg}"):
                    matched.append(d)
            else:
                matched.append(d)
    return matched


def choose_version(releases: dict) -> str:
    """Pick a release version from CLI arg or interactive menu."""
    versions = sorted(releases.keys())

    # CLI: python pdv_parser.py 135.0 ...
    if len(sys.argv) > 1 and sys.argv[1] in releases:
        return sys.argv[1]

    print("Available release versions:")
    for i, ver in enumerate(versions, 1):
        days = releases[ver]["days"]
        day_labels = ", ".join(d["label"] for d in days)
        print(f"  [{i}] {ver}  ({len(days)} days: {day_labels})")
    while True:
        choice = input(f"Enter version (e.g. {versions[-1]}): ").strip()
        if choice in releases:
            return choice
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(versions):
                return versions[idx]
        except ValueError:
            pass
        print("Invalid choice, try again.")


def choose_days(days: list) -> list:
    """Let user pick specific days or all. Returns list of day dicts.
    CLI args:  python pdv_parser.py 135.0 staging
               python pdv_parser.py 135.0 prod 4
               python pdv_parser.py 135.0 preprod 1
               python pdv_parser.py 135.0 all
    """
    if len(sys.argv) > 2:
        env_arg = sys.argv[2].lower()
        if env_arg == "all":
            return days
        num_arg = sys.argv[3] if len(sys.argv) > 3 else None
        matched = _match_days(days, env_arg, num_arg)
        if matched:
            return matched
        print(f"  Warning: no days matched '{env_arg}{' ' + num_arg if num_arg else ''}', showing menu.")

    print("\nAvailable days:")
    print(f"  [0] ALL")
    for i, d in enumerate(days, 1):
        print(f"  [{i}] {d['label']:20s}  (release_day_id={d['release_day_id']})")
    while True:
        choice = input(f"Choose day(s) [0=ALL]: ").strip()
        if not choice or choice == "0":
            return days
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(days):
                return [days[idx]]
        except ValueError:
            pass
        print("Invalid choice, try again.")


def process_day(version: str, day: dict, token: str, dc_names: dict, show_all_comp: bool = False):
    """Fetch, display, and save data for one release day."""
    import inspect
    frame = inspect.currentframe().f_back
    show_all_comp = frame.f_locals.get('show_all_comp', False)
    label = day["label"]
    rid = day["release_day_id"]
    safe_label = label.replace(" ", "_")
    profile = day_to_profile(version, day)

    print(f"\nFetching {version} / {label}  (release_day_id={rid}) ...")
    try:
        data, new_token = fetch_with_retry(token, profile)
    except requests.exceptions.HTTPError as exc:
        print(f"  HTTP error: {exc}")
        return token
    except requests.exceptions.RequestException as exc:
        print(f"  Request failed: {exc}")
        return token

    # Save full response
    full_path = os.path.join(CACHE_DIR, f"full_response_{version}_{safe_label}.json")
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Extract & display
    all_apps = extract_all_components(data)

    print(f"\n{'#'*70}")
    print(f"  {version} / {label}  (release_day_id={rid})")
    print(f"{'#'*70}")
    print_all_components(all_apps, dc_names, TARGET_COMPONENTS, show_all_comp=show_all_comp)

    # ── Auto-analyze FAILURE components ──────────────────────────────────────
    analyze_failure_jenkins_logs(
        token, all_apps, profile,
        dc_names=dc_names, target_components=TARGET_COMPONENTS,
    )

    # Save cleaned JSON
    cleaned = {}
    for app_name, components in all_apps.items():
        cleaned[app_name] = {}
        for comp_id, comp_data in components.items():
            cleaned[app_name][comp_id] = {"datacenters": {}}
            for dc_id, dc_info in comp_data.get("datacenters", {}).items():
                entry = {
                    "datacenterName": dc_names.get(dc_id, ""),
                    "releaseComponentId": dc_info.get("releaseComponentId"),
                    "enabled": dc_info.get("enabled"),
                    "pdvRun": dc_info.get("pdvRun", {}),
                    "deployment": {
                        "status": dc_info.get("deployment", {}).get("status"),
                        "version": dc_info.get("deployment", {}).get("version"),
                    },
                }
                cleaned[app_name][comp_id]["datacenters"][dc_id] = entry

    comp_path = os.path.join(CACHE_DIR, f"component_data_{version}_{safe_label}.json")
    with open(comp_path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)

    return new_token


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("version", nargs="?", help="Release version (e.g. 135.0)")
    parser.add_argument("env", nargs="?", help="Environment (prod, preprod, staging, all)")
    parser.add_argument("day", nargs="?", help="Day number (e.g. 1, 2, 3, 4)")
    parser.add_argument("--show-all-comp", dest="show_all_comp", action="store_true", default=True, help="Show all components (default: True)")
    args = parser.parse_args()

    ensure_data_dir()

    releases = load_releases()

    # Auto-sync when user requests a version not present in releases.json
    if args.version and args.version not in releases:
        print(
            f"[sync] Version {args.version} not found in {RELEASES_FILE}. "
            "Trying to sync from API ..."
        )
        token = load_token()
        do_sync_releases(token, version_filter=args.version)
        releases = load_releases()
        if args.version not in releases:
            raise SystemExit(
                f"Version {args.version} still not found after sync. "
                "Please verify the version/env in Insights."
            )

    # Use CLI args if provided, else fallback to interactive
    if args.version and args.version in releases:
        version = args.version
    else:
        version = choose_version(releases)
    days = releases[version]["days"]
    if args.env:
        env_arg = args.env.lower()
        num_arg = args.day if args.day else None
        if env_arg == "all":
            selected_days = days
        else:
            matched = _match_days(days, env_arg, num_arg)
            selected_days = matched if matched else choose_days(days)
    else:
        selected_days = choose_days(days)

    now_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*70}")
    print(f"  Release {version}  --  {now_ts}")
    print(f"{'='*70}")

    token = load_token()
    dc_names = load_dc_cache()

    for day in selected_days:
        token = process_day(version, day, token, dc_names, show_all_comp=args.show_all_comp)

    print(f"\n{'='*70}")
    print(f"  Done. Processed {len(selected_days)} day(s) for release {version}.")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
