"""
scraper.py
==========
CLI entry point and main orchestration loop for the HTML Directory Scraper.

Responsibilities:
  - Argument parsing (--config, --fresh flags)
  - Logging initialisation (console + file handler)
  - Banner display
  - Service-by-service → listing pages → concurrent profile fetches
  - Keyboard controls: P pause / R resume / S stop / W stats
  - tqdm live progress bar on the profile-fetch inner loop
  - Checkpoint save/load coordination
  - Final summary and Excel flush

v1.1.0 changes
--------------
  - ThreadPoolExecutor(max_workers=3) for concurrent profile fetches
  - Keyboard P/R/S/W controls wired up via controller.get_key()
  - tqdm progress bar on the inner profile loop
  - Category now taken from card["services"][0] (badge images) when available
  - Location populated from listing-card meta text (no regex required)
  - Website crawl timeout reduced to 6 s; ConnectTimeoutError skips retries
"""

from __future__ import annotations

import argparse
import logging
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    from tqdm import tqdm as _tqdm

    _TQDM_OK = True
except ImportError:
    _TQDM_OK = False

# Capture original working directory to resolve CLI paths correctly
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
        Parsed namespace with .config and .fresh attributes.
    """
    p = argparse.ArgumentParser(
        description="Scrape any paginated HTML business directory to a styled Excel file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Keyboard controls (press key while running — no Enter needed):\n"
            "  P — pause after current page\n"
            "  R — resume from pause\n"
            "  S — save checkpoint and stop\n"
            "  W — print live stats to log\n"
            "\n"
            "File controls (write to command.txt in engine folder):\n"
            "  echo pause  > command.txt\n"
            "  echo resume > command.txt\n"
            "  echo stop   > command.txt\n"
            "  echo fresh  > command.txt\n"
        ),
    )
    p.add_argument(
        "--config",
        default="config.yaml",
        metavar="PATH",
        help="Path to YAML configuration file (default: config.yaml)",
    )
    p.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore any existing checkpoint and start from scratch",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=3,
        metavar="N",
        help="Concurrent profile-page workers (default: 3, max recommended: 5)",
    )
    return p.parse_args()


# =============================================================================
# Logging setup
# =============================================================================


def setup_logging(log_file: str = "scraper.log") -> None:
    """
    Configure the 'scraper' logger with console and file handlers.

    When tqdm is active, log output is routed through tqdm.write() to avoid
    breaking the progress bar. Console handler is replaced accordingly.

    Args:
        log_file: Path to the output log file.
    """
    logger = logging.getLogger("scraper")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return

    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")

    ch: logging.Handler
    if _TQDM_OK:
        # Route console output through tqdm.write so bar is not broken
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


def _category_for_service(service: str, categories: list) -> str:
    """
    Return the category name that corresponds to a service slug.

    Args:
        service:    Service/sector string from ``all_services`` config list.
        categories: List of ``{name: ...}`` dicts from config.

    Returns:
        Matching category name string, or first category as fallback.
    """
    names = [c["name"] for c in categories]
    if service in names:
        return service
    for name in names:
        if name.lower() == service.lower():
            return str(name)
    return str(names[0]) if names else service


def _print_stats(
    total_scraped: int,
    flagged: int,
    clean_rows: list,
    svc_label: str,
    page: int,
) -> None:
    """
    Print a live stats summary to the log (triggered by W key).

    Args:
        total_scraped: Clean records saved so far.
        flagged:       Flagged records so far.
        clean_rows:    List of clean record dicts.
        svc_label:     Current service label.
        page:          Current listing page.
    """
    with_email = sum(1 for r in clean_rows if r.get("Email"))
    with_website = sum(1 for r in clean_rows if r.get("Website"))
    log.info(
        "STATS | saved: %d | flagged: %d | email: %d | web: %d | svc: %s | p%d | %s",
        total_scraped,
        flagged,
        with_email,
        with_website,
        svc_label,
        page,
        fetcher.elapsed(),
    )


# =============================================================================
# Concurrent profile fetch
# =============================================================================


def _fetch_profiles_concurrent(
    cards: list[dict],
    cfg: dict,
    n_workers: int,
    delay_min: float,
    delay_max: float,
) -> list[tuple[dict, dict | None]]:
    """
    Fetch multiple profile pages concurrently using a thread pool.

    Each worker gets its own httpx.Client to avoid connection-pool contention.
    Results are returned in the same order as the input cards list.

    Args:
        cards:      List of card dicts from parse_cards (must have url, name).
        cfg:        Validated configuration dict.
        n_workers:  Number of concurrent threads (recommended: 2-5).
        delay_min:  Minimum random delay before each fetch (per thread).
        delay_max:  Maximum random delay before each fetch (per thread).

    Returns:
        List of (card, record_or_None) tuples in input order.
    """
    results: dict[int, tuple[dict, dict | None]] = {}

    def _fetch_one(idx: int, card: dict) -> tuple[int, dict, dict | None]:
        time.sleep(random.uniform(delay_min, delay_max))
        # Each thread uses its own client to avoid shared-state issues
        thread_client = fetcher.make_client(cfg)
        try:
            rec = parser.scrape_profile(thread_client, card, cfg)
        except Exception as exc:
            log.debug("Profile fetch error for %s: %s", card.get("name", "?"), exc)
            rec = None
        finally:
            try:
                thread_client.close()
            except Exception:
                pass
        return idx, card, rec

    with ThreadPoolExecutor(max_workers=n_workers, thread_name_prefix="profile") as pool:
        futures = {pool.submit(_fetch_one, i, card): i for i, card in enumerate(cards)}
        for future in as_completed(futures):
            try:
                idx, card, rec = future.result()
                results[idx] = (card, rec)
            except Exception as exc:
                i = futures[future]
                log.warning("Thread error card #%d: %s", i, exc)
                results[i] = (cards[i], None)

    return [results[i] for i in range(len(cards))]


# =============================================================================
# Main
# =============================================================================


def main() -> None:
    """
    Main entry point.

    Loads configuration, creates the HTTP client, resumes or starts a
    checkpoint, then runs the service-first scrape loop with concurrent
    profile fetching and live keyboard controls.
    """
    args = parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        alt_path = INVOCATION_DIR / args.config
        if alt_path.exists():
            config_path = alt_path

    cfg = cfg_module.load_config(str(config_path))
    setup_logging(cfg.get("log_file", "scraper.log"))

    # Wire circuit-breaker callback (avoids circular import in fetcher.py)
    fetcher.set_circuit_break_callback(controls._trigger_pause)

    # Push config-driven timeouts into fetcher module
    fetcher.set_timeouts(
        cfg.get("profile_timeout_seconds", 6),
        cfg.get("timeout_seconds", 25),
    )

    # -- Config values ---------------------------------------------------------
    tool_name = cfg.get("tool_name", "HTML Directory Scraper")
    prefix = cfg.get("output_prefix", "DirectoryExport")
    date_str = datetime.now().strftime("%Y%m%d")
    output_file = f"{prefix}_{date_str}.xlsx"
    ckpt_path = cfg.get("checkpoint_file", "checkpoint.json")
    page_size = int(cfg.get("page_size", 10))
    delay_min = float(cfg.get("delay_min", 1.0))
    delay_max = float(cfg.get("delay_max", 2.5))
    stop_at = cfg.get("stop_at", "")
    base_url = cfg["base_url"]
    list_url = base_url + cfg["list_path"]
    categories = cfg["categories"]
    all_services = cfg.get("all_services", [])
    service_param = cfg.get("service_param_name", "service")
    page_param = cfg.get("page_param_name", "page")
    max_pages = int(cfg.get("max_pages", 9999))
    base_params = dict(cfg.get("base_params", {}))
    n_workers = min(max(1, args.workers), 8)  # cap at 8 for safety

    # -- Banner ----------------------------------------------------------------
    print("=" * 60)
    print(f"  {tool_name}")
    print(f"  Target   : {base_url}")
    print(f"  Services : {', '.join(all_services) if all_services else '(all)'}")
    print(f"  Workers  : {n_workers} concurrent profile threads")
    print(f"  Keys     : P=pause  R=resume  S=stop  W=stats")
    print("=" * 60)
    print()

    # -- Checkpoint / fresh-start ----------------------------------------------
    checkpoint = ckpt_module.CheckpointManager(ckpt_path)
    controls.handle_fresh_command(checkpoint)

    if args.fresh:
        checkpoint.clear()
        log.warning("--fresh: checkpoint cleared")

    cp: dict = checkpoint.load() or {}

    clean_rows: list = cp.get("clean_rows", [])
    flagged_rows: list = cp.get("flagged_rows", [])
    seen_urls: set = set(cp.get("seen_urls", []))
    total_scraped: int = cp.get("total_scraped", 0)
    start_si: int = cp.get("service_index", 0)
    start_page: int = cp.get("page", 1)

    if cp:
        log.info(
            "RESUMING — service #%d, page %d  (%d already saved)",
            start_si + 1,
            start_page,
            total_scraped,
        )
        controls.beep("resume")
    else:
        log.info("Starting fresh run")
        controls.beep("start")

    log.info("Output      : %s", output_file)
    log.info("Page param  : %s=N", page_param)
    log.info("Workers     : %d concurrent threads", n_workers)
    log.info("Delays      : %.1f-%.1fs per worker", delay_min, delay_max)
    log.info("Verify email: %s", cfg.get("verify_email", False))
    print()

    # -- Controls --------------------------------------------------------------
    # Use shared client only for listing pages; profile fetches use per-thread clients
    listing_client = fetcher.make_client(cfg)
    controller = controls.InputController()
    controller.start()
    controls.start_cmd_watcher()

    location_re = (
        re.compile(cfg["location_filter_regex"], re.I) if cfg.get("location_filter_regex") else None
    )

    run_stats = {
        "start_time": datetime.now().isoformat(),
        "source": cfg.get("source_label", "Directory"),
        "status": "PARTIAL",
    }
    stop_flag = False

    service_list = all_services if all_services else [""]

    try:
        for si, service in enumerate(service_list):
            if si < start_si:
                continue

            service_fallback_cat = _category_for_service(service, categories)
            page = start_page if si == start_si else 1
            start_page = 1
            total_pages = max_pages
            empty_streak = 0

            svc_label = service or "(all)"
            log.info(
                "=== Service %d/%d : %s ===",
                si + 1,
                len(service_list),
                svc_label,
            )

            while page <= total_pages:

                # -- keyboard controls (P/S act via threading.Event in listener) -----
                # W is the only key returned via get_key(); P/R/S are event-driven
                key = controller.get_key()
                if key == "w":
                    _print_stats(total_scraped, len(flagged_rows), clean_rows, svc_label, page)

                # Read event flags set directly by the listener thread
                if controller.is_stopped():
                    log.warning("STOP key — saving and exiting")
                    stop_flag = True
                    break

                if controller.is_paused():
                    log.warning("PAUSED — press R to resume")
                    controls.beep("interrupted")
                    while controller.is_paused():
                        if controller.is_stopped():
                            stop_flag = True
                            break
                        time.sleep(0.5)
                    if not stop_flag:
                        log.info("RESUMED — continuing")
                        controls.beep("resume")

                # -- file-based command.txt guard ------------------------------
                if controls.check_stop_time(stop_at):
                    log.warning("Daily stop time %s reached", stop_at)
                    stop_flag = True
                    break
                controls.check_disk(cfg.get("min_free_disk_mb", 500))
                if controls.check_cmd(ckpt_path) == "stop":
                    stop_flag = True
                    break

                # -- build request params --------------------------------------
                params = list(base_params.items()) + [(page_param, str(page))]
                if service:
                    params.append((service_param, service))

                body, status = fetcher.safe_get(listing_client, list_url, params=params)
                if not body:
                    log.warning("[%s] p%d — empty response", svc_label, page)
                    page += 1
                    continue

                soup = BeautifulSoup(body, "html.parser")

                if page == 1:
                    detected = fetcher.get_total_pages(soup, page_size)
                    if detected < max_pages:
                        total_pages = detected
                        log.info(
                            "  [%s] %d pages detected (~%d results)",
                            svc_label,
                            total_pages,
                            total_pages * page_size,
                        )

                cards = parser.parse_cards(soup, cfg)
                if not cards:
                    empty_streak += 1
                    log.warning("  [%s] p%d — no cards (streak %d)", svc_label, page, empty_streak)
                    if empty_streak >= 3 or page > total_pages:
                        log.info("  [%s] Sector complete", svc_label)
                        break
                    page += 1
                    continue
                empty_streak = 0

                new_cards = [c for c in cards if c["url"] not in seen_urls]
                for c in new_cards:
                    seen_urls.add(c["url"])

                eta = fetcher.rate_eta(total_scraped, total_pages * page_size)
                log.info(
                    "  [%s] p%d/%d | %d new | saved: %d%s",
                    svc_label,
                    page,
                    total_pages,
                    len(new_cards),
                    total_scraped,
                    f" | {eta}" if eta else "",
                )

                # -- concurrent profile fetch ----------------------------------
                pairs = _fetch_profiles_concurrent(new_cards, cfg, n_workers, delay_min, delay_max)

                # tqdm wraps the result-processing loop (fast — no I/O here)
                if _TQDM_OK and pairs:
                    iter_pairs = _tqdm(
                        pairs,
                        desc=f"  p{page}/{total_pages}",
                        unit="rec",
                        leave=False,
                        dynamic_ncols=True,
                    )
                else:
                    iter_pairs = pairs

                for card, rec in iter_pairs:
                    # re-check stop/pause inside result processing
                    inner_key = controller.get_key()
                    if inner_key == "w":
                        _print_stats(total_scraped, len(flagged_rows), clean_rows, svc_label, page)
                    if controller.is_stopped():
                        stop_flag = True
                        break

                    if not rec:
                        flagged_rows.append(
                            {
                                "Company": card["name"],
                                "Email": "",
                                "Phone": "",
                                "Website": "",
                                "Location": card.get("location", ""),
                                "Category": (
                                    card.get("services", [service_fallback_cat])[0]
                                    if card.get("services")
                                    else service_fallback_cat
                                ),
                                "Source": cfg.get("source_label", "Directory"),
                                "Flag Reason": "Profile page fetch failed",
                            }
                        )
                        continue

                    # -- FIX: category from badge images (card["services"]) ----
                    # Use the first badge-derived category; fall back to service name
                    if card.get("services"):
                        rec["Category"] = card["services"][0]
                    else:
                        rec["Category"] = service_fallback_cat

                    # -- FIX: location from card meta text --------------------
                    # Populate Location from listing-card meta if profile regex missed it
                    if not rec.get("Location") and card.get("location"):
                        rec["Location"] = card["location"]

                    # -- location filter ---------------------------------------
                    if location_re and not location_re.search(rec.get("Location", "")):
                        flagged_rows.append({**rec, "Flag Reason": "Outside geographic filter"})
                        continue

                    clean_rows.append(rec)
                    total_scraped += 1
                    fetcher.record_scrape()

                    loc = rec.get("Location") or "--"
                    em = rec.get("Email") or "--"
                    web = "Y" if rec.get("Website") else "--"
                    log.info(
                        "    %4d. %-40s  email:%-26s  loc:%-12s  web:%s",
                        total_scraped,
                        card["name"][:40],
                        em[:26],
                        loc[:12],
                        web,
                    )

                if stop_flag:
                    break

                # -- persist ---------------------------------------------------
                exporter.export_excel(
                    clean_rows,
                    flagged_rows,
                    output_file,
                    {**run_stats, "total_scraped": total_scraped},
                )
                checkpoint.save(
                    {
                        "service_index": si,
                        "page": page + 1,
                        "total_scraped": total_scraped,
                        "seen_urls": list(seen_urls),
                        "clean_rows": clean_rows,
                        "flagged_rows": flagged_rows,
                    }
                )

                if page >= total_pages:
                    log.info("  [%s] All pages done", svc_label)
                    break
                page += 1

            if stop_flag:
                break
            print()

        if not stop_flag:
            run_stats["status"] = "COMPLETE"
            log.info("All services complete")

    except KeyboardInterrupt:
        log.warning("Stopped by Ctrl+C — checkpoint saved")
        controls.beep("interrupted")

    finally:
        controller.stop()
        listing_client.close()

    # -- final write -----------------------------------------------------------
    exporter.export_excel(
        clean_rows,
        flagged_rows,
        output_file,
        {**run_stats, "total_scraped": total_scraped},
    )

    # -- summary ---------------------------------------------------------------
    elapsed_str = fetcher.elapsed()
    with_email = sum(1 for r in clean_rows if r.get("Email"))
    with_website = sum(1 for r in clean_rows if r.get("Website"))

    print()
    print("=" * 60)
    log.info("  Total saved     : %d", total_scraped)
    log.info("  Flagged         : %d", len(flagged_rows))
    log.info("  With email      : %d", with_email)
    log.info("  With website    : %d", with_website)
    log.info("  Time elapsed    : %s", elapsed_str)
    log.info("  Output file     : %s", os.path.abspath(output_file))
    print("=" * 60)

    if total_scraped > 0:
        controls.beep("done")


if __name__ == "__main__":
    main()
