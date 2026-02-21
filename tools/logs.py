"""Log parsing and windowing tools for multi-GB robot logs."""

import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from mcp_instance import mcp
from config import HOST_HOME, DOCKER_IMAGE, LOG_PARSER_BIN_DOCKER, RG_BIN, WORK_DIR

TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)")
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S.%f"

# Log line structure:
#   TIMESTAMP [SEVERITY][MODULE|SUBSYSTEM]:SOURCE_FILE:LINE:[TID: N] MESSAGE
# Severity: * = WARN, # = ERROR, (none) = INFO
_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\s+"
    r"([*#])?"                           # optional severity prefix
    r"\[([^|\]]+)\|([^\]]*)\]"           # [Module|Subsystem]
    r":(\w+\.\w+):(\d+)"                # :source_file:line
    r":\[TID:\s*(\d+)\]\s*"             # :[TID: N]
    r"(.*)"                              # message
)

_SEVERITY_MAP = {"*": "WARN", "#": "ERROR", None: "INFO", "": "INFO"}

# Normalize variable parts of messages for pattern grouping
_VARIABLE_RE = re.compile(
    r"(?<![a-zA-Z_])"
    r"("
    r"-?\d+\.\d+(?:,\s*-?\d+\.\d+)*"   # float sequences
    r"|-?\d+"                            # integers
    r"|0x[0-9a-fA-F]+"                  # hex
    r"|[0-9a-f]{8,}"                    # long hex strings
    r")"
    r"(?![a-zA-Z_])"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_docker_image() -> str | None:
    if DOCKER_IMAGE:
        return DOCKER_IMAGE
    detect = subprocess.run(
        ["docker", "images", "vc-dev", "--format", "{{.Tag}}"],
        capture_output=True, text=True,
    )
    tags = detect.stdout.strip().splitlines()
    return f"vc-dev:{tags[0]}" if tags else None


def _host_to_docker_path(host_path: str) -> str:
    return host_path.replace(str(HOST_HOME), "/home/host")


def _parse_timestamp(ts_str: str, reference_date: str | None = None) -> datetime:
    ts_str = ts_str.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+$", ts_str):
        return datetime.strptime(ts_str, TIMESTAMP_FMT)
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", ts_str):
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    if re.match(r"^\d{2}:\d{2}:\d{2}$", ts_str):
        if not reference_date:
            raise ValueError("Time-only timestamp requires a reference date")
        return datetime.strptime(f"{reference_date} {ts_str}", "%Y-%m-%d %H:%M:%S")
    raise ValueError(f"Unrecognized timestamp format: {ts_str!r}")


def _get_reference_date(path: Path) -> str | None:
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = TIMESTAMP_RE.match(line)
            if m:
                return m.group(1)[:10]
    return None


def _parse_line(line: str) -> dict | None:
    """Parse a log line into structured fields. Returns None if not parseable."""
    m = _LINE_RE.match(line)
    if not m:
        return None
    return {
        "timestamp": m.group(1),
        "severity": _SEVERITY_MAP.get(m.group(2), "INFO"),
        "module": m.group(3),
        "subsystem": m.group(4),
        "source_file": m.group(5),
        "source_line": m.group(6),
        "thread": m.group(7),
        "message": m.group(8),
        "raw": line,
    }


def _normalize_message(msg: str) -> str:
    """Replace variable values with <N> for pattern grouping."""
    # Truncate very long messages (collision data) before normalizing
    if len(msg) > 200:
        msg = msg[:200]
    return _VARIABLE_RE.sub("<N>", msg)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def parse_log(log_path: str) -> str:
    """Parse a binary .log file into a readable .txt file.

    Runs the proprietary parser inside Docker. If a .txt already exists
    from a previous run, skips parsing. You can also pass a .txt path directly.
    """
    path = Path(log_path)
    if not path.exists():
        return f"Error: file not found: {path}"

    raw_txt = path if path.suffix == ".txt" else path.with_suffix(".txt")

    # Skip if already fully processed
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    out_path = WORK_DIR / raw_txt.name
    if out_path.exists() and out_path.stat().st_size > 0:
        size = out_path.stat().st_size
        return f"Already parsed: {out_path} (~{size // 150:,} estimated lines)"

    # If raw .txt exists, just join continuation lines
    if raw_txt.exists() and raw_txt.stat().st_size > 0:
        line_count = 0
        with open(raw_txt, "r", errors="replace") as fin, open(out_path, "w") as fout:
            pending: str | None = None
            for line in fin:
                if TIMESTAMP_RE.match(line):
                    if pending is not None:
                        fout.write(pending + "\n")
                        line_count += 1
                    pending = line.rstrip("\n")
                else:
                    if pending is not None:
                        pending += " " + line.strip()
            if pending is not None:
                fout.write(pending + "\n")
                line_count += 1
        return f"Parsed log written to {out_path} ({line_count:,} lines)"

    if path.suffix != ".log":
        return f"Error: expected a .log or .txt file, got {path.suffix}"

    # Run parser inside Docker
    image = _get_docker_image()
    if not image:
        return "Error: no vc-dev Docker image found and VC_DOCKER_IMAGE not set"

    docker_log_path = _host_to_docker_path(str(path))
    docker_txt_path = _host_to_docker_path(str(raw_txt))
    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{HOST_HOME}:/home/host",
            f"--user={os.getuid()}:{os.getgid()}",
            image,
            LOG_PARSER_BIN_DOCKER, docker_log_path, docker_txt_path,
        ],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        return f"Error: parser failed (exit {result.returncode}): {result.stderr.strip()}"
    if not raw_txt.exists():
        return f"Error: parser did not produce output file: {raw_txt}"

    # Join continuation lines
    line_count = 0
    with open(raw_txt, "r", errors="replace") as fin, open(out_path, "w") as fout:
        pending: str | None = None
        for line in fin:
            if TIMESTAMP_RE.match(line):
                if pending is not None:
                    fout.write(pending + "\n")
                    line_count += 1
                pending = line.rstrip("\n")
            else:
                if pending is not None:
                    pending += " " + line.strip()
        if pending is not None:
            fout.write(pending + "\n")
            line_count += 1

    return f"Parsed log written to {out_path} ({line_count:,} lines)"


