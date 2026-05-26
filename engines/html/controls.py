"""
controls.py
===========
Runtime controls and audio feedback for the HTML Directory Scraper.

Responsibilities:
  - Cross-platform non-blocking keyboard listener (InputController class)
  - File-based command.txt watcher thread (unique to the HTML engine)
  - Disk-space guard
  - Daily auto-stop time check
  - Audio beep notifications
  - sound_sequence() for multi-tone alerts

Keyboard keys (no Enter needed):
  P — pause after current page
  R — resume from pause
  S — save checkpoint and stop
  W — print live stats (handled in scraper.py via get_key())

Key fix v1.2.1: P and S now set threading.Event flags directly inside
the listener thread so they take effect immediately even when the main
loop is blocked in a time.sleep() or tqdm iteration.

On Windows : uses msvcrt for instant keypress detection.
On Unix/Mac: uses tty + select for raw keypress detection (no Enter needed).
Audio falls back to terminal bell on non-Windows platforms.
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Shared threading.Event flags ─────────────────────────────────────────────
_pause_event = threading.Event()   # set = paused
_stop_event  = threading.Event()   # set = stop requested
_last_cmd    = ""

CMD_FILE = "command.txt"


# =============================================================================
# Keyboard listener
# =============================================================================

class InputController:
    """
    Background-thread non-blocking keyboard listener.

    Keys P and S set the shared threading.Event flags directly so they take
    effect immediately — even when the main loop is sleeping or inside a tqdm
    iteration. Key W is stored in _key and consumed by scraper.py's get_key().

    Usage::

        controller = InputController()
        controller.start()
        key = controller.get_key()   # returns "w" or None
        controller.stop()

    On Windows uses ``msvcrt``; on Unix/macOS uses ``tty`` + ``select``.
    """

    def __init__(self) -> None:
        self._key: Optional[str]   = None
        self._lock                  = threading.Lock()
        self._running               = True
        self._thread                = threading.Thread(
            target=self._listen, daemon=True, name="KeyboardListener"
        )
        self._unix_fd: Optional[int]   = None
        self._unix_old_settings: Optional[list] = None

    def start(self) -> None:
        """Initialise platform terminal state and start the listener thread."""
        if sys.platform != "win32":
            self._init_unix_raw()
        self._thread.start()
        log.debug(
            "Input controller started (%s mode).",
            "Windows" if sys.platform == "win32" else "Unix",
        )

    def stop(self) -> None:
        """Stop the listener thread and restore the terminal."""
        self._running = False
        if sys.platform != "win32":
            self._restore_unix()
        log.debug("Input controller stopped.")

    def _init_unix_raw(self) -> None:
        try:
            import termios, tty  # noqa: E401
            self._unix_fd           = sys.stdin.fileno()
            self._unix_old_settings = termios.tcgetattr(self._unix_fd)
            tty.setraw(self._unix_fd)
            atexit.register(self._restore_unix)
        except Exception as exc:
            log.warning("Could not set raw terminal mode (%s).", exc)

    def _restore_unix(self) -> None:
        if self._unix_old_settings is not None and self._unix_fd is not None:
            try:
                import termios
                termios.tcsetattr(
                    self._unix_fd, termios.TCSADRAIN, self._unix_old_settings
                )
                self._unix_old_settings = None
            except Exception:
                pass

    def _listen(self) -> None:
        if sys.platform == "win32":
            self._listen_windows()
        else:
            self._listen_unix()

    def _handle_key(self, ch: str) -> None:
        """
        Process a key press.  P/S/R act on threading.Events immediately.
        W is stored for scraper.py to consume via get_key().

        Args:
            ch: Single lowercase character.
        """
        if ch == "p":
            _pause_event.set()
            # Print directly so it appears even inside a tqdm bar
            sys.stderr.write("\n[PAUSED] Press R to resume\n")
            sys.stderr.flush()
        elif ch == "r":
            _pause_event.clear()
            sys.stderr.write("\n[RESUMED]\n")
            sys.stderr.flush()
        elif ch in ("s", "q"):
            _stop_event.set()
            sys.stderr.write("\n[STOPPING] Saving checkpoint…\n")
            sys.stderr.flush()
        elif ch == "w":
            # Store for scraper.py to consume and print stats
            with self._lock:
                self._key = ch
        # Ignore all other keys

    def _listen_windows(self) -> None:
        import msvcrt
        while self._running:
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                try:
                    raw = msvcrt.getch()  # type: ignore[attr-defined]
                    # Handle extended keys (arrow keys send 0x00 or 0xe0 prefix)
                    if raw in (b'\x00', b'\xe0'):
                        msvcrt.getch()  # type: ignore[attr-defined]  # consume the second byte, ignore
                    else:
                        ch = raw.decode("utf-8", errors="ignore").lower()
                        if ch:
                            self._handle_key(ch)
                except Exception:
                    pass
            time.sleep(0.05)

    def _listen_unix(self) -> None:
        import select
        while self._running:
            try:
                readable, _, _ = select.select([sys.stdin], [], [], 0.05)
                if readable:
                    ch = sys.stdin.read(1).lower()
                    if ch:
                        self._handle_key(ch)
            except Exception:
                break

    def get_key(self) -> Optional[str]:
        """
        Return and clear the last stored key (only 'w' is stored here;
        P/R/S act on events directly and are not returned via this method).

        Returns:
            ``"w"`` if W was pressed since last call, otherwise ``None``.
        """
        with self._lock:
            key       = self._key
            self._key = None
            return key

    def is_paused(self) -> bool:
        """Return True if the pause event is currently set."""
        return _pause_event.is_set()

    def is_stopped(self) -> bool:
        """Return True if the stop event is currently set."""
        return _stop_event.is_set()


# =============================================================================
# Audio
# =============================================================================

def beep(kind: str = "error") -> None:
    """
    Play a named audio cue. Fails silently if audio hardware is unavailable.

    Args:
        kind: One of ``"start"``, ``"resume"``, ``"done"``,
              ``"interrupted"``, ``"error"``.
    """
    try:
        if sys.platform != "win32":
            sys.stdout.write("\a")
            sys.stdout.flush()
            return
        import winsound
        patterns = {
            "start":       [(500, 100), (700, 100), (900, 100), (1100, 200)],
            "resume":      [(600, 150), (900, 250)],
            "done":        [(400, 80), (600, 80), (800, 80), (1000, 80), (1200, 300)],
            "interrupted": [(900, 200), (600, 200), (400, 400)],
            "error":       [(350, 150)],
        }
        for freq, dur in patterns.get(kind, [(350, 150)]):
            winsound.Beep(freq, dur)
    except Exception:
        pass


def beep_raw(freq: int = 1000, duration_ms: int = 200) -> None:
    """
    Emit a single tone. Fails silently.

    Args:
        freq:        Frequency in Hz.
        duration_ms: Duration in milliseconds.
    """
    try:
        if sys.platform == "win32":
            import winsound
            winsound.Beep(freq, duration_ms)
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        pass


def sound_sequence(
    freqs: list, duration_ms: int = 200, gap_s: float = 0.05
) -> None:
    """
    Play a sequence of tones with a short gap between each.

    Args:
        freqs:       List of integer frequencies in Hz.
        duration_ms: Duration of each tone in milliseconds.
        gap_s:       Pause between tones in seconds.
    """
    for freq in freqs:
        beep_raw(freq, duration_ms)
        time.sleep(gap_s)


# =============================================================================
# command.txt watcher (unique to the HTML engine)
# =============================================================================

def _watch_cmd() -> None:
    """Background thread: polls command.txt every second for operator commands."""
    global _last_cmd
    while True:
        time.sleep(1)
        try:
            if not os.path.exists(CMD_FILE):
                continue
            cmd = Path(CMD_FILE).read_text().strip().lower()
            if cmd == _last_cmd:
                continue
            _last_cmd = cmd

            if cmd == "pause":
                _pause_event.set()
                log.warning("Command: PAUSE — finishing current page…")
            elif cmd == "resume":
                _pause_event.clear()
                log.info("Command: RESUME — continuing…")
                beep("resume")
            elif cmd == "stop":
                _stop_event.set()
                log.warning("Command: STOP — saving and exiting…")
            elif cmd == "status":
                log.info("STATUS — check terminal for live counts")
                Path(CMD_FILE).write_text("")
        except Exception:
            pass


def start_cmd_watcher() -> None:
    """Launch the command.txt background polling thread (idempotent)."""
    t = threading.Thread(target=_watch_cmd, daemon=True, name="CmdWatcher")
    t.start()


def handle_fresh_command(checkpoint) -> None:  # type: ignore[no-untyped-def]
    """
    If command.txt contains 'fresh', clear the checkpoint and reset the file.

    Args:
        checkpoint: CheckpointManager instance.
    """
    if not os.path.exists(CMD_FILE):
        return
    try:
        cmd = Path(CMD_FILE).read_text().strip().lower()
        if cmd == "fresh":
            checkpoint.clear()
            Path(CMD_FILE).write_text("")
            log.warning("Fresh start — checkpoint cleared via command.txt")
    except Exception:
        pass


# =============================================================================
# Runtime command check (called from the main loop)
# =============================================================================

def check_cmd(checkpoint_path: str) -> Optional[str]:
    """
    Check threading.Event flags and block while paused.

    Because P and S now set the events directly in the listener thread,
    this function just reads the flags — no polling delay.

    Args:
        checkpoint_path: Path to checkpoint file (for 'fresh' clearing).

    Returns:
        ``"stop"`` if stop was requested, otherwise ``None``.
    """
    global _last_cmd

    if _last_cmd == "fresh":
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
        log.warning("Checkpoint cleared — next run starts fresh")
        _last_cmd = ""

    if _stop_event.is_set():
        return "stop"

    if _pause_event.is_set():
        log.warning("Paused — press R (or write 'resume' to command.txt) to continue")
        while _pause_event.is_set():
            if _stop_event.is_set():
                return "stop"
            _pause_event.wait(timeout=0.5)  # unblocks instantly when R pressed

    return None


# =============================================================================
# System guards
# =============================================================================

def check_disk(min_mb: int = 500) -> None:
    """
    Pause automatically if free disk space falls below the threshold.

    Args:
        min_mb: Minimum free disk space in megabytes before pausing.
    """
    try:
        free_bytes = shutil.disk_usage(".").free
        if free_bytes < min_mb * 1024 * 1024:
            log.warning("Low disk space (< %d MB) — pausing!", min_mb)
            beep("interrupted")
            _pause_event.set()
    except Exception:
        pass


def _trigger_pause() -> None:
    """
    Activate the pause state.
    Called by fetcher's circuit breaker via the registered callback.
    """
    beep("interrupted")
    _pause_event.set()
    while _pause_event.is_set():
        time.sleep(0.5)


def check_stop_time(stop_at: str) -> bool:
    """
    Return True if the current time has reached the daily stop time.

    Args:
        stop_at: 24-hour time string ``"HH:MM"``, or ``""`` to disable.

    Returns:
        True if *stop_at* is non-empty and current time ≥ *stop_at*.
    """
    if not stop_at:
        return False
    return datetime.now().strftime("%H:%M") >= stop_at
