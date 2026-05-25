"""
scraper.py
==========
CLI entry point and main orchestration loop for the WordPress Directory Scraper.

Responsibilities:
  - Argument parsing (--config, --fresh, --workers flags)
  - Logging initialisation (console + file handler)
  - Banner display
  - Sector-by-sector AJAX scrape loop with concurrent profile fetching
  - Keyboard controls: P pause / R resume / S stop / W stats
  - tqdm live progress bar on the profile-fetch inner loop
  - Nonce extraction at startup + mid-run refresh on expiry
  - Checkpoint save/load coordination
  - Final summary and Excel flush

v1.1.0 changes
--------------
  - ThreadPoolExecutor(max_workers=3) for concurrent profile fetches
  - Keyboard P/R/S/W controls fully wired up
  - tqdm progress bar on inner profile loop
  - W key prints live stats snapshot to log
  - Website crawl timeout reduced to 6 s via fetcher; ConnectTimeout = no retry
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

try:
    from tqdm import tqdm as _tqdm

    _TQDM_OK = True
except ImportError:
    _TQDM_OK = False

INVOCATION_DIR = Path.cwd()
ENGINE_DIR = Path(__file__).parent.resolve()

os.chdir(ENGINE_DIR)
load_dotenv(ENGINE_DIR / ".env")

import config as cfg_module
import fetcher
import parser
import exporter
import checkpoint as ckpt_module
import controls

log = logging.getLogger("scraper")


# =============================================================================
# Argument parsing
# =============================================================================


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns:
        Parsed namespace with .config, .fresh, and .workers attributes.
    """
    p = argparse.ArgumentParser(
        description="Scrape any WordPress directory via admin-ajax.php to 3-sheet Excel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Keyboard controls (press key while running — no Enter needed):\n"
            "  P — pause after current AJAX page\n"
            "  R — resume from pause\n"
            "  S / Q — save checkpoint and quit\n"
            "  W — print live stats to log\n"
        ),
    )
    p.add_argument("--config", default="config.yaml", metavar="PATH")
    p.add_argument("--fresh", action="store_true")
    p.add_argument(
        "--workers",
        type=int,
        default=3,
        metavar="N",
        help="Concurrent profile-page workers (default: 3)",
    )
    return p.parse_args()


# =============================================================================
# Logging setup
# =============================================================================


def setup_logging(log_file: str = "scraper.log") -> None:
    """
    Configure the 'scraper' logger with console and file handlers.

    Routes console output through tqdm.write() when tqdm is active.

    Args:
        log_file: Path to the output log file.
    """
    logger = logging.getLogger("scraper")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")

    if _TQDM_OK:

        class TqdmHandler(logging.StreamHandler):
            def emit(self, record):
                try:
                    from tqdm import tqdm as _t

                    _t.write(self.format(record))
                except Exception:
                    super().emit(record)

        ch = TqdmHandler()
    else:
        ch = logging.StreamHandler(sys.stdout)

    ch.setFormatter(fmt)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(ch)
    logger.addHandler(fh)


# =============================================================================
# Helpers
# =============================================================================


def _print_stats(
    total_scraped: int,
    flagged: int,
    clean_rows: list,
    sector_name: str,
    page: int,
) -> None:
    """
    Print a live stats snapshot to the log (triggered by W key).

    Args:
        total_scraped: Clean records saved so far.
        flagged:       Flagged records so far.
        clean_rows:    List of clean record dicts.
        sector_name:   Current sector name.
        page:          Current AJAX page number.
    """
    with_email = sum(1 for r in clean_rows if r.get("Email"))
    with_phone = sum(1 for r in clean_rows if r.get("Phone"))
    with_website = sum(1 for r in clean_rows if r.get("Website"))
    log.info(
        "STATS | saved: %d | flagged: %d | email: %d | phone: %d | web: %d | sector: %s | p%d | %s",
        total_scraped,
        flagged,
        with_email,
        with_phone,
        with_website,
        sector_name,
        page,
        fetcher.elapsed(),
    )


