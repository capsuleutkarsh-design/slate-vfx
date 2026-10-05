"""
The Timeline Viewer's own player: watch the lineup inside Slate.

The Help promised scrubbing the lineup in Slate and there was no player at
all (MED-084); the user asked for one to be built. RV stays the review
tool and the EDLs go to editorial - this is for looking.

  * A strip across the top shows the lineup as blocks in edit order, as long
    as each shot runs; click one to go there. The current shot is lit.
  * The player shows the chosen layer (Scan, Comp, ...) of the current shot,
    falling back to the plate when that department has not rendered yet.
  * "Play lineup" plays shot after shot; each clip can be scrubbed with the
    player's own bar.
  * It plays the review proxy when there is one, else the movie, else the
    frames themselves. A heavy EXR sequence without a proxy says so and
    offers "Make proxy" for that shot - proxies are made on request, never
    behind anyone's back.
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QRectF, QThread
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from ...core.controls import make_button
from ...widgets.advanced_player import AdvancedPlayer

logger = logging.getLogger(__name__)

HEAVY = {".exr", ".dpx", ".tif", ".tiff"}


def media_for(entry, layer: str):
    """
    (path, kind, clip) to play for this shot and layer.

    kind is "proxy", "movie" or "frames". The plate stands in for a layer the
    shot does not have yet. A proxy older than its render is passed over.
    """
    from slate.core.domain.proxy_builder import first_frame_file, proxy_is_current, proxy_path_for
    clips = getattr(entry, "clips", {}) or {}
    clip = clips.get(layer) or clips.get("scan")
    if clip is None:
        return None, "", None
    if proxy_is_current(clip, entry.name):
        return proxy_path_for(clip, entry.name), "proxy", clip
    if not clip.is_sequence:
        return Path(clip.path), "movie", clip
    return first_frame_file(clip), "frames", clip


def strip_label(metrics, name: str, width: int) -> str:
    """
    The shot's name if it fits, else its last part (SEQ010_SH010 -> SH010),
    which tells neighbouring shots apart - not "SEQ010_S..." on every block.
    """
    if metrics.horizontalAdvance(name) <= width:
        return name
    tail = name.replace("-", "_").split("_")[-1]
    if tail and metrics.horizontalAdvance(tail) <= width:
        return tail
    return metrics.elidedText(tail or name, Qt.TextElideMode.ElideRight, width)


class LineupStrip(QWidget):
    """The lineup as blocks, as long as each shot; click to go to one."""

    shot_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []
        self.current = -1
        self.setMinimumHeight(30)
        self.setMaximumHeight(30)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_entries(self, entries):
        self.entries = list(entries or [])
        self.current = 0 if self.entries else -1
        self.update()

    def set_current(self, index):
        self.current = index
        self.update()

    def _lengths(self):
        """
        Each shot's share of the strip. A shot whose length is not known gets
        the typical length of the others, not the 100-frame placeholder that
        made it the widest block of all (MED2-046).
        """
        known = sorted(e.get_frame_count() for e in self.entries if e.length_known)
        nominal = known[len(known) // 2] if known else 24
        return [e.get_frame_count() if e.length_known else nominal for e in self.entries]

    def _blocks(self):
        lengths = self._lengths()
        total = sum(lengths) or 1
        width = max(1.0, self.width() - 2.0)
        x = 1.0
        for index, entry in enumerate(self.entries):
            w = width * lengths[index] / total
            yield index, entry, QRectF(x, 2, max(2.0, w - 1), self.height() - 4)
            x += w

    def block_at(self, x: float) -> int:
        for index, _entry, rect in self._blocks():
            if rect.left() <= x <= rect.right() + 1:
                return index
        return -1

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(Gate.PANEL))
        reels = {}
        for index, entry, rect in self._blocks():
            reel_index = reels.setdefault(entry.reel, len(reels))
            base = QColor(Gate.RAISED_HI) if reel_index % 2 == 0 else QColor(Gate.LINE)
            if index == self.current:
                base = QColor(Gate.ACCENT)
            painter.setBrush(base)
            painter.setPen(QPen(QColor(Gate.LINE_SOFT), 1))
            painter.drawRoundedRect(rect, 2, 2)
            if not entry.length_known:
                # Hatched: this length is a guess.
                painter.setBrush(QBrush(QColor(Gate.TEXT_DIM), Qt.BrushStyle.BDiagPattern))
                painter.drawRoundedRect(rect, 2, 2)
            if rect.width() > 30:
                painter.setPen(QColor(Gate.TEXT_ON_ACCENT if index == self.current else Gate.TEXT_2))
                text = strip_label(painter.fontMetrics(), entry.name, int(rect.width()) - 6)
                painter.drawText(rect.adjusted(3, 0, -3, 0), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

    def mousePressEvent(self, event):
        index = self.block_at(event.position().x())
        if index >= 0:
            self.shot_clicked.emit(index)

    def mouseMoveEvent(self, event):
        index = self.block_at(event.position().x())
        if 0 <= index < len(self.entries):
            entry = self.entries[index]
            length = (f"{entry.frames_text()} frames" if entry.length_known
                      else "length unknown")
            self.setToolTip(f"{entry.reel} {entry.name} - {length}")


class _ProxyJob(QThread):
    done = Signal(object)

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = jobs

    def run(self):
        from slate.core.domain.proxy_builder import build
        self.done.emit(build(self.jobs))


class LineupPreview(QWidget):
    """Scrub and play the lineup inside Slate."""

    shot_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []
        self.index = -1
        self.continuous = False
        self._seen_playing = False
        self._proxy_job = None
        self.project_root = None
        self.folder_resolver = None

        layout = QVBoxLayout(self)
        # Clear of the splitter, like the rest of the page (MED2-051).
        layout.setContentsMargins(10, 0, 0, 0)
        layout.setSpacing(6)

        self.strip = LineupStrip()
        self.strip.shot_clicked.connect(lambda i: self.show_shot(i, autoplay=self.continuous))
        layout.addWidget(self.strip)

        self.player = AdvancedPlayer()
        self.player.setMinimumHeight(220)
        self.player.prev_requested.connect(lambda: self.step(-1))
        self.player.next_requested.connect(lambda: self.step(1))
        self.player.set_context("shot")
        self.player.media_finished.connect(self._on_finished)
        self.player.playing_changed.connect(self._on_playing)
        layout.addWidget(self.player, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.lbl_shot = QLabel("No shot")
        self.lbl_shot.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
        row.addWidget(self.lbl_shot)
        row.addStretch(1)
        row.addWidget(QLabel("Layer"))
        self.combo_layer = QComboBox()
        self.combo_layer.setToolTip("Which department to watch. The plate stands in for a "
                                    "department that has not rendered this shot yet.")
        # During Play lineup the new layer goes on playing (MED2-044).
        self.combo_layer.currentIndexChanged.connect(
            lambda _i: self.show_shot(self.index, autoplay=self.continuous))
        row.addWidget(self.combo_layer)
        self.btn_play_all = make_button("Play lineup", "primary", icon="play",
                                        tooltip="Play every shot in order, from this one")
        self.btn_play_all.setCheckable(True)
        self.btn_play_all.toggled.connect(self.set_continuous)
        row.addWidget(self.btn_play_all)
        layout.addLayout(row)

        notice = QHBoxLayout()
        self.lbl_media = QLabel("")
        self.lbl_media.setWordWrap(True)
        self.lbl_media.setStyleSheet(f"color: {Gate.TEXT_DIM};")
        notice.addWidget(self.lbl_media, 1)
        self.btn_make_proxy = make_button("Make proxy", "secondary", icon="film",
                                          tooltip="Make a review MP4 of this shot so it plays smoothly")
        self.btn_make_proxy.clicked.connect(self.make_proxy_for_current)
        self.btn_make_proxy.hide()
        notice.addWidget(self.btn_make_proxy)
        layout.addLayout(notice)

    # ------------------------------------------------------------ content
    def set_lineup(self, entries, layout=None):
        """
        A new lineup. The shot being watched stays, at its new place, when it
        is still in it - ticking another shot threw the player back to the
        first one (MED2-043); otherwise the nearest one is shown.
        """
        watching = self.entries[self.index] if 0 <= self.index < len(self.entries) else None
        was_index, was_layer = self.index, self.layer()
        self.entries = list(entries or [])
        self.strip.set_entries(self.entries)
        self.combo_layer.blockSignals(True)
        self.combo_layer.clear()
        from slate.core.domain.lineup import layout_for
        for label, key in (layout or layout_for(self.entries)):
            self.combo_layer.addItem(label, key)
        self.combo_layer.setCurrentIndex(max(0, self.combo_layer.findData(was_layer)))
        self.combo_layer.blockSignals(False)
        self.index = -1
        if watching in self.entries and self.layer() == was_layer:
            keep = self.entries.index(watching)
            self.index = keep
            self.strip.set_current(keep)
            self.lbl_shot.setText(f"{watching.reel}  {watching.name}  ·  {keep + 1} of "
                                  f"{len(self.entries)}")
        elif self.entries:
            self.show_shot(self.entries.index(watching) if watching in self.entries
                           else min(max(was_index, 0), len(self.entries) - 1))
        else:
            self.player.stop_media()
            self.player.screen.set_text("No shots with a scan to show yet.")
            self.lbl_shot.setText("No shot")
            self.lbl_media.setText("")
            self.btn_make_proxy.hide()

    def layer(self) -> str:
        return self.combo_layer.currentData() or "scan"

    def show_shot(self, index, autoplay=False):
        if not (0 <= index < len(self.entries)):
            return
        self.index = index
        self.strip.set_current(index)
        entry = self.entries[index]
        path, kind, clip = media_for(entry, self.layer())
        shown_layer = getattr(clip, "department", "") or "scan"
        layer_label = self.combo_layer.currentText() or "Scan"
        self.lbl_shot.setText(f"{entry.reel}  {entry.name}  ·  {index + 1} of {len(self.entries)}")
        if path is None:
            self.player.stop_media()
            self.player.screen.set_text("Nothing to play for this shot.")
            return
        notes = []
        if shown_layer != self.layer():
            notes.append(f"No {layer_label} render yet - showing the plate.")
        heavy = kind == "frames" and Path(str(path)).suffix.lower() in HEAVY
        if kind == "proxy":
            notes.append("Playing the review proxy.")
        elif heavy:
            notes.append("Playing the frames directly, which can stutter. A proxy plays smoothly.")
        self.lbl_media.setText(" ".join(notes))
        self.btn_make_proxy.setVisible(heavy and self._proxy_job is None)
        self._seen_playing = False
        self.player.btn_loop.setChecked(not self.continuous)
        self.player._pending_autoplay = bool(autoplay)
        options = {}
        if kind == "proxy" and clip.is_sequence:
            # The plate's own frame numbers, not 1 / 24 (MED2-047).
            from slate.core.domain.proxy_builder import first_frame_file
            options = {"audio_source": str(first_frame_file(clip)),
                       "first_frame": clip.first_frame or None}
        self.player.load(str(path), **options)
        self.shot_changed.emit(index)

    def step(self, direction):
        self.show_shot(self.index + direction, autoplay=self.continuous)

    # ------------------------------------------------------------ playing
    def set_continuous(self, on):
        self.continuous = bool(on)
        self.player.btn_loop.setChecked(not self.continuous)
        self.btn_play_all.setText("Stop lineup" if on else "Play lineup")
        if on:
            if self.index < 0 and self.entries:
                self.show_shot(0, autoplay=True)
            else:
                self.player.play()
        else:
            self.player.pause()

    def _on_playing(self, playing):
        if playing:
            self._seen_playing = True

    def _on_finished(self):
        """The clip ran out: in "Play lineup", on to the next shot."""
        if not (self.continuous and self._seen_playing):
            return
        if self.index + 1 < len(self.entries):
            self.show_shot(self.index + 1, autoplay=True)
        else:
            self.btn_play_all.setChecked(False)

    # ------------------------------------------------------------ proxies
    def make_proxy_for_current(self):
        """Make the review proxy of the current shot's shown layer, on request."""
        if not (0 <= self.index < len(self.entries)) or self._proxy_job is not None:
            return
        from slate.core.domain.proxy_builder import (
            ProxyJob, first_frame_file, needs_proxy, proxy_path_for,
        )
        entry = self.entries[self.index]
        _path, _kind, clip = media_for(entry, self.layer())
        if clip is None or not needs_proxy(clip):
            return
        job = ProxyJob(shot_name=entry.name, department=clip.department or "scan",
                       source=first_frame_file(clip), target=proxy_path_for(clip, entry.name),
                       is_sequence=clip.is_sequence)
        self.btn_make_proxy.setEnabled(False)
        self.btn_make_proxy.setText("Making proxy…")
        worker = _ProxyJob([job], self)
        worker.done.connect(self._on_proxy_done)
        worker.finished.connect(worker.deleteLater)
        self._proxy_job = worker
        worker.start()

    def _on_proxy_done(self, result):
        from ...components.feedback import toast
        self._proxy_job = None
        self.btn_make_proxy.setEnabled(True)
        self.btn_make_proxy.setText("Make proxy")
        if result.built:
            toast(self, f"Proxy made: {result.built[0]}.", "success")
            self.show_shot(self.index)
        else:
            toast(self, "The proxy could not be made.", "error", details=result.summary())

    def busy_reason(self):
        if self._proxy_job is not None and self._proxy_job.isRunning():
            return "The Timeline Viewer is still making a proxy."
        return None

    def cleanup(self):
        self.player.stop_media()
        if self._proxy_job is not None:
            self._proxy_job.wait(10000)
