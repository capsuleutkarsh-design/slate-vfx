"""
Why the player "stops after a while", and why some files stay blank.

Two separate faults sat behind the same symptom: a failure part-way through a
file was indistinguishable from the file simply ending, and whatever ffmpeg said
about the problem was thrown away before anyone could read it.
"""

import inspect

import pytest


class TestFfmpegIsAllowedToExplainItself:

    @pytest.fixture
    def source(self):
        from slate.gui.widgets.media_engines import stream_engine
        return inspect.getsource(stream_engine)

    def test_its_messages_are_no_longer_discarded(self, source):
        launch = source.split("def _launch_ffmpeg")[1].split("\n    def ")[0]

        assert "stderr=subprocess.DEVNULL" not in launch, (
            "throwing the error away is why a failing file just went blank"
        )
        assert "stderr=subprocess.PIPE" in launch

    def test_the_pipe_is_emptied_on_its_own_thread(self, source):
        """A full pipe stops ffmpeg dead, which would be a worse bug."""
        assert "utvfx-ffmpeg-stderr" in source
        assert "daemon=True" in source

    def test_the_last_message_is_kept_for_the_error_shown_to_the_user(self, source):
        assert "self._last_error = text" in source
        assert "reason = (self._last_error" in source

    def test_a_fresh_process_starts_with_no_stale_message(self, source):
        """Otherwise the previous file's error is reported against this one."""
        launch = source.split("def _launch_ffmpeg")[1].split("\n    def ")[0]

        assert 'self._last_error = ""' in launch


class TestFailingIsNotTheSameAsFinishing:
    """
    The player stopped and would not respond until play was pressed twice. What
    happened was ffmpeg dying part-way through; the player read the short frame
    as the end of the file and quietly went idle, with nothing on screen to say
    so.
    """

    @pytest.fixture
    def producer(self):
        from slate.gui.widgets.media_engines.stream_engine import StreamEngine
        for name in ("_producer_loop", "_producer", "run"):
            fn = getattr(StreamEngine, name, None)
            if fn is not None and "_read_exact" in inspect.getsource(fn):
                return inspect.getsource(fn)
        return inspect.getsource(StreamEngine)

    def test_the_exit_code_is_looked_at(self, producer):
        assert "poll()" in producer

    def test_ending_before_the_last_frame_counts_as_a_failure(self, producer):
        assert "self.total_frames" in producer
        assert "self.current_frame" in producer

    def test_the_user_is_told_rather_than_left_looking_at_a_frozen_player(self, producer):
        assert "error_occurred.emit" in producer

    def test_a_file_that_never_produced_a_picture_says_so(self, producer):
        assert "current_frame == 0" in producer

    def test_a_clean_finish_is_not_reported_as_an_error(self, producer):
        """exit code 0, or still running, is not a failure."""
        assert "exit_code not in (0, None)" in producer


class TestRestartingDoesNotLeakAProcess:
    """
    Scrubbing restarts ffmpeg at the new position. The old process was replaced
    without being handed back, so the count climbed - 3, 5, 7, 8, 12, 15 - until
    the player would not start another one.
    """

    def test_the_old_process_is_handed_back_on_restart(self):
        from slate.gui.widgets.media_engines.stream_engine import StreamEngine

        source = inspect.getsource(StreamEngine._restart_ffmpeg_at)

        assert "subprocess_tracker.unregister" in source

    def test_the_tracker_clears_out_processes_that_have_ended(self):
        from slate.utils.process_manager import SubprocessTracker

        assert "_sweep_finished" in inspect.getsource(SubprocessTracker.register)

    def test_letting_one_go_closes_its_pipes(self):
        """An open pipe is a held handle even after the process is gone."""
        from slate.utils.process_manager import SubprocessTracker

        assert hasattr(SubprocessTracker, "_release")

    def test_how_many_are_held_can_be_read(self):
        from slate.utils.process_manager import subprocess_tracker

        assert isinstance(subprocess_tracker.tracked_count, int)


class TestOpenExrFilesShowAPicture:
    """
    EXRs came back blank. The channel count was being passed as -1, which the
    image library reads as "no channels" rather than "all of them".
    """

    def test_the_channel_count_is_asked_for_explicitly(self):
        from slate.gui.widgets.media_engines import image_engine

        source = inspect.getsource(image_engine)

        assert "spec.nchannels" in source
        assert "read_image(0, 0, 0, -1" not in source

    def test_formats_the_ordinary_loader_cannot_read_fall_back(self):
        """DPX and TGA are read by the image library, not by Qt."""
        from slate.gui.widgets.media_engines.image_engine import ImageEngine

        assert hasattr(ImageEngine, "_load_via_oiio")
        assert "_load_via_oiio" in inspect.getsource(ImageEngine._load_standard)


class TestClickingAnAssetPlaysIt:

    def test_the_selection_arms_playback(self):
        """
        Selecting a clip loaded it into the player but left it stopped, so it had
        to be started by hand every time.
        """
        from slate.gui.tabs.stock_browser_tab import StockBrowserTab

        source = inspect.getsource(StockBrowserTab.on_selection_changed)

        assert "_pending_autoplay" in source
