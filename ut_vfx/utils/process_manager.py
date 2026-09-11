"""
Process and Subprocess Lifecycle Manager for UT_VFX.
Ensures no orphan child processes (FFmpeg, Python, Olive, etc.) remain running
after the main application closes.
"""
import atexit
import logging
import os
import subprocess
import sys
from typing import Set

logger = logging.getLogger(__name__)

class SubprocessTracker:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._processes: Set[subprocess.Popen] = set()
            cls._instance._registered_atexit = False
        return cls._instance

    def register(self, proc: subprocess.Popen) -> subprocess.Popen:
        """Register a child subprocess to ensure it is terminated on exit."""
        if proc and isinstance(proc, subprocess.Popen):
            # Drop anything that has already exited before adding another.
            # This set is the only thing referencing those objects, so without
            # this they are never collected and each keeps its output pipe
            # open. The preview player restarts ffmpeg on every scrub, so a
            # long browsing session ran the application out of file handles
            # and playback simply stopped working.
            self._sweep_finished()
            self._processes.add(proc)
            self._ensure_atexit()
        return proc

    def unregister(self, proc: subprocess.Popen):
        """Unregister a finished subprocess, closing anything it still holds."""
        self._processes.discard(proc)
        self._release(proc)

    def _sweep_finished(self):
        """Forget every process that has already exited."""
        for proc in [p for p in self._processes if p.poll() is not None]:
            self._processes.discard(proc)
            self._release(proc)

    @staticmethod
    def _release(proc: subprocess.Popen):
        """Close the pipes a finished process left behind."""
        if proc is None:
            return
        for stream_name in ("stdout", "stderr", "stdin"):
            stream = getattr(proc, stream_name, None)
            if stream is None:
                continue
            try:
                stream.close()
            except Exception:
                pass

    @property
    def tracked_count(self) -> int:
        """How many child processes are being held. Used by the tests."""
        return len(self._processes)

    def _ensure_atexit(self):
        if not self._registered_atexit:
            atexit.register(self.terminate_all)
            self._registered_atexit = True

    def terminate_all(self, timeout: float = 1.0):
        """Terminate all registered child processes."""
        for proc in list(self._processes):
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception as exc:
                logger.debug("Subprocess cleanup notice: %s", exc)
        self._processes.clear()


subprocess_tracker = SubprocessTracker()
