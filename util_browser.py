"""
util_browser.py - Shared Chrome remote-debugging helpers.

Provides:
- Browser launch with remote debugging
- Cookie extraction for a target host
- Netskope bearer token extraction from localStorage
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from urllib.parse import urlparse

import requests
import websocket

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_URL = "http://localhost:9222/json"
DEFAULT_USER_DATA_DIR = os.path.join(SCRIPT_DIR, "local_profile")
DEFAULT_DASHBOARD_URL = (
    "https://insights.netskope.io/pdv/release/prod/dashboard"
    "?releaseVersion=135.0&releaseDay=Day+1&application=DP"
)
TOKEN_KEYS = ["token", "access_token", "id_token"]
OKTA_TOKEN_KEY = "okta-token-storage"


def launch_chrome(
    url: str,
    user_data_dir: str = DEFAULT_USER_DATA_DIR,
    prefix: str = "[util_browser]",
) -> None:
    """Launch Chrome with remote debugging enabled for the given URL."""
    cmd = (
        "start chrome"
        f' --user-data-dir="{user_data_dir}"'
        " --remote-debugging-port=9222"
        " --remote-allow-origins=*"
        f' "{url}"'
    )
    print(f"{prefix} Launching Chrome with: {cmd}")
    subprocess.Popen(f"cmd /c {cmd}", shell=True)


def get_tabs(debug_url: str = DEBUG_URL) -> list[dict]:
    """Return current Chrome tabs from the DevTools debug endpoint."""
    return requests.get(debug_url, timeout=3).json()


def wait_for_tabs(
    debug_url: str = DEBUG_URL,
    max_wait: int = 30,
    prefix: str = "[util_browser]",
) -> list[dict] | None:
    """Poll for tabs until Chrome debug endpoint is reachable or timeout."""
    poll_interval = 2
    for attempt in range(1, max_wait // poll_interval + 1):
        print(
            f"{prefix} Waiting for Chrome ... "
            f"({attempt * poll_interval}/{max_wait}s)",
            end="\r",
        )
        time.sleep(poll_interval)
        try:
            tabs = get_tabs(debug_url)
            print()
            return tabs
        except requests.exceptions.RequestException:
            continue
    print()
    return None


def find_tab_by_host(tabs: list[dict], target_host: str) -> dict | None:
    """Find first tab whose URL host contains target_host."""
    for tab in tabs:
        tab_url = tab.get("url", "")
        host = urlparse(tab_url).hostname or ""
        if target_host.lower() in host.lower():
            return tab
    return None


def find_tab_by_keyword(tabs: list[dict], keyword: str) -> dict | None:
    """Find first tab whose URL contains a keyword."""
    low_keyword = keyword.lower()
    for tab in tabs:
        if low_keyword in tab.get("url", "").lower():
            return tab
    return None


def ws_send_and_wait(
    ws: websocket.WebSocket,
    request_id: int,
    method: str,
    params: dict | None = None,
) -> dict:
    """Send one CDP command and wait for the matching response id."""
    payload = {"id": request_id, "method": method}
    if params:
        payload["params"] = params
    ws.send(json.dumps(payload))

    while True:
        message = json.loads(ws.recv())
        if message.get("id") == request_id:
            return message


def cookie_matches_host(cookie_domain: str, target_host: str) -> bool:
    """Return True if cookie domain applies to target host."""
    domain = (cookie_domain or "").lstrip(".").lower()
    host = target_host.lower()
    return domain == host or host.endswith(f".{domain}")


def get_cookie_from_browser(
    url: str,
    debug_url: str = DEBUG_URL,
    prefix: str = "[util_xpas]",
) -> str | None:
    """Get cookie header value for target URL from Chrome debug session."""
    target_host = urlparse(url).hostname
    if not target_host:
        return None

    tabs = None
    try:
        tabs = get_tabs(debug_url)
    except requests.exceptions.RequestException:
        launch_chrome(url, prefix=prefix)
        tabs = wait_for_tabs(debug_url, prefix=prefix)

    if tabs is None:
        print(f"{prefix} Chrome debug endpoint is unavailable.")
        return None

    target_tab = find_tab_by_host(tabs, target_host)
    if not target_tab:
        launch_chrome(url, prefix=prefix)
        tabs = wait_for_tabs(debug_url, prefix=prefix)
        if not tabs:
            return None
        target_tab = find_tab_by_host(tabs, target_host)

    if not target_tab:
        print(f"{prefix} No browser tab found for host: {target_host}")
        return None

    ws_url = target_tab.get("webSocketDebuggerUrl")
    if not ws_url:
        print(f"{prefix} Target tab has no WebSocket debugger URL.")
        return None

    try:
        ws = websocket.create_connection(ws_url, timeout=5)
        ws_send_and_wait(ws, 1, "Network.enable")
        cookie_resp = ws_send_and_wait(ws, 2, "Network.getAllCookies")
        ws.close()
    except Exception as exc:
        print(f"{prefix} Failed to read browser cookies: {exc}")
        return None

    all_cookies = cookie_resp.get("result", {}).get("cookies", [])
    pairs = []
    seen_names = set()
    for item in all_cookies:
        domain = item.get("domain", "")
        name = item.get("name", "")
        value = item.get("value", "")
        if not name or name in seen_names:
            continue
        if cookie_matches_host(domain, target_host):
            pairs.append(f"{name}={value}")
            seen_names.add(name)

    cookie_value = "; ".join(pairs).strip()
    if not cookie_value:
        print(f"{prefix} No matching cookies found for host: {target_host}")
        return None

    print(f"{prefix} Cookie loaded from browser for host: {target_host}")
    return cookie_value


def get_token_from_browser(
    debug_url: str = DEBUG_URL,
    dashboard_url: str = DEFAULT_DASHBOARD_URL,
    prefix: str = "[util_token]",
) -> str | None:
    """Get Netskope bearer token from browser localStorage using CDP."""
    tabs = None
    try:
        tabs = get_tabs(debug_url)
    except requests.exceptions.RequestException:
        launch_chrome(dashboard_url, prefix=prefix)
        tabs = wait_for_tabs(debug_url, prefix=prefix)

    if tabs is None:
        print(
            f"\n{prefix} Chrome did not become reachable. "
            "Close all Chrome windows and retry."
        )
        return None

    target_tab = find_tab_by_host(tabs, "insights.netskope.io")
    if not target_tab:
        print(f"{prefix} No insights.netskope.io tab found. Open dashboard and retry.")
        return None

    ws_url = target_tab.get("webSocketDebuggerUrl")
    if not ws_url:
        print(f"{prefix} Tab has no WebSocket debugger URL (try refresh).")
        return None

    # Build the JS expression once
    flat_keys_js = " || ".join(f"localStorage.getItem('{k}')" for k in TOKEN_KEYS)
    okta_js = (
        "(function(){"
        f"var raw=localStorage.getItem('{OKTA_TOKEN_KEY}');"
        "if(!raw)return null;"
        "try{var o=JSON.parse(raw);"
        "return (o.accessToken&&o.accessToken.accessToken)||null;}"
        "catch(e){return null;}"
        "})()"
    )
    js = f"{flat_keys_js} || {okta_js}"

    # Poll until the token appears (page may still be loading / auth redirecting)
    max_token_wait = 30  # seconds
    poll_interval = 3
    token = None
    for elapsed in range(0, max_token_wait, poll_interval):
        try:
            ws = websocket.create_connection(ws_url, timeout=5)
            ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {"expression": js},
                    }
                )
            )
            result = json.loads(ws.recv())
            ws.close()
            token = result.get("result", {}).get("result", {}).get("value")
            if token and token.startswith("eyJ"):
                break
            token = None
        except Exception:
            pass
        print(
            f"{prefix} Waiting for token (page loading) ... "
            f"({elapsed + poll_interval}/{max_token_wait}s)",
            end="\r",
        )
        time.sleep(poll_interval)
    # Clear the progress line
    if not token:
        print()

    if not token:
        try:
            ws2 = websocket.create_connection(ws_url, timeout=5)
            ws2.send(
                json.dumps(
                    {
                        "id": 2,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": (
                                "JSON.stringify(Object.fromEntries("
                                "Object.keys(localStorage).map(k => [k, "
                                "(() => { try { return "
                                "JSON.parse(localStorage.getItem(k)); } "
                                "catch(e) { return "
                                "localStorage.getItem(k); } })()]))"
                                ")"
                            )
                        },
                    }
                )
            )
            dump_result = json.loads(ws2.recv())
            ws2.close()
            raw = dump_result.get("result", {}).get("result", {}).get("value", "{}")
            parsed = json.loads(raw) if raw else {}
            keys = list(parsed.keys())
            print(
                f"{prefix} Token not found under keys {TOKEN_KEYS}.\n"
                f"  Available localStorage keys: {', '.join(keys)}"
            )
            cache_dir = os.path.join(SCRIPT_DIR, "cache")
            os.makedirs(cache_dir, exist_ok=True)
            dump_path = os.path.join(cache_dir, "localStorage_dump.json")
            with open(dump_path, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2)
            print(f"{prefix} Full localStorage dumped to {dump_path}")
        except Exception as exc:
            print(f"{prefix} Token not found; dump failed: {exc}")
        return None

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    return token
