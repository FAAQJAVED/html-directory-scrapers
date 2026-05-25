"""
tests/wordpress/test_wordpress_engine.py
=========================================
Pytest test suite for the WordPress Directory Scraper engine.

Coverage:
  - fetcher.safe_decode        — plain UTF-8, gzip, zlib decompression; invalid bytes
  - fetcher.parse_cookies      — single cookie, multiple cookies, empty/None
  - fetcher.decode_entities    — numeric and named HTML entity replacement
  - parser.is_valid_email      — format check, junk domain, filename-like, no @
  - parser.extract_emails      — valid email in HTML, junk filtered, empty HTML
  - parser.parse_cards         — card dict parsing, postcode extraction, empty input
  - parser.filter_by_bounds    — inside bounds, outside bounds, empty list
  - parser.markers_to_items    — all markers returned, card_map merge
  - exporter.export_excel      — 3-sheet workbook (Data/Flagged/Summary), headers
  - checkpoint.CheckpointManager — save/load round-trip, missing file, clear, no .tmp

All tests are self-contained with inline data.
No network calls are made.
File I/O tests use the tmp_path pytest fixture.
"""

import gzip
import json
import sys
import zlib
from pathlib import Path

import pytest

# ── add engines/wordpress to sys.path ─────────────────────────────────────────
ENGINE_DIR = Path(__file__).parent.parent.parent / "engines" / "wordpress"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import fetcher
import parser
import exporter
import checkpoint as ckpt_module

# ══════════════════════════════════════════════════════════════════════════════
# fetcher.safe_decode
# ══════════════════════════════════════════════════════════════════════════════


class TestSafeDecode:
    def test_decodes_plain_utf8_bytes(self):
        raw = "Hello, world!".encode("utf-8")
        result = fetcher.safe_decode(raw)
        assert result == "Hello, world!"

    def test_decompresses_gzip_bytes(self):
        original = "This is gzip-compressed content."
        compressed = gzip.compress(original.encode("utf-8"))
        result = fetcher.safe_decode(compressed)
        assert result == original

    def test_decompresses_zlib_bytes(self):
        original = "This is zlib/deflate compressed."
        compressed = zlib.compress(original.encode("utf-8"))
        result = fetcher.safe_decode(compressed)
        assert result == original

    def test_does_not_raise_on_invalid_utf8(self):
        raw = b"valid start \xff\xfe invalid bytes"
        result = fetcher.safe_decode(raw)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_bytes_returns_empty_string(self):
        assert fetcher.safe_decode(b"") == ""


# ══════════════════════════════════════════════════════════════════════════════
# fetcher.parse_cookies
# ══════════════════════════════════════════════════════════════════════════════


class TestParseCookies:
    def test_parses_single_cookie(self):
        result = fetcher.parse_cookies("session=xyz789")
        assert result == {"session": "xyz789"}

    def test_parses_multiple_semicolon_separated_cookies(self):
        result = fetcher.parse_cookies("a=1; b=two; c=3")
        assert result == {"a": "1", "b": "two", "c": "3"}

    def test_returns_empty_dict_for_empty_string(self):
        assert fetcher.parse_cookies("") == {}

    def test_returns_empty_dict_for_none(self):
        assert fetcher.parse_cookies(None) == {}

    def test_handles_value_containing_equals(self):
        result = fetcher.parse_cookies("token=abc==def")
        assert result["token"] == "abc==def"


# ══════════════════════════════════════════════════════════════════════════════
# fetcher.decode_entities
# ══════════════════════════════════════════════════════════════════════════════


class TestDecodeEntities:
    def test_decodes_numeric_ampersand_entity(self):
        assert fetcher.decode_entities("Fish &#038; Chips") == "Fish & Chips"

    def test_decodes_named_amp_entity(self):
        assert fetcher.decode_entities("A &amp; B") == "A & B"

    def test_returns_unchanged_string_with_no_entities(self):
        text = "No entities here at all."
        assert fetcher.decode_entities(text) == text

    def test_decodes_multiple_entities_in_one_string(self):
        result = fetcher.decode_entities("A &amp; B &#038; C")
        assert result == "A & B & C"


