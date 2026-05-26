"""
controls.py
===========
Runtime controls and audio feedback for the WordPress Directory Scraper.

Responsibilities:
  - Cross-platform non-blocking keyboard listener (InputController class)
    P (pause) / R (resume) / S/Q (stop) / W (stats)
  - Audio beep notifications
  - sound_sequence() for multi-tone alerts

Key fix v1.2.1: P, S/Q now set threading.Event flags directly inside
the listener thread so they take effect immediately even when the main
loop is blocked inside a tqdm iteration or time.sleep().

On Windows : uses msvcrt for instant keypress detection.
On Unix/Mac: uses tty + select for raw keypress detection (no Enter needed).
Audio falls back to the terminal bell on non-Windows platforms.
"""

from __future__ import annotations

import atexit
import logging
import sys
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

# ── Shared threading.Event flags ─────────────────────────────────────────────
_pause_event = threading.Event()   # set = paused
_stop_event  = threading.Event()   # set = stop requested


# =============================================================================
# Keyboard listener
# =============================================================================

class InputController:
    """
    Background-thread non-blocking keyboard listener.

    P and S/Q set threading.Event flags immediately so the main loop
    responds even when blocked in sleep or tqdm. W is stored in _key
    and consumed by scraper.py via get_key() to print live stats.

    Usage::

        controller = InputController()
        controller.start()
        key = controller.get_key()   # returns "w" or None
        controller.stop()
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
        Process a key press, acting on events immediately for P/S/R.

        Args:
            ch: Single lowercase character.
        """
        if ch == "p":
            _pause_event.set()
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
            with self._lock:
                self._key = ch

    def _listen_windows(self) -> None:
        import msvcrt
        while self._running:
            if msvcrt.kbhit():  # type: ignore[attr-defined]
                try:
                    raw = msvcrt.getch()  # type: ignore[attr-defined]
                    if raw in (b'\x00', b'\xe0'):
                        msvcrt.getch()  # type: ignore[attr-defined]  # discard second byte of extended key
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
        Return and clear the last stored key (only 'w' is stored here).

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
