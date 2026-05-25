"""
checkpoint.py
=============
Checkpoint persistence for the WordPress Directory Scraper.

Responsibilities:
  - Saving scraper state to a JSON file so long sector-by-sector runs can
    be resumed after interruption (Ctrl+C, network drop, nonce expiry, etc.)
  - Loading a previously saved checkpoint on startup
  - Clearing the checkpoint file to force a fresh start (also triggered by
    the --fresh CLI flag)
  - Atomic write via .tmp rename to prevent checkpoint corruption on crash

The checkpoint stores:
  - output_file   : path to the in-progress Excel file
  - sector_index  : index of the current sector in cfg["sectors"]
  - page          : next AJAX page to fetch within the current sector
  - seen          : list of [name, postcode] pairs already processed
  - total_scraped : number of clean records saved so far
  - clean_rows    : validated records accumulated so far
  - flagged_rows  : excluded records accumulated so far
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class CheckpointManager:
    """
    Thread-safe JSON checkpoint file handler.

    Writes to a ``.tmp`` file first, then renames atomically to prevent
    checkpoint corruption if the process is killed mid-write.

    Args:
        path: File path for the checkpoint JSON file.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def save(self, state: Dict[str, Any]) -> None:
        """
        Serialise and atomically write *state* to the checkpoint file.

        Writes to ``<path>.tmp`` first, then renames to ``<path>`` so that
        a crash during the write never leaves a partial or corrupt checkpoint.

        Args:
            state: Dictionary of scraper state to persist.
        """
        tmp_path = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            tmp_path.replace(self.path)
            log.debug("Checkpoint saved → %s", self.path)
        except OSError as exc:
            log.warning("Could not save checkpoint: %s", exc)

    def load(self) -> Optional[Dict[str, Any]]:
        """
        Load and return checkpoint state from disk.

        Returns:
            State dictionary if the file exists and is valid JSON,
            otherwise ``None``.
        """
        if not self.path.exists():
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                state = json.load(f)
            log.info("Checkpoint loaded from %s", self.path)
            return state
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not load checkpoint (%s) — starting fresh.", exc)
            return None

    def clear(self) -> None:
        """Delete the checkpoint file if it exists."""
        if self.path.exists():
            try:
                self.path.unlink()
                log.info("Checkpoint cleared.")
            except OSError as exc:
                log.warning("Could not clear checkpoint: %s", exc)

    def exists(self) -> bool:
        """
        Return True if a checkpoint file is present on disk.

        Returns:
            bool: True if the checkpoint file exists.
        """
        return self.path.exists()