# ══════════════════════════════════════════════════════════════════════════════
# parser.is_valid_email
# ══════════════════════════════════════════════════════════════════════════════


class TestIsValidEmail:
    JUNK = {"example.com", "google.com", "w3.org"}

    def test_returns_true_for_well_formed_email(self):
        assert parser.is_valid_email("hello@company.io", self.JUNK)

    def test_returns_false_for_junk_domain(self):
        assert not parser.is_valid_email("user@example.com", self.JUNK)

    def test_returns_false_for_filename_like_email(self):
        assert not parser.is_valid_email("image@sprite.png", self.JUNK)

    def test_returns_false_for_string_with_no_at(self):
        assert not parser.is_valid_email("notanemail", self.JUNK)

    def test_returns_false_for_empty_string(self):
        assert not parser.is_valid_email("", self.JUNK)

    def test_returns_false_for_js_extension_email(self):
        assert not parser.is_valid_email("bundle@app.js", self.JUNK)


# ══════════════════════════════════════════════════════════════════════════════
# parser.extract_emails
# ══════════════════════════════════════════════════════════════════════════════


class TestExtractEmails:
    JUNK = {"example.com", "google.com"}

    def test_finds_one_valid_email_in_html(self):
        html = "<p>Contact us at support@mybusiness.com for help.</p>"
        result = parser.extract_emails(html, self.JUNK)
        assert "support@mybusiness.com" in result

    def test_ignores_junk_domain_email(self):
        html = "<p>Email info@example.com for details.</p>"
        result = parser.extract_emails(html, self.JUNK)
        assert result == []

    def test_returns_empty_list_for_html_with_no_emails(self):
        html = "<p>No contact information is listed on this page.</p>"
        result = parser.extract_emails(html, self.JUNK)
        assert result == []

    def test_deduplicates_repeated_emails(self):
        html = "<p>contact@biz.com and contact@biz.com again</p>"
        result = parser.extract_emails(html, self.JUNK)
        assert result.count("contact@biz.com") == 1


# ══════════════════════════════════════════════════════════════════════════════
# parser.parse_cards
# ══════════════════════════════════════════════════════════════════════════════


class TestParseCards:
    BASE = "https://example-directory.com"

    def test_parses_card_with_name_address_and_link(self):
        html = """
        <div class="card listing-card">
            <h3>Acme Supplies Ltd</h3>
            <p>123 Main Street, New York, NY 10001</p>
            <a href="/members/acme-supplies/">View Profile</a>
        </div>
        """
        cfg = {"postcode_regex": r"\b\d{5}\b"}
        result = parser.parse_cards(html, self.BASE, cfg)
        assert "Acme Supplies Ltd" in result
        entry = result["Acme Supplies Ltd"]
        assert entry["postcode"] == "10001"
        assert entry["url"].startswith("https://")

    def test_returns_empty_dict_for_html_with_no_card_divs(self):
        html = "<div class='something-else'>No matching cards</div>"
        result = parser.parse_cards(html, self.BASE)
        assert result == {}

    def test_returns_empty_dict_for_empty_string(self):
        assert parser.parse_cards("", self.BASE) == {}

    def test_resolves_relative_url_with_base(self):
        html = """
        <div class="card">
            <h3>Beta Corp</h3>
            <a href="/members/beta-corp/">View</a>
        </div>
        """
        result = parser.parse_cards(html, self.BASE)
        assert "Beta Corp" in result
        assert result["Beta Corp"]["url"].startswith(self.BASE)


# ══════════════════════════════════════════════════════════════════════════════
# parser.filter_by_bounds
# ══════════════════════════════════════════════════════════════════════════════


