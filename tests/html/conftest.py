# tests/html/conftest.py
# =============================================================================
# sys.path isolation for the HTML engine test suite.
#
# Problem: pytest collects both test subdirectories in the same process.
# When tests/html/ is collected first, bare module names (fetcher, parser,
# exporter, etc.) are imported from engines/html/ and cached in sys.modules.
# When tests/wordpress/ is collected next, those cached HTML modules are
# returned by bare `import` statements, causing AttributeError on WP-only
# functions (safe_decode, filter_by_bounds, etc.).
#
# Fix: each subdirectory conftest.py runs as module-level code before
# its test file is imported. This conftest:
#   1. Removes engines/wordpress/ from sys.path if present
#   2. Ensures engines/html/ is at the front of sys.path
#   3. Evicts any previously cached engine modules from sys.modules
#      so the next import picks up the correct engine's files.
# =============================================================================

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
_HTML_ENGINE = str(_ROOT / "engines" / "html")
_WP_ENGINE = str(_ROOT / "engines" / "wordpress")

# ── Remove the competing engine from sys.path ─────────────────────────────────
if _WP_ENGINE in sys.path:
    sys.path.remove(_WP_ENGINE)

# ── Ensure this engine is at the front ───────────────────────────────────────
if _HTML_ENGINE in sys.path:
    sys.path.remove(_HTML_ENGINE)
sys.path.insert(0, _HTML_ENGINE)

# ── Evict any cached engine modules so fresh imports load the right files ────
_ENGINE_MODULES = [
    "fetcher",
    "parser",
    "exporter",
    "checkpoint",
    "config",
    "controls",
    "scraper",
]
for _mod in _ENGINE_MODULES:
    sys.modules.pop(_mod, None)
