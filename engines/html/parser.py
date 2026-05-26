"""
parser.py
=========
Pure HTML parsing for the HTML Directory Scraper.

Responsibilities:
  - Parsing listing-page result cards into structured dicts
  - Visiting individual profile pages and extracting contact fields
  - Cloudflare XOR email decoding (two patterns)
  - Plain mailto: email extraction
  - Generic phone number normalisation (7-15 digit, E.164-compatible)

v1.1.0 changes
--------------
  - parse_cards() now populates card["location"] from the listing-card
    meta text (e.g. <span class="meta">Ringwood</span>) so the Location
    column is populated even when no postcode regex is configured.
  - scrape_profile() no longer used for the website timeout fix; timeout
    is controlled by fetcher.safe_get(is_profile=True) which uses 6 s.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


# =============================================================================
# Cloudflare email decoding
# =============================================================================


def decode_cf_email(encoded: str) -> str:
    """
    Decode a Cloudflare XOR-obfuscated email string.

    Args:
        encoded: Hex string from the data-cfemail attribute or CF href fragment.

    Returns:
        Decoded email string, or "" on any error.
    """
    if not encoded:
        return ""
    try:
        enc = bytes.fromhex(encoded)
        key = enc[0]
        return "".join(chr(b ^ key) for b in enc[1:])
    except Exception:
        return ""


def extract_email(soup: BeautifulSoup) -> str:
    """
    Extract an email address from a BeautifulSoup page object.

    Tries three strategies in order:
      1. Cloudflare email-protection href
      2. data-cfemail span attribute
      3. Plain mailto: link

    Args:
        soup: Parsed BeautifulSoup document.

    Returns:
        Lowercase email address string, or "" if none found.
    """
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "/cdn-cgi/l/email-protection#" in href:
            email = decode_cf_email(href.split("#")[-1])
            if "@" in email:
                return email.lower().strip()

    for span in soup.find_all("span", {"data-cfemail": True}):
        email = decode_cf_email(str(span["data-cfemail"]))
        if "@" in email:
            return email.lower().strip()

    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if href.startswith("mailto:"):
            return href[7:].strip().lower()

    return ""


# =============================================================================
# Phone normalisation
# =============================================================================


def clean_phone(raw: str) -> str:
    """
    Normalise a raw phone string to digits only and validate its length.

    Strips non-digit characters and returns the result if 7-15 digits long.
    No country-specific logic applied (E.164-compatible).

    Args:
        raw: Raw phone string (e.g. "+1 (800) 555-1234").

    Returns:
        Digit-only string if 7-15 digits, otherwise "".
    """
    digits = re.sub(r"[^\d]", "", raw or "")
    if 7 <= len(digits) <= 15:
        return digits
    return ""


# =============================================================================
# Listing-page card parser
# =============================================================================


def parse_cards(soup: BeautifulSoup, cfg: dict) -> list[dict]:
    """
    Parse all result cards from a listing page into structured dicts.

    Each card yields a dict with:
      - name     : company/member name
      - url      : absolute profile page URL
      - services : list of category strings from badge image keywords
      - location : location text from the card meta element (v1.1.0)

    The ``location`` field is populated from the first element matching
    the ``selectors.card_meta`` selector (e.g. <span class="meta">).
    If the selector is not configured, it falls back to an empty string.
    This provides a Location value even when no postcode regex is set.

    Args:
        soup: BeautifulSoup object for the listing page.
        cfg:  Validated configuration dictionary.

    Returns:
        List of card dicts. Returns [] if no cards match the selector.
    """
    sel = cfg["selectors"]
    base_url = cfg["base_url"]
    badge_map: dict[str, str] = cfg.get("badge_image_keywords", {})
    meta_sel = sel.get("card_meta", "")  # e.g. "span.meta" or ".member-location"

    cards: list[dict] = []
    for item in soup.select(sel["card_container"]):
        link = item.select_one(sel["profile_link"])
        if not link:
            continue

        name_el = item.select_one(sel.get("member_name", "")) if sel.get("member_name") else None
        name = name_el.get_text(strip=True) if name_el else link.get_text(strip=True)

        url = str(link.get("href", "") or "")
        if url and not url.startswith("http"):
            url = base_url.rstrip("/") + "/" + url.lstrip("/")

        # Badge image → service/category list
        services: list[str] = []
        badge_sel = sel.get("badge_images", "")
        if badge_sel:
            for img in item.select(badge_sel):
                src = str(img.get("src", "") or "").lower()
                for keyword, category_name in badge_map.items():
                    if keyword.lower() in src and category_name not in services:
                        services.append(category_name)

        # Location from card meta text (v1.1.0 fix)
        location = ""
        if meta_sel:
            meta_el = item.select_one(meta_sel)
            if meta_el:
                location = meta_el.get_text(strip=True)

        cards.append(
            {
                "name": name,
                "url": url,
                "services": services,
                "location": location,
            }
        )

    return cards


# =============================================================================
# Profile page scraper
# =============================================================================


def scrape_profile(client, card: dict, cfg: dict) -> Optional[dict]:
    """
    Fetch a member profile page and extract contact details.

    Profile fetch uses is_profile=True so fetcher applies the short (6 s)
    timeout — dead company websites do not stall the run.

    Args:
        client: httpx.Client (from fetcher.make_client).
        card:   Card dict with at least name and url keys.
        cfg:    Validated configuration dictionary.

    Returns:
        Record dict with keys Company, Email, Phone, Website, Location,
        Category, Source — or None if the profile page could not be fetched.
    """
    import fetcher as _fetcher

    base_url = cfg["base_url"]
    sel = cfg["selectors"]
    location_re = (
        re.compile(cfg["location_filter_regex"], re.I) if cfg.get("location_filter_regex") else None
    )

    body, status = _fetcher.safe_get(client, card["url"], is_profile=True)
    if not body or status != 200:
        return None

    soup = BeautifulSoup(body, "html.parser")
    email = extract_email(soup)

    if cfg.get("verify_email") and email:
        email = email if _fetcher.smtp_verify(email) else ""

    # External website from the detail section
    website = ""
    detail_sel = sel.get("detail_section", "")
    detail = soup.select_one(detail_sel) if detail_sel else soup
    if not detail:
        detail = soup
    self_domain = base_url.replace("https://", "").replace("http://", "").rstrip("/").split("/")[0]
    for a in detail.find_all("a", href=True):
        href = str(a["href"])
        if href.startswith("//"):
            website = "https:" + href
            break
        if href.startswith("http") and self_domain not in href:
            website = href
            break

    # Location from profile page text via configured regex
    location = ""
    if location_re:
        match = location_re.search(soup.get_text(" "))
        if match:
            location = match.group(0).strip().upper()

    # Phone: tel: link preferred, then regex on page text
    phone_raw = ""
    for a in soup.find_all("a", href=True):
        if str(a["href"]).startswith("tel:"):
            phone_raw = str(a["href"])[4:].strip()
            break

    return {
        "Company": card["name"],
        "Email": email,
        "Phone": clean_phone(phone_raw),
        "Website": website,
        "Location": location,
        "Category": "",  # overwritten by scraper.py from card["services"]
        "Source": cfg.get("source_label", "Directory"),
    }