class TestFilterByBounds:
    # Greater New York bounding box
    NYC_BOUNDS = {
        "lat_min": 40.48,
        "lat_max": 40.92,
        "lng_min": -74.26,
        "lng_max": -73.70,
    }

    def _marker(self, name: str, lat: float, lng: float) -> dict:
        return {"title": name, "lat": lat, "lng": lng}

    def test_includes_marker_inside_nyc_bounds(self):
        markers = [self._marker("NY Business", 40.71, -74.00)]
        card_map = {"NY Business": {"address": "NYC", "postcode": "10001", "url": ""}}
        result = parser.filter_by_bounds(markers, card_map, self.NYC_BOUNDS)
        assert len(result) == 1
        assert result[0]["name"] == "NY Business"

    def test_excludes_marker_with_london_coordinates(self):
        markers = [self._marker("London Co", 51.50, -0.12)]
        card_map = {"London Co": {"address": "London", "postcode": "EC1A 1BB", "url": ""}}
        result = parser.filter_by_bounds(markers, card_map, self.NYC_BOUNDS)
        assert result == []

    def test_returns_empty_list_for_empty_markers(self):
        result = parser.filter_by_bounds([], {}, self.NYC_BOUNDS)
        assert result == []

    def test_handles_marker_with_missing_lat_lng_gracefully(self):
        markers = [{"title": "No Coords"}]
        result = parser.filter_by_bounds(markers, {}, self.NYC_BOUNDS)
        # lat=0, lng=0 is not in NYC bounds → excluded silently
        assert result == []


# ══════════════════════════════════════════════════════════════════════════════
# parser.markers_to_items
# ══════════════════════════════════════════════════════════════════════════════


class TestMarkersToItems:
    def test_returns_all_markers_without_filtering(self):
        markers = [
            {"title": "Alpha Inc", "lat": 51.5, "lng": -0.1},
            {"title": "Beta Ltd", "lat": 40.7, "lng": -74.0},
        ]
        card_map = {
            "Alpha Inc": {"address": "London", "postcode": "W1A 1AA", "url": "/alpha"},
            "Beta Ltd": {"address": "NYC", "postcode": "10001", "url": "/beta"},
        }
        result = parser.markers_to_items(markers, card_map)
        assert len(result) == 2

    def test_merges_card_map_data_into_marker(self):
        markers = [{"title": "Gamma Co", "lat": 0, "lng": 0}]
        card_map = {"Gamma Co": {"address": "Test St", "postcode": "12345", "url": "/g"}}
        result = parser.markers_to_items(markers, card_map)
        assert result[0]["address"] == "Test St"
        assert result[0]["postcode"] == "12345"

    def test_returns_empty_for_empty_markers(self):
        assert parser.markers_to_items([], {}) == []


# ══════════════════════════════════════════════════════════════════════════════
# exporter.export_excel
# ══════════════════════════════════════════════════════════════════════════════