def _fetch_profiles_concurrent(
    items: list[dict],
    category: str,
    source_label: str,
    headers: dict,
    cfg: dict,
    junk_domains: set,
    skip_domains: set,
    n_workers: int,
    delay_min: float,
    delay_max: float,
) -> list[dict]:
    """
    Fetch multiple WordPress profile pages concurrently.

    Each worker gets its own requests.Session to avoid shared-state issues.
    Results are returned in the same order as the input items list.

    Args:
        items:        List of business item dicts from filter_by_bounds / markers_to_items.
        category:     Category label for this sector.
        source_label: Value for the Source column.
        headers:      HTTP headers dict.
        cfg:          Configuration dict.
        junk_domains: Email domains to reject.
        skip_domains: Website domains to exclude.
        n_workers:    Number of concurrent threads.
        delay_min:    Minimum per-thread delay before fetch.
        delay_max:    Maximum per-thread delay before fetch.

    Returns:
        List of record dicts in input order (may have empty Email/Phone).
    """
    results: dict[int, dict] = {}

    def _fetch_one(idx: int, item: dict) -> tuple[int, dict]:
        time.sleep(random.uniform(delay_min, delay_max))
        thread_sess = fetcher.make_session(cfg)
        try:
            rec = parser.scrape_profile(
                thread_sess,
                item,
                category,
                source_label,
                headers,
                cfg,
                junk_domains,
                skip_domains,
            )
        except Exception as exc:
            log.debug("Profile error %s: %s", item.get("name", "?"), exc)
            rec = {
                "Company": item["name"],
                "Email": "",
                "Phone": "",
                "Website": "",
                "Address": item.get("address", ""),
                "Postcode": item.get("postcode", ""),
                "Category": category,
                "Source": source_label,
            }
        finally:
            try:
                thread_sess.close()
            except Exception:
                pass
        return idx, rec

    with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="wp_profile") as pool:
        futures = {pool.submit(_fetch_one, i, item): i for i, item in enumerate(items)}
        for future in as_completed(futures):
            try:
                idx, rec = future.result()
                results[idx] = rec
            except Exception as exc:
                i = futures[future]
                log.warning("Thread error item #%d: %s", i, exc)
                it = items[i]
                results[i] = {
                    "Company": it["name"],
                    "Email": "",
                    "Phone": "",
                    "Website": "",
                    "Address": it.get("address", ""),
                    "Postcode": it.get("postcode", ""),
                    "Category": category,
                    "Source": source_label,
                }

    return [results[i] for i in range(len(items))]


# =============================================================================
# Main
# =============================================================================


