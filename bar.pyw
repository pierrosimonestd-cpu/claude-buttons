"""Starts the bar without a console window (run with pythonw.exe)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from claude_buttons.bar import main  # noqa: E402

main()
