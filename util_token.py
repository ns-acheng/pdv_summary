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

import os
from util_browser import get_token_from_browser

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "data", "token.txt")


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
