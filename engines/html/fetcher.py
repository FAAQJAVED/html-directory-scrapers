"""
fetcher.py
==========
All HTTP communication for the HTML Directory Scraper.

Responsibilities:
  - Building the httpx.Client with config-driven headers and cookies
  - Cookie string parsing
  - Fault-tolerant GET requests with exponential backoff
  - Circuit-breaker: pauses after 3 consecutive failures
  - Listing-page total-pages extraction
  - Elapsed-time tracking and rolling rate/ETA calculation
  - ASCII progress bar rendering (kept for log output)
  - Optional SMTP email verification (requires dnspython)

v1.1.0 changes
--------------
  - is_profile=True now applies 6 s timeout (reduced from 8 s) with 0 retries
    on ConnectTimeoutError — dead company websites no longer stall the run
  - make_client() accepts n_workers param for connection pool sizing
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional

import httpx

log = logging.getLogger(__name__)

_start: float = time.time()
_scrape_ts: list[float] = []
_fail_streak: int = 0

# Callback registered by scraper.py to avoid a circular import with controls.py
_circuit_break_callback: Optional[Callable[[], None]] = None


def set_circuit_break_callback(cb: Callable[[], None]) -> None:
    """
    Register a callback invoked when 3 consecutive failures trigger
    the circuit breaker. Called instead of directly importing controls.

    Args:
        cb: Zero-argument callable — typically controls._trigger_pause.
    """
    global _circuit_break_callback
    _circuit_break_callback = cb


# =============================================================================
# Cookie parsing
# =============================================================================


def parse_cookies(raw: str) -> dict[str, str]:
    """
    Parse a raw Cookie header string into a name->value dict.

    Args:
        raw: Cookie string from browser DevTools (e.g. "session=abc; tok=xyz").

    Returns:
        Dict mapping cookie name to value. Returns {} for empty input.
    """
    out: dict[str, str] = {}
    for part in re.split(r";\s*", (raw or "").strip()):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            if k.strip():
                out[k.strip()] = v.strip()
    return out


# =============================================================================
# HTTP client factory
# =============================================================================


def make_client(cfg: dict, n_workers: int = 1) -> httpx.Client:
    """
    Build an httpx.Client configured from the loaded config dict.

    Args:
        cfg:       Validated configuration dictionary.
        n_workers: Number of concurrent threads that will share this client.
                   Used to size the connection pool (not used in profile fetches
                   which each create their own client).

    Returns:
        Ready-to-use httpx.Client instance.
    """
    cookies = parse_cookies(cfg.get("cookies_raw", ""))
    limits = httpx.Limits(
        max_connections=max(n_workers * 2, 10),
        max_keepalive_connections=max(n_workers, 5),
    )
    return httpx.Client(
        headers=cfg.get("headers", {}),
        cookies=cookies,
        timeout=cfg.get("timeout_seconds", 25),
        follow_redirects=True,
        limits=limits,
    )


# =============================================================================
# Timeout configuration (overridden at startup via set_timeouts)
# =============================================================================

_PROFILE_TIMEOUT: int = 6  # default — overridden at startup by make_client
_LISTING_TIMEOUT: int = 25  # default — overridden at startup by make_client


def set_timeouts(profile_s: int, listing_s: int) -> None:
    """
    Override default timeout values from config.

    Args:
        profile_s: Timeout in seconds for profile/crawl requests.
        listing_s: Timeout in seconds for listing page requests.
    """
    global _PROFILE_TIMEOUT, _LISTING_TIMEOUT
    _PROFILE_TIMEOUT = profile_s
    _LISTING_TIMEOUT = listing_s


# =============================================================================
# Fault-tolerant GET with circuit breaker
# =============================================================================


def safe_get(
    client: httpx.Client,
    url: str,
    params: Optional[list] = None,
    retries: int = 4,
    is_profile: bool = False,
) -> tuple[str, int]:
    """
    Fetch a URL with exponential-backoff retry and a circuit-breaker guard.

    Profile fetches (is_profile=True):
      - 6-second timeout (reduced from 8 s in v1.1.0)
      - ConnectTimeoutError immediately returns ("", 0) with no retry
      - ReadTimeout / other errors get 1 retry with a 5 s wait
        so dead company websites do not stall concurrent workers
      - Single attempt only

    Listing-page fetches:
      - 25-second timeout, up to `retries` attempts

    After 3 consecutive failures the circuit breaker pauses the run until
    the operator resumes via command.txt or keyboard.

    Args:
        client:     Configured httpx.Client.
        url:        Full URL to fetch.
        params:     Optional list of (key, value) query-parameter tuples.
        retries:    Maximum attempts for listing-page fetches.
        is_profile: When True, uses short timeout, no retry on timeout.

    Returns:
        Tuple of (response_text, status_code). Returns ("", 0) on failure.
    """
    global _fail_streak

    timeout = _PROFILE_TIMEOUT if is_profile else _LISTING_TIMEOUT
    max_ret = 2 if is_profile else retries  # 1 retry on profile pages (not ConnectTimeout)

    for attempt in range(max_ret):
        try:
            r = client.get(url, params=params, timeout=timeout)
            _fail_streak = 0
            return r.text, r.status_code

        except httpx.ConnectTimeout:
            # FIX v1.1.0: connection timeout = host is dead, never retry
            log.debug("ConnectTimeout (no retry): %s", url)
            return "", 0

        except httpx.TimeoutException as exc:
            if is_profile:
                log.debug("Timeout on profile %s", url)
                return "", 0
            wait = (attempt + 1) * 5
            log.warning("Timeout (attempt %d) — retrying in %ds: %s", attempt + 1, wait, exc)
            time.sleep(wait)
            _fail_streak += 1

        except Exception as exc:
            err = str(exc)
            is_reset = any(k in err for k in ("10054", "ConnectionReset", "Connection aborted"))
            wait = (attempt + 1) * 5
            if is_reset:
                log.warning("Connection reset — retrying in %ds", wait)
            else:
                log.warning("GET error (attempt %d): %s — retrying in %ds", attempt + 1, exc, wait)
            time.sleep(wait)
            _fail_streak += 1

            if _fail_streak >= 3:
                log.error(
                    "3 consecutive failures — pausing. "
                    "Write 'resume' to command.txt to continue."
                )
                if _circuit_break_callback:
                    _circuit_break_callback()
                _fail_streak = 0

    return "", 0


# =============================================================================
# Listing-page total-pages extraction
# =============================================================================


def get_total_pages(soup, page_size: int) -> int:
    """
    Parse the total result count from a listing page and calculate page count.

    Args:
        soup:      BeautifulSoup object for the listing page.
        page_size: Number of results the site shows per page.

    Returns:
        Total number of pages. Returns 999 as fallback if no count found.
    """
    text = soup.get_text()
    match = re.search(r"(\d[\d,]+)\s*result", text, re.I)
    if match:
        total = int(match.group(1).replace(",", ""))
        return max(1, (total + page_size - 1) // page_size)
    return 999


# =============================================================================
# Timing, rate, and progress helpers
# =============================================================================


def elapsed() -> str:
    """Return human-readable elapsed time since module import."""
    s = int(time.time() - _start)
    return f"{s // 60}m{s % 60:02d}s"


def record_scrape() -> None:
    """Record a scrape timestamp. Call once per successfully saved record."""
    _scrape_ts.append(time.time())


def rate_eta(done: int, total: int) -> str:
    """
    Calculate current scrape rate and ETA using a 30-record rolling window.

    Args:
        done:  Records saved so far.
        total: Estimated total records.

    Returns:
        Formatted string like "42/min | ETA ~8m", or "" if insufficient data.
    """
    if len(_scrape_ts) < 2:
        return ""
    window = _scrape_ts[-30:]
    secs = window[-1] - window[0]
    if secs <= 0:
        return ""
    rate = len(window) / secs * 60
    remaining = total - done
    if rate > 0 and remaining > 0:
        eta_min = remaining / rate
        eta_str = f"~{int(eta_min)}m" if eta_min < 60 else f"~{eta_min / 60:.1f}h"
    else:
        eta_str = "done"
    return f"{rate:.0f}/min | ETA {eta_str}"


def progress_bar(done: int, total: int, width: int = 30) -> str:
    """
    Render a simple ASCII progress bar for log output.

    Args:
        done:  Current progress value.
        total: Target value.
        width: Bar interior width in characters.

    Returns:
        String like "[████░░░░░░░░░░░░░░░░░░░░░░░░░░] 27%".
    """
    pct = done / max(total, 1)
    fill = int(pct * width)
    bar = "█" * fill + "░" * (width - fill)
    return f"[{bar}] {pct * 100:.0f}%"


# =============================================================================
# Optional SMTP email verification
# =============================================================================


def smtp_verify(email: str) -> bool:
    """
    Perform a lightweight SMTP RCPT handshake to validate an email address.

    Does NOT send mail. Enable via verify_email: true in config.yaml.
    Requires: pip install dnspython

    Args:
        email: Email address to verify.

    Returns:
        True if SMTP server accepts RCPT TO for this address.
    """
    import smtplib

    try:
        import dns.resolver
    except ImportError:
        log.error("dnspython required for SMTP verification: pip install dnspython")
        return False
    try:
        domain = email.split("@")[1]
        mx_records = dns.resolver.resolve(domain, "MX")
        mx_host = sorted(mx_records, key=lambda r: r.preference)[0].exchange.to_text()  # type: ignore[attr-defined]
        with smtplib.SMTP(mx_host, 25, timeout=5) as smtp:
            smtp.ehlo("check.local")
            smtp.mail("probe@check.local")
            code, _ = smtp.rcpt(email)
            return code == 250
    except Exception as exc:
        log.debug("SMTP verify failed for %s: %s", email, exc)
        return False