"""Datacenter name mapping utilities.

Provides helper functions to:
- discover GUID -> DC name mappings from API endpoints
- load/save DC cache + manual mapping files
- resolve unknown/missing DC names for one fetched release-day payload
"""

from __future__ import annotations

import json
import os
from typing import Callable

import requests


BASE_API = "https://insights-platform.netskope.io/releasemgmtserv/v1"
ROBIN_BASE_API = "https://insights-platform.netskope.io/robingotoucan/v1"


def extract_dc_mapping(data, mapping=None) -> dict:
    """Recursively extract UUID-like id -> datacenter name mappings."""
    if mapping is None:
        mapping = {}
    if isinstance(data, dict):
        # Prefer UUID-like id fields over numeric/internal ids.
        obj_id = None
        for key in ("datacenterId", "uuid", "dc_id", "id"):
            value = data.get(key)
            if isinstance(value, str) and len(value) == 36 and "-" in value:
                obj_id = value
                break

        obj_name = (
            data.get("name") or data.get("datacenterName")
            or data.get("dcName") or data.get("dc_name")
            or data.get("label") or data.get("dcname")
        )
        if obj_id and isinstance(obj_name, str) and obj_name:
            mapping[obj_id] = obj_name

        for v in data.values():
            extract_dc_mapping(v, mapping)
    elif isinstance(data, list):
        for item in data:
            extract_dc_mapping(item, mapping)
    return mapping


