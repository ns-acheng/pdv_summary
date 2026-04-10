"""
jenkins_log_analyze.py - Standalone tool to fetch and analyze Jenkins logs.

Usage:
    python jenkins_log_analyze.py --url "https://cqejenkins-xpas-prod03.netskope.io/job/MPAS/11934/"

Flow:
  1. Auto-extract cookie from Chrome debug session (port 9222).
  2. Normalize URL -> /consoleFull, fetch HTML, follow consoleText link.
  3. Save the plain-text log under cache-xpas/.
  4. Parse and display failed test cases or repeating patterns.
"""

from __future__ import annotations

import argparse
import os

from util_xpas import (
    fetch_and_analyze,
    parse_job_build,
    CACHE_DIR,
)
from util_output import print_xpas_failed_cases

PREFIX = "[jenkins]"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch and analyze a Jenkins job log."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Jenkins job URL (e.g. .../job/MPAS/11934/)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Normalize URL for parse_job_build (needs /consoleFull suffix)
    clean = args.url.rstrip("/")
    if not clean.endswith(("/consoleFull", "/consoleText", "/console")):
        clean += "/consoleFull"
    job_name, build_number = parse_job_build(clean)
    print(f"{PREFIX} Fetching Jenkins log for {job_name} #{build_number} ...")

    saved_path = fetch_and_analyze(
        jenkins_url=args.url,
        timeout=args.timeout,
        verify_ssl=not args.insecure,
        prefix=PREFIX,
        concise_output=False,
    )

    if not saved_path:
        print(f"{PREFIX} No log downloaded.")
        return

    # Display saved file path relative to cache-xpas/
    rel_path = os.path.relpath(saved_path, os.path.dirname(CACHE_DIR))
    print(f"\n{PREFIX} Log saved: {rel_path}")
    print(f"{PREFIX} Analysis complete.")


if __name__ == "__main__":
    main()
