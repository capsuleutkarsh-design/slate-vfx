import sys
import threading
import logging
import traceback
from datetime import datetime

def _handle_exception(exc_type, exc_value, exc_traceback):
    """
    Global exception handler for the main thread.
    Catches all uncaught exceptions and logs them securely before the app crashes.
    """
    if issubclass(exc_type, KeyboardInterrupt):
        # Ignore KeyboardInterrupt so Ctrl+C works normally in console
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logging.critical(f"UNCAUGHT FATAL EXCEPTION:\n{error_msg}")
    
    # Optionally, we could attempt to show a critical QMessageBox here, 
    # but it's dangerous if the UI event loop is already corrupted.
    
    # Call the default handler to ensure standard stderr output
    sys.__excepthook__(exc_type, exc_value, exc_traceback)


def _handle_thread_exception(args):
    """
    Global exception handler for background threads.
    """
    error_msg = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    thread_name = args.thread.name if args.thread else "Unknown Thread"
    logging.critical(f"UNCAUGHT THREAD EXCEPTION in [{thread_name}]:\n{error_msg}")


def _enable_native_crash_dump():
    """
    Write a Python stack for every thread when the process dies natively.

    The hooks below see Python exceptions only. A crash inside Qt, ffmpeg,
    OpenImageIO or OpenCV - an access violation from two threads touching one
    object - kills the process with nothing in the log at all: the last line
    is whatever happened to be logged before it. faulthandler writes each
    thread's Python stack at that moment to a file, which is the only way to
    learn which two threads were where.
    """
    try:
        import faulthandler
        import os
        from pathlib import Path

        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        path = Path(base) / "Slate" / "Logs" / "native_crash.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a", buffering=1)
        handle.write("\n===== %s pid %d =====\n" % (datetime.now().isoformat(timespec="seconds"), os.getpid()))
        faulthandler.enable(file=handle, all_threads=True)
        # Kept referenced for the life of the process; faulthandler keeps the
        # file descriptor, but the object must not be collected.
        global _native_crash_file
        _native_crash_file = handle
        logging.info("Native crash dumps go to %s", path)
    except Exception as exc:
        logging.debug("Native crash dumps not enabled: %s", exc)


_native_crash_file = None


def setup_global_crash_handler():
    """
    Injects custom exception hooks into the Python runtime to ensure all crashes
    (both main thread and background threads) are properly recorded in the telemetry logs.
    """
    logging.info("Initializing global crash handler...")
    sys.excepthook = _handle_exception
    threading.excepthook = _handle_thread_exception
    _enable_native_crash_dump()
