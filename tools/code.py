"""Codebase search and reading tools using ripgrep."""

import subprocess
from pathlib import Path

from mcp_instance import mcp
from config import REPO_ROOT, RG_BIN, MAX_SEARCH_RESULTS, MAX_CODE_LINES


def _find_file(filename: str) -> Path | None:
    """Find a file by name anywhere under REPO_ROOT."""
    for match in REPO_ROOT.rglob(filename):
        if match.is_file():
            return match
    return None


@mcp.tool()
def search_code(query: str, glob_filter: str | None = None) -> str:
    """Search the C++ codebase for a string or regex pattern using ripgrep.

    Returns matching lines with file paths and line numbers (max 50 results).
    Use glob_filter to narrow by file type, e.g. "*.cpp", "*.h".
    """
    if not REPO_ROOT.exists():
        return f"Error: repo root not found: {REPO_ROOT}"

    cmd: list[str] = [RG_BIN, "--no-heading", "--line-number", "--color=never"]
    if glob_filter:
        cmd += ["--glob", glob_filter]
    cmd += ["--max-count", str(MAX_SEARCH_RESULTS), query, str(REPO_ROOT)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode == 1:
        return f"No matches found for: {query!r}"
    if result.returncode not in (0, 1):
        return f"Error: ripgrep failed (exit {result.returncode}): {result.stderr.strip()}"

    lines = result.stdout.strip().splitlines()
    if len(lines) > MAX_SEARCH_RESULTS:
        lines = lines[:MAX_SEARCH_RESULTS]

    return f"{len(lines)} matches:\n" + "\n".join(lines)


@mcp.tool()
def read_code_segment(file_path: str, start_line: int, end_line: int) -> str:
    """Read a specific line range from a file in the codebase.

    file_path can be a full path, relative to repo root, or just a filename
    (will be searched recursively). Returns the code with line numbers.
    Use search_code first to find relevant locations, then read the
    surrounding context with this tool.
    """
    path = Path(file_path)

    # Try in order: absolute, relative to repo root, recursive search
    if path.is_absolute() and path.exists():
        full_path = path
    elif (REPO_ROOT / file_path).exists():
        full_path = REPO_ROOT / file_path
    else:
        found = _find_file(path.name)
        if not found:
            return f"Error: file not found: {file_path} (searched in {REPO_ROOT})"
        full_path = found

    if end_line - start_line + 1 > MAX_CODE_LINES:
        return f"Error: requested {end_line - start_line + 1} lines, max is {MAX_CODE_LINES}"
    if start_line < 1:
        return "Error: start_line must be >= 1"

    output_lines: list[str] = []
    with open(full_path, "r", errors="replace") as f:
        for i, line in enumerate(f, start=1):
            if i > end_line:
                break
            if i >= start_line:
                output_lines.append(f"{i}: {line.rstrip()}")

    if not output_lines:
        return f"Error: file has fewer than {start_line} lines"

    return f"{full_path}:{start_line}-{end_line}\n" + "\n".join(output_lines)