"""End-of-run summary helpers.

Builds a compact, human-readable metrics block and shows it in a native Windows
pop-up. The pop-up uses user32 ``MessageBoxTimeoutW`` so it auto-dismisses after a
timeout and can never hang an unattended/scheduled run. It is a no-op on
non-Windows platforms; callers should always log the summary separately so it is
captured everywhere.
"""

import sys
import threading

from config.logger_config import logger

# user32 MessageBox flags
_MB_OK = 0x00000000
_MB_ICONINFORMATION = 0x00000040
_MB_SETFOREGROUND = 0x00010000
_MB_TOPMOST = 0x00040000


def build_run_summary(metrics: dict) -> str:
    """Build an aligned metrics block for the end-of-run summary.

    Args:
        metrics: keys ``applied``, ``skipped``, ``failed``, ``discovered``,
            ``processed`` (ints) and optional ``duration`` / ``reason`` strings.
    """
    rows = [
        ("Applied", metrics.get("applied", 0)),
        ("Skipped", metrics.get("skipped", 0)),
        ("Failed", metrics.get("failed", 0)),
        ("Discovered", metrics.get("discovered", 0)),
        ("Processed", metrics.get("processed", 0)),
    ]
    width = max(len(label) for label, _ in rows)
    lines = [f"{label.ljust(width)} : {value}" for label, value in rows]

    duration = metrics.get("duration")
    reason = metrics.get("reason")
    if duration or reason:
        lines.append("")
    if duration:
        lines.append(f"Duration   : {duration}")
    if reason:
        lines.append(f"Result     : {reason}")
    return "\n".join(lines)


def show_summary_popup(title: str, body: str, timeout_ms: int = 120000) -> None:
    """Show ``body`` in a native Windows pop-up that auto-closes after a timeout.

    No-op on non-Windows platforms. Runs in a background (non-daemon) thread so a
    one-shot run stays alive until the box is dismissed or auto-closed, while a
    looped/scheduled run is never blocked. Any failure is swallowed so the
    summary can never break a run.
    """
    if sys.platform != "win32":
        return

    def _worker() -> None:
        try:
            import ctypes

            message_box = ctypes.windll.user32.MessageBoxTimeoutW
            message_box.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
                ctypes.c_ushort,
                ctypes.c_uint,
            ]
            message_box.restype = ctypes.c_int
            message_box(
                None,
                body,
                title,
                _MB_OK | _MB_ICONINFORMATION | _MB_SETFOREGROUND | _MB_TOPMOST,
                0,
                int(timeout_ms),
            )
        except Exception as e:  # never let the summary pop-up break a run
            logger.debug(f"Could not show run-summary pop-up: {e}")

    try:
        threading.Thread(target=_worker, name="run-summary-popup", daemon=False).start()
    except Exception as e:
        logger.debug(f"Could not start run-summary pop-up thread: {e}")
