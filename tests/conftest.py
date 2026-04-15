"""Pytest configuration — adds project root to sys.path so core/, tools/, prompts/ are importable."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
