"""
util_log.py - Step-based Jenkins text log parsing helpers.

Current parsing step implemented:
1) Parse the "short test summary info" section and return failed cases.
"""

from __future__ import annotations

import argparse
import json
import re
import textwrap
from typing import Iterable

SHORT_SUMMARY_MARKER = "short test summary info"
FAILED_LINE_RE = re.compile(r"^FAILED\s+(\S+)(?:\s+-\s+(.*))?$")


def normalize_line(line: str) -> str:
    """Normalize one Jenkins log line by removing leading timestamp prefix."""
    # Example prefix: [2026-02-25T22:17:29.675Z] 
    return re.sub(r"^\[[^\]]+\]\s*", "", line.rstrip("\n"))


def normalize_lines(lines: Iterable[str]) -> list[str]:
    """Normalize all lines from raw Jenkins text log."""
    return [normalize_line(line) for line in lines]


def find_short_summary_range(lines: list[str]) -> tuple[int, int] | None:
    """Return (start, end) line indices for short test summary section."""
    start = None
    for idx, line in enumerate(lines):
        if SHORT_SUMMARY_MARKER in line.lower():
            start = idx + 1
            break

    if start is None:
        return None

    end = len(lines)
    for idx in range(start, len(lines)):
        current = lines[idx].strip()
        if current.startswith("====") and SHORT_SUMMARY_MARKER not in current.lower():
            end = idx
            break
        if current.startswith("[Pipeline]"):
            end = idx
            break

    return start, end


def get_short_test_summary_lines(lines: list[str]) -> list[str]:
    """Extract normalized lines inside short test summary info section."""
    bounds = find_short_summary_range(lines)
    if not bounds:
        return []
    start, end = bounds
    return [line for line in lines[start:end] if line.strip()]


def parse_failed_cases_from_summary(summary_lines: list[str]) -> list[dict]:
    """Parse failed test case entries from short test summary lines."""
    failed = []
    for line in summary_lines:
        match = FAILED_LINE_RE.match(line.strip())
        if not match:
            continue
        failed.append(
            {
                "nodeid": match.group(1),
                "reason": (match.group(2) or "").strip(),
                "raw": line.strip(),
            }
        )
    return failed


def parse_failed_cases_from_text(text: str) -> list[dict]:
    """High-level API: parse failed cases from raw Jenkins text content."""
    lines = normalize_lines(text.splitlines())
    summary_lines = get_short_test_summary_lines(lines)
    return parse_failed_cases_from_summary(summary_lines)


def parse_failed_cases_from_file(file_path: str) -> list[dict]:
    """High-level API: parse failed cases from a Jenkins text log file."""
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()
    return parse_failed_cases_from_text(text)


def format_failed_cases_for_display(
    failed_cases: list[dict],
    width: int = 110,
) -> list[str]:
    """Format failed cases into readable wrapped lines for terminal output."""
    lines = [f"Found {len(failed_cases)} failed case(s):"]
    for idx, case in enumerate(failed_cases, 1):
        lines.append(f"{idx}. {case['nodeid']}")
        if not case.get("reason"):
            continue
        reason_block = textwrap.fill(
            f"- {case['reason']}",
            width=width,
            initial_indent="   ",
            subsequent_indent="   ",
        )
        lines.append(reason_block)
    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse Jenkins short test summary info and output FAILED cases."
    )
    parser.add_argument("--file", required=True, help="Path to Jenkins plain-text log")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output instead of plain list",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    failed_cases = parse_failed_cases_from_file(args.file)

    if args.json:
        print(json.dumps(failed_cases, indent=2, ensure_ascii=False))
        return

    if not failed_cases:
        print("No FAILED cases found in short test summary info.")
        return

    for line in format_failed_cases_for_display(failed_cases):
        print(line)


if __name__ == "__main__":
    main()