@mcp.tool()
def extract_log_window(
    txt_path: str,
    target_timestamp: str,
    window_seconds: float = 30.0,
) -> str:
    """Extract a time window of log lines around a target timestamp.

    Writes all lines within ±window_seconds to a new file. Default ±30s.
    These logs are very dense (~9K lines/sec), so start small.
    Logs are chronological so this exits early once past the window.
    After extracting, use summarize_log to understand the window before
    reading individual lines.
    """
    path = Path(txt_path)
    if not path.exists():
        return f"Error: file not found: {path}"

    reference_date = _get_reference_date(path)
    try:
        target = _parse_timestamp(target_timestamp, reference_date)
    except ValueError as e:
        return f"Error: {e}"

    window = timedelta(seconds=window_seconds)
    start = target - window
    end = target + window

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    safe_ts = target_timestamp.replace(" ", "_").replace(":", "-")
    out_path = WORK_DIR / f"{path.stem}_window_{safe_ts}.txt"
    line_count = 0
    first_ts: str | None = None
    last_ts: str | None = None

    with open(path, "r", errors="replace") as fin, open(out_path, "w") as fout:
        for line in fin:
            m = TIMESTAMP_RE.match(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group(1), TIMESTAMP_FMT)
            except ValueError:
                continue
            if ts < start:
                continue
            if ts > end:
                break
            fout.write(line)
            line_count += 1
            if first_ts is None:
                first_ts = m.group(1)
            last_ts = m.group(1)

    if line_count == 0:
        return f"No lines found within ±{window_seconds}s of {target_timestamp}"

    return (
        f"Extracted {line_count:,} lines to {out_path}\n"
        f"Time range: {first_ts} → {last_ts}\n"
        f"Next step: use summarize_log on this file to understand its contents."
    )


