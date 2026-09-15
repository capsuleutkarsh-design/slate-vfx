"""
The web API, run inside the server process.

It used to be launched as a separate process through a Python found by walking
up from this file to ..\\python_portable. In a checkout that works. In an
installed build this file is unpacked into a temporary folder with no such
neighbour, the lookup fell through to the bare name "python" - which on the
studio's machines is the Windows Store stub - and the dashboard's web interface
never started on any installed server. The log said so, quietly.

uvicorn can run in a thread of the process that already has every dependency
loaded, so it does that now, for the checkout and the installed build alike.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class ApiServer:
    """uvicorn serving slate.api.main:app in a daemon thread."""

    def __init__(self, port: int = 8000, host: str = "0.0.0.0"):
        self.port = int(port)
        self.host = host
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self.error: str = ""

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def is_answering(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                return True
        except OSError:
            return False

    def start(self) -> bool:
        """Start serving. Returns False, with self.error set, if it could not."""
        if self.is_running():
            return True
        self.error = ""
        try:
            import uvicorn
        except ImportError as exc:
            self.error = f"uvicorn is not available: {exc}"
            return False

        config = uvicorn.Config("slate.api.main:app", host=self.host, port=self.port,
                                log_level="warning", loop="asyncio", lifespan="on")
        self._server = uvicorn.Server(config)

        def run():
            try:
                # uvicorn installs signal handlers only on the main thread, so
                # this is safe from a worker thread.
                self._server.run()
            except Exception as exc:          # noqa: BLE001 - reported to the window
                self.error = str(exc)
                logger.exception("The web API stopped: %s", exc)

        self._thread = threading.Thread(target=run, name="slate-api", daemon=True)
        self._thread.start()
        return True

    def wait_until_ready(self, timeout: float = 10.0) -> bool:
        """True once the port answers; False if the thread died or time ran out."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_answering():
                return True
            if not self.is_running():
                return False
            time.sleep(0.25)
        return self.is_answering()

    def stop(self, timeout: float = 5.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self._server = None
