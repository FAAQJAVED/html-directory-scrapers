"""
fetcher.py
==========
All HTTP communication for the WordPress Directory Scraper.

Responsibilities:
  - Building a requests.Session with config-driven cookies and proxy
  - Cookie string parsing
  - Standard GET requests with exponential-backoff retry
  - AJAX POST to WordPress admin-ajax.php with nonce injection
  - WordPress nonce extraction from page source (3 pattern strategies)
  - Manual gzip/zlib decompression of AJAX responses (safe_decode)
  - Website crawl for email enrichment (crawl_for_email)
  - HTML entity decoding for company names returned by AJAX
  - Elapsed-time tracking and rolling rate/ETA calculation

v1.1.0 changes
--------------
  - http_get() now accepts is_crawl=True flag:
      * 6-second timeout for third-party website crawls (was 25 s)
      * requests.exceptions.ConnectTimeout returns ("", 0) immediately,
        no retry — dead company websites never stall concurrent workers
  - crawl_for_email() passes is_crawl=True to http_get()
  - make_session() used by concurrent profile threads (one session per thread)
"""

from __future__ import annotations

import gzip
import json
import logging
import random
import re
import time
import zlib
from typing import Optional
from urllib.parse import urljoin

import requests

log = logging.getLogger(__name__)

_start_time: float = time.time()
_scrape_times: list[float] = []

_HTML_ENTITY_MAP: dict[str, str] = {
    "&#038;": "&",
    "&amp;": "&",
    "&#8217;": "'",
    "&#8216;": "'",
    "&#8220;": "\u201c",
    "&#8221;": "\u201d",
}


# =============================================================================
# Cookie parsing
# =============================================================================


def parse_cookies(raw: Optional[str]) -> dict:
    """
    Parse a raw Cookie header string into a name->value dict.

    Args:
        raw: Cookie string from browser DevTools, or None/empty.

    Returns:
        Dict mapping cookie name to value. Returns {} for empty/None input.
    """
    out: dict = {}
    for part in re.split(r";\s*|\n", (raw or "").strip()):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            if k.strip():
                out[k.strip()] = v.strip()
    return out


# =============================================================================
# Decompression
# =============================================================================


def safe_decode(raw: bytes) -> str:
    """
    Decode raw HTTP response bytes, transparently handling gzip/zlib encoding.

    WordPress AJAX endpoints sometimes return compressed content without the
    correct Content-Encoding header. This function sniffs magic bytes and
    decompresses manually.

    Magic byte signatures:
      - x1fx8b  -> gzip
      - x78     -> zlib/deflate

    Args:
        raw: Raw bytes from an HTTP response body.

    Returns:
        Decoded UTF-8 string. Uses errors="replace" for invalid byte sequences.
    """
    if raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw).decode("utf-8", errors="replace")
        except Exception:
            pass
    if raw[:1] == b"\x78":
        try:
            return zlib.decompress(raw).decode("utf-8", errors="replace")
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


# =============================================================================
# HTML entity decoding
# =============================================================================


def decode_entities(text: str) -> str:
    """
    Replace common HTML entities that BeautifulSoup may leave intact.

    Args:
        text: Input string potentially containing HTML entity sequences.

    Returns:
        String with known entities replaced by their Unicode equivalents.
    """
    for entity, char in _HTML_ENTITY_MAP.items():
        text = text.replace(entity, char)
    return text


# =============================================================================
# Header builders
# =============================================================================


def build_headers(cfg: dict) -> dict:
    """
    Build standard HTTP request headers for page GET requests.

    Args:
        cfg: Validated configuration dictionary.

    Returns:
        Dict of HTTP header name -> value.
    """
    return {
        "User-Agent": cfg.get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36",
        ),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


