"""
parser.py
=========
Pure HTML and JSON parsing for the WordPress Directory Scraper.

Responsibilities:
  - Parsing the HTML card grid returned by admin-ajax.php AJAX responses
  - Geographic bounding-box filtering of map marker arrays
  - Converting marker arrays to business item dicts (no geo filtering)
  - Visiting individual profile pages to extract contact fields
  - Email address validation and extraction
  - HTML entity decoding (delegates to fetcher.decode_entities)

This module performs NO direct HTTP calls except within scrape_profile(),
which calls fetcher.http_get.  All other functions are pure transformations
on strings, dicts, or lists.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

import fetcher as _fetcher

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Public re-export of entity decoder
# ══════════════════════════════════════════════════════════════════════════════


def decode_entities(text: str) -> str:
    """
    Replace common HTML entities that appear in WordPress AJAX responses.

    Public wrapper around fetcher.decode_entities so callers never need
    to import fetcher directly for this utility.

    Args:
        text: Input string potentially containing HTML entity sequences.

    Returns:
        String with known entities replaced by their Unicode equivalents.
    """
    return str(_fetcher.decode_entities(text))


# Keep the private alias for internal use within this module
_decode_entities = decode_entities


# ══════════════════════════════════════════════════════════════════════════════
# Email helpers
# ══════════════════════════════════════════════════════════════════════════════


def is_valid_email(email: str, junk_domains: set) -> bool:
    """
    Validate an email address against format rules and a junk-domain blocklist.

    Rejects addresses that look like filenames (e.g. ``image@sprite.png``) and
    any domain present in *junk_domains*.

    Args:
        email:        Email string to validate (any case accepted).
        junk_domains: Set of domain substrings to reject outright.

    Returns:
        True if the email passes all checks.
    """
    e = (email or "").lower().strip()
    if not re.match(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$", e):
        return False
    domain = e.split("@")[-1]
    if any(j in domain for j in junk_domains):
        return False
    if re.search(r"\.(png|jpg|gif|js|css)$", e):
        return False
    return True


def extract_emails(html: str, junk_domains: set) -> list[str]:
    """
    Extract all valid email addresses from raw HTML or plain text.

    Args:
        html:         Raw HTML or text content to scan.
        junk_domains: Set of domains to reject.

    Returns:
        List of unique, lowercase, validated email strings.
        May be empty if no valid addresses are found.
    """
    found = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", html)
    seen: set[str] = set()
    result: list[str] = []
    for e in found:
        e_lower = e.lower()
        if e_lower not in seen and is_valid_email(e_lower, junk_domains):
            seen.add(e_lower)
            result.append(e_lower)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Card HTML parser
# ══════════════════════════════════════════════════════════════════════════════


def parse_cards(cards_html: str, base_url: str, cfg: Optional[dict] = None) -> dict:
    """
    Parse the HTML card grid returned by the admin-ajax.php AJAX endpoint.

    Each result card contains a company name (in an ``<h3>`` element), an
    address block (whose first ``<p>`` may contain a postcode), and a link to
    the company's directory profile page.

    Returns a dict keyed by company name to allow O(1) lookup when correlating
    with the map marker array that arrives in the same AJAX response.

    The ``postcode_regex`` config key (optional) defines the pattern used to
    extract a postcode or ZIP code from the address text. If the key is absent
    or empty, ``postcode`` is always set to "".

    Examples::

        UK postcode:  "\\b[A-Z]{1,2}[0-9][0-9A-Z]?\\s*[0-9][A-Z]{2}\\b"
        US ZIP code:  "\\b\\d{5}(-\\d{4})?\\b"
        AU postcode:  "\\b[0-9]{4}\\b"

    Args:
        cards_html: Raw HTML string of the search-results card grid.
        base_url:   Site root URL — used to resolve relative profile URLs.
        cfg:        Optional configuration dict. Used to read ``postcode_regex``.

    Returns:
        Dict mapping company name → ``{address, postcode, url}``.
        Returns ``{}`` for empty or unparseable input.
    """
    result: dict = {}
    if not cards_html:
        return result

    soup = BeautifulSoup(cards_html, "html.parser")

    for card in soup.find_all("div", class_=re.compile(r"card")):
        h3 = card.find("h3")
        name = _fetcher.decode_entities(h3.get_text(strip=True)) if h3 else ""
        if not name:
            continue

        paras = card.find_all("p")
        address = paras[0].get_text(" ", strip=True) if paras else ""
        postcode_re_str: str = cfg.get("postcode_regex", "") if cfg else ""
        pc_match = re.search(postcode_re_str, address, re.I) if postcode_re_str else None

        a_tag = card.find("a", href=True)
        url = ""
        if a_tag:
            url = str(a_tag["href"]).split("?")[0].split("&#")[0]
            if url and not url.startswith("http"):
                url = base_url.rstrip("/") + "/" + url.lstrip("/")

        result[name] = {
            "address": address,
            "postcode": pc_match.group(0).upper() if pc_match else "",
            "url": url,
        }

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Geographic filtering
# ══════════════════════════════════════════════════════════════════════════════


def filter_by_bounds(markers: list, card_map: dict, bounds: dict) -> list:
    """
    Filter map markers to those within a geographic bounding box.

    Markers contain the lat/lng coordinates already computed server-side, so
    regional filtering requires no geocoding — just numeric comparisons.
    Markers outside the box are excluded entirely (and flagged by the
    orchestrator with reason "Outside geographic bounds").

    Args:
        markers:  List of marker dicts from the AJAX response data.
                  Each dict is expected to have keys: ``lat``, ``lng``, ``title``.
        card_map: Output of ``parse_cards()``, keyed by company name.
        bounds:   Dict with keys: ``lat_min``, ``lat_max``, ``lng_min``, ``lng_max``.

    Returns:
        List of business dicts for markers within the bounding box.
        Each dict has keys: ``name``, ``address``, ``postcode``, ``url``.
    """
    lat_min = float(bounds.get("lat_min", -90))
    lat_max = float(bounds.get("lat_max", 90))
    lng_min = float(bounds.get("lng_min", -180))
    lng_max = float(bounds.get("lng_max", 180))

    items: list[dict] = []
    for m in markers:
        try:
            lat = float(m.get("lat", 0))
            lng = float(m.get("lng", 0))
            name = _fetcher.decode_entities(m.get("title", ""))
            if not (lat_min <= lat <= lat_max and lng_min <= lng <= lng_max):
                continue
            info = card_map.get(name, {})
            items.append(
                {
                    "name": name,
                    "address": info.get("address", ""),
                    "postcode": info.get("postcode", ""),
                    "url": info.get("url", ""),
                }
            )
        except Exception:
            continue

    return items


def markers_to_items(markers: list, card_map: dict) -> list:
    """
    Convert all map markers to business dicts without geographic filtering.

    Used when no ``geo_bounds`` block is present in the config, so all
    markers from the current AJAX page are returned regardless of location.

    Args:
        markers:  List of marker dicts from the AJAX response data.
        card_map: Output of ``parse_cards()``, keyed by company name.

    Returns:
        List of all business dicts from the current AJAX page.
        Each dict has keys: ``name``, ``address``, ``postcode``, ``url``.
    """
    items: list[dict] = []
    for m in markers:
        try:
            name = _fetcher.decode_entities(m.get("title", ""))
            info = card_map.get(name, {})
            items.append(
                {
                    "name": name,
                    "address": info.get("address", ""),
                    "postcode": info.get("postcode", ""),
                    "url": info.get("url", ""),
                }
            )
        except Exception:
            continue
    return items


# ══════════════════════════════════════════════════════════════════════════════
# Profile scraper
# ══════════════════════════════════════════════════════════════════════════════


def scrape_profile(
    sess,
    item: dict,
    category: str,
    source_label: str,
    headers: dict,
    cfg: dict,
    junk_domains: set,
    skip_domains: set,
) -> dict:
    """
    Visit a business's directory profile page and extract contact details.

    Extraction strategy:
      - **Email**: first ``mailto:`` link → regex scan of full body.
        If none found and ``crawl_websites: true``, visits the company's
        own website contact pages via ``fetcher.crawl_for_email()``.
      - **Phone**: labeled paragraph (``Phone: ...``) → ``tel:`` link.
      - **Website**: first external link not in ``skip_domains``.

    A fully populated record dict is always returned (fields may be empty
    strings if not found) so callers can still append the record with
    flag information.

    Args:
        sess:         Active requests.Session.
        item:         Business dict with keys ``name``, ``url``, ``address``,
                      ``postcode``.
        category:     Human-readable category label for this business.
        source_label: Value for the ``Source`` column.
        headers:      HTTP request headers.
        cfg:          Config dict.  Reads: ``crawl_websites``, ``contact_paths``.
        junk_domains: Email domains to reject outright.
        skip_domains: Website domains to ignore when extracting company URL.

    Returns:
        Record dict with keys: Company, Email, Phone, Website, Address,
        Postcode, Category, Source.
    """
    rec: dict = {
        "Company": item["name"],
        "Email": "",
        "Phone": "",
        "Website": "",
        "Address": item["address"],
        "Postcode": item["postcode"],
        "Category": category,
        "Source": source_label,
    }

    if not item.get("url"):
        return rec

    body, status = _fetcher.http_get(sess, item["url"], headers)
    if not body or status != 200:
        log.warning("Profile HTTP %d: %s", status, item["name"])
        return rec

    soup = BeautifulSoup(body, "html.parser")
    email = phone = website = ""

    # ── Email: mailto links → regex scan ──────────────────────────────────────
    for a in soup.find_all("a", href=re.compile(r"^mailto:", re.I)):
        candidate = str(a["href"])[7:].strip().lower()
        if is_valid_email(candidate, junk_domains):
            email = candidate
            break
    if not email:
        found = extract_emails(body, junk_domains)
        if found:
            email = found[0]

    # ── Phone: labeled paragraph first, then tel: links ───────────────────────
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        m = re.match(r"Phone\s+(?:number\s*)?:\s*([0-9][0-9\s\+\-\(\)]{6,18})", t, re.I)
        if m:
            phone = re.sub(r"\s+", "", m.group(1))
            break
    if not phone:
        for a in soup.find_all("a", href=re.compile(r"^tel:", re.I)):
            phone = str(a["href"])[4:].strip()
            break

    # ── Website: first external non-social/utility link ───────────────────────
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        txt = a.get_text(strip=True)
        if not href.startswith("http"):
            continue
        if any(s in href for s in skip_domains):
            continue
        if re.match(r"www\.", txt, re.I) or "www." in href:
            website = href
            break

    # ── Email enrichment via company website crawl ────────────────────────────
    if not email and website and cfg.get("crawl_websites", False):
        contact_paths = cfg.get("contact_paths") or None
        email = _fetcher.crawl_for_email(sess, website, headers, junk_domains, contact_paths)

    rec["Email"] = email
    rec["Phone"] = phone
    rec["Website"] = website
    return rec