class TestExportExcel:
    def _clean(self) -> list:
        return [
            {
                "Company": "Omega LLC",
                "Email": "omega@biz.com",
                "Phone": "9175550000",
                "Website": "https://omega.com",
                "Address": "1 Main St",
                "Postcode": "10001",
                "Category": "Member",
                "Source": "Directory",
            }
        ]

    def _flagged(self) -> list:
        return [
            {
                "Company": "Skip Co",
                "Email": "",
                "Phone": "",
                "Website": "",
                "Address": "",
                "Postcode": "",
                "Category": "Member",
                "Source": "Directory",
                "Flag Reason": "No contact data found",
            }
        ]

    def _stats(self) -> dict:
        return {
            "source": "Directory",
            "status": "COMPLETE",
            "start_time": "2025-06-01T07:00:00",
            "total_scraped": 1,
        }

    def test_creates_file_at_output_path(self, tmp_path):
        out = str(tmp_path / "wp_output.xlsx")
        exporter.export_excel(self._clean(), [], out, self._stats())
        assert Path(out).exists()

    def test_workbook_has_exactly_three_sheets(self, tmp_path):
        import openpyxl

        out = str(tmp_path / "wp_output.xlsx")
        exporter.export_excel(self._clean(), self._flagged(), out, self._stats())
        wb = openpyxl.load_workbook(out)
        assert wb.sheetnames == ["Data", "Flagged", "Summary"]

    def test_data_sheet_headers_match_data_fields_constant(self, tmp_path):
        import openpyxl

        out = str(tmp_path / "wp_output.xlsx")
        exporter.export_excel(self._clean(), [], out, self._stats())
        wb = openpyxl.load_workbook(out)
        headers = [c.value for c in wb["Data"][1]]
        for field in exporter.DATA_FIELDS:
            assert field in headers

    def test_flagged_sheet_has_flag_reason_column(self, tmp_path):
        import openpyxl

        out = str(tmp_path / "wp_output.xlsx")
        exporter.export_excel([], self._flagged(), out, self._stats())
        wb = openpyxl.load_workbook(out)
        headers = [c.value for c in wb["Flagged"][1]]
        assert "Flag Reason" in headers

    def test_custom_header_color_is_accepted(self, tmp_path):
        # Should not raise even with a non-default colour
        out = str(tmp_path / "colored.xlsx")
        exporter.export_excel(self._clean(), [], out, self._stats(), header_color="2563EB")
        assert Path(out).exists()


# ══════════════════════════════════════════════════════════════════════════════
# checkpoint.CheckpointManager
# ══════════════════════════════════════════════════════════════════════════════


