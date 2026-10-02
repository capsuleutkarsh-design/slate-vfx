"""
The sound of a clip, kept in step with the picture.

The preview engines decode pictures through ffmpeg and nothing else, so every
preview was silent - an SFX library with no sound (MED-118). This plays the
original file's audio through Qt Multimedia and follows the picture: play,
pause, seek, speed and loop, with a correction whenever the two drift apart
by more than a few frames. Mute and volume are the player's.

Nothing here is required: if Qt Multimedia or the audio device is not there,
has_audio stays False and the player shows "No sound" - the picture plays on.
"""

import logging
import os

from PySide6.QtCore import QObject, QUrl, Signal

# How far the sound may drift from the picture before it is put back (s).
DRIFT = 0.12


class AudioTrack(QObject):
    """One clip's audio. has_audio is known once the file has been opened."""

    availability_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = None
        self.output = None
        self.has_audio = False
        self.source = ""
        self._muted = False
        self._volume = 0.8
        self._rate = 1.0
        self._pending_play = None   # seconds to start from once the media is loaded

    def _ensure(self) -> bool:
        if self.player is not None:
            return True
        if os.environ.get("SLATE_NO_AUDIO"):
            return False
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.player = QMediaPlayer(self)
            self.output = QAudioOutput(self)
            self.player.setAudioOutput(self.output)
            # Tests and the audit harness must not make a sound on the
            # machine they run on; they still exercise everything else.
            self.output.setMuted(self._muted or bool(os.environ.get("HEADLESS_TESTING")))
            self.output.setVolume(self._volume)
            self.player.mediaStatusChanged.connect(self._on_status)
            self.player.errorOccurred.connect(self._on_error)
            return True
        except Exception as exc:
            logging.info("No audio in previews on this machine: %s", exc)
            self.player = None
            return False

    def load(self, path: str):
        """Open a clip's sound. has_audio follows when the file has been read."""
        self.clear()
        self.source = str(path or "")
        if not self.source or not self._ensure():
            return
        self.player.setSource(QUrl.fromLocalFile(self.source))

    def clear(self):
        self._pending_play = None
        self.source = ""
        if self.player is not None:
            try:
                self.player.stop()
                self.player.setSource(QUrl())
            except RuntimeError:
                pass
        if self.has_audio:
            self.has_audio = False
            self.availability_changed.emit(False)

    def _on_status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            has = bool(self.player.hasAudio())
            if has != self.has_audio:
                self.has_audio = has
                self.availability_changed.emit(has)
            if self._pending_play is not None and has:
                start = self._pending_play
                self._pending_play = None
                self.play(start)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia and self.has_audio:
            self.has_audio = False
            self.availability_changed.emit(False)

    def _on_error(self, *args):
        logging.debug("Preview audio: %s", self.player.errorString() if self.player else args)

    # ------------------------------------------------------------ control
    def play(self, at_seconds: float = 0.0):
        if self.player is None or not self.source:
            return
        if not self.has_audio:
            self._pending_play = float(at_seconds or 0.0)
            return
        self.player.setPlaybackRate(self._rate)
        self.player.setPosition(int(max(0.0, at_seconds) * 1000))
        self.player.play()

    def pause(self):
        self._pending_play = None
        if self.player is not None:
            self.player.pause()

    def seek(self, seconds: float):
        if self.player is not None and self.has_audio:
            self.player.setPosition(int(max(0.0, seconds) * 1000))

    def sync(self, video_seconds: float, playing: bool):
        """Put the sound back with the picture when it has drifted."""
        if self.player is None or not self.has_audio or not playing:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.play(video_seconds)
            return
        if abs(self.player.position() / 1000.0 - video_seconds) > DRIFT:
            self.player.setPosition(int(video_seconds * 1000))

    def set_rate(self, rate: float):
        self._rate = max(0.1, float(rate or 1.0))
        if self.player is not None:
            self.player.setPlaybackRate(self._rate)

    def set_muted(self, muted: bool):
        self._muted = bool(muted)
        if self.output is not None:
            self.output.setMuted(self._muted or bool(os.environ.get("HEADLESS_TESTING")))

    def is_muted(self) -> bool:
        return self._muted

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, float(volume)))
        if self.output is not None:
            self.output.setVolume(self._volume)

    def volume(self) -> float:
        return self._volume

    def position_seconds(self) -> float:
        if self.player is None:
            return 0.0
        return self.player.position() / 1000.0

    def is_playing(self) -> bool:
        if self.player is None:
            return False
        from PySide6.QtMultimedia import QMediaPlayer
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