def build_ajax_headers(cfg: dict, register_url: str) -> dict:
    """
    Build HTTP headers for AJAX/JSON POST requests to admin-ajax.php.

    Args:
        cfg:          Validated configuration dictionary.
        register_url: Full URL of the directory's main search page (for Referer).

    Returns:
        Dict of HTTP header name -> value.
    """
    return {
        **build_headers(cfg),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": register_url,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }


# =============================================================================
# Session factory
# =============================================================================


def make_session(cfg: dict) -> requests.Session:
    """
    Build a requests.Session with cookies and optional proxy from config.

    Called once per scraper run for the main session, and once per
    concurrent thread for profile/crawl fetches.

    Args:
        cfg: Validated configuration dictionary.

    Returns:
        Configured requests.Session ready for use.
    """
    session = requests.Session()
    cookies_raw = cfg.get("cookies_raw", "")
    proxy = cfg.get("proxy", "")

    if cookies_raw:
        session.cookies.update(parse_cookies(cookies_raw))
        log.debug("Cookies loaded (%d keys)", len(session.cookies))
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
        log.debug("Proxy configured: %s", proxy.split("@")[-1])

    return session


# =============================================================================
# GET with retry — v1.1.0: is_crawl flag for fast third-party timeouts
# =============================================================================


def http_get(
    sess: requests.Session,
    url: str,
    headers: dict,
    params: Optional[dict] = None,
    retries: int = 3,
    is_crawl: bool = False,
) -> tuple[str, int]:
    """
    Perform a GET request with exponential-backoff retry on failure.

    When is_crawl=True (third-party website enrichment):
      - Uses a 6-second timeout instead of 25 seconds.
      - ConnectTimeout returns ("", 0) immediately with zero retries.
      - ReadTimeout / other errors get 1 retry with a 4 s wait.
        Dead company websites never stall concurrent worker threads.

    When is_crawl=False (TPOS/directory profile pages):
      - Uses 25-second timeout with full retry logic.

    Args:
        sess:      Active requests.Session.
        url:       URL to fetch.
        headers:   HTTP headers dict.
        params:    Optional query parameters dict.
        retries:   Maximum number of attempts (ignored for is_crawl ConnectTimeout).
        is_crawl:  True for third-party website crawls (short timeout, no retry
                   on connection timeout).

    Returns:
        Tuple of (decoded response body, HTTP status code).
        Returns ("", 0) on total failure.
    """
    timeout = 6 if is_crawl else 25
    max_tries = 2 if is_crawl else retries  # 1 retry on crawl pages (not ConnectTimeout)

    for attempt in range(max_tries):
        try:
            r = sess.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
                allow_redirects=True,
            )
            return safe_decode(r.content), r.status_code

        except requests.exceptions.ConnectTimeout:
            # FIX v1.1.0: TCP-level failure — host is unreachable, never retry
            log.debug("ConnectTimeout (no retry): %s", url)
            return "", 0

        except requests.exceptions.Timeout:
            if is_crawl:
                log.debug("Read timeout on crawl (no retry): %s", url)
                return "", 0
            wait = (attempt + 1) * 4
            log.warning("Timeout (attempt %d) — retry in %ds: %s", attempt + 1, wait, url)
            time.sleep(wait)

        except Exception as exc:
            if attempt < max_tries - 1:
                wait = (attempt + 1) * 4
                log.warning(
                    "GET error (attempt %d): %s — retrying in %ds",
                    attempt + 1,
                    exc,
                    wait,
                )
                time.sleep(wait)
            else:
                log.error("GET failed after %d tries: %s — %s", max_tries, url, exc)

    return "", 0


# =============================================================================
# Nonce extraction
# =============================================================================


