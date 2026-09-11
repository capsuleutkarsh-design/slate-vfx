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


def setup_global_crash_handler():
    """
    Injects custom exception hooks into the Python runtime to ensure all crashes
    (both main thread and background threads) are properly recorded in the telemetry logs.
    """
    logging.info("Initializing global crash handler...")
    sys.excepthook = _handle_exception
    threading.excepthook = _handle_thread_exception
