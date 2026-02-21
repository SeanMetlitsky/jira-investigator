# Agentic Post-Mortem Investigator — Specification

## Overview

An MCP server that gives an LLM the tools to autonomously investigate production incidents by bridging three data sources: **Jira tickets**, **binary robot logs**, and a **C++ codebase**.

The LLM follows an iterative reasoning loop: read ticket → extract relevant logs → search/read code → form hypothesis → gather more evidence → converge on root cause.

## Architecture

Single MCP server, modular codebase. Stdio transport.

```
postmortem-investigator/
├── server.py                  # Entry point — imports & registers all tools
├── config.py                  # Paths, credentials, constants
├── tools/
│   ├── __init__.py
│   ├── logs.py                # Log parsing & windowing tools
│   ├── code.py                # Codebase search & reading tools
│   └── jira.py                # Jira ticket & attachment tools
├── requirements.txt
└── README.md
```

---

## Phase 1: Log Tools (`tools/logs.py`)

### Status: Code exists, needs migration into module structure.

### Tool: `parse_log`
- **Input:** Path to a `.log` binary log file
- **Action:** Runs the proprietary C++ parser to convert `.log` → `.txt`. The parser creates a `.txt` with the same name in the same directory. After parsing, streams through the output to join continuation lines (lines without a leading timestamp) onto the previous timestamped line.
- **Output:** String with path to created `.txt` and line count
- **Key constraint:** Streams line-by-line. Logs can be multi-GB.

### Tool: `extract_log_window`
- **Input:** Path to a parsed `.txt` file, a target timestamp, optional window size (default ±1 min)
- **Action:** Streams through the `.txt`, writes all lines within the time window to a new `.txt` file. Exits early once past the window end (logs are chronologically ordered).
- **Output:** String with output file path, line count, and time range
- **Timestamp formats accepted:**
  - Full: `"2026-02-11 09:24:14.417263"`
  - No microseconds: `"2026-02-11 09:24:14"`
  - Time-only: `"09:24:14"` (infers date from first log line)
- **Key constraint:** Streams line-by-line, early exit. Output can be thousands of lines (saved to disk, not returned to LLM).

### Tool: `list_log_timestamps`
- **Input:** Path to a `.txt` log file
- **Action:** Streams through file, tracks first/last timestamps
- **Output:** String with time range, line counts, duration

### Log line format
```
2026-02-11 09:24:14.417263 [RKS|Robot Right]:cMasterNavigator.cpp:1160:[TID: 13] Forcefully holding Right arm position, position is: (-120.00014, -47.99815, -73.02168, -0.00275, -80.00931, 55.82766, 75.27956)
```

### Timestamp regex
```python
TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)")
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S.%f"
```

---

## Phase 2: Codebase Tools (`tools/code.py`)

### Tool: `search_code`
- **Input:** Search query string, optional file glob filter (e.g. `"*.cpp"`, `"*.h"`)
- **Action:** Runs `ripgrep` (`rg`) as a subprocess on the local C++ repo. Returns matching lines with file paths and line numbers.
- **Output:** String with matches (capped at a reasonable limit, e.g. 50 results). Each result: `file:line: content`
- **Config needed:** Repo root path in `config.py`
- **Future:** Add Git API (GitHub/GitLab/Bitbucket) as fallback for remote access

### Tool: `read_code_segment`
- **Input:** File path (relative to repo root), start line, end line
- **Action:** Reads the specified line range from the file. Should provide enough context (e.g. function boundaries).
- **Output:** String with the code segment, including line numbers
- **Key constraint:** Returns a bounded segment, not entire files. The LLM should be able to request specific ranges after finding them via `search_code`.

---

## Phase 3: Jira Tools (`tools/jira.py`)

### Tool: `get_jira_ticket`
- **Input:** Jira ticket ID (e.g. `"PROJ-1234"`)
- **Action:** Calls Jira REST API to fetch ticket summary, description, comments, timestamps, status, and reporter.
- **Output:** Formatted string with ticket details
- **Config needed:** Jira base URL, auth (API token or PAT) in `config.py`

### Tool: `get_jira_attachments`
- **Input:** Jira ticket ID, optional filename filter
- **Action:** Lists attachments on the ticket. Optionally downloads `.log` files to a local working directory.
- **Output:** List of attachment names, or path to downloaded file
- **Config needed:** Download directory in `config.py`

---

## Phase 4: System Prompt & Reasoning Loop

The reasoning loop is driven by the LLM via a system prompt. No tool needed. The prompt should instruct the LLM to:

1. Read the Jira ticket to understand the reported issue and get timestamps
2. Fetch/parse the log file associated with the ticket
3. Extract the relevant time window from the logs
4. Analyze the windowed logs for errors, warnings, state transitions
5. Search the codebase for relevant error strings, function names, or variables found in logs
6. Read the relevant code segments to understand the logic
7. Form a hypothesis about root cause
8. If inconclusive, widen the window or search for more code — iterate
9. Present findings: root cause, evidence chain, suggested fix

---

## Config (`config.py`)

```python
from pathlib import Path

# Paths
REPO_ROOT = Path("/path/to/your/cpp/repo")
LOG_PARSER_BIN = Path("/path/to/your/proprietary/parser")
WORK_DIR = Path("/tmp/postmortem-investigator")

# Jira
JIRA_BASE_URL = "https://yourcompany.atlassian.net"
JIRA_EMAIL = "you@company.com"
JIRA_API_TOKEN = "your-api-token"  # Or use env vars

# Tool limits
MAX_SEARCH_RESULTS = 50
MAX_CODE_LINES = 200
```

---

## Dependencies

```
mcp
requests        # For Jira API
```

`ripgrep` (`rg`) must be installed on the system for code search.