def get_nonce(sess: requests.Session, register_url: str, headers: dict) -> str:
    """
    Fetch the register page and extract the WordPress nonce security token.

    The nonce is a server-generated CSRF token required for all AJAX search
    requests. May expire during long runs — call again if AJAX responses
    return empty or {success: false}.

    Tries three extraction patterns in order:
      1. JSON property:   "nonce":"<hex>"
      2. JS/HTML generic: nonce = '<hex>' or nonce: '<hex>'
      3. Data attribute:  data-nonce="<hex>"

    Args:
        sess:         Active requests.Session.
        register_url: Full URL of the directory's main search/register page.
        headers:      HTTP headers for the GET request.

    Returns:
        Nonce string, or "" if extraction fails.
    """
    body, status = http_get(sess, register_url, headers)
    if not body or status != 200:
        log.warning("Register page returned HTTP %d", status)
        return ""

    patterns = [
        r'"nonce"\s*:\s*"([a-f0-9]+)"',
        r"""nonce['"']?\s*[:=]\s*['"]([a-f0-9]+)['"]""",
        r'data-nonce="([a-f0-9]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, body)
        if m:
            return m.group(1)
    return ""


# =============================================================================
# AJAX POST
# =============================================================================


def post_ajax(
    sess: requests.Session,
    cfg: dict,
    ajax_url: str,
    ajax_headers: dict,
    sector_name: str,
    page: int,
    nonce: str,
    retries: int = 3,
) -> dict:
    """
    POST to a WordPress admin-ajax.php endpoint to fetch one page of results.

    The POST body is assembled entirely from config values so the scraper
    can target any WordPress directory without code changes.

    Args:
        sess:         Active requests.Session.
        cfg:          Configuration dict.
        ajax_url:     Full URL to admin-ajax.php.
        ajax_headers: Headers dict from build_ajax_headers.
        sector_name:  Sector/category string for the AJAX handler.
        page:         1-based page number to fetch.
        nonce:        WordPress nonce security token.
        retries:      Maximum attempts before giving up.

    Returns:
        Parsed JSON response dict, or {} on failure.
    """
    data: dict = {
        "action": cfg["ajax_action"],
        "business-name": "",
        "location": cfg.get("ajax_location", ""),
        "business-sector": sector_name,
        "status": cfg.get("ajax_status", ""),
        "paged": page,
    }
    if nonce:
        data["nonce"] = nonce

    for attempt in range(retries):
        try:
            r = sess.post(ajax_url, data=data, headers=ajax_headers, timeout=25)
            return json.loads(safe_decode(r.content))
        except Exception as exc:
            if attempt < retries - 1:
                wait = (attempt + 1) * 4
                log.warning("AJAX error (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
                time.sleep(wait)
            else:
                log.error("AJAX failed: %s", exc)
    return {}


# =============================================================================
# Email enrichment via website crawl — v1.1.0: 6 s timeout, no retry on timeout
# =============================================================================


def crawl_for_email(
    sess: requests.Session,
    website: str,
    headers: dict,
    junk_domains: set,
    contact_paths: Optional[list] = None,
) -> str:
    """
    Visit a company's website and common contact pages to find an email.

    Uses is_crawl=True in http_get so each path fetch uses a 6-second timeout
    and ConnectTimeout returns immediately — no multi-minute stalls on dead sites.

    Args:
        sess:          Active requests.Session.
        website:       Base URL of the company's external website.
        headers:       HTTP headers for GET requests.
        junk_domains:  Set of domains whose emails should be rejected.
        contact_paths: URL paths to try in order. Defaults to common slugs.

    Returns:
        First valid email address found, or "" if none found.
    """

    def _is_valid(email: str) -> bool:
        e = email.lower().strip()
        if not re.match(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$", e):
            return False
        domain = e.split("@")[-1]
        if any(j in domain for j in junk_domains):
            return False
        if re.search(r"\.(png|jpg|gif|js|css)$", e):
            return False
        return True

    def _find_emails(html: str) -> list:
        found = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", html)
        seen_set: set = set()
        result = []
        for e in found:
            el = e.lower()
            if el not in seen_set and _is_valid(el):
                seen_set.add(el)
                result.append(el)
        return result

    paths = contact_paths or ["", "/contact", "/contact-us", "/about", "/about-us"]
    for path in paths:
        try:
            url = urljoin(website, path) if path else website
            # FIX v1.1.0: is_crawl=True → 6 s timeout, ConnectTimeout = instant skip
            body, status = http_get(sess, url, headers, is_crawl=True)
            if body and status == 200:
                found = _find_emails(body)
                if found:
                    return found[0]
            time.sleep(random.uniform(0.3, 0.6))
        except Exception:
            continue
    return ""


# =============================================================================
# Timing and rate helpers
# =============================================================================


def elapsed() -> str:
    """Return human-readable elapsed time since module import."""
    secs = int(time.time() - _start_time)
    m, s = divmod(secs, 60)
    return f"{m}m{s:02d}s"


def record_scrape() -> None:
    """Record a scrape timestamp. Call once per successfully saved record."""
    _scrape_times.append(time.time())


def rate_and_eta(total_scraped: int, total_est: int) -> str:
    """
    Calculate scrape rate and ETA using a 30-record rolling window.

    Args:
        total_scraped: Records written so far.
        total_est:     Estimated total records.

    Returns:
        Formatted string like "42/min | ETA ~8m", or "" if insufficient data.
    """
    if len(_scrape_times) < 2:
        return ""
    window = _scrape_times[-30:]
    secs = window[-1] - window[0]
    if secs <= 0:
        return ""
    rate = len(window) / secs * 60
    remaining = total_est - total_scraped
    if rate > 0 and remaining > 0:
        eta_mins = remaining / rate
        eta_str = f"~{int(eta_mins)}m" if eta_mins < 60 else f"~{eta_mins / 60:.1f}h"
    else:
        eta_str = "done"
    return f"{rate:.0f}/min | ETA {eta_str}"
