"""Shared pytest fixtures.

Adds src/ to sys.path the same way src/ui/app.py does at runtime, so tests
can `import` this project's modules directly (e.g. `from profile.ingest
import ...`) without an editable install.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
