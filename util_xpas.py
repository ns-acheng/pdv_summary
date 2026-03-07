"""
util_xpas.py - Helper to fetch Jenkins logs and cache them.

Example:
    python util_xpas.py --url "https://cqejenkins-.../consoleFull"

Notes:
- Cookie is auto-extracted from Chrome local-profile debug session.
- Output is written under cache-xpas/ with auto-generated filenames.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
from urllib.parse import urljoin, urlparse

import time

import requests
from util_browser import DEBUG_URL
from util_browser import get_tabs
from util_browser import get_cookie_from_browser
from util_output import print_xpas_failed_cases

DEFAULT_URL = (
    "https://cqejenkins-xpas-nonprod.netskope.io/job/MPAS/20268/consoleFull"
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache-xpas")
SEC_CH_UA = '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"'
_LAST_SUCCESS_FQDN: str | None = None


def ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def parse_job_build(url: str) -> tuple[str, str]:
    """Extract job name and build number from a Jenkins console URL."""
    match = re.search(r"/job/([^/]+)/([0-9]+)/(consoleFull|consoleText|console)", url)
    if not match:
        return "unknown_job", "unknown_build"
    return match.group(1), match.group(2)


def build_headers(url: str, cookie: str) -> dict[str, str]:
    """Build request headers similar to the provided curl request."""
    referer = url.replace("/consoleFull", "/console").replace("/consoleText", "/console")
    return {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": referer,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "sec-ch-ua": SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Cookie": cookie,
    }


def fetch_console_full(
    url: str,
    cookie: str,
    timeout: int = 60,
    verify_ssl: bool = True,
) -> str:
    """Fetch Jenkins consoleFull page text using authenticated browser cookie."""
    if not cookie or not cookie.strip():
        raise ValueError("Cookie is required but was not provided.")

    headers = build_headers(url, cookie.strip())
    response = requests.get(url, headers=headers, timeout=timeout, verify=verify_ssl)
    response.raise_for_status()
    return response.text


def extract_console_text_href(html_text: str) -> str | None:
    """Extract consoleText href from Jenkins console HTML."""
    match = re.search(
        r"<a[^>]+href=[\"']([^\"']*consoleText[^\"']*)[\"']",
        html_text,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def resolve_console_text_url(base_url: str, html_text: str) -> str | None:
    """Resolve relative consoleText href to an absolute URL."""
    href = extract_console_text_href(html_text)
    if not href:
        return None
    return urljoin(base_url, href)


def save_output(
    url: str,
    content: str,
    output_path: str | None = None,
    *,
    prefix: str,
    extension: str,
) -> str:
    """Save content into cache-xpas with a deterministic file name."""
    ensure_cache_dir()
    if output_path:
        target = output_path
    else:
        job_name, build_number = parse_job_build(url)
        filename = f"{prefix}_{job_name}_{build_number}.{extension}"
        target = os.path.join(CACHE_DIR, filename)

    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    return target


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "POC: fetch Jenkins consoleFull HTML, parse consoleText link, "
            "and save outputs under cache-xpas/."
        )
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Jenkins consoleFull URL")
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification (POC/debug only).",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional absolute/relative output path for HTML file.",
    )
    parser.add_argument(
        "--output-text",
        default="",
        help="Optional absolute/relative output path for plain text console log.",
    )
    parser.add_argument(
        "--from-html-file",
        default="",
        help=(
            "Parse an existing Jenkins HTML console file, resolve consoleText URL, "
            "and fetch plain text log."
        ),
    )
    parser.add_argument(
        "--keep-html",
        action="store_true",
        help="Keep source HTML file after plain-text download succeeds.",
    )
    return parser.parse_args()


def remove_html_if_needed(html_path: str, keep_html: bool) -> None:
    """Remove source HTML log when plain text is successfully downloaded."""
    if keep_html:
        print("[util_xpas] Keeping source HTML file (--keep-html).")
        return
    if not html_path:
        return
    if os.path.isfile(html_path):
        os.remove(html_path)
        print(f"[util_xpas] Removed source HTML file: {html_path}")


def is_chrome_debug_running() -> bool:
    """Return True if Chrome DevTools endpoint on port 9222 is reachable."""
    try:
        tabs = get_tabs(DEBUG_URL)
        return isinstance(tabs, list)
    except requests.exceptions.RequestException:
        return False


def fetch_and_analyze(
    jenkins_url: str,
    cookie: str | None = None,
    timeout: int = 60,
    verify_ssl: bool = True,
    prefix: str = "[util_xpas]",
    concise_output: bool = False,
    output_text_filename: str | None = None,
) -> str | None:
    """Download a Jenkins consoleFull page, save the plain-text log, and print
    a failed-case summary.  Returns the saved .txt path, or None on error.

    *jenkins_url* should be the .../consoleFull URL.
    *cookie* is auto-extracted from the Chrome local-profile session when omitted.
    """
    # Ensure URL ends with /consoleFull
    global _LAST_SUCCESS_FQDN

    clean_url = jenkins_url.rstrip("/")
    if not clean_url.endswith("/consoleFull"):
        clean_url = clean_url.rstrip("/console").rstrip("/consoleText") + "/consoleFull"
    current_fqdn = (urlparse(clean_url).hostname or "").strip().lower()

    max_retries = 3
    retry_delay = 10  # seconds

    for attempt in range(1, max_retries + 1):
        try:
            if not cookie:
                if not concise_output:
                    print(f"{prefix} No cookie provided, extracting from Chrome session...")
                    cookie = get_cookie_from_browser(clean_url)
                else:
                    # Hide noisy browser-helper logs in concise mode.
                    with contextlib.redirect_stdout(io.StringIO()):
                        cookie = get_cookie_from_browser(clean_url, prefix=prefix)
                if not cookie:
                    print(f"{prefix} Could not obtain Jenkins cookie from browser.")
                    return None

            html_text = fetch_console_full(
                url=clean_url,
                cookie=cookie,
                timeout=timeout,
                verify_ssl=verify_ssl,
            )
            html_source = save_output(
                clean_url, html_text, None, prefix="xpas_console", extension="log"
            )
            if not concise_output:
                print(f"{prefix} Saved Jenkins console HTML to: {html_source}")

            plain_url = resolve_console_text_url(clean_url, html_text)
            if not plain_url:
                print(f"{prefix} consoleText link not found in HTML.")
                if concise_output:
                    with contextlib.redirect_stdout(io.StringIO()):
                        remove_html_if_needed(html_source, False)
                else:
                    remove_html_if_needed(html_source, False)
                return None

            plain_text = fetch_console_full(
                url=plain_url,
                cookie=cookie,
                timeout=timeout,
                verify_ssl=verify_ssl,
            )
            txt_output_path = None
            if output_text_filename:
                name = output_text_filename.strip()
                if not name.lower().endswith(".txt"):
                    name += ".txt"
                txt_output_path = os.path.join(CACHE_DIR, name)

            saved_text = save_output(
                plain_url,
                plain_text,
                txt_output_path,
                prefix="xpas_console",
                extension="txt",
            )
            if concise_output:
                with contextlib.redirect_stdout(io.StringIO()):
                    remove_html_if_needed(html_source, False)
            else:
                remove_html_if_needed(html_source, False)
            _LIGHT_BROWN = "\033[38;5;180m"
            _RESET = "\033[0m"
            print(
                f"{prefix} Saved plain-text log to: "
                f"{_LIGHT_BROWN}{saved_text}{_RESET}"
            )
            if current_fqdn:
                _LAST_SUCCESS_FQDN = current_fqdn
            if not concise_output:
                print(f"{prefix} Retrieved {len(plain_text)} characters of plain text.")
                print_xpas_failed_cases(saved_text, prefix=prefix)
            return saved_text

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            if status == 403 and attempt < max_retries:
                if not concise_output:
                    print(
                        f"{prefix} HTTP 403 on attempt {attempt}/{max_retries}. "
                        f"Re-fetching cookie in {retry_delay}s..."
                    )
                time.sleep(retry_delay)
                cookie = None  # force re-extraction on next attempt
                continue
            print(f"{prefix} HTTP error ({status}): {exc}")
            if status == 403 and is_chrome_debug_running():
                if _LAST_SUCCESS_FQDN and current_fqdn and _LAST_SUCCESS_FQDN != current_fqdn:
                    print(
                        f"{prefix} Detected Jenkins FQDN changed from "
                        f"{_LAST_SUCCESS_FQDN} to {current_fqdn}."
                    )
                    print(
                        f"{prefix} Chrome may still be using old-site session context."
                    )
                    print(
                        f"{prefix} Please reopen Chrome, open the new Jenkins site once, "
                        "then run the tool again."
                    )
                print(
                    f"{prefix} Chrome debug session is running on port 9222, "
                    "but request is still 403."
                )
                print(
                    f"{prefix} Please refresh the Jenkins tab in Chrome and "
                    "run the tool again."
                )
            return None
        except Exception as exc:
            print(f"{prefix} Failed to fetch/analyze Jenkins log: {exc}")
            return None


def test_main() -> None:
    args = parse_args()
    try:
        print("[util_xpas] Extracting cookie from Chrome session...")
        cookie = get_cookie_from_browser(args.url)
        if not cookie:
            raise ValueError(
                "Could not obtain Jenkins cookie. Ensure Chrome is running "
                "with --remote-debugging-port=9222 and has the Jenkins tab open."
            )

        if args.from_html_file:
            with open(args.from_html_file, "r", encoding="utf-8") as f:
                html_text = f.read()
            html_source = args.from_html_file
            print(f"[util_xpas] Loaded HTML from: {html_source}")
        else:
            html_text = fetch_console_full(
                url=args.url,
                cookie=cookie,
                timeout=args.timeout,
                verify_ssl=not args.insecure,
            )
            html_source = save_output(
                args.url,
                html_text,
                args.output or None,
                prefix="xpas_console",
                extension="log",
            )
            print(f"[util_xpas] Saved Jenkins console HTML to: {html_source}")

        plain_url = resolve_console_text_url(args.url, html_text)
        if not plain_url:
            raise ValueError("consoleText link not found in HTML.")

        plain_text = fetch_console_full(
            url=plain_url,
            cookie=cookie,
            timeout=args.timeout,
            verify_ssl=not args.insecure,
        )
        saved_text = save_output(
            plain_url,
            plain_text,
            args.output_text or None,
            prefix="xpas_console",
            extension="txt",
        )
        remove_html_if_needed(html_source, args.keep_html)
        print(f"[util_xpas] Resolved plain-text URL: {plain_url}")
        print(f"[util_xpas] Saved Jenkins plain-text log to: {saved_text}")
        print(f"[util_xpas] Retrieved {len(plain_text)} characters of plain text.")
        print_xpas_failed_cases(saved_text)
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        print(f"[util_xpas] HTTP error ({status}): {exc}")
        if status == 403 and is_chrome_debug_running():
            print(
                "[util_xpas] Chrome debug session is running on port 9222, "
                "but request is still 403."
            )
            print(
                "[util_xpas] Please refresh the Jenkins tab in Chrome and run "
                "the tool again."
            )
        raise SystemExit(1)
    except Exception as exc:
        print(f"[util_xpas] Failed: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    test_main()
