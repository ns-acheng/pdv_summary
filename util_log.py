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


# ── NSClient tunnel / TLS RCA detection ───────────────────────────────────────
#
# Signatures observed on a real failing run (DPAS_25562, 139.0 staging):
#   * Many   "Tunnel status: NSTUNNEL_CONNECTING"        (>= 10)
#   * Some   "Tunnel status: NSTUNNEL_DISCONNECTED_ERROR"
#   * Zero   "Tunnel status: NSTUNNEL_CONNECTED"
#   * Gateway hostname from "Gateway:: <fqdn>."
# Optional deeper evidence from stAgentSvc client log (when present):
#   * nsssl.cpp:NNN  nsssl TLS failed to connect to <host>:443, err: 10060
#   * tunnel.cpp:NNN nsTunnel TLS nsssl_connect failed, err: -1
#   * tunnel.cpp:NNN nsTunnel TLS failed to setup SSL tunnel
# err=10060 maps to WSAETIMEDOUT → TCP to :443 timed out → gateway not
# accepting connections → escalate to nsproxy / network team.

_TUNNEL_STATUS_RE = re.compile(r"Tunnel status::?\s*(NSTUNNEL_\w+)")
_GATEWAY_RE = re.compile(r"Gateway::\s*([A-Za-z0-9._-]+)")
_NSSSL_TLS_FAIL_RE = re.compile(
    r"nsssl\.cpp:\d+\s+nsssl\s+TLS failed to connect to\s+([^\s,]+)"
    r".*?err:\s*(-?\d+)",
    re.IGNORECASE,
)
_TUNNEL_CPP_SETUP_FAIL_RE = re.compile(
    r"tunnel\.cpp:\d+\s+nsTunnel\s+(?:TLS\s+)?"
    r"(nsssl_connect failed|failed to setup SSL tunnel)",
    re.IGNORECASE,
)

TUNNEL_CONNECTING_THRESHOLD = 10
_WSAETIMEDOUT = "10060"

# Friendly explanation per terminal tunnel state. CONNECTING is *not* success —
# it means the handshake is still in progress when the log/test ended.
_TUNNEL_STATE_HINT = {
    "NSTUNNEL_CONNECTED": "established (success)",
    "NSTUNNEL_CONNECTING": "still attempting — handshake not completed",
    "NSTUNNEL_DISCONNECTED_ERROR": "last attempt failed with error",
    "NSTUNNEL_DISCONNECTED_BYUSER": "tunnel was disabled by user/script",
    "NSTUNNEL_DISCONNECTED": "tunnel was disconnected",
}