def main() -> None:
    """Main entry point for the WordPress Directory Scraper."""
    args = parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        alt_path = INVOCATION_DIR / args.config
        if alt_path.exists():
            config_path = alt_path

    cfg = cfg_module.load_config(str(config_path))
    setup_logging(cfg.get("log_file", "scraper.log"))

    base_url = cfg["base_url"].rstrip("/")
    register_url = base_url + cfg["register_path"]
    ajax_url = base_url + cfg["ajax_path"]
    ckpt_path = cfg.get("checkpoint_file", "scraper_checkpoint.json")
    output_prefix = cfg.get("output_prefix", "Results")
    sectors = cfg["sectors"]
    skip_domains = set(cfg.get("skip_domains", []))
    junk_domains = set(cfg.get("junk_domains", ["example.com", "google.com", "w3.org"]))
    source_label = cfg.get("source_label", "Directory")
    bounds = cfg.get("geo_bounds")
    max_pages = int(cfg.get("max_pages", 9999))
    delay_min = float(cfg.get("delay_min", 1.0))
    delay_max = float(cfg.get("delay_max", 2.0))
    n_workers = min(max(1, args.workers), 8)

    print()
    print("=" * 60)
    print("  WordPress Directory Scraper")
    print(f"  Target   : {base_url}")
    print(f"  Sectors  : {len(sectors)}")
    print(f"  Workers  : {n_workers} concurrent profile threads")
    print(f"  Keys     : P=pause  R=resume  S=stop  W=stats")
    print("=" * 60)
    print()

    # -- Checkpoint ------------------------------------------------------------
    checkpoint = ckpt_module.CheckpointManager(ckpt_path)
    if args.fresh:
        checkpoint.clear()
        log.warning("--fresh: checkpoint cleared")

    cp = checkpoint.load()
    resuming = bool(cp)

    if resuming:
        output_file = cp.get(
            "output_file", f"{output_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        )
        start_si = cp.get("sector_index", 0)
        start_page = cp.get("page", 1)
        seen = set(tuple(x) for x in cp.get("seen", []))
        total_scraped = cp.get("total_scraped", 0)
        clean_rows = cp.get("clean_rows", [])
        flagged_rows = cp.get("flagged_rows", [])
        log.info(
            "RESUMING from sector #%d, page %d (%d saved)", start_si + 1, start_page, total_scraped
        )
    else:
        output_file = f"{output_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        start_si = 0
        start_page = 1
        seen: set = set()
        clean_rows: list = []
        flagged_rows: list = []
        total_scraped = 0
        log.info("Starting fresh run")

    log.info("Output   : %s", output_file)
    log.info("Sectors  : %s", [s["name"] for s in sectors])
    log.info("Workers  : %d concurrent threads", n_workers)
    log.info("Crawl    : %s", cfg.get("crawl_websites", False))
    if bounds:
        log.info(
            "Bounds   : lat [%s-%s] lng [%s-%s]",
            bounds.get("lat_min"),
            bounds.get("lat_max"),
            bounds.get("lng_min"),
            bounds.get("lng_max"),
        )
    print()

    # -- Session + nonce -------------------------------------------------------
    sess = fetcher.make_session(cfg)
    headers = fetcher.build_headers(cfg)
    nonce = fetcher.get_nonce(sess, register_url, headers)
    if nonce:
        log.info("Nonce obtained (%s...)", nonce[:8])
    else:
        log.warning("Nonce not found — AJAX may not work correctly")

    ajax_headers = fetcher.build_ajax_headers(cfg, register_url)
    controller = controls.InputController()
    controller.start()

    run_stats = {
        "start_time": datetime.now().isoformat(),
        "source": source_label,
        "status": "PARTIAL",
    }
    stop_flag = False

    try:
        for si, sector in enumerate(sectors):
            if si < start_si:
                continue

            sector_name = sector["name"]
            category = sector["category"]
            log.info("=== Sector %d/%d: %s ===", si + 1, len(sectors), sector_name)

            page = start_page if si == start_si else 1
            start_page = 1
            empty_streak = 0

            while page <= max_pages:

                # -- keyboard controls (P/S act via threading.Event in listener) -----
                key = controller.get_key()
                if key == "w":
                    _print_stats(total_scraped, len(flagged_rows), clean_rows, sector_name, page)

                if controller.is_stopped():
                    log.warning("STOP key — saving and exiting")
                    stop_flag = True
                    break

                if controller.is_paused():
                    log.warning("PAUSED — press R to resume")
                    controls.beep_raw(600, 200)
                    while controller.is_paused():
                        if controller.is_stopped():
                            stop_flag = True
                            break
                        time.sleep(0.5)
                    if not stop_flag:
                        log.info("RESUMED — continuing")
                        controls.beep_raw(900, 200)

                data = fetcher.post_ajax(
                    sess, cfg, ajax_url, ajax_headers, sector_name, page, nonce
                )

                # -- nonce refresh ---------------------------------------------
                if not data or not data.get("success", True):
                    log.warning("Nonce may have expired — refreshing...")
                    nonce = fetcher.get_nonce(sess, register_url, headers)
                    if nonce:
                        log.info("New nonce (%s...)", nonce[:8])
                        ajax_headers = fetcher.build_ajax_headers(cfg, register_url)
                        data = fetcher.post_ajax(
                            sess, cfg, ajax_url, ajax_headers, sector_name, page, nonce
                        )
                    if not data:
                        log.error("Still empty after nonce refresh — skipping sector")
                        break

                markers = data.get("data", {}).get("markers", [])
                cards_html = data.get("data", {}).get("cards", "")
                total_pages = int(data.get("data", {}).get("total_pages", 1))

                if not markers:
                    empty_streak += 1
                    if empty_streak >= 3:
                        log.warning("3 empty pages — sector complete")
                        break
                    page += 1
                    continue
                empty_streak = 0

                card_map = parser.parse_cards(cards_html, base_url, cfg)

                if bounds:
                    page_items = parser.filter_by_bounds(markers, card_map, bounds)
                    filter_note = f"{len(page_items)}/{len(markers)} in bounds"
                else:
                    page_items = parser.markers_to_items(markers, card_map)
                    filter_note = f"{len(page_items)} items"

                eta_str = fetcher.rate_and_eta(
                    total_scraped, max(total_pages * 9, total_scraped + 1)
                )
                log.info(
                    "  p%d/%d | %s | saved: %d%s",
                    page,
                    total_pages,
                    filter_note,
                    total_scraped,
                    f" | {eta_str}" if eta_str else "",
                )

                # -- deduplicate -----------------------------------------------
                new_items = []
                for item in page_items:
                    k = (item["name"].lower().strip(), item["postcode"].upper().strip())
                    if k not in seen:
                        seen.add(k)
                        new_items.append(item)

                # -- concurrent profile fetch ----------------------------------
                recs = _fetch_profiles_concurrent(
                    new_items,
                    category,
                    source_label,
                    headers,
                    cfg,
                    junk_domains,
                    skip_domains,
                    n_workers,
                    delay_min,
                    delay_max,
                )

                # -- process results with tqdm ---------------------------------
                if _TQDM_OK and recs:
                    iter_recs = _tqdm(
                        zip(new_items, recs),
                        total=len(recs),
                        desc=f"  p{page}/{total_pages}",
                        unit="rec",
                        leave=False,
                        dynamic_ncols=True,
                    )
                else:
                    iter_recs = zip(new_items, recs)

                for item, rec in iter_recs:
                    inner_key = controller.get_key()
                    if inner_key == "w":
                        _print_stats(
                            total_scraped, len(flagged_rows), clean_rows, sector_name, page
                        )
                    if controller.is_stopped():
                        stop_flag = True
                        break

                    if not rec.get("Email") and not rec.get("Phone"):
                        flagged_rows.append({**rec, "Flag Reason": "No contact data found"})
                    else:
                        clean_rows.append(rec)
                        total_scraped += 1
                        fetcher.record_scrape()

                    em = rec.get("Email") or "—"
                    ph = rec.get("Phone") or "—"
                    web = "Y" if rec.get("Website") else "—"
                    log.info(
                        "  %4d. %-42s  📧%-28s  ☎%s  🌐%s",
                        total_scraped,
                        item["name"][:42],
                        em[:28],
                        ph,
                        web,
                    )

                if stop_flag:
                    break

                # -- geo-excluded → Flagged ------------------------------------
                if bounds:
                    for m in markers:
                        mname = parser.decode_entities(m.get("title", ""))
                        if not any(mname == it["name"] for it in page_items):
                            flagged_rows.append(
                                {
                                    "Company": mname,
                                    "Email": "",
                                    "Phone": "",
                                    "Website": "",
                                    "Address": "",
                                    "Postcode": "",
                                    "Category": category,
                                    "Source": source_label,
                                    "Flag Reason": "Outside geographic bounds",
                                }
                            )

                # -- persist ---------------------------------------------------
                exporter.export_excel(
                    clean_rows,
                    flagged_rows,
                    output_file,
                    {**run_stats, "total_scraped": total_scraped},
                    header_color=cfg.get("output", {}).get("header_color", "1F4E79"),
                )
                checkpoint.save(
                    {
                        "output_file": output_file,
                        "sector_index": si,
                        "page": page + 1,
                        "seen": [list(s) for s in seen],
                        "total_scraped": total_scraped,
                        "clean_rows": clean_rows,
                        "flagged_rows": flagged_rows,
                    }
                )

                if page >= total_pages:
                    log.info("  Sector complete (%d pages)", total_pages)
                    break
                page += 1

            if stop_flag:
                break
            print()

    except KeyboardInterrupt:
        log.warning("Stopped by Ctrl+C — checkpoint saved")
        controls.beep_raw(400, 300)

    except Exception as exc:
        log.error("Unexpected error: %s", exc)
        import traceback

        traceback.print_exc()

    finally:
        controller.stop()
        sess.close()

    # -- final write -----------------------------------------------------------
    run_stats["status"] = "COMPLETE" if not stop_flag else "PARTIAL"
    exporter.export_excel(
        clean_rows,
        flagged_rows,
        output_file,
        {**run_stats, "total_scraped": total_scraped},
        header_color=cfg.get("output", {}).get("header_color", "1F4E79"),
    )

    elapsed_str = fetcher.elapsed()
    print()
    print("=" * 60)
    log.info("  Records scraped : %d", total_scraped)
    log.info("  Flagged         : %d", len(flagged_rows))
    log.info("  With email      : %d", sum(1 for r in clean_rows if r.get("Email")))
    log.info("  With phone      : %d", sum(1 for r in clean_rows if r.get("Phone")))
    log.info("  With website    : %d", sum(1 for r in clean_rows if r.get("Website")))
    log.info("  Time elapsed    : %s", elapsed_str)
    log.info("  Output file     : %s", os.path.abspath(output_file))
    print("=" * 60)

    if total_scraped > 0:
        controls.beep_raw(1000, 200)


if __name__ == "__main__":
    main()
