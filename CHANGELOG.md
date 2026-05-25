# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [1.2.1] — 2025-05-25

### Fixed

#### Both Engines
- **Keyboard controls now work reliably** — P, R, S/Q, W keys previously had
  no effect unless pressed during a narrow window between page-loop iterations.
  Root cause: the listener thread stored keys in `_key` but the main loop
  only called `get_key()` once per page (every 10–30 s), so most keypresses
  were missed. Fix: P and S/Q now set `threading.Event` flags (`_pause_event`,
  `_stop_event`) **directly inside the listener thread**, so they take effect
  immediately — even when the main loop is blocked in `time.sleep()` or a
  `tqdm` iteration. R clears `_pause_event` immediately. W remains stored
  in `_key` and is consumed by the main loop to print stats.
  `InputController` gains `is_paused()` and `is_stopped()` helper methods;
  both scrapers updated to poll these instead of a local `paused` variable.
- **Dead-website retry: 0 → 1** — profile/crawl fetches now retry once on
  `ReadTimeout` and general connection errors. `ConnectTimeout` (TCP-level
  failure — host is unreachable) still returns `("", 0)` immediately with
  zero retries, so truly dead sites never stall the run.

### Added

#### Documentation
- **`docs/` folder** added to the repository root for visual assets.
  Contains `docs/README.md` with step-by-step instructions for recording
  `terminal-demo.gif` (asciinema) and capturing `excel-preview.png`.
  `docs/.gitkeep` ensures Git tracks the empty folder.
- **README Output Preview** — placeholder text replaced with an HTML comment
  block that is ready to uncomment once assets are placed in `docs/`.
- **README Project Structure** — updated to show the `docs/` folder.

## [1.2.0] — 2025-05-23

### Fixed

#### Both Engines
- **pyproject.toml build backend corrected** — changed from non-existent
  `setuptools.backends.legacy:build` to the correct `setuptools.build_meta`.
  The toolkit is now installable via `pip install .` and `pip install -e .`.
- **`|| true` removed from mypy CI step** — type checking is now fully enforcing
  in GitHub Actions. `types-PyYAML` and `types-requests` stubs added so mypy
  resolves third-party annotations cleanly.
- **`.gitignore` updated** — `.mypy_cache/`, `.coverage`, `.coverage.*`,
  `.pytest_cache/`, and `htmlcov/` are now excluded from version control.

#### WordPress Engine
- **UK postcode regex removed from `parser.parse_cards()`** — the hardcoded
  UK pattern `\b[A-Z]{1,2}[0-9][0-9A-Z]?\s*[0-9][A-Z]{2}\b` is replaced by
  an optional `postcode_regex` config key. The scraper now produces an empty
  Postcode column for non-UK directories rather than silently failing to match.
- **NYC geo example removed from `config.yaml.example`** — `geo_bounds` now
  uses `YOUR_LAT_MIN` / `YOUR_LAT_MAX` / `YOUR_LNG_MIN` / `YOUR_LNG_MAX`
  placeholders so the config template is genuinely neutral.

### Added

#### Both Engines
- **CLI entry points** — `html-scraper` and `wp-scraper` commands available
  after `pip install -e .` from the repo root.
- **`engines/__init__.py`, `engines/html/__init__.py`,
  `engines/wordpress/__init__.py`** — engines are now proper Python packages.
- **`postcode_regex` config key (WordPress engine)** — optional regex added to
  `config.yaml.example`; `parse_cards()` signature updated to accept `cfg`
  as an optional third argument; `pc_match.group(1)` corrected to
  `pc_match.group(0)` so config-supplied patterns without capture groups
  work correctly (fixes `IndexError: no such group` at runtime).
- **macOS runner added to CI** — `smoke-platforms` job matrix now covers
  `windows-latest` and `macos-latest`.
- **Coverage threshold raised to 80%** — both test jobs enforce
  `--cov-fail-under=80`.