@mcp.tool()
def summarize_log(txt_path: str, top_n: int = 30) -> str:
    """Summarize a log file: patterns, counts by module, subsystem, thread, source.

    This is your map of the log. Use it to understand what's in a file
    before drilling in with filter_log or read_log_range.

    Log line format:
      TIMESTAMP [SEVERITY][MODULE|SUBSYSTEM]:SOURCE:LINE:[TID: N] MESSAGE
      Severity: * = WARN, # = ERROR, (none) = INFO
    """
    path = Path(txt_path)
    if not path.exists():
        return f"Error: file not found: {path}"

    module_counts: Counter[str] = Counter()
    subsystem_counts: Counter[str] = Counter()
    thread_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    pattern_counts: Counter[str] = Counter()
    total = 0

    with open(path, "r", errors="replace") as f:
        for line in f:
            parsed = _parse_line(line)
            if not parsed:
                continue
            total += 1
            severity_counts[parsed["severity"]] += 1
            module_counts[parsed["module"]] += 1
            if parsed["subsystem"]:
                subsystem_counts[f"{parsed['module']}|{parsed['subsystem']}"] += 1
            thread_counts[f"TID:{parsed['thread']}"] += 1
            source_counts[f"{parsed['source_file']}:{parsed['source_line']}"] += 1
            pattern_counts[_normalize_message(parsed["message"])] += 1

    if total == 0:
        return "No parseable log lines found"

    parts: list[str] = [f"Total lines: {total:,}", ""]

    parts.append("Severity:")
    for sev in ["ERROR", "WARN", "INFO"]:
        c = severity_counts.get(sev, 0)
        if c:
            parts.append(f"  {sev}: {c:,} ({100*c/total:.1f}%)")

    parts.append("")
    parts.append("Modules:")
    for mod, c in module_counts.most_common(10):
        parts.append(f"  {mod}: {c:,}")

    parts.append("")
    parts.append("Subsystems:")
    for sub, c in subsystem_counts.most_common(20):
        parts.append(f"  [{sub}]: {c:,}")

    parts.append("")
    parts.append("Threads:")
    for tid, c in thread_counts.most_common(10):
        parts.append(f"  {tid}: {c:,}")

    parts.append("")
    parts.append("Source locations:")
    for src, c in source_counts.most_common(15):
        parts.append(f"  {src}: {c:,}")

    parts.append("")
    parts.append(f"Top {top_n} message patterns:")
    for pat, c in pattern_counts.most_common(top_n):
        display = pat[:140] + "..." if len(pat) > 140 else pat
        parts.append(f"  [{c:>7,}x] {display}")

    return "\n".join(parts)


@mcp.tool()
def filter_log(
    txt_path: str,
    module: str | None = None,
    subsystem: str | None = None,
    thread: str | None = None,
    source_file: str | None = None,
    keyword: str | None = None,
    severity: str | None = None,
    max_results: int = 100,
) -> str:
    """Filter a log file by module, subsystem, thread, source file, keyword, or severity.

    Narrow down from a window to just the lines you care about. Returns
    the matching lines directly for you to read. At least one filter required.

    Examples:
      filter_log(path, module="RKS")
      filter_log(path, subsystem="Collision Detection")
      filter_log(path, thread="13")
      filter_log(path, source_file="cEngageLogic.cpp")
      filter_log(path, keyword="detach")
      filter_log(path, severity="ERROR")
      filter_log(path, module="RKS", keyword="detach")  # combine filters
    """
    path = Path(txt_path)
    if not path.exists():
        return f"Error: file not found: {path}"

    if not any([module, subsystem, thread, source_file, keyword, severity]):
        return "Error: provide at least one filter"

    matches: list[str] = []
    skipped = 0

    with open(path, "r", errors="replace") as f:
        for line in f:
            parsed = _parse_line(line)
            if not parsed:
                continue

            if module and module.lower() not in parsed["module"].lower():
                continue
            if subsystem and subsystem.lower() not in parsed["subsystem"].lower():
                continue
            if thread and parsed["thread"] != thread:
                continue
            if source_file and source_file.lower() not in parsed["source_file"].lower():
                continue
            if keyword and keyword.lower() not in line.lower():
                continue
            if severity and parsed["severity"] != severity.upper():
                continue

            if len(matches) < max_results:
                # Truncate very long lines (collision data) for readability
                display = line.rstrip()
                if len(display) > 300:
                    display = display[:300] + "... [truncated]"
                matches.append(display)
            else:
                skipped += 1

    if not matches:
        filters = {k: v for k, v in [
            ("module", module), ("subsystem", subsystem), ("thread", thread),
            ("source_file", source_file), ("keyword", keyword), ("severity", severity),
        ] if v}
        return f"No matches for: {filters}"

    header = f"{len(matches)} matches shown"
    if skipped:
        header += f" ({skipped:,} more not shown, increase max_results to see)"
    return header + ":\n" + "\n".join(matches)


