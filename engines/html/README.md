# HTML Directory Scraper

> Scrapes any paginated HTML member or business directory using configurable CSS selectors — listing-page → profile-page pattern, Cloudflare email decoding, checkpoint-resumable.

---

## Overview

The HTML Directory Scraper is a config-driven Python tool for extracting contact records from any paginated business or member directory that renders its listings directly in HTML. It operates in two phases: first crawling listing pages to discover profile URLs, then visiting each profile page to extract company name, email, phone, website, and location. All CSS selectors, pagination parameters, and category mappings are defined in `config.yaml` — no code changes are needed to retarget a completely different directory. Output is a three-sheet Excel workbook containing clean records, flagged records, and a run summary.

---

## Features

- **Config-driven CSS selectors** — every HTML selector, pagination parameter, and category keyword is in `config.yaml`; point the scraper at any paginated HTML directory by editing the config alone
- **Listing → profile two-phase crawl** — discovers all profile URLs from paginated search result pages, then visits each profile to extract full contact details
- **Cloudflare XOR email decoding** — transparently decodes email addresses obfuscated by Cloudflare's email-protection service (`/cdn-cgi/l/email-protection` and `data-cfemail` attribute patterns)
- **Geographic regex filter** — applies a configurable Python regex to each profile page's full text; only records whose location field matches are saved to the Data sheet (non-matching records go to Flagged)
- **3-sheet Excel output** — Data sheet with all clean records (with `Category` column), Flagged sheet for excluded records with reason, and Summary sheet with run metadata
- **`command.txt` runtime controls** — pause, resume, stop, or force a fresh start at any time by writing a single word to `command.txt`; no Enter key or interactive terminal required (ideal for headless/cron runs)
- **Exponential backoff + circuit breaker** — retries failed requests with increasing delays; after 3 consecutive failures the scraper pauses automatically until the operator resumes it
- **Optional SMTP email verification** — validates each extracted email via an SMTP RCPT handshake (no mail is sent); requires `dnspython`; enable with `verify_email: true` in config
- **Daily auto-stop time** — configures a wall-clock time at which the scraper saves its state and exits cleanly each day
- **Low-disk space guard** — pauses automatically when free disk space falls below a configurable threshold

---

## Tech Stack

| Library | Purpose |
|---|---|
| `httpx` | HTTP client — async-ready, supports cookies and redirects |
| `beautifulsoup4` | HTML parsing for listing and profile pages |
| `lxml` | Fast HTML parser backend for BeautifulSoup |
| `openpyxl` | Excel workbook creation and styling |
| `pyyaml` | YAML configuration file parsing |
| `python-dotenv` | `.env` file loading for cookie injection |
| `dnspython` | MX record lookup for optional SMTP email verification |

---

## Project Structure

```
engines/html/
├── scraper.py          # CLI entry point + main orchestration loop (thin)
├── config.py           # YAML loader, key validation, env var injection
├── fetcher.py          # httpx client, safe_get, progress, SMTP verify
├── parser.py           # parse_cards, scrape_profile, CF email decode
├── exporter.py         # 3-sheet Excel workbook (Data / Flagged / Summary)
├── checkpoint.py       # CheckpointManager — atomic JSON save/load/clear
├── controls.py         # command.txt watcher, InputController, beep
├── config.yaml.example # Fully-commented config template (copy → config.yaml)
├── .env.example        # Cookie injection template (copy → .env)
└── requirements.txt    # Python dependencies
```

---

## Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/FAAQJAVED/html-directory-scrapers.git
   cd html-directory-scrapers/engines/html
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

5. **Add session cookies (if required)**
   ```bash
   cp .env.example .env
   ```
   Paste your browser cookie string as `SCRAPER_COOKIES=...` in `.env`.  
   See the `.env.example` comment block for step-by-step instructions.

---

## Configuration

Edit `config.yaml` to target any paginated HTML directory. The table below lists every configurable section:

