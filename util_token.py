"""
util_token.py – Retrieve a Netskope Bearer token from an open Chrome tab
via Chrome Remote Debugging Protocol (no manual F12 needed).

Requirements
------------
- Start Chrome with: --remote-debugging-port=9222
- Have a netskope.io tab open (e.g. the PDV dashboard)

Usage (standalone)
------------------
    python util_token.py
        → fetches token and saves it to data/token.txt

API
---
    from util_token import get_token_from_browser
    token = get_token_from_browser()   # returns str | None
"""

import json
import os
import subprocess
import time

import requests
import websocket

DEBUG_URL = "http://localhost:9222/json"
TOKEN_KEYS = ["token", "access_token", "id_token"]
# Okta stores tokens as JSON under this key; nested path: accessToken.accessToken
OKTA_TOKEN_KEY = "okta-token-storage"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "data", "token.txt")
DASHBOARD_URL = (
    "https://insights.netskope.io/pdv/release/prod/dashboard?releaseVersion=135.0&releaseDay=Day+1&application=DP"
)


CONFIG_FILE = os.path.join(os.path.dirname(__file__), "data", "config.json")


def _load_config() -> dict:
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


DEFAULT_USER_DATA_DIR = r"\temp1\debug-profile"


def _launch_chrome():
    config = _load_config()
    user_data_dir = config.get("user_data_dir", DEFAULT_USER_DATA_DIR)
    cmd = (
        f'start chrome'
        f' --user-data-dir="{user_data_dir}"'
        f' --remote-debugging-port=9222'
        f' --remote-allow-origins=*'
        f' "{DASHBOARD_URL}"'
    )
    print(f"[util_token] Launching Chrome with: {cmd}")
    subprocess.Popen(f'cmd /c {cmd}', shell=True)
    

def get_token_from_browser(debug_url: str = DEBUG_URL) -> str | None:
    """Connect to a Chrome remote-debugging port and extract the Bearer token
    from localStorage of the first open netskope.io tab.

    If Chrome is not running with remote debugging, it will be launched
    automatically. Returns the raw token string (without 'Bearer ' prefix)
    on success, or None on failure.
    """
    # ── 1. Discover open tabs (launch Chrome if not reachable, then poll) ─
    MAX_WAIT = 30  # seconds
    POLL_INTERVAL = 2
    tabs = None
    try:
        tabs = requests.get(debug_url, timeout=3).json()
    except:
        _launch_chrome()
        for attempt in range(1, MAX_WAIT // POLL_INTERVAL + 1):
            print(f"[util_token] Waiting for Chrome ... ({attempt * POLL_INTERVAL}/{MAX_WAIT}s)", end="\r")
            time.sleep(POLL_INTERVAL)
            try:
                tabs = requests.get(debug_url, timeout=3).json()
                print()  # newline after the \r progress line
                break
            except requests.exceptions.ConnectionError:
                continue
        if tabs is None:
            print(
                "\n[util_token] Chrome did not become reachable after "
                f"{MAX_WAIT}s.\n"
                "  Make sure no Chrome instance is already running without "
                "--remote-debugging-port=9222.\n"
                "  Close all Chrome windows and retry."
            )
            return None

    # ── 2. Find a netskope.io tab ──────────────────────────────────────────
    target_tab = next(
        (t for t in tabs if "netskope.io" in t.get("url", "")), None
    )
    if not target_tab:
        print(
            "[util_token] No netskope.io tab found. "
            "Please open the PDV dashboard in Chrome."
        )
        return None

    ws_url = target_tab.get("webSocketDebuggerUrl")
    if not ws_url:
        print("[util_token] Tab has no WebSocket debugger URL (try refreshing the tab).")
        return None

    # ── 3. Read localStorage via DevTools protocol ─────────────────────────
    try:
        ws = websocket.create_connection(ws_url, timeout=5)

        # Try simple flat keys first, then dig into okta-token-storage
        flat_keys_js = " || ".join(f"localStorage.getItem('{k}')" for k in TOKEN_KEYS)
        okta_js = (
            "(function(){"
            f"var raw=localStorage.getItem('{OKTA_TOKEN_KEY}');"
            "if(!raw)return null;"
            "try{var o=JSON.parse(raw);return (o.accessToken&&o.accessToken.accessToken)||null;}"
            "catch(e){return null;}"
            "})()"
        )
        js = f"{flat_keys_js} || {okta_js}"
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": js}}))
        result = json.loads(ws.recv())
        ws.close()
    except Exception as exc:
        print(f"[util_token] WebSocket error: {exc}")
        return None

    token = result.get("result", {}).get("result", {}).get("value")

    if not token:
        # Helpful debug: dump all localStorage keys and values to cache/
        try:
            ws2 = websocket.create_connection(ws_url, timeout=5)
            ws2.send(json.dumps({
                "id": 2, "method": "Runtime.evaluate",
                "params": {
                    "expression": (
                        "JSON.stringify(Object.fromEntries("
                        "Object.keys(localStorage).map(k => [k, (() => { try { return JSON.parse(localStorage.getItem(k)); } catch(e) { return localStorage.getItem(k); } })()]))"
                        ")"
                    )
                }
            }))
            dump_result = json.loads(ws2.recv())
            ws2.close()
            raw = dump_result.get("result", {}).get("result", {}).get("value", "{}")
            parsed = json.loads(raw) if raw else {}
            keys = list(parsed.keys())
            print(f"[util_token] Token not found under keys {TOKEN_KEYS}.\n"
                  f"  Available localStorage keys: {', '.join(keys)}")
            cache_dir = os.path.join(os.path.dirname(__file__), "cache")
            os.makedirs(cache_dir, exist_ok=True)
            dump_path = os.path.join(cache_dir, "localStorage_dump.json")
            with open(dump_path, "w", encoding="utf-8") as f:
                json.dump(parsed, f, indent=2)
            print(f"[util_token] Full localStorage dumped to {dump_path}")
        except Exception as exc:
            print(f"[util_token] Token not found under keys {TOKEN_KEYS}. (dump failed: {exc})")
        return None

    # Strip 'Bearer ' prefix if present
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    return token


def fetch_and_save_token() -> str | None:
    """Fetch token from browser and persist it to data/token.txt.
    Returns the token string, or None if unavailable.
    """
    token = get_token_from_browser()
    if token:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(token)
        print(f"[util_token] Token saved to {TOKEN_FILE}")
    return token


if __name__ == "__main__":
    fetch_and_save_token()