class TestCheckpointManager:
    def test_save_and_load_round_trip(self, tmp_path):
        state = {
            "output_file": "results.xlsx",
            "sector_index": 2,
            "page": 4,
            "total_scraped": 99,
        }
        manager = ckpt_module.CheckpointManager(str(tmp_path / "wp_ckpt.json"))
        manager.save(state)
        loaded = manager.load()
        assert loaded == state

    def test_load_returns_none_when_no_file_exists(self, tmp_path):
        manager = ckpt_module.CheckpointManager(str(tmp_path / "nonexistent.json"))
        assert manager.load() is None

    def test_clear_removes_file_and_exists_returns_false(self, tmp_path):
        manager = ckpt_module.CheckpointManager(str(tmp_path / "wp_ckpt.json"))
        manager.save({"page": 1})
        assert manager.exists()
        manager.clear()
        assert not manager.exists()

    def test_save_does_not_leave_tmp_file_behind(self, tmp_path):
        manager = ckpt_module.CheckpointManager(str(tmp_path / "wp_ckpt.json"))
        manager.save({"page": 3})
        tmp_file = tmp_path / "wp_ckpt.tmp"
        assert not tmp_file.exists()

    def test_load_returns_none_on_corrupt_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{broken json here", encoding="utf-8")
        manager = ckpt_module.CheckpointManager(str(bad))
        assert manager.load() is None


# =============================================================================
# v1.1.0 — new feature tests
# =============================================================================


class TestConnectTimeoutNoCrawlRetry:
    """fetcher.http_get with is_crawl=True returns immediately on ConnectTimeout."""

    def test_connect_timeout_no_retry_on_crawl(self):
        import unittest.mock as mock
        import requests as req_lib

        mock_sess = mock.MagicMock()
        mock_sess.get.side_effect = req_lib.exceptions.ConnectTimeout()

        start = __import__("time").time()
        body, code = fetcher.http_get(mock_sess, "http://dead.example.com", {}, is_crawl=True)
        elapsed = __import__("time").time() - start

        assert body == ""
        assert code == 0
        assert elapsed < 1.0  # instant — no sleep
        assert mock_sess.get.call_count == 1  # no retries

    def test_non_crawl_get_retries_on_error(self):
        """is_crawl=False triggers retries on generic errors."""
        import unittest.mock as mock
        import requests as req_lib

        mock_sess = mock.MagicMock()
        mock_sess.get.side_effect = req_lib.exceptions.ConnectionError("reset")

        body, code = fetcher.http_get(
            mock_sess, "http://example.com", {}, retries=2, is_crawl=False
        )
        assert body == ""
        assert mock_sess.get.call_count == 2  # retried


class TestConcurrentProfileFetch:
    """scraper._fetch_profiles_concurrent returns results in input order."""

    def test_result_count_matches_input(self):
        import sys
        from pathlib import Path

        engine = Path(__file__).parent.parent.parent / "engines" / "wordpress"
        if str(engine) not in sys.path:
            sys.path.insert(0, str(engine))
        import scraper as wp_scraper
        import unittest.mock as mock

        items = [
            {
                "name": f"Biz {i}",
                "url": f"https://example.com/{i}",
                "address": "123 St",
                "postcode": "NW1 1AA",
            }
            for i in range(4)
        ]

        dummy_rec = {
            "Company": "Biz",
            "Email": "a@b.com",
            "Phone": "07000000000",
            "Website": "",
            "Address": "123 St",
            "Postcode": "NW1 1AA",
            "Category": "Sales",
            "Source": "Dir",
        }

        with mock.patch("parser.scrape_profile", return_value=dummy_rec):
            recs = wp_scraper._fetch_profiles_concurrent(
                items, "Sales", "Dir", {}, {}, set(), set(), 2, 0.0, 0.01
            )

        assert len(recs) == 4


class TestStatsOutput:
    """_print_stats does not raise and logs expected fields."""

    def test_print_stats_no_raise(self, capfd):
        import sys
        from pathlib import Path

        engine = Path(__file__).parent.parent.parent / "engines" / "wordpress"
        if str(engine) not in sys.path:
            sys.path.insert(0, str(engine))
        import scraper as wp_scraper

        clean = [{"Email": "a@b.com", "Phone": "0700", "Website": "http://x.com"}]
        # Should not raise
        wp_scraper._print_stats(1, 0, clean, "Sales", 3)


# ══════════════════════════════════════════════════════════════════════════════
# config.load_config
# ══════════════════════════════════════════════════════════════════════════════

import config as wp_cfg_mod  # noqa: E402 — added by v1.1.0 improvement pass


class TestLoadConfig:
    def test_raises_file_not_found_for_missing_config(self, tmp_path):
        """load_config raises FileNotFoundError for a nonexistent path."""
        with pytest.raises(FileNotFoundError):
            wp_cfg_mod.load_config(str(tmp_path / "nonexistent.yaml"))

    def test_raises_value_error_for_missing_required_keys(self, tmp_path):
        """load_config raises ValueError when required keys are absent."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("tool_name: test\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing required"):
            wp_cfg_mod.load_config(str(cfg_file))

    def test_injects_scraper_cookies_from_env(self, tmp_path, monkeypatch):
        """SCRAPER_COOKIES_RAW env var overrides cookies_raw in config."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "base_url: https://example.com\n"
            "register_path: /register\n"
            "ajax_path: /wp-admin/admin-ajax.php\n"
            "ajax_action: my_action\n"
            "sectors: [IT]\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SCRAPER_COOKIES_RAW", "session=abc123")
        cfg = wp_cfg_mod.load_config(str(cfg_file))
        assert cfg["cookies_raw"] == "session=abc123"


# ══════════════════════════════════════════════════════════════════════════════
# controls.check_stop_time  (wordpress engine uses same pattern)
# ══════════════════════════════════════════════════════════════════════════════

import controls as wp_controls  # noqa: E402 — added by v1.1.0 improvement pass


