"""
The preview player must not leak as people browse.

Reported symptom: the stock browser plays fine for a while, then the player
stops working. Cause: every scrub restarts ffmpeg, and the replaced process was
killed but never handed back to the tracker. The tracker's set was the only
thing referencing it, so it was never collected and its output pipe stayed
open. Browsing a real library exhausted the process's file handles, and then no
further ffmpeg could be launched.

Measured before the fix, over six rounds of browsing and scrubbing six clips:
3, 5, 7, 8, 12, 15 dead processes held. After: 1 throughout, 0 once closed.
"""

import subprocess

import pytest

from ut_vfx.utils.process_manager import SubprocessTracker, subprocess_tracker


class _FakeProc:
    """A Popen-shaped stand-in, so no real process is needed."""

    def __init__(self, finished=True):
        self._finished = finished
        self.stdout = _FakeStream()
        self.stderr = None
        self.stdin = None

    def poll(self):
        return 0 if self._finished else None

    def terminate(self):
        self._finished = True

    def kill(self):
        self._finished = True

    def wait(self, timeout=None):
        return 0


class _FakeStream:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def tracker(monkeypatch):
    """A tracker with its own set, so the real one is left alone."""
    instance = SubprocessTracker()
    monkeypatch.setattr(instance, "_processes", set(), raising=False)
    return instance


def _register(tracker, proc):
    """Register without the isinstance check, which a stand-in cannot pass."""
    tracker._sweep_finished()
    tracker._processes.add(proc)
    return proc


class TestFinishedProcessesAreLetGo:

    def test_a_finished_process_is_swept_when_the_next_one_arrives(self, tracker):
        for _ in range(20):
            _register(tracker, _FakeProc(finished=True))

        assert tracker.tracked_count <= 1, (
            f"{tracker.tracked_count} finished processes still held; each keeps "
            "a pipe open"
        )

    def test_a_running_process_is_kept(self, tracker):
        """They must still be terminated when the application exits."""
        running = _FakeProc(finished=False)
        _register(tracker, running)
        _register(tracker, _FakeProc(finished=True))

        assert running in tracker._processes

    def test_browsing_does_not_accumulate(self, tracker):
        """
        The shape of the real bug: one live process at a time, a long trail of
        finished ones behind it.
        """
        live = _FakeProc(finished=False)
        _register(tracker, live)
        counts = []
        for _ in range(50):
            live.kill()                       # the scrub replaces it
            live = _FakeProc(finished=False)
            _register(tracker, live)
            counts.append(tracker.tracked_count)

        assert max(counts) <= 2, f"held count climbed to {max(counts)}"

    def test_unregistering_closes_the_pipe(self, tracker):
        proc = _FakeProc(finished=True)
        _register(tracker, proc)

        tracker.unregister(proc)

        assert proc.stdout.closed, "the output pipe was left open"

    def test_sweeping_closes_the_pipe_too(self, tracker):
        proc = _FakeProc(finished=True)
        _register(tracker, proc)

        _register(tracker, _FakeProc(finished=False))

        assert proc.stdout.closed


class TestTheRestartPathReleasesTheOldProcess:
    """Scrubbing is what restarts ffmpeg, so this is the path that leaked."""

    def test_restarting_hands_the_old_process_back(self):
        import inspect
        from ut_vfx.gui.widgets.media_engines.stream_engine import StreamEngine

        source = inspect.getsource(StreamEngine._restart_ffmpeg_at)

        assert "subprocess_tracker.unregister" in source, (
            "the replaced ffmpeg process is still never released"
        )

    def test_stopping_hands_it_back_as_well(self):
        import inspect
        from ut_vfx.gui.widgets.media_engines.stream_engine import StreamEngine

        source = inspect.getsource(StreamEngine._stop_producer)

        assert "subprocess_tracker.unregister" in source


class TestTheRealTrackerStillWorks:

    def test_it_reports_how_many_it_holds(self):
        assert isinstance(subprocess_tracker.tracked_count, int)

    def test_registering_something_that_is_not_a_process_is_ignored(self):
        before = subprocess_tracker.tracked_count

        subprocess_tracker.register(None)
        subprocess_tracker.register("not a process")

        assert subprocess_tracker.tracked_count == before
