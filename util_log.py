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
SUMMARY_LINE_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$")


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
    """Parse failed/error test case entries from short test summary lines."""
    failed = []
    for line in summary_lines:
        match = SUMMARY_LINE_RE.match(line.strip())
        if not match:
            continue
        failed.append(
            {
                "status": match.group(1),
                "nodeid": match.group(2),
                "reason": (match.group(3) or "").strip(),
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


# ── Repeating-pattern detection ───────────────────────────────────────────────

_TIMESTAMP_RE = re.compile(r"^\[[\dT:.Z-]+\]\s*")
_SKIP_RE = re.compile(
    r"^\[Pipeline\]|^Sleeping for ", re.IGNORECASE
)

REPEAT_THRESHOLD = 10  # minimum occurrences to report


def _content_lines(raw_lines: list[str]) -> list[tuple[str, str]]:
    """Return (original_line, normalized_key) pairs, skipping noise lines."""
    result = []
    for line in raw_lines:
        stripped = line.rstrip("\n")
        if not stripped.strip():
            continue
        norm = _TIMESTAMP_RE.sub("", stripped).strip()
        if not norm or _SKIP_RE.match(norm):
            continue
        result.append((stripped, norm))
    return result


def find_repeating_patterns(
    text: str, threshold: int = REPEAT_THRESHOLD
) -> list[dict]:
    """Detect groups of consecutive lines that repeat many times.

    Returns a list of dicts:
        {"count": int, "raw_lines": [str, ...]}
    Each raw_lines entry is one occurrence (with original timestamps).
    Only patterns repeating >= *threshold* times are returned.
    Overlapping / subset patterns are pruned so only the most informative
    grouping is kept.
    """
    raw_lines = text.splitlines()
    content = _content_lines(raw_lines)
    if not content:
        return []

    # Collect candidates for group sizes 1..4
    candidates: list[tuple[tuple[str, ...], int, list[str]]] = []
    for group_size in range(1, 5):
        counts: dict[tuple[str, ...], int] = {}
        first_raw: dict[tuple[str, ...], list[str]] = {}
        for i in range(len(content) - group_size + 1):
            key = tuple(content[i + j][1] for j in range(group_size))
            counts[key] = counts.get(key, 0) + 1
            if key not in first_raw:
                first_raw[key] = [content[i + j][0] for j in range(group_size)]

        for key, count in counts.items():
            if count >= threshold:
                candidates.append((key, count, first_raw[key]))

    # Prune overlapping patterns to keep the most informative grouping.
    # Strategy: process from smallest to largest. A larger pattern is dropped
    # if its distinct lines are already fully covered by a smaller kept pattern
    # (i.e. it's just the small cycle repeated). Single-line patterns that
    # appear in a multi-line kept pattern are also dropped.
    candidates.sort(key=lambda c: len(c[0]))
    kept: list[tuple[tuple[str, ...], int, list[str]]] = []
    for key, count, raw in candidates:
        key_set = set(key)
        # Skip if a smaller (or equal-size) kept pattern already covers all
        # distinct lines of this candidate
        if any(key_set <= set(k) for k, _, _ in kept if len(k) <= len(key)):
            continue
        kept.append((key, count, raw))

    # Drop single-line entries whose line is in a multi-line kept pattern
    multi_lines = set()
    for key, _, _ in kept:
        if len(key) > 1:
            multi_lines.update(key)
    kept = [(k, c, r) for k, c, r in kept if len(k) > 1 or k[0] not in multi_lines]

    results = [{"count": count, "raw_lines": raw} for _, count, raw in kept]
    # Sort by count descending
    results.sort(key=lambda r: r["count"], reverse=True)
    return results


def find_repeating_patterns_from_file(
    file_path: str, threshold: int = REPEAT_THRESHOLD
) -> list[dict]:
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()
    return find_repeating_patterns(text, threshold)


def format_failed_cases_for_display(
    failed_cases: list[dict],
    width: int = 110,
    max_lines_per_case: int = 3,
) -> list[str]:
    """Format failed cases into readable wrapped lines for terminal output."""
    lines = [f"Found {len(failed_cases)} failed case(s):"]
    for idx, case in enumerate(failed_cases, 1):
        case_lines = [f"{idx}. {case['nodeid']}"]
        if not case.get("reason"):
            lines.extend(case_lines[:max_lines_per_case])
            continue

        reason_block = textwrap.fill(
            f"- {case['reason']}",
            width=width,
            initial_indent="   ",
            subsequent_indent="   ",
        )
        case_lines.extend(reason_block.splitlines())

        if len(case_lines) > max_lines_per_case:
            case_lines = case_lines[:max_lines_per_case]
            case_lines[-1] = case_lines[-1].rstrip() + " ..."

        lines.extend(case_lines)
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
