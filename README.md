# HTML Directory Scrapers

[![CI](https://github.com/FAAQJAVED/html-directory-scrapers/actions/workflows/ci.yml/badge.svg)](https://github.com/FAAQJAVED/html-directory-scrapers/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

> Two-engine Python toolkit for scraping paginated HTML and WordPress AJAX business directories — config-driven, modular, checkpoint-resumable, Excel output.

---

## Overview

Different business directories run on different technology stacks. This toolkit covers the two most common HTML-based patterns: directories that render their listings directly in HTML page source, and directories built on WordPress that serve results through the `admin-ajax.php` JSON endpoint. Each engine is fully standalone — a single config file change is all that is needed to retarget it at a completely different directory. Both engines share the same gold-standard architecture: seven single-responsibility modules, a `CheckpointManager` for atomic pause-and-resume, a three-sheet Excel output format, and exponential-backoff retry on every HTTP call.

---

## Engines

| Engine | Target platform | Entry point | Unique technique |
|---|---|---|---|
| [HTML Scraper](engines/html/) | Any paginated HTML directory | `engines/html/scraper.py` | CSS selectors, listing→profile crawl, Cloudflare XOR email decode |
| [WordPress Scraper](engines/wordpress/) | WordPress admin-ajax.php directories | `engines/wordpress/scraper.py` | Nonce extraction + mid-run refresh, AJAX POST, gzip decompression |

---

## Quick Start

**HTML Engine:**
```bash
git clone https://github.com/FAAQJAVED/html-directory-scrapers.git
cd html-directory-scrapers/engines/html
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp config.yaml.example config.yaml
# Edit config.yaml — fill in base_url, list_path, and selectors
python scraper.py

# Optional: install as a command so you can run it from anywhere
pip install -e ../../
# Then: html-scraper --config config.yaml
```

**WordPress Engine:**
```bash
git clone https://github.com/FAAQJAVED/html-directory-scrapers.git
cd html-directory-scrapers/engines/wordpress
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp config.yaml.example config.yaml
# Edit config.yaml — fill in base_url, ajax_action, and sectors
python scraper.py

# Optional: install as a command so you can run it from anywhere
pip install -e ../../
# Then: wp-scraper --config config.yaml
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | ≥ 3.9 | Tested on 3.9, 3.10, 3.11, 3.12 |
| pip | any current | Bundled with Python |
| Git | any | For cloning |
| OS | Windows / Linux / macOS | Windows and macOS have CI smoke tests; Linux is the primary CI platform |

No browser driver (Playwright / Selenium) is needed. Both engines use pure HTTP requests only.

---

## Shared Features

- **Config-driven** — zero code edits required to retarget either engine at a new directory; all selectors, URLs, and parameters live in `config.yaml`
- **Checkpoint / resume** — both engines save state after every page; restart at any time and the run continues exactly where it left off
- **Concurrent profile fetching** — `--workers N` flag (default: 3) fetches multiple profile pages in parallel; ~3× throughput vs sequential
- **Geographic filtering** — the HTML engine uses a regex on location text; the WordPress engine uses the lat/lng bounding box from AJAX map markers
- **Exponential-backoff retry** — all HTTP calls retry with increasing delays on failure; `ConnectTimeout` on dead sites returns immediately
- **3-sheet Excel output** — every run produces a Data sheet (clean records), a Flagged sheet (excluded records with reason), and a Summary sheet (run metadata)
- **Keyboard controls** — `P` pause / `R` resume / `S` stop / `W` live stats — no Enter key needed
- **Polite rate limiting** — configurable `delay_min` / `delay_max` per request
- **Structured logging** — console and file logging with timestamps and level labels

---

## Output Preview

**Terminal during a run:**

```
============================================================
  Member Directory Scraper
  Target   : https://YOUR_TARGET_SITE.example.com
  Services : service-slug-one, service-slug-two
  Workers  : 3 concurrent profile threads
  Keys     : P=pause  R=resume  S=stop  W=stats
============================================================

14:02:31 | INFO     | Starting fresh run
14:02:31 | INFO     | Output      : DirectoryExport_20250517.xlsx
14:02:32 | INFO     | === Service 1/2 : service-slug-one ===
14:02:33 | INFO     |   [service-slug-one] 12 pages detected (~120 results)
14:02:34 | INFO     |   [service-slug-one] p1/12 | 10 new | saved: 0
  p1/12: 100%|████████████████| 10 rec [00:08, 1.2 rec/s]
14:02:42 | INFO     |      1. Acme Consulting Ltd          email:info@acmeconsult.com     loc:AB12 3CD    web:Y
14:02:42 | INFO     |      2. Beta Solutions               email:--                       loc:EF45 6GH    web:Y
...
14:18:05 | INFO     |   Total saved     : 118
14:18:05 | INFO     |   Flagged         : 4
14:18:05 | INFO     |   With email      : 97
14:18:05 | INFO     |   Time elapsed    : 15m34s
```

**Excel output — 3-sheet workbook:**

| Sheet | Contents |
|---|---|
| **Data** | All clean records — Company, Email, Phone, Website, Location, Category, Source. Frozen header row, alternating row shading, auto-sized columns. |
| **Flagged** | Records excluded by geographic filter or failed profile fetches, with a Flag Reason column. |
| **Summary** | Run metadata — source, status, record counts, email/phone/website hit rates, start time, elapsed time. |

> **Add your own visuals:** place assets in the `docs/` folder and uncomment the lines below.
> See [`docs/README.md`](docs/README.md) for exact filenames and how to create them.

<!-- Uncomment after adding assets to docs/ :
![Terminal demo](docs/terminal-demo.gif)
![Excel output](docs/excel-preview.png)
-->

---

## Performance & Benchmarks

Numbers measured on a standard broadband connection against a live directory with ~500 records.

| Mode | Throughput | Notes |
|---|---|---|
| v1.0.0 sequential | ~12 records/min | One profile fetch at a time |
| v1.1.0 concurrent (3 workers) | ~35 records/min | ~3× improvement |
| v1.1.0 concurrent (5 workers) | ~50 records/min | Diminishing returns above 5 |

**Tuning guidance:**
- Set `--workers 3` (default) for polite scraping on shared-hosting directories.
- Set `--workers 5` for faster runs on directories with robust rate limiting.
- Never exceed `--workers 8` (enforced by a hard cap in `scraper.py`).
- Increase `delay_min` / `delay_max` if you receive HTTP 429 responses.
- The `profile_timeout_seconds: 6` default eliminates stalls on dead company websites. Reduce to `4` for very high-volume runs; increase to `10` only if many legitimate sites load slowly.

**Expected runtime formula:**
```
minutes ≈ total_records / (workers × 12) × delay_avg
```

---

## Choosing an Engine

Use the **HTML engine** if the directory renders its listings directly in the HTML page source — you can see company names and links when you use View Source in your browser.

Use the **WordPress engine** if you see POST requests to `/wp-admin/admin-ajax.php` in browser DevTools' Network tab when you trigger a search on the directory page. The response will be a JSON object containing marker and card data rather than a full HTML page.

---

## Project Structure

```
html-directory-scrapers/
├── README.md
├── LICENSE
├── CHANGELOG.md
├── CONTRIBUTING.md
├── pyproject.toml
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml
├── engines/
│   ├── __init__.py
│   ├── html/                    ← HTML Directory Scraper
│   │   ├── __init__.py
│   │   ├── scraper.py           # Thin orchestrator (CLI entry point)
│   │   ├── config.py            # YAML loader + env var injection
│   │   ├── fetcher.py           # httpx client, safe_get, progress, SMTP
│   │   ├── parser.py            # parse_cards, scrape_profile, CF email
│   │   ├── exporter.py          # 3-sheet Excel workbook
│   │   ├── checkpoint.py        # CheckpointManager (atomic JSON)
│   │   ├── controls.py          # command.txt watcher, InputController
│   │   ├── config.yaml.example
│   │   ├── .env.example
│   │   └── requirements.txt
│   └── wordpress/               ← WordPress Directory Scraper
│       ├── __init__.py
│       ├── scraper.py           # Thin orchestrator (CLI entry point)
│       ├── config.py            # YAML loader + env var injection
│       ├── fetcher.py           # Session, AJAX POST, nonce, safe_decode
│       ├── parser.py            # parse_cards, filter_by_bounds, profile
│       ├── exporter.py          # 3-sheet Excel workbook
│       ├── checkpoint.py        # CheckpointManager (atomic JSON)
│       ├── controls.py          # InputController (P/R/S/Q keyboard)
│       ├── config.yaml.example
│       ├── .env.example
│       └── requirements.txt
├── docs/                            ← Visual assets (GIF, screenshots)
│   ├── .gitkeep
│   ├── README.md                    # Instructions for adding assets
│   ├── terminal-demo.gif            # (add after first live run)
│   └── excel-preview.png            # (add after first live run)
└── tests/
    ├── conftest.py
    ├── html/
    │   ├── conftest.py
    │   └── test_html_engine.py
    └── wordpress/
        ├── conftest.py
        └── test_wordpress_engine.py
```

---

## Configuration Reference

### HTML Engine — key options

| Key | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | — | Root URL of the target directory (required) |
| `list_path` | string | — | Path to the listing/search-results page (required) |
| `categories` | list | — | Category name objects `{name: ...}` (required) |
| `selectors` | dict | — | CSS selectors for card and profile elements (required) |
| `all_services` | list | `[]` | Service slugs to iterate; empty = single pass with no filter |
| `location_filter_regex` | string | `""` | Regex applied to profile page text; empty = no filter |
| `delay_min` / `delay_max` | float | 1.0 / 2.5 | Per-request random delay range in seconds |
| `profile_timeout_seconds` | int | 6 | Timeout for company profile page fetches |
| `verify_email` | bool | false | SMTP RCPT handshake per extracted email (slow) |
| `stop_at` | string | `""` | 24-hour time to auto-stop, e.g. `"23:00"` |
| `postcode_regex` | string | `""` | Regex to extract postcode/ZIP from card meta text |

### WordPress Engine — key options

| Key | Type | Default | Description |
|---|---|---|---|
| `base_url` | string | — | WordPress directory root URL (required) |
| `register_path` | string | — | Path to the search page for nonce extraction (required) |
| `ajax_action` | string | — | WordPress AJAX action name (required) |
| `sectors` | list | — | `[{name: ..., category: ...}]` objects (required) |
| `geo_bounds` | dict | none | `lat_min/max`, `lng_min/max` bounding box; omit to disable |
| `crawl_websites` | bool | true | Visit company websites to find emails |
| `postcode_regex` | string | `""` | Regex to extract postcode/ZIP from AJAX card addresses |
| `skip_domains` | list | `[]` | Website domains to exclude from company URL extraction |
| `junk_domains` | list | `[...]` | Email domains to reject |

Full option documentation is in `config.yaml.example` inside each engine folder.

---

## Known Limitations

- **JavaScript-rendered directories are not supported.** Both engines use direct HTTP requests. If the directory's listings only appear after JavaScript executes (single-page app, lazy-load), neither engine will find any cards. Use browser DevTools → Network tab → look for XHR/Fetch requests to confirm the content is accessible via HTTP.

- **WordPress nonce expiry on very long runs.** WordPress nonces typically expire after 24 hours. The WordPress engine detects expiry (empty AJAX response) and automatically refreshes the nonce mid-run. If a run exceeds 24 hours, a second expiry may occur after refresh — restart with `--fresh` if the scraper stops finding records after a very long session.

- **Rate limiting is per-worker, not per-run.** With `--workers 3` and `delay_min: 1.0`, the effective request rate is up to 3 concurrent requests per second during profile fetches. If the target directory has aggressive rate limiting, reduce workers to 1 and increase delays.

- **SMTP email verification is slow.** `verify_email: true` adds a DNS + SMTP handshake per extracted email. On a 500-record run this can add 10–20 minutes. Use only when email accuracy is critical.

- **The HTML engine cannot parse AJAX-loaded profile pages.** If a profile page loads contact details via a second JavaScript call, the profile page HTML will not contain the email or phone number. The WordPress engine is purpose-built for this pattern.

---

## FAQ

**Q: Can I scrape a directory that requires a login?**
A: Yes. Log in via your browser, copy the session cookies from DevTools → Network → any request → Cookie header, and paste them into your `.env` file as `SCRAPER_COOKIES=...` (HTML engine) or `SCRAPER_COOKIES_RAW=...` (WordPress engine). Cookies typically expire after 24–72 hours.

**Q: The scraper saves no records. What do I check first?**
A: (1) Confirm the page source contains the listings — use View Source in your browser. If you only see JavaScript, the directory is JS-rendered and not compatible with these engines. (2) Open DevTools → Network → reload the page → check for XHR requests to `admin-ajax.php`. If present, you need the WordPress engine, not the HTML engine. (3) Check `selectors.card_container` — verify it matches at least one element on the listing page.

**Q: How do I retarget the scraper at a completely different directory?**
A: Edit only `config.yaml` — no Python changes needed. Update `base_url`, `list_path` (or `register_path`/`ajax_action` for WordPress), and the CSS selectors. The selectors are the only site-specific values in the entire codebase.

**Q: Can I run both engines at the same time?**
A: Yes. Each engine has its own working directory, checkpoint file, and output file. Run them in separate terminal windows or use a process manager. They do not share any state.

**Q: The scraper appears stuck — nothing is happening.**
A: Press `W` to print a live stats snapshot without interrupting the run. If the stats counter is incrementing, the scraper is working. If the counter has not moved for more than 60 seconds, press `S` to stop, then check `scraper.log` for timeout or error messages. The most common cause in v1.0.x was `ConnectTimeout` on dead company websites — this is fixed in v1.1.0+.

---

## Related Projects

This toolkit is part of the **FAAQJAVED B2B Lead Generation Suite**:

| Tool | Purpose |
|---|---|
| [Google Maps Business Scraper](https://github.com/FAAQJAVED/Google-Maps-Business-Scraper) | Scrapes Google Maps listings and enriches with website contact data |
| [Email & Phone Enrichment Tool](https://github.com/FAAQJAVED/Email-Phone-Number-Enrichment-Tool) | Two-pass contact enricher for CSV/URL lists |
| [LeadHunter Pro](https://github.com/FAAQJAVED/Leadhunter_Pro) | Multi-engine search scraper with lead scoring |
| [Trustpilot Business Scraper](https://github.com/FAAQJAVED/trustpilot-business-scraper) | Extracts business contact data from Trustpilot |
| [JSON Directory Harvester](https://github.com/FAAQJAVED/json-directory-harvester) | Harvests records from any JSON-based directory API |
| [HTML Directory Scrapers](https://github.com/FAAQJAVED/html-directory-scrapers) | Two-engine toolkit for HTML and WordPress directories *(this repo)* |

---

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. The PR checklist covers code style (black + isort), type checking (mypy), and test coverage (≥ 80%).

---

## License

MIT License — see [LICENSE](LICENSE) for details.