| Section | What to configure |
|---|---|
| `base_url` | Root URL of the directory site (no trailing slash) |
| `list_path` | Path to the paginated listing/search-results page |
| `page_size` | Number of results the site shows per listing page |
| `categories` | Names of the member categories you want to capture |
| `all_services` | Service slug values appended as query parameters |
| `selectors.card_container` | CSS selector for each result card on the listing page |
| `selectors.profile_link` | CSS selector for the `<a>` tag linking to each profile |
| `selectors.member_name` | CSS selector for the company/member name inside each card |
| `selectors.badge_images` | CSS selector for badge/category icon `<img>` elements |
| `selectors.detail_section` | CSS selector for the contact-detail container on profile pages |
| `badge_image_keywords` | Keyword → category name mapping (matched against badge image src paths) |
| `location_filter_regex` | Python regex for geographic filtering; `""` to disable |
| `headers` | HTTP request headers (User-Agent, Accept, etc.) |
| `delay_min` / `delay_max` | Random per-request delay range in seconds |
| `stop_at` | Daily auto-stop time in `HH:MM` format; `""` to disable |
| `min_free_disk_mb` | Low-disk pause threshold in megabytes |
| `verify_email` | `true` to enable SMTP email verification (slow; requires dnspython) |

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

**Resume an interrupted run** (automatic — the scraper detects `checkpoint.json` and resumes):
```bash
python scraper.py
```

**Force a fresh start** (ignore existing checkpoint):
```bash
echo fresh > command.txt
python scraper.py
```

### Runtime controls (command.txt)

Write any of the following commands to `command.txt` while the scraper is running. The background watcher thread picks up the change within 1 second — no interactive terminal required.

| Command | Effect |
|---|---|
| `echo pause > command.txt` | Finish the current listing page, then pause |
| `echo resume > command.txt` | Resume from a paused state |
| `echo stop > command.txt` | Save checkpoint and exit cleanly |
| `echo fresh > command.txt` | Clear checkpoint on the next run |
| `echo status > command.txt` | Print a live-stats reminder to the log |

---

## Output

The scraper writes a single `.xlsx` file named `{output_prefix}_{YYYYMMDD}.xlsx`.

| Sheet | Contents |
|---|---|
| **Data** | All clean, validated records — frozen header, alternating row shading, auto-width columns |
| **Flagged** | Records excluded by the geographic filter or where the profile page could not be fetched — each row includes a "Flag Reason" column |
| **Summary** | Run metadata: generated timestamp, source label, total clean, total flagged, with-email count, with-phone count, with-website count, run status (COMPLETE / PARTIAL) |

Columns in the Data sheet: `Company`, `Email`, `Phone`, `Website`, `Location`, `Category`, `Source`.

---

## Scheduling

**Linux/macOS (cron)** — run daily at 06:00:
```cron
0 6 * * * cd /path/to/engines/html && /path/to/venv/bin/python scraper.py >> cron.log 2>&1
```

**Windows (Task Scheduler):**
```
Program : C:\path\to\venv\Scripts\python.exe
Arguments: C:\path\to\engines\html\scraper.py
Start in : C:\path\to\engines\html
```

Set `stop_at` in `config.yaml` to the time before your next scheduled run to ensure a clean daily handover.

---

## Extending

| To do this | Change this |
|---|---|
| Target a different HTML directory | Update `base_url`, `list_path`, and all `selectors` in `config.yaml` |
| Add a new member category | Add an entry to `categories` and a matching entry to `badge_image_keywords` |
| Add an output column | Add the key to `DATA_FIELDS` in `exporter.py` and populate it in `parser.scrape_profile()` |
| Change the geographic filter | Update `location_filter_regex` in `config.yaml` (see examples in the file) |
| Enable email verification | Set `verify_email: true` in `config.yaml` and install `dnspython` |
| Adjust request rate | Change `delay_min` and `delay_max` in `config.yaml` |

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