def fetch_dc_names(
    token: str,
    profile: dict,
    headers: dict,
    cache_dir: str,
    verbose: bool = True,
    *,
    base_api: str = BASE_API,
    robin_base_api: str = ROBIN_BASE_API,
) -> dict:
    """Try several endpoints to discover datacenter GUID -> name mapping."""
    rid = profile["release_day_id"]
    dash = profile.get("dashboard", "release")
    rtype = profile.get("releaseType", "prod")
    rver = profile.get("releaseVersion", "")
    rday = profile.get("releaseDay", "day1")

    candidate_urls = [
        ("release_days/{id}", f"{base_api}/release_days/{rid}"),
        ("release_days/{id}/datacenters", f"{base_api}/release_days/{rid}/datacenters"),
        ("datacenters", f"{base_api}/datacenters"),
        ("robingotoucan/datacenters", f"{robin_base_api}/datacenters"),
        ("components", f"{base_api}/components"),
        ("pdv/serviceDc", f"{base_api}/pdv/serviceDc/{dash}/{rtype}/{rver}/{rday}"),
        ("pdv/applications", f"{base_api}/pdv/applications/{dash}/{rtype}/{rver}/{rday}"),
        ("pdv/releases", f"{base_api}/pdv/releases/{dash}?releaseType={rtype}"),
        ("pdv/summary (no app)", f"{base_api}/pdv/summary/{dash}/{rtype}/{rver}/{rday}"),
        ("pdv/signoff", f"{base_api}/pdv/signoff/{dash}/{rtype}/{rver}/{rday}"),
    ]

    combined = {}
    got_403 = False

    for label, url in candidate_urls:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if verbose:
                print(f"    [{resp.status_code}] {label}: {url}")
            if resp.status_code == 403:
                got_403 = True
            if resp.status_code != 200:
                continue

            data = resp.json()
            mapping = extract_dc_mapping(data)
            if mapping:
                combined.update(mapping)
                if verbose:
                    print(f"           -> found {len(mapping)} GUID-name pair(s)")
            elif verbose:
                text = json.dumps(data, indent=2)
                if len(text) > 2000:
                    text = text[:2000] + "\n... (truncated)"
                print(f"           -> response (no GUID-name pairs):\n{text}")

            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)
                probe_file = os.path.join(cache_dir, f"probe_{label.replace('/', '_')}.json")
                with open(probe_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
        except Exception as exc:
            if verbose:
                print(f"    [ERR]  {label}: {exc}")
            continue

    if got_403 and not combined:
        raise requests.exceptions.HTTPError(
            "403 Forbidden during DC name resolution",
            response=type("R", (), {"status_code": 403})(),
        )
    return combined


def load_dc_cache(dc_cache_file: str, dc_mapping_file: str) -> dict:
    """Load DC names from cache and overlay manual mapping (manual wins)."""
    result = {}
    if os.path.isfile(dc_cache_file):
        try:
            with open(dc_cache_file, "r", encoding="utf-8") as f:
                result.update(json.load(f))
        except Exception:
            pass
    if os.path.isfile(dc_mapping_file):
        try:
            with open(dc_mapping_file, "r", encoding="utf-8") as f:
                result.update(json.load(f))
        except Exception:
            pass
    return result


def save_dc_cache(mapping: dict, dc_cache_file: str) -> None:
    os.makedirs(os.path.dirname(dc_cache_file), exist_ok=True)
    with open(dc_cache_file, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2)


def is_unknown_dc_name(name: str | None) -> bool:
    text = (name or "").strip()
    if not text:
        return True
    upper = text.upper()
    if upper in {"UNKNOWN", "UNKOWN", "XX"}:
        return True
    return upper.startswith("UNKNOWN_") or upper.startswith("UNMAPPED_")


def collect_all_dc_ids(all_apps: dict) -> set[str]:
    dc_ids: set[str] = set()
    for components in all_apps.values():
        for comp_data in components.values():
            for dc_id in comp_data.get("datacenters", {}).keys():
                if isinstance(dc_id, str) and dc_id:
                    dc_ids.add(dc_id)
    return dc_ids


def save_dc_mapping_updates(updates: dict[str, str], dc_mapping_file: str) -> None:
    if not updates:
        return
    os.makedirs(os.path.dirname(dc_mapping_file), exist_ok=True)

    mapping = {}
    if os.path.isfile(dc_mapping_file):
        try:
            with open(dc_mapping_file, "r", encoding="utf-8") as f:
                mapping = json.load(f)
        except Exception:
            mapping = {}

    mapping.update(updates)
    with open(dc_mapping_file, "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
        f.write("\n")


def resolve_unknown_dc_names(
    token: str,
    profile: dict,
    all_apps: dict,
    dc_names: dict,
    *,
    fetch_dc_names_func: Callable[[str, dict, bool], dict],
    refresh_token_func: Callable[[str], str],
    dc_mapping_file: str,
    dc_cache_file: str,
) -> tuple[str, dict, int]:
    """Resolve unknown/missing DC names before rendering output.

    Returns: (token, updated_dc_names, resolved_count)
    """
    dc_ids_in_day = collect_all_dc_ids(all_apps)

    # Decide unresolved state based on manual mapping file (authoritative output),
    # not the merged in-memory cache view.
    manual_mapping = {}
    if os.path.isfile(dc_mapping_file):
        try:
            with open(dc_mapping_file, "r", encoding="utf-8") as f:
                manual_mapping = json.load(f)
        except Exception:
            manual_mapping = {}

    unresolved_ids = sorted(
        dc_id for dc_id in dc_ids_in_day
        if dc_id not in manual_mapping or is_unknown_dc_name(manual_mapping.get(dc_id))
    )

    version = str(profile.get("releaseVersion", "?")).strip()
    day_label = str(profile.get("releaseDay", "?")).strip()
    env = str(profile.get("releaseType", "?")).strip()
    context = f"{version} / {day_label} / {env}"

    if not unresolved_ids:
        print(f"[dc] [{context}] No unresolved datacenter names found.")
        return token, dc_names, 0

    print(
        f"[dc] [{context}] Found {len(unresolved_ids)} unresolved datacenter id(s). "
        "Trying to resolve names from API ..."
    )
    print(f"[dc] [{context}] Unresolved IDs: {', '.join(unresolved_ids)}")

    discovered = {}
    while True:
        try:
            discovered = fetch_dc_names_func(token, profile, False)
            break
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 403:
                token = refresh_token_func(
                    "Token expired while resolving datacenter names (403)."
                )
                continue
            print(f"[dc] Failed to resolve datacenter names: {exc}")
            break
        except Exception as exc:
            print(f"[dc] Failed to resolve datacenter names: {exc}")
            break

    updates = {}
    for dc_id in unresolved_ids:
        # Prefer freshly discovered names; fall back to existing in-memory cache
        # so manual mapping can still be repaired from prior resolved values.
        candidate = discovered.get(dc_id, "") or dc_names.get(dc_id, "")
        if not is_unknown_dc_name(candidate):
            updates[dc_id] = candidate

    if not updates:
        print(f"[dc] [{context}] Could not resolve new datacenter names this run.")
        return token, dc_names, 0

    dc_names.update(updates)
    save_dc_mapping_updates(updates, dc_mapping_file)
    save_dc_cache(dc_names, dc_cache_file)

    resolved_pairs = [f"{dc_id} => {updates[dc_id]}" for dc_id in sorted(updates.keys())]
    resolved_names = sorted({name for name in updates.values()})
    print(
        f"[dc] [{context}] Resolved and saved {len(updates)} datacenter name(s) "
        f"into data/dc_mapping.json: {', '.join(resolved_names)}"
    )
    print(f"[dc] [{context}] Resolved mappings: {'; '.join(resolved_pairs)}")
    return token, dc_names, len(updates)