def analyze_tunnel_health(text: str) -> dict | None:
    """Diagnose NSClient tunnel/TLS connectivity issues from a Jenkins log.

    Returns a dict describing the diagnosis, or None if there's no tunnel
    telemetry / nothing actionable.

    Distinguishes:
      * status polls  — every `Tunnel status:` line (script polled state)
      * retry cycles  — DISCONNECTED_ERROR → CONNECTING transitions
                        (i.e. a fresh connect attempt after a failure)
      * final state   — the last `Tunnel status:` value observed in the log,
                        used to decide whether the tunnel ended in failure

    Output shape:
        {
            "state_counts":  {"NSTUNNEL_CONNECTING": int, ...},
            "state_sequence": [state, ...],   # in log order
            "final_state":   "NSTUNNEL_...",
            "retry_cycles":  int,             # err -> connecting transitions
            "gateways":      [fqdn, ...],
            "tls_failures":  [{"target": "host:port", "err": "10060"}, ...],
            "tunnel_setup_failures": int,
            "tunnel_never_up":     bool,
            "final_state_failed":  bool,
            "headline":      "<short status line>",
            "conclusion":    "<RCA + recommended owner>",
        }
    """
    state_sequence: list[str] = [m.group(1) for m in _TUNNEL_STATUS_RE.finditer(text)]
    if not state_sequence:
        return None

    state_counts: dict[str, int] = {}
    for state in state_sequence:
        state_counts[state] = state_counts.get(state, 0) + 1

    # Count retry cycles: transitions from a DISCONNECTED_* state to CONNECTING.
    # Each such transition marks the start of a fresh connect attempt.
    retry_cycles = 0
    prev = None
    for state in state_sequence:
        if (
            state == "NSTUNNEL_CONNECTING"
            and prev is not None
            and prev.startswith("NSTUNNEL_DISCONNECTED")
        ):
            retry_cycles += 1
        prev = state

    final_state = state_sequence[-1]
    final_state_failed = final_state != "NSTUNNEL_CONNECTED"
    final_state_hint = _TUNNEL_STATE_HINT.get(final_state, "non-CONNECTED state")

    gateways: list[str] = []
    seen: set[str] = set()
    for match in _GATEWAY_RE.finditer(text):
        host = match.group(1).rstrip(".")
        if host and host not in seen:
            seen.add(host)
            gateways.append(host)

    tls_failures: list[dict] = []
    for match in _NSSSL_TLS_FAIL_RE.finditer(text):
        tls_failures.append(
            {"target": match.group(1).rstrip(",."), "err": match.group(2)}
        )

    setup_fail_count = len(_TUNNEL_CPP_SETUP_FAIL_RE.findall(text))

    connecting = state_counts.get("NSTUNNEL_CONNECTING", 0)
    connected = state_counts.get("NSTUNNEL_CONNECTED", 0)
    disc_error = state_counts.get("NSTUNNEL_DISCONNECTED_ERROR", 0)

    tunnel_never_up = (
        connecting >= TUNNEL_CONNECTING_THRESHOLD
        and connected == 0
        and disc_error >= 1
    )

    if (
        not tunnel_never_up
        and not tls_failures
        and setup_fail_count == 0
        and not (final_state_failed and disc_error >= 1)
    ):
        return None

    has_timeout = any(f["err"] == _WSAETIMEDOUT for f in tls_failures)
    gw = gateways[0] if gateways else "<unknown gateway>"

    if tunnel_never_up:
        headline = (
            f"NSClient tunnel never reached CONNECTED on {gw}: "
            f"{retry_cycles} retry cycle(s); last observed state "
            f"{final_state} ({final_state_hint})."
        )
    else:
        headline = (
            f"NSClient tunnel ended in non-CONNECTED state on {gw}: "
            f"last observed state {final_state} ({final_state_hint}) after "
            f"{retry_cycles} retry cycle(s)."
        )

    if has_timeout:
        conclusion = (
            "TLS connect to gateway:443 timed out (err 10060 = WSAETIMEDOUT). "
            "DNS resolved but TCP to :443 is not being accepted — tunnel/"
            "gateway connectivity issue. Escalate to nsproxy / network team."
        )
    elif tls_failures or setup_fail_count:
        conclusion = (
            "TLS handshake to gateway failed during tunnel setup. "
            "Tunnel/gateway connectivity issue — escalate to nsproxy / "
            "network team."
        )
    elif tunnel_never_up:
        conclusion = (
            f"Tunnel retried {retry_cycles} time(s) but never reached "
            f"CONNECTED — last attempt was still in {final_state} when the "
            "job ended. No TCP/TLS path to gateway:443 is being established "
            "— escalate to nsproxy / network team."
        )
    else:
        conclusion = (
            f"Tunnel ended in non-CONNECTED state {final_state} after "
            f"{retry_cycles} retry cycle(s). Escalate to nsproxy / network team."
        )

    return {
        "state_counts": state_counts,
        "state_sequence": state_sequence,
        "final_state": final_state,
        "final_state_hint": final_state_hint,
        "retry_cycles": retry_cycles,
        "gateways": gateways,
        "tls_failures": tls_failures,
        "tunnel_setup_failures": setup_fail_count,
        "tunnel_never_up": tunnel_never_up,
        "final_state_failed": final_state_failed,
        "headline": headline,
        "conclusion": conclusion,
    }


def analyze_tunnel_health_from_file(file_path: str) -> dict | None:
    with open(file_path, "r", encoding="utf-8") as f:
        return analyze_tunnel_health(f.read())


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