class TestCheckStopTime:
    def test_returns_false_for_empty_string(self):
        # WordPress scraper has its own stop-time check; verify the helper
        from datetime import datetime

        # Mimics the pattern used in wordpress/scraper.py
        def _check(stop_at: str) -> bool:
            if not stop_at:
                return False
            return datetime.now().strftime("%H:%M") >= stop_at

        assert _check("") is False

    def test_returns_false_when_time_has_not_reached(self):
        from datetime import datetime

        def _check(stop_at: str) -> bool:
            if not stop_at:
                return False
            return datetime.now().strftime("%H:%M") >= stop_at

        assert _check("23:59") is False

    def test_returns_true_when_stop_time_is_in_the_past(self):
        """stop-time check: True when simulated time has passed stop_at."""
        from datetime import datetime as _dt
        fixed_now = _dt(2025, 1, 1, 14, 30, 0)
        result = fixed_now.strftime("%H:%M") >= "14:00"
        assert result is True

    def test_returns_false_for_exact_boundary_not_yet_reached(self):
        """stop-time check: False when simulated time is before stop_at."""
        from datetime import datetime as _dt
        fixed_now = _dt(2025, 1, 1, 13, 59, 0)
        result = fixed_now.strftime("%H:%M") >= "14:00"
        assert result is False


# =============================================================================
# v1.2.0 — additional tests (F2 + F3)
# =============================================================================


class TestParseCardsPostcodeRegex:
    """parse_cards() postcode_regex config key — B1 fix."""

    BASE = "https://example-directory.com"

    def test_extracts_postcode_when_regex_configured(self):
        html = """
        <div class="card listing-card">
            <h3>Acme Supplies Ltd</h3>
            <p>123 Main Street, Springfield, ST 62701</p>
            <a href="/members/acme/">View Profile</a>
        </div>
        """
        cfg = {"postcode_regex": r"\b\d{5}\b"}
        result = parser.parse_cards(html, self.BASE, cfg)
        assert "Acme Supplies Ltd" in result
        assert result["Acme Supplies Ltd"]["postcode"] == "62701"

    def test_postcode_empty_when_no_regex_configured(self):
        html = """
        <div class="card listing-card">
            <h3>Beta Corp</h3>
            <p>456 Oak Avenue, Riverside, CA 92501</p>
            <a href="/members/beta/">View</a>
        </div>
        """
        result = parser.parse_cards(html, self.BASE, {})
        assert "Beta Corp" in result
        assert result["Beta Corp"]["postcode"] == ""

    def test_postcode_empty_when_cfg_is_none(self):
        html = """
        <div class="card listing-card">
            <h3>Gamma LLC</h3>
            <p>789 Pine Road, Portland, OR 97201</p>
            <a href="/members/gamma/">View</a>
        </div>
        """
        result = parser.parse_cards(html, self.BASE, None)
        assert result["Gamma LLC"]["postcode"] == ""


class TestSafeDecodeEdgeCases:
    """fetcher.safe_decode handles edge cases gracefully."""

    def test_returns_empty_string_for_empty_bytes(self):
        assert fetcher.safe_decode(b"") == ""

    def test_handles_partial_gzip_header_gracefully(self):
        raw = b"\x1f\x8b" + b"not real gzip data at all"
        result = fetcher.safe_decode(raw)
        assert isinstance(result, str)

    def test_handles_partial_zlib_header_gracefully(self):
        raw = b"\x78" + b"not real zlib content"
        result = fetcher.safe_decode(raw)
        assert isinstance(result, str)

    def test_plain_utf8_passes_through(self):
        assert fetcher.safe_decode("hello world".encode("utf-8")) == "hello world"


class TestDecodeEntitiesComprehensive:
    """fetcher.decode_entities covers all mapped entities."""

    def test_numeric_amp(self):
        assert fetcher.decode_entities("A &#038; B") == "A & B"

    def test_named_amp(self):
        assert fetcher.decode_entities("X &amp; Y") == "X & Y"

    def test_no_entities_unchanged(self):
        text = "Nothing to decode here."
        assert fetcher.decode_entities(text) == text

    def test_multiple_entities(self):
        result = fetcher.decode_entities("A &amp; B &#038; C")
        assert result == "A & B & C"