#### Documentation
- **README expanded to 15 sections** — added Prerequisites, Performance &
  Benchmarks, Configuration Reference, Known Limitations, FAQ, and Contributing.
- **Output Preview** — placeholder text replaced with representative terminal
  output block and Excel sheet description table.
- **Configuration Reference tables** — full key/type/default/description tables
  for both engines.
- **Contributing section** — links to `CONTRIBUTING.md`.

#### Tests
- **`TestParseCardsPostcodeRegex`** (3 tests) — covers regex extraction, absent
  regex, and `cfg=None` in the WordPress engine.
- **`TestCheckStopTime` fixed** in both test suites — replaced bare string
  comparison with time-invariant assertions: `check_stop_time("00:00")` is
  always `True` (current time is always past midnight); `check_stop_time("")`
  is always `False` (empty string disables the feature). Avoids
  `mock.patch("controls.datetime")` which fails when controls uses
  `from datetime import datetime` (no module-level `datetime` attribute).
- **`TestFetcherRateEta`** (2 tests) — covers insufficient-data and
  sufficient-data branches of `rate_eta()`.
- **`TestExtractEmailPriority`** (3 tests) — verifies CF href takes precedence
  over `data-cfemail` over plain `mailto:`.
- **`TestSafeDecodeEdgeCases`** (4 tests) — empty bytes, partial gzip header,
  partial zlib header, plain UTF-8 pass-through.
- **`TestDecodeEntitiesComprehensive`** (4 tests) — numeric entity, named
  entity, no-entity pass-through, multiple entities.
- **`TestProgressBar`** (3 tests) — bracket delimiters, 100% display,
  zero-total guard.
- Total test count raised to **120+** across both engines.

## [1.1.0] — 2025-05-17

### Added

#### Both Engines
- **Concurrent profile fetching** — `ThreadPoolExecutor(max_workers=3)` fetches all profile pages on a listing/AJAX page in parallel. Each worker uses its own HTTP client/session to avoid shared-state issues. `--workers N` CLI flag (default: 3, cap: 8) overrides the worker count at runtime. Expected throughput improvement: ~3× (from ~12/min to ~35/min on a typical run).
- **tqdm live progress bar** — inner profile-fetch loop now shows a live tqdm bar (`p{page}/{total} [████░░] N rec/s`) that updates in-place without breaking log output. Falls back gracefully if tqdm is not installed.
- **W key for live stats** — pressing `W` during a run prints a full stats snapshot (saved, flagged, email count, phone count, website count, current sector, page, elapsed time) to the log without interrupting the scrape.
- **tqdm-safe logging** — when tqdm is active, console log output is routed through `tqdm.write()` so the progress bar is never broken by log lines.
- `tqdm>=4.66.0` added to both `requirements.txt` files.
- `--workers N` CLI flag added to both `scraper.py` files.

#### HTML Engine (`engines/html/`)
- **Keyboard controls fully wired up** — `P` (pause), `R` (resume), `S` (stop), `W` (stats) are now read via `controller.get_key()` in both the outer page loop and the inner result-processing loop. Previously the `InputController` thread ran but its output was never consumed by the HTML scraper.
- **Category from badge images** — `scraper.py` now reads `card["services"][0]` (populated by `parse_cards()` from badge image keyword matching) as the record category instead of always falling back to the first category in config. Fixes the bug where all records showed "Residential Sales" regardless of the member's actual service types.
- **Location from listing-card meta text** — `parse_cards()` now reads a `selectors.card_meta` CSS selector (e.g. `span.meta`) from the config and populates `card["location"]` from it. `scraper.py` writes this to the `Location` field when the profile-page regex returns empty. Fixes the bug where the Location column was always blank for sites like Propertymark.
- **`--fresh` CLI flag** added to HTML `scraper.py` (previously only in WordPress engine).

#### WordPress Engine (`engines/wordpress/`)
- **Keyboard controls fully wired up** — `P`, `R`, `S`/`Q`, `W` keys read in both outer AJAX page loop and inner result-processing loop.

### Fixed

