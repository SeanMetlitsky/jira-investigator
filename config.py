import os
from pathlib import Path

# Paths
HOST_HOME = Path.home()
REPO_ROOT = Path("/home/sean.metlitsky/projects/VC")
WORK_DIR = Path("/tmp/postmortem-investigator")
TICKETS_DIR = Path(__file__).parent / "tickets"

# Docker-based log parser
DOCKER_IMAGE = os.environ.get("VC_DOCKER_IMAGE", "")  # e.g. "vc-dev:v8", empty = auto-detect latest
LOG_PARSER_BIN_DOCKER = "/home/host/projects/Launcher_/cmake-build-debug/ServiceHub/logger/logger_parser/logger_parser"

# Jira (loaded from environment variables)

JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")

# Ripgrep binary
RG_BIN = "/usr/local/lib/node_modules/@anthropic-ai/claude-code/vendor/ripgrep/x64-linux/rg"

# Tool limits
MAX_SEARCH_RESULTS = 50
MAX_CODE_LINES = 200