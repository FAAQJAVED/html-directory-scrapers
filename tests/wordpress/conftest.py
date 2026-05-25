# tests/wordpress/conftest.py
# =============================================================================
# sys.path isolation for the WordPress engine test suite.
#
# Problem: pytest collects both test subdirectories in the same process.
# When tests/html/ runs first, bare module names (fetcher, parser, exporter,
# etc.) are cached in sys.modules pointing at engines/html/ files.
# When tests/wordpress/ runs next, those stale HTML modules are returned
# instead of the WordPress equivalents, causing:
#   - AttributeError: module 'fetcher' has no attribute 'safe_decode'
#   - AttributeError: module 'parser' has no attribute 'filter_by_bounds'
#   - TypeError: string indices must be integers (wrong parse_cards signature)
#   - TypeError: export_excel() got unexpected keyword argument 'header_color'
#
# Fix: this conftest.py runs as module-level code before
# tests/wordpress/test_wordpress_engine.py is imported. It:
#   1. Removes engines/html/ from sys.path if present
#   2. Ensures engines/wordpress/ is at the front of sys.path
#   3. Evicts all cached engine module names from sys.modules so the
#      next import re-loads them from the WordPress engine directory.
# =============================================================================

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
_HTML_ENGINE = str(_ROOT / "engines" / "html")
_WP_ENGINE = str(_ROOT / "engines" / "wordpress")

# ── Remove the competing engine from sys.path ─────────────────────────────────
if _HTML_ENGINE in sys.path:
    sys.path.remove(_HTML_ENGINE)

# ── Ensure this engine is at the front ───────────────────────────────────────
if _WP_ENGINE in sys.path:
    sys.path.remove(_WP_ENGINE)
sys.path.insert(0, _WP_ENGINE)

# ── Evict all cached engine modules so fresh imports load WP files ───────────
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

import pytest


@pytest.fixture(autouse=True)
def _evict_scraper_before_each():
    """
    Re-evict engine modules and enforce WP at sys.path[0] before every test.

    When both test suites run together, test_html_engine.py inserts HTML at
    sys.path[0] during collection.  Without re-ordering, `import scraper`
    inside a WP test resolves to the HTML scraper, causing arity errors.
    """
    # Re-enforce correct path order: WP first, HTML removed
    if _HTML_ENGINE in sys.path:
        sys.path.remove(_HTML_ENGINE)
    if _WP_ENGINE in sys.path:
        sys.path.remove(_WP_ENGINE)
    sys.path.insert(0, _WP_ENGINE)

    # Evict stale cached module objects
    for mod in _ENGINE_MODULES:
        sys.modules.pop(mod, None)
    yield