#### Both Engines
- **Website crawl timeout reduced from 25 s to 6 s** — third-party company websites now use a 6-second timeout. This reduces per-dead-site stall time from up to 7.5 minutes to under 6 seconds.
- **`ConnectTimeout` no longer retried** — a TCP-level connection timeout means the host is unreachable; retrying wastes time. HTML engine: `httpx.ConnectTimeout` in `fetcher.safe_get(is_profile=True)` now returns `("", 0)` immediately. WordPress engine: `requests.exceptions.ConnectTimeout` in `fetcher.http_get(is_crawl=True)` now returns `("", 0)` immediately with no sleep. This eliminates the primary cause of the scraper appearing "stuck".

#### HTML Engine (`engines/html/`)
- **`from bs4 import BeautifulSoup` moved to top-level import** — was incorrectly placed inside the main `for page` loop in the previous version.
- **Unused `key` variable removed** — dead code in the record-save block.
- **`sound_sequence` / `beep_raw` ordering fixed** — `beep_raw` is now defined before `sound_sequence` which calls it.

#### WordPress Engine (`engines/wordpress/`)
- **`crawl_for_email()` circular import eliminated** — replaced deferred `from parser import ...` inside `crawl_for_email` with self-contained inline helpers `_is_valid` and `_find_emails`.
- **`decode_entities` made public** — renamed from `_decode_entities` to `decode_entities` in `parser.py`; `scraper.py` call updated accordingly.
- **`seen: set = set()` annotation cleaned** — inconsistent whitespace in inline type annotation corrected.

### Changed

#### Both Engines
- Banner now shows `Workers: N concurrent profile threads` and `Keys: P=pause R=resume S=stop W=stats`.
- Per-record log line now shows `loc:` field with up to 12 characters.
- `make_session()` log level for cookie/proxy messages changed from `INFO` to `DEBUG` to reduce noise in concurrent runs.

---

## [1.0.0] — 2025-01-01

### Added

#### HTML Engine (`engines/html/`)
- Modular 7-file architecture (scraper, config, fetcher, parser, exporter, checkpoint, controls)
- Config-driven CSS selectors — no code changes needed to retarget any paginated HTML directory
- Two-phase crawl: listing pages → individual profile pages
- Cloudflare XOR email decoding (handles `/cdn-cgi/l/email-protection` and `data-cfemail` patterns)
- Generic phone normalisation (7–15 digit, international E.164-compatible)
- Geographic regex filter on extracted location text
- `command.txt` runtime controls: pause / resume / stop / fresh / status (no interactive terminal needed)
- Exponential backoff + circuit breaker (3 consecutive failures → auto-pause)
- Optional SMTP email verification via SMTP RCPT handshake (`dnspython`)
- Daily auto-stop time and low-disk space guard
- 3-sheet Excel output: Data, Flagged (geo-filtered + failed fetches), Summary
- `CheckpointManager` class with atomic `.tmp` → rename write
- `InputController` cross-platform keyboard listener (Windows msvcrt + Unix select/tty)
- `--config` CLI flag

#### WordPress Engine (`engines/wordpress/`)
- Modular 7-file architecture (scraper, config, fetcher, parser, exporter, checkpoint, controls)
- Config-driven sectors and AJAX parameters — retarget any WordPress directory via config only
- WordPress nonce auto-extraction (3 regex patterns)
- Mid-run nonce refresh on empty/failed AJAX response
- `admin-ajax.php` POST pagination
- Manual gzip/zlib decompression of AJAX responses
- Geographic bounding-box filter using lat/lng from AJAX markers
- Email enrichment via company website crawl
- Deduplication by (name, postcode)
- Exponential-backoff retry on all GET and POST requests
- `P/R/S/Q` keyboard controls (Windows msvcrt + Unix select/tty)
- Configurable Excel header colour
- 3-sheet Excel output: Data, Flagged, Summary
- `CheckpointManager` with atomic `.tmp` → rename write
- `--fresh` and `--config` CLI flags
