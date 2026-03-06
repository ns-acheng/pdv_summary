import re
from util_log import format_failed_cases_for_display
from util_log import parse_failed_cases_from_file

MAX_COL_WIDTH = 50   # hard cap on any single column width

# ANSI colour helpers
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_BLUE = "\033[96m"
_LIGHT_BROWN = "\033[38;5;180m"
_RESET = "\033[0m"
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Return *text* with ANSI escape sequences removed."""
    return _ANSI_RE.sub("", text)


def _colorize_status(text: str) -> str:
    """Wrap known status keywords with ANSI colour codes."""
    up = text.strip().upper()
    if up == "FAILURE":
        return f"{_RED}{text}{_RESET}"
    if up in ("SUCCESS", "APPROVED", "DEPLOYED"):
        return f"{_GREEN}{text}{_RESET}"
    if up in ("QUEUE", "QUEUED", "RUNNING"):
        return f"{_YELLOW}{text}{_RESET}"
    if up == "TODO":
        return f"{_BLUE}{text}{_RESET}"
    return text


def _colorize_datacenter(text: str) -> str:
    """Render datacenter values in light-brown colour."""
    if not text:
        return text
    return f"{_LIGHT_BROWN}{text}{_RESET}"


def _wrap_text(text: str, width: int) -> list:
    """Word-wrap *text* into lines of at most *width* characters.
    Uses visible (ANSI-stripped) length so colour codes don't cause
    spurious wrapping."""
    if len(_strip_ansi(text)) <= width:
        return [text]
    # Strip ANSI for wrapping logic, then re-apply to first line only
    plain = _strip_ansi(text)
    lines, line = [], ""
    for word in plain.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        lines.append(line)
    # If a single word exceeds width, hard-break it
    final = []
    for ln in lines:
        while len(ln) > width:
            final.append(ln[:width])
            ln = ln[width:]
        final.append(ln)
    return final if final else [""]


def _print_table(title: str, headers: list, rows: list):
    """Print an ASCII table with the given title, headers, and rows.
    Long cell values are word-wrapped to MAX_COL_WIDTH."""

    # Determine column widths (capped) — ignore ANSI codes for sizing
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            visible_len = len(_strip_ansi(val))
            col_widths[i] = max(col_widths[i], min(visible_len, MAX_COL_WIDTH))

    # Widen status columns by 3 to give breathing room for colour tags
    _STATUS_HEADERS = {"PDV Status", "Deploy Status"}
    for i, h in enumerate(headers):
        if h in _STATUS_HEADERS:
            col_widths[i] += 3

    def fmt_line(values):
        parts = []
        for i, v in enumerate(values):
            visible = len(_strip_ansi(v))
            pad = col_widths[i] - visible
            parts.append(v + " " * max(pad, 0))
        return "| " + " | ".join(parts) + " |"

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"

    print(f"\n  {title} ({len(rows)} datacenter(s))")
    print(f"  {sep}")
    print(f"  {fmt_line(headers)}")
    print(f"  {sep}")
    for row in rows:
        # Wrap each cell and print multi-line rows
        wrapped = [_wrap_text(val, col_widths[i]) for i, val in enumerate(row)]
        max_lines = max(len(w) for w in wrapped)
        for line_idx in range(max_lines):
            cells = [
                w[line_idx] if line_idx < len(w) else ""
                for w in wrapped
            ]
            print(f"  {fmt_line(cells)}")
    print(f"  {sep}")


def _build_dc_rows(datacenters: dict, dc_names: dict):
    """Build header list and row list for a datacenter dict."""
    has_names = any(dc_names.get(dc_id) for dc_id in datacenters)

    # Determine the type string for the current application/component context
    type_hint = datacenters.get("__type_hint__") if isinstance(datacenters, dict) else None
    def get_type(app_name):
        if app_name is None:
            return ""
        if app_name.startswith("MPServices_Compliance"):
            return "MP_Comp"
        if app_name.startswith("MPServices"):
            return "MP"
        if app_name.startswith("MP_Manual_Compliance"):
            return "MP_Manual_Comp"
        if app_name.startswith("MP_Compliance"):
            return "MP_Comp"
        if app_name.startswith("MP"):
            return "MP"
        return app_name.split("_")[0] if "_" in app_name else app_name

    # Insert Type as the first column
    if has_names:
        headers = [
            "Type", "Datacenter", "DC UUID (short)", "Enabled",
            "PDV Status", "PDV Notes",
            "Deploy Status", "Deploy Version",
        ]
    else:
        headers = [
            "Type", "Datacenter", "Enabled",
            "PDV Status", "PDV Notes",
            "Deploy Status", "Deploy Version",
        ]

    rows = []
    # Sort by datacenter name if available, otherwise by UUID
    dc_items = sorted(datacenters.items(), key=lambda x: (dc_names.get(x[0]) or x[0]).lower())
    for dc_id, dc_info in dc_items:
        if dc_id == "__type_hint__":
            continue
        pdv = dc_info.get("pdvRun", {})
        dep = dc_info.get("deployment", {})
        pdv_status = _colorize_status(pdv.get("status", ""))
        dep_status = _colorize_status(dep.get("status", "") or "(none)")
        pdv_notes = (pdv.get("notes", "") or "").rstrip("\r\n") or "(none)"
        type_val = get_type(type_hint)
        if has_names:
            rows.append([
                type_val,
                _colorize_datacenter(dc_names.get(dc_id, "(unknown)")),
                dc_id[:12] + "...",
                str(dc_info.get("enabled", "")),
                pdv_status,
                pdv_notes,
                dep_status,
                dep.get("version", "") or "(none)",
            ])
        else:
            rows.append([
                type_val,
                _colorize_datacenter(dc_id),
                str(dc_info.get("enabled", "")),
                pdv_status,
                pdv_notes,
                dep_status,
                dep.get("version", "") or "(none)",
            ])
    return headers, rows


def print_all_components(all_apps: dict, dc_names: dict, target_components: dict, show_all_comp: bool = False):
    """Print tables for every application and every component, or only client/nsclient if show_all_comp is False."""
    def should_show(comp_id):
        if show_all_comp:
            return True
        friendly = target_components.get(comp_id, comp_id).lower()
        return ("client" in friendly or "nsclient" in friendly)

    if not all_apps:
        print("\n  No applications found in the response.")
        return
    for app_name, components in sorted(all_apps.items()):
        for comp_id, comp_data in components.items():
            if not should_show(comp_id):
                continue
            datacenters = comp_data.get("datacenters", {})
            # Pass app_name as a type hint via a special key
            if isinstance(datacenters, dict):
                datacenters = dict(datacenters)  # shallow copy
                datacenters["__type_hint__"] = app_name
            headers, rows = _build_dc_rows(datacenters, dc_names)
            friendly = target_components.get(comp_id, f"(unknown: {comp_id})")
            title = f"{friendly} ({comp_id})"
            _print_table(title, headers, rows)


def print_xpas_failed_cases(log_path: str, prefix: str = "[util_xpas]") -> None:
    """Print XPAS failed test cases from short test summary info in blue."""
    failed_cases = parse_failed_cases_from_file(log_path)
    if not failed_cases:
        print(f"{prefix} No FAILED cases found in short test summary info.")
        return

    lines = format_failed_cases_for_display(failed_cases)
    print(f"{_BLUE}{prefix} {lines[0]}{_RESET}")
    for line in lines[1:]:
        print(f"{_BLUE}{prefix} {line}{_RESET}")
