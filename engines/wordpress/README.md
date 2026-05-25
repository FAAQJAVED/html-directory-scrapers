# WordPress Directory Scraper

> Scrapes any WordPress-based business directory using the admin-ajax.php endpoint — nonce extraction, geo-filtered, checkpoint-resumable, email enrichment via website crawl.

---

## Overview

The WordPress Directory Scraper is a config-driven Python tool for extracting contact records from business directories built on WordPress. Instead of crawling HTML pages, it communicates directly with the site's internal `admin-ajax.php` JSON endpoint — the same endpoint the site's own search form uses. This makes it more efficient and more resilient than HTML parsing alone. It iterates over configurable sectors (categories), paginates through AJAX responses, applies an optional geographic bounding-box filter using the lat/lng coordinates embedded in every response, and optionally crawls each company's own website to find email addresses not listed on the directory profile. Output is a three-sheet Excel workbook containing clean records, flagged records, and a run summary.

---

## Features

- **Config-driven sectors and AJAX parameters** — every sector name, AJAX action, and filter value is in `config.yaml`; retarget any WordPress directory by editing the config alone, with zero code changes
- **WordPress nonce auto-extraction** — the scraper fetches the register page before the main loop and extracts the nonce security token using three regex patterns, covering all common WordPress embedding styles
- **Mid-run nonce refresh** — if the nonce expires during a long run (common after 30–60 minutes), the scraper detects the empty response, re-fetches the register page, extracts a fresh nonce, and retries transparently
- **`admin-ajax.php` POST pagination** — pages through results by POSTing to the same AJAX endpoint the site's own search form uses, with a `paged` parameter incrementing each iteration
- **Manual gzip/zlib decompression** — AJAX endpoints often return compressed content without the correct `Content-Encoding` header; the scraper sniffs magic bytes and decompresses manually so the caller always receives clean UTF-8 text
- **Geographic bounding-box filter** — uses the lat/lng map marker coordinates already embedded in every AJAX response to restrict results to a configurable region; no geocoding required
- **Email enrichment via website crawl** — when no email is found on a directory profile, optionally visits the company's own website and tries configurable contact page paths to find one
- **Deduplication by name + postcode** — prevents duplicate records when the same company appears in multiple sectors
- **Exponential-backoff retry** — all GET and POST requests retry with increasing delays on failure
- **`P/R/S/Q` keyboard controls** — pause, resume, or quit cleanly during a run without losing progress; works cross-platform (Windows `msvcrt` + Unix `select/tty`)
- **Configurable Excel header colour** — set `output.header_color` in config to distinguish this scraper's output at a glance
- **3-sheet Excel output** — Data, Flagged, and Summary sheets
- **`--fresh` and `--config` CLI flags** — force a clean start or point at a custom config file

---

## How the WordPress AJAX Pattern Works

WordPress directories expose a server-side search handler registered under a custom `action` name. When you trigger a search on the directory page, your browser POSTs a form to `/wp-admin/admin-ajax.php` with fields including `action`, `paged`, and a `nonce` CSRF token. The server processes this and returns a JSON response containing an array of map `markers` (each with lat/lng and company name) and a `cards` HTML string of result cards. This scraper replicates those POSTs exactly, page by page, for each configured sector.

The nonce is a short-lived security token the server generates fresh on each page load and embeds in the HTML source. Without it, AJAX requests are rejected. The scraper extracts it by fetching the register page and scanning for common embedding patterns before the main loop starts. If the token expires mid-run — typically after 30–60 minutes — the scraper detects the empty response, re-fetches the page, extracts a new nonce, and continues from exactly where it left off.

---

## Tech Stack

| Library | Purpose |
|---|---|
| `requests` | HTTP session management — GET requests and AJAX POSTs |
| `beautifulsoup4` | HTML parsing for profile pages and card grids |
| `lxml` | Fast HTML parser backend for BeautifulSoup |
| `openpyxl` | Excel workbook creation and styling |
| `pyyaml` | YAML configuration file parsing |
| `python-dotenv` | `.env` file loading for cookie and proxy injection |
| `gzip` / `zlib` | Standard library — manual AJAX response decompression |

---

## Project Structure

```
engines/wordpress/
├── scraper.py          # CLI entry point + main orchestration loop (thin)
├── config.py           # YAML loader, key validation, env var injection
├── fetcher.py          # Session, GET, AJAX POST, nonce, safe_decode, crawl
├── parser.py           # parse_cards, filter_by_bounds, scrape_profile, email
├── exporter.py         # 3-sheet Excel workbook (Data / Flagged / Summary)
├── checkpoint.py       # CheckpointManager — atomic JSON save/load/clear
├── controls.py         # InputController (P/R/S/Q keyboard), beep
├── config.yaml.example # Fully-commented config template (copy → config.yaml)
├── .env.example        # Cookie/proxy injection template (copy → .env)
└── requirements.txt    # Python dependencies
```

---

## Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/FAAQJAVED/html-directory-scrapers.git
   cd html-directory-scrapers/engines/wordpress
   ```

2. **Create and activate a virtual environment**
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # macOS/Linux:
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create your config file**
   ```bash
   cp config.yaml.example config.yaml
   ```
   Edit `config.yaml` and fill in every `YOUR_*` placeholder.  
   Key fields to find using browser DevTools → Network → AJAX POST:
   - `ajax_action` — the `action` field in the POST body
   - `sectors[*].name` — the sector name values sent in the POST body

5. **Add session cookies (if required)**
   ```bash
   cp .env.example .env
   ```
   Paste your browser cookie string as `SCRAPER_COOKIES_RAW=...` in `.env`.

---

## Configuration

Edit `config.yaml` to target any WordPress-based directory. The table below lists every configurable key:

| Key | What to configure |
|---|---|
| `base_url` | Root URL of the WordPress directory site (no trailing slash) |
| `register_path` | Path to the directory's main search/register page (for nonce extraction) |
| `ajax_path` | Path to admin-ajax.php (standard: `/wp-admin/admin-ajax.php`) |
| `ajax_action` | The `action` POST field value (find in DevTools → Network → Payload) |
| `ajax_location` | Location filter sent with every POST; `""` to disable |
| `ajax_status` | Membership/status filter sent with every POST; `""` to disable |
| `sectors` | List of `{name, category}` dicts — one AJAX query sequence per sector |
| `geo_bounds` | Bounding box `{lat_min, lat_max, lng_min, lng_max}`; remove block to disable |
| `crawl_websites` | `true` to visit company websites for email enrichment |
| `max_pages` | Maximum AJAX pages per sector (reduce for testing) |
| `delay_min` / `delay_max` | Random per-request delay range in seconds |
| `contact_paths` | URL paths tried when crawling company websites for email |
| `skip_domains` | Domains excluded from website URL extraction |
| `junk_domains` | Email domains rejected during extraction |
| `output.header_color` | Hex colour for Excel header rows (no `#`) |
| `output_prefix` | Output filename prefix |
| `source_label` | Value written to the Source column |
| `checkpoint_file` | Resume-state filename |
| `cookies_raw` | Session cookies (prefer `.env` / `SCRAPER_COOKIES_RAW`) |
| `proxy` | Proxy URL (prefer `.env` / `SCRAPER_PROXY`) |

---

## Usage

**Basic run (uses `config.yaml` in the same directory):**
```bash
python scraper.py
```

**Use a custom config file:**
```bash
python scraper.py --config custom.yaml
```

**Force a fresh start (ignore existing checkpoint):**
```bash
python scraper.py --fresh
```

**Resume an interrupted run** (automatic — the scraper detects `scraper_checkpoint.json` and resumes):
```bash
python scraper.py
```

### Keyboard controls

Press a key at any time during the run — no Enter key needed:

| Key | Effect |
|---|---|
| `P` | Pause after the current AJAX page completes |
| `R` | Resume from a paused state |
| `S` or `Q` | Save checkpoint and quit cleanly |
| `Ctrl+C` | Emergency stop — checkpoint is still saved |

---

## Output

The scraper writes a single `.xlsx` file named `{output_prefix}_YYYYMMDD_HHMMSS.xlsx`.

| Sheet | Contents |
|---|---|
| **Data** | All clean records with contact data — frozen header, alternating row shading, auto-width columns |
| **Flagged** | Records excluded because: (a) no email and no phone found, or (b) marker coordinates fell outside the geographic bounding box — each row includes a "Flag Reason" column |
| **Summary** | Run metadata: generated timestamp, source label, total clean, total flagged, with-email count, with-phone count, with-website count, run status (COMPLETE / PARTIAL) |

Columns in the Data sheet: `Company`, `Email`, `Phone`, `Website`, `Address`, `Postcode`, `Category`, `Source`.

---

## Scheduling

**Linux/macOS (cron)** — run daily at 06:00:
```cron
0 6 * * * cd /path/to/engines/wordpress && /path/to/venv/bin/python scraper.py >> cron.log 2>&1
```

**Windows (Task Scheduler):**
```
Program : C:\path\to\venv\Scripts\python.exe
Arguments: C:\path\to\engines\wordpress\scraper.py
Start in : C:\path\to\engines\wordpress
```

The scraper saves a checkpoint after every AJAX page, so a cron-triggered restart always picks up from where it stopped.

---

## Extending

| To do this | Change this |
|---|---|
| Target a different WordPress directory | Update `base_url`, `register_path`, `ajax_action`, and `sectors` in `config.yaml` |
| Add a new sector | Add a `{name, category}` entry to `sectors` in `config.yaml` |
| Add an output column | Add the key to `DATA_FIELDS` in `exporter.py` and populate it in `parser.scrape_profile()` |
| Change the geographic region | Update `geo_bounds` in `config.yaml` (use `boundingbox.klokantech.com`) |
| Disable geo filtering | Remove the entire `geo_bounds` block from `config.yaml` |
| Change the Excel header colour | Update `output.header_color` in `config.yaml` |

---

## Related Projects

This engine is part of the **FAAQJAVED B2B Lead Generation Suite**:

| Tool | Purpose |
|---|---|
| [Google Maps Business Scraper](https://github.com/FAAQJAVED/Google-Maps-Business-Scraper) | Scrapes Google Maps listings and enriches with website contact data |
| [Email & Phone Enrichment Tool](https://github.com/FAAQJAVED/Email-Phone-Number-Enrichment-Tool) | Two-pass contact enricher for CSV/URL lists |
| [LeadHunter Pro](https://github.com/FAAQJAVED/Leadhunter_Pro) | Multi-engine search scraper with lead scoring |
| [Trustpilot Business Scraper](https://github.com/FAAQJAVED/trustpilot-business-scraper) | Extracts business contact data from Trustpilot |
| [JSON Directory Harvester](https://github.com/FAAQJAVED/json-directory-harvester) | Harvests records from any JSON-based directory API |
| [HTML Directory Scrapers](https://github.com/FAAQJAVED/html-directory-scrapers) | Two-engine toolkit for HTML and WordPress directories *(this repo)* |

---

## License

MIT License — see [LICENSE](../../LICENSE) for details.
