"""Starts the MCP server over stdio (registered with `claude mcp add`)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_buttons.server import main  # noqa: E402

main()
