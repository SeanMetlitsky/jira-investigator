import sys
from pathlib import Path

# Ensure the project root is on sys.path so tool imports work
# regardless of the working directory when spawned by an MCP client.
sys.path.insert(0, str(Path(__file__).parent))

from mcp_instance import mcp  # noqa: E402

import tools.logs  # noqa: E402, F401
import tools.code  # noqa: E402, F401
import tools.jira  # noqa: E402, F401

if __name__ == "__main__":
    mcp.run()