@mcp.tool()
def read_log_range(
    txt_path: str,
    target_timestamp: str,
    num_lines: int = 50,
) -> str:
    """Read N lines centered around a specific timestamp. The magnifying glass.

    Finds the first line at or after the target timestamp, then returns
    num_lines/2 before and after. Use this to read the actual log content
    around a point of interest found via summarize_log or filter_log.
    """
    path = Path(txt_path)
    if not path.exists():
        return f"Error: file not found: {path}"

    if num_lines > 500:
        return "Error: max 500 lines per read"

    reference_date = _get_reference_date(path)
    try:
        target = _parse_timestamp(target_timestamp, reference_date)
    except ValueError as e:
        return f"Error: {e}"

    half = num_lines // 2

    # First pass: find line number closest to target
    target_line_num = 0
    with open(path, "r", errors="replace") as f:
        for i, line in enumerate(f):
            m = TIMESTAMP_RE.match(line)
            if not m:
                continue
            try:
                ts = datetime.strptime(m.group(1), TIMESTAMP_FMT)
            except ValueError:
                continue
            target_line_num = i
            if ts >= target:
                break

    # Second pass: read the range
    start_line = max(0, target_line_num - half)
    end_line = target_line_num + half
    output: list[str] = []

    with open(path, "r", errors="replace") as f:
        for i, line in enumerate(f):
            if i < start_line:
                continue
            if i > end_line:
                break
            display = line.rstrip()
            if len(display) > 300:
                display = display[:300] + "... [truncated]"
            output.append(display)

    if not output:
        return f"No lines found near {target_timestamp}"

    return f"Lines {start_line}–{end_line} around {target_timestamp}:\n" + "\n".join(output)


@mcp.tool()
def list_log_timestamps(txt_path: str) -> str:
    """Show the time range and estimated size of a parsed .txt log file.

    Reads only the start and end of the file — does not scan every line.
    """
    path = Path(txt_path)
    if not path.exists():
        return f"Error: file not found: {path}"

    first_ts: datetime | None = None
    last_ts: datetime | None = None

    with open(path, "r", errors="replace") as f:
        for line in f:
            m = TIMESTAMP_RE.match(line)
            if m:
                try:
                    first_ts = datetime.strptime(m.group(1), TIMESTAMP_FMT)
                except ValueError:
                    continue
                break

    file_size = path.stat().st_size
    with open(path, "rb") as f:
        seek_pos = max(0, file_size - 65536)
        f.seek(seek_pos)
        tail = f.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            m = TIMESTAMP_RE.match(line)
            if m:
                try:
                    last_ts = datetime.strptime(m.group(1), TIMESTAMP_FMT)
                except ValueError:
                    continue
                break

    if first_ts is None or last_ts is None:
        return "No timestamped lines found in file"

    estimated_lines = file_size // 150
    duration = last_ts - first_ts
    return (
        f"File: {path}\n"
        f"Size: {file_size / (1024*1024):.1f} MB\n"
        f"Estimated lines: ~{estimated_lines:,}\n"
        f"First: {first_ts.strftime(TIMESTAMP_FMT)}\n"
        f"Last:  {last_ts.strftime(TIMESTAMP_FMT)}\n"
        f"Duration: {duration}"
    )