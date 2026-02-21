# CLAUDE.md — Project Context for Claude Code

## What is this project?

An MCP server (Python, stdio transport) called **Agentic Post-Mortem Investigator**. It provides tools for an LLM to autonomously debug production incidents by cross-referencing Jira tickets, multi-GB robot logs, and a C++ codebase.

## Project structure

```
postmortem-investigator/
├── server.py          # Entry point — creates FastMCP, imports tools
├── config.py          # All paths, credentials, constants
├── tools/
│   ├── __init__.py
│   ├── logs.py        # parse_log, extract_log_window, list_log_timestamps
│   ├── code.py        # search_code, read_code_segment
│   └── jira.py        # get_jira_ticket, get_jira_attachments
├── requirements.txt
├── SPEC.md            # Full design spec — READ THIS FIRST
├── CLAUDE.md          # This file
└── README.md
```

## Key design decisions

- **Single MCP server**, split into tool modules by domain (logs, code, jira)
- **All file I/O must stream line-by-line** — logs can be multi-GB, never load full files into memory
- **Log tools use early exit** — logs are chronologically ordered, stop reading once past the window
- **Tool return values are short summary strings** — file contents stay on disk, only metadata goes to the LLM
- **Codebase search uses ripgrep (`rg`)** via subprocess — fast even on large repos
- **Config is centralized** in `config.py` — paths, auth, limits

## Log format

```
2026-02-11 09:24:14.417263 [RKS|Robot Right]:cMasterNavigator.cpp:1160:[TID: 13] Forcefully holding Right arm position...
```

Timestamp regex: `^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)`

Lines without a leading timestamp are continuation lines — they belong to the previous timestamped line.

## Build order

1. Project skeleton: `server.py`, `config.py`, `tools/__init__.py`
2. `tools/logs.py` — log parsing and windowing (code exists in SPEC.md)
3. `tools/code.py` — ripgrep search + file reading
4. `tools/jira.py` — REST API integration
5. System prompt for the reasoning loop
6. README with setup instructions

## Coding guidelines

- Python 3.12+, type hints everywhere
- Use `from mcp.server.fastmcp import FastMCP` — the modern MCP Python SDK
- Each tool is decorated with `@mcp.tool()` where `mcp` is the shared FastMCP instance from `server.py`
- Tool docstrings matter — they become the tool descriptions the LLM sees
- Keep tool return strings concise and informative — this is what the LLM reasons about
- Use `subprocess.run` for external commands (ripgrep, log parser), not `os.system`
- Use `pathlib.Path` over `os.path`
- Handle errors gracefully — return error strings, don't raise exceptions in tools
- For Jira: use `requests` library, auth via config

## How to run

```bash
pip install -r requirements.txt
python server.py
```

## Testing

You can test tools directly:
```bash
python -c "from tools.logs import parse_log; print(parse_log('/path/to/test.log'))"
```