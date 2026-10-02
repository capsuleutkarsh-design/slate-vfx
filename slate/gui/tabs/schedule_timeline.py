"""
The schedule as a Gantt timeline, and as people.

The sidebar has always promised "Gantt Charts"; the tab was a seven-column
table. This draws the same milestones (same filters, same selection) as bars
on a calendar:

    Timeline   one row per milestone, grouped under its project (click a
               project to fold it); bars coloured by status, overdue ones
               outlined in red, finished ones faded; dependency arrows from
               the end of one bar to the start of the next, red when the
               order is broken; weekly offs and studio holidays shaded (hover
               a holiday for its name); a line for today. Drag a bar to move
               it, drag its right edge to change its end - both go through
               the same shift preview as the Shift dates button, so what
               depends on it is shown before anything is saved. Double-click
               opens the milestone.
    People     one lane per person: the milestones they own and their
               dashboard assignments (read-only here - the dashboard owns
               those), their approved leave shaded "away", and a red line
               under any day booked with more than a day of work.

Zoom: Day / Week / Month, Ctrl + mouse wheel, or Fit. Export PNG saves what
is drawn. The maths (dates to pixels, ticks, rows, arrow routes) lives in
core/domain/scheduling.py where it is tested on its own.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QGridLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QSplitter, QVBoxLayout, QWidget,
)

from slate.core.domain import scheduling as DS
from slate.core.domain.dates import format_date, format_range
from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button
from slate.gui.core.table_style import status_colour

logger = logging.getLogger(__name__)

ROW_H = 28
HEADER_H = 26
RULER_H = 50
LABEL_W = 260
BAR_PAD = 5


def _colour(value: str, alpha: Optional[float] = None) -> QColor:
    return Gate.qcolor(value, alpha) if alpha is not None else QColor(value)


# ------------------------------------------------------------------ bars

class _Bar(QGraphicsRectItem):
    """One bar. Paints its own label; drags in whole days when it may move."""

    EDGE = 6

    def __init__(self, gantt: "GanttView", key, rect: QRectF, fill: str, text: str,
                 tooltip: str, *, outline: str = "", faded: float = 1.0, strike: bool = False,
                 draggable: bool = False, selected: bool = False, dim_text: bool = False):
        super().__init__(rect)
        self.gantt = gantt
        self.key = key
        self.fill = fill
        self.text = text
        self.outline = outline
        self.strike = strike
        self.draggable = draggable
        self.is_selected = selected
        self.dim_text = dim_text
        self.setOpacity(faded)
        self.setToolTip(tooltip)
        self.setAcceptHoverEvents(True)
        self.setZValue(5)
        self._press = None
        self._mode = ""
        self._origin = QRectF(rect)

    def paint(self, painter: QPainter, option, widget=None):
        rect = self.rect()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QBrush(_colour(self.fill, 0.88)))
        if self.is_selected:
            painter.setPen(QPen(_colour(Gate.TEXT), 2))
        elif self.outline:
            painter.setPen(QPen(_colour(self.outline), 2))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 4, 4)
        if self.strike:
            painter.setPen(QPen(_colour(Gate.TEXT_ON_BAD), 1))
            painter.drawLine(QPointF(rect.left() + 3, rect.center().y()),
                             QPointF(rect.right() - 3, rect.center().y()))
        if rect.width() > 24 and self.text:
            font = QFont(painter.font())
            font.setPixelSize(Gate.SIZE_SM)
            painter.setFont(font)
            metrics = QFontMetrics(font)
            text = metrics.elidedText(self.text, Qt.TextElideMode.ElideRight, int(rect.width() - 10))
            painter.setPen(_colour(Gate.TEXT_ON_BAD if not self.dim_text else Gate.TEXT))
            painter.drawText(rect.adjusted(6, 0, -4, 0),
                             int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)

    # -- dragging
    def _near_edge(self, pos) -> bool:
        return self.rect().right() - pos.x() <= self.EDGE

    def hoverMoveEvent(self, event):
        if self.draggable:
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._near_edge(event.pos())
                           else Qt.CursorShape.OpenHandCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        self.gantt._bar_clicked(self.key)
        if self.draggable and event.button() == Qt.MouseButton.LeftButton:
            self._press = event.scenePos()
            self._mode = "resize" if self._near_edge(event.pos()) else "move"
            self._origin = QRectF(self.rect())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is None:
            return super().mouseMoveEvent(event)
        ppd = self.gantt.scale.px_per_day
        days = round((event.scenePos().x() - self._press.x()) / ppd)
        rect = QRectF(self._origin)
        if self._mode == "move":
            rect.translate(days * ppd, 0)
        else:
            rect.setRight(max(rect.left() + ppd, self._origin.right() + days * ppd))
        self.setRect(rect)
        self.gantt._show_drag_hint(self.key, self._mode, days)

    def mouseReleaseEvent(self, event):
        if self._press is None:
            return super().mouseReleaseEvent(event)
        ppd = self.gantt.scale.px_per_day
        days = round((event.scenePos().x() - self._press.x()) / ppd)
        mode, self._press = self._mode, None
        self.setRect(self._origin)
        self.gantt._show_drag_hint(None, "", 0)
        if days:
            self.gantt._bar_dragged(self.key, mode, days)

    def mouseDoubleClickEvent(self, event):
        self.gantt._bar_activated(self.key)


class _Header(QGraphicsRectItem):
    """A clickable label row (project to fold, person)."""

    def __init__(self, gantt, key, rect):
        super().__init__(rect)
        self.gantt = gantt
        self.key = key
        self.setPen(Qt.PenStyle.NoPen)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        self.gantt._header_clicked(self.key)


class _View(QGraphicsView):
    def __init__(self, scene, gantt, *, wheel_zoom=False):
        super().__init__(scene)
        self.gantt = gantt
        self.wheel_zoom = wheel_zoom
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setBackgroundBrush(QBrush(_colour(Gate.GROUND)))

    def wheelEvent(self, event):
        if self.wheel_zoom and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.gantt.zoom_by(1.25 if event.angleDelta().y() > 0 else 0.8)
            event.accept()
            return
        super().wheelEvent(event)


# ------------------------------------------------------------------ the widget

class GanttView(QWidget):
    """
    The drawing: a fixed ruler on top, fixed labels on the left, the bars in
    the scrolling body. show_milestones() / show_people() choose what is drawn.
    """

    activated = Signal(int)                 # milestone id double-clicked
    selected = Signal(int)                  # milestone id clicked
    dragged = Signal(int, object, object)   # milestone id, new start, new end

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zoom = DS.ZOOM_WEEK
        self.scale = DS.TimeScale(date.today(), DS.ZOOMS[self.zoom])
        self.first = self.last = date.today()
        self.collapsed: Set[str] = set()
        self.selected_ids: Set[int] = set()
        self._render = None                 # how to draw again (zoom, fold)
        self._bars: Dict[object, _Bar] = {}
        self._drag_info = None
        self.bar_count = 0
        self.arrow_count = 0
        self.shade_count = 0
        self.today_line = None

        self.ruler_scene, self.label_scene, self.body_scene = (QGraphicsScene(self) for _ in range(3))
        self.ruler = _View(self.ruler_scene, self)
        self.labels = _View(self.label_scene, self)
        self.body = _View(self.body_scene, self, wheel_zoom=True)
        for view in (self.ruler, self.labels):
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.ruler.setFixedHeight(RULER_H)
        self.labels.setFixedWidth(LABEL_W)
        self.body.horizontalScrollBar().valueChanged.connect(self.ruler.horizontalScrollBar().setValue)
        self.body.verticalScrollBar().valueChanged.connect(self.labels.verticalScrollBar().setValue)

        self.corner = QLabel("")
        self.corner.setFixedSize(LABEL_W, RULER_H)
        self.corner.setStyleSheet(f"color: {Gate.TEXT_2}; font-weight: 600; padding-left: 8px; "
                                  f"background: {Gate.PANEL}; border-bottom: 1px solid {Gate.LINE};")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.corner, 0, 0)
        grid.addWidget(self.ruler, 0, 1)
        grid.addWidget(self.labels, 1, 0)
        grid.addWidget(self.body, 1, 1)

    # ------------------------------------------------------------ zoom
    def set_zoom(self, zoom: str) -> None:
        if zoom in DS.ZOOMS:
            self.zoom = zoom
            self._redraw(DS.ZOOMS[zoom])

    def zoom_by(self, factor: float) -> None:
        ppd = min(max(self.scale.px_per_day * factor, 1.0), 60.0)
        self.zoom = DS.ZOOM_DAY if ppd >= 20 else (DS.ZOOM_WEEK if ppd >= 6 else DS.ZOOM_MONTH)
        self._redraw(ppd)

    def fit(self) -> None:
        days = max((getattr(self, "content_last", self.last) - self.first).days + 1, 1)
        width = max(self.body.viewport().width() - 4, 200)
        ppd = min(max(width / days, 1.0), 60.0)
        self.zoom = DS.ZOOM_DAY if ppd >= 20 else (DS.ZOOM_WEEK if ppd >= 6 else DS.ZOOM_MONTH)
        self._redraw(ppd)

    def scroll_to(self, day: date) -> None:
        x = self.scale.x(day) - self.body.viewport().width() / 3
        self.body.horizontalScrollBar().setValue(int(max(x, 0)))

    def _redraw(self, ppd: float) -> None:
        keep = self.body.horizontalScrollBar().value() / max(self.scale.px_per_day, 0.1)
        self.scale = DS.TimeScale(self.first, ppd)
        if self._render:
            self._render()
        self.body.horizontalScrollBar().setValue(int(keep * ppd))

    # ------------------------------------------------------------ callbacks from items
    def _bar_clicked(self, key):
        if isinstance(key, int):
            self.set_selected({key})
            self.selected.emit(key)

    def _bar_activated(self, key):
        if isinstance(key, int):
            self.activated.emit(key)

    def _bar_dragged(self, key, mode, days):
        info = self._drag_info or {}
        m = info.get(key)
        if m is None or m.start is None or m.end is None:
            return
        delta = timedelta(days=days)
        if mode == "move":
            self.dragged.emit(key, m.start + delta, m.end + delta)
        else:
            self.dragged.emit(key, m.start, max(m.end + delta, m.start))

    def _show_drag_hint(self, key, mode, days):
        info = (self._drag_info or {}).get(key)
        if info is None or not days:
            self.corner.setText(self._corner_text)
            return
        what = "Move" if mode == "move" else "End"
        self.corner.setText(f"{what} {'+' if days > 0 else ''}{days} d")

    def _header_clicked(self, key):
        if isinstance(key, str) and key.startswith("project:"):
            code = key.split(":", 1)[1]
            self.collapsed.symmetric_difference_update({code})
            if self._render:
                self._render()

    def set_selected(self, ids: Iterable[int]) -> None:
        self.selected_ids = {int(i) for i in ids if i is not None}
        for key, bar in self._bars.items():
            bar.is_selected = key in self.selected_ids
            bar.update()

    # ------------------------------------------------------------ drawing helpers
    def _reset(self, first: date, last: date, corner: str, ppd: Optional[float] = None):
        self.scale = DS.TimeScale(first, ppd or self.scale.px_per_day)
        # Draw at least as far as the window is wide, so the calendar does not
        # stop in the middle of the screen.
        room = int(self.body.viewport().width() / max(self.scale.px_per_day, 0.1)) + 1
        self.content_last = last
        last = max(last, first + timedelta(days=room))
        self.first, self.last = first, last
        for scene in (self.ruler_scene, self.label_scene, self.body_scene):
            scene.clear()
        self._bars = {}
        self.bar_count = self.arrow_count = self.shade_count = 0
        self._corner_text = corner
        self.corner.setText(corner)

    def _draw_ruler(self, width: float, today: date):
        scene = self.ruler_scene
        scene.addRect(QRectF(0, 0, width, RULER_H), QPen(Qt.PenStyle.NoPen),
                      QBrush(_colour(Gate.PANEL)))
        line_pen = QPen(_colour(Gate.LINE), 1)
        for start, end, label in DS.month_bands(self.first, self.last):
            x = self.scale.x(start)
            scene.addLine(x, 0, x, RULER_H, line_pen)
            text = QGraphicsSimpleTextItem(label)
            text.setBrush(QBrush(_colour(Gate.TEXT_2)))
            text.setPos(x + 4, 2)
            scene.addItem(text)
        for day, label, major in DS.ruler_ticks(self.first, self.last, self.zoom):
            x = self.scale.x(day)
            scene.addLine(x, RULER_H - (14 if major else 9), x, RULER_H, line_pen)
            if self.zoom != DS.ZOOM_MONTH or self.scale.px_per_day * 28 > 60:
                text = QGraphicsSimpleTextItem(label)
                font = text.font()
                font.setPixelSize(Gate.SIZE_XS)
                text.setFont(font)
                text.setBrush(QBrush(_colour(Gate.TEXT if major else Gate.TEXT_DIM)))
                text.setPos(x + 3, RULER_H - 22)
                scene.addItem(text)
        scene.addLine(0, RULER_H - 1, width, RULER_H - 1, line_pen)
        if self.first <= today <= self.last:
            x = self.scale.x(today) + self.scale.px_per_day / 2
            scene.addLine(x, RULER_H - 16, x, RULER_H, QPen(_colour(Gate.ACCENT), 2))
        scene.setSceneRect(QRectF(0, 0, width, RULER_H))

    def _draw_days(self, width: float, height: float, calendar: DS.WorkCalendar, today: date):
        """Weekly offs and holidays shaded across every row, and the today line."""
        scene = self.body_scene
        scene.addRect(QRectF(0, 0, width, height), QPen(Qt.PenStyle.NoPen),
                      QBrush(_colour(Gate.GROUND)))
        cursor = self.first
        ppd = self.scale.px_per_day
        while cursor <= self.last:
            if not calendar.is_working(cursor):
                holiday = cursor in calendar.holidays
                colour = Gate.tint(Gate.WARN, 0.14) if holiday else Gate.overlay(0.045)
                rect = scene.addRect(QRectF(self.scale.x(cursor), 0, ppd, height),
                                     QPen(Qt.PenStyle.NoPen), QBrush(QColor(_rgba(colour))))
                rect.setZValue(0)
                rect.setToolTip(f"{format_date(cursor, weekday=True)}: "
                                f"{calendar.why_not_working(cursor)}")
                self.shade_count += 1
            cursor += timedelta(days=1)
        if self.zoom != DS.ZOOM_MONTH:
            pen = QPen(_colour(Gate.LINE_SOFT or Gate.LINE), 1)
            for day, _label, major in DS.ruler_ticks(self.first, self.last, self.zoom):
                if major or self.zoom == DS.ZOOM_WEEK:
                    line = scene.addLine(self.scale.x(day), 0, self.scale.x(day), height, pen)
                    line.setZValue(1)
        self.today_line = None
        if self.first <= today <= self.last:
            x = self.scale.x(today) + ppd / 2
            self.today_line = scene.addLine(x, 0, x, height, QPen(_colour(Gate.ACCENT), 2))
            self.today_line.setZValue(8)
            self.today_line.setToolTip(f"Today, {format_date(today)}")

    def _row_band(self, y: float, h: float, width: float, *, header: bool, label: str,
                  key=None, detail: str = "", bold: bool = False, tooltip: str = ""):
        """A label on the left and a band across the body."""
        fill = Gate.PANEL if header else Gate.GROUND
        band = _Header(self, key, QRectF(0, y, LABEL_W, h)) if key else None
        if band is not None:
            band.setBrush(QBrush(_colour(fill)))
            self.label_scene.addItem(band)
        else:
            self.label_scene.addRect(QRectF(0, y, LABEL_W, h), QPen(Qt.PenStyle.NoPen),
                                     QBrush(_colour(fill)))
        text = QGraphicsSimpleTextItem()
        font = text.font()
        font.setPixelSize(Gate.SIZE_SM if not header else Gate.SIZE_MD)
        font.setBold(bold or header)
        text.setFont(font)
        metrics = QFontMetrics(font)
        room = LABEL_W - 16 - (metrics.horizontalAdvance(detail) + 8 if detail else 0)
        text.setText(metrics.elidedText(label, Qt.TextElideMode.ElideRight, int(room)))
        text.setBrush(QBrush(_colour(Gate.TEXT if header or bold else Gate.TEXT_2)))
        text.setPos(8 if header else 18, y + (h - metrics.height()) / 2)
        text.setToolTip(tooltip or label)
        self.label_scene.addItem(text)
        if detail:
            d = QGraphicsSimpleTextItem(detail)
            d.setFont(font)
            d.setBrush(QBrush(_colour(Gate.TEXT_DIM)))
            d.setPos(LABEL_W - 8 - metrics.horizontalAdvance(detail), y + (h - metrics.height()) / 2)
            self.label_scene.addItem(d)
        self.label_scene.addLine(0, y + h - 0.5, LABEL_W, y + h - 0.5,
                                 QPen(_colour(Gate.LINE_SOFT or Gate.LINE), 1))
        if header:
            r = self.body_scene.addRect(QRectF(0, y, width, h), QPen(Qt.PenStyle.NoPen),
                                        QBrush(_colour(Gate.PANEL, 0.55)))
            r.setZValue(2)
        line = self.body_scene.addLine(0, y + h - 0.5, width, y + h - 0.5,
                                       QPen(_colour(Gate.LINE_SOFT or Gate.LINE), 1))
        line.setZValue(1)

    def _finish(self, width: float, height: float):
        height = max(height, 1)
        self.label_scene.setSceneRect(QRectF(0, 0, LABEL_W, height))
        self.body_scene.setSceneRect(QRectF(0, 0, width, height))

    def _arrow(self, a: QRectF, b: QRectF, broken: bool):
        points = DS.arrow_route(a.right(), a.center().y(), b.left(), b.center().y())
        path = QPainterPath(QPointF(*points[0]))
        for p in points[1:]:
            path.lineTo(QPointF(*p))
        colour = Gate.BAD if broken else Gate.TEXT_DIM
        item = QGraphicsPathItem(path)
        item.setPen(QPen(_colour(colour), 1.4))
        item.setZValue(4)
        item.setToolTip("This starts before what it waits on has ended." if broken
                        else "Waits on the bar it comes from.")
        self.body_scene.addItem(item)
        tip = QPointF(*points[-1])
        head = QPolygonF([tip, QPointF(tip.x() - 6, tip.y() - 4), QPointF(tip.x() - 6, tip.y() + 4)])
        head_item = self.body_scene.addPolygon(head, QPen(Qt.PenStyle.NoPen), QBrush(_colour(colour)))
        head_item.setZValue(4)
        self.arrow_count += 1

    # ------------------------------------------------------------ milestones
    def show_milestones(self, milestones: Sequence[DS.Milestone], calendar: DS.WorkCalendar,
                        *, today: Optional[date] = None, all_by_id: Dict[int, DS.Milestone] = None,
                        editable: bool = False) -> None:
        def render():
            self._draw_milestones(milestones, calendar, today or date.today(),
                                  all_by_id or DS.index(milestones), editable)
        self._render = render
        render()

    def _draw_milestones(self, milestones, calendar, today, by_id, editable):
        first, last = DS.timeline_range(milestones, today)
        self._reset(first, last, "Milestone")
        self._drag_info = {m.id: m for m in milestones}
        width = self.scale.x_end(self.last)
        groups: "Dict[str, List[DS.Milestone]]" = {}
        names: Dict[str, str] = {}
        for m in milestones:
            groups.setdefault(m.project_code, []).append(m)
            names.setdefault(m.project_code, m.project_name)

        layout = []          # (kind, payload, y, h)
        y = 0.0
        for code in sorted(groups, key=str.casefold):
            layout.append(("project", code, y, HEADER_H))
            y += HEADER_H
            if code in self.collapsed:
                continue
            for m in sorted(groups[code], key=lambda m: (m.start or date.max, m.id or 0)):
                layout.append(("milestone", m, y, ROW_H))
                y += ROW_H
        height = max(y, self.body.viewport().height())
        self._draw_ruler(width, today)
        self._draw_days(width, height, calendar, today)

        rects: Dict[int, QRectF] = {}
        for kind, payload, ry, rh in layout:
            if kind == "project":
                code = payload
                count = len(groups[code])
                open_count = sum(1 for m in groups[code] if m.is_open)
                fold = "▸ " if code in self.collapsed else "▾ "
                title = code + (f" – {names[code]}" if names.get(code) and
                                names[code].casefold() != code.casefold() else "")
                self._row_band(ry, rh, width, header=True, label=fold + title,
                               key=f"project:{code}", detail=f"{open_count}/{count}",
                               tooltip=f"{title}: {open_count} open of {count}. Click to "
                                       f"{'show' if code in self.collapsed else 'hide'} its milestones.")
                # A summary bar across the project's span.
                dated = [m for m in groups[code] if m.has_dates]
                if dated:
                    s = min(m.start for m in dated)
                    e = max(m.end for m in dated)
                    r = QRectF(self.scale.x(s), ry + HEADER_H / 2 - 2, self.scale.width(s, e), 4)
                    bar = self.body_scene.addRect(r, QPen(Qt.PenStyle.NoPen),
                                                  QBrush(_colour(Gate.TEXT_DIM, 0.6)))
                    bar.setZValue(3)
                    bar.setToolTip(f"{title}: {format_range(s, e)}")
                continue
            m: DS.Milestone = payload
            detail = ""
            if not m.has_dates:
                detail = "no dates"
            self._row_band(ry, rh, width, header=False, label=m.name, detail=detail,
                           tooltip=self._tooltip(m, by_id, calendar, today))
            if not m.has_dates:
                continue
            start, end = (m.start, m.end) if not m.dates_reversed else (m.end, m.start)
            rect = QRectF(self.scale.x(start), ry + BAR_PAD, self.scale.width(start, end),
                          rh - 2 * BAR_PAD)
            overdue = DS.is_overdue(m, today)
            # The status keeps its colour; overdue is the red outline (the legend says so).
            tone = DS.status_tone(m.status)
            fill = status_colour(tone) if tone != "idle" else Gate.TEXT_DIM
            bar = _Bar(self, m.id, rect, fill, m.name, self._tooltip(m, by_id, calendar, today),
                       outline=Gate.BAD if (overdue or m.dates_reversed) else "",
                       faded=0.45 if not m.is_open else 1.0,
                       strike=DS.normalise_status(m.status) == DS.CANCELLED,
                       draggable=editable and m.is_open and not m.archived,
                       selected=m.id in self.selected_ids)
            self.body_scene.addItem(bar)
            self._bars[m.id] = bar
            rects[m.id] = rect
            self.bar_count += 1

        for kind, m, _y, _h in layout:
            if kind != "milestone" or not m.depends_on_id:
                continue
            parent = by_id.get(m.depends_on_id)
            if parent is None or parent.id not in rects or m.id not in rects:
                continue
            broken = bool(parent.end and m.start and m.start <= parent.end and m.is_open)
            self._arrow(rects[parent.id], rects[m.id], broken)
        self._finish(width, height)

    @staticmethod
    def _tooltip(m: DS.Milestone, by_id, calendar, today) -> str:
        lines = [m.name, f"{m.project_code}" + (f" – {m.project_name}" if m.project_name else "")]
        if m.has_dates:
            lines.append(f"{format_range(m.start, m.end)} · "
                         f"{DS.plural(calendar.working_days(m.start, m.end), 'working day')}")
        else:
            lines.append("No dates")
        status = m.status or "No status"
        if DS.is_overdue(m, today):
            status += f" · {DS.plural(DS.days_late(m, today), 'day')} late"
        lines.append(status)
        dep, _tone = DS.dependency_label(m, by_id)
        if dep:
            lines.append(f"Depends on {dep}")
        if m.owner:
            try:
                from slate.core.domain.people import display_name
                lines.append(f"Owner: {display_name(m.owner) or m.owner}")
            except Exception:
                lines.append(f"Owner: {m.owner}")
        return "\n".join(lines)

    # ------------------------------------------------------------ people
    def show_people(self, plan: DS.PeoplePlan, calendar: DS.WorkCalendar, *,
                    today: Optional[date] = None, names: Dict[str, str] = None,
                    milestone_tone: Dict[int, str] = None) -> None:
        def render():
            self._draw_people(plan, calendar, today or date.today(), names or {},
                              milestone_tone or {})
        self._render = render
        render()

    def _draw_people(self, plan, calendar, today, names, milestone_tone):
        spans = [today]
        for items in plan.items.values():
            for item in items:
                spans += [item.start, item.end]
        for away in plan.away.values():
            for a in away:
                spans += [a.start, a.end]
        first = min(spans) - timedelta(days=7)
        first -= timedelta(days=first.weekday())
        last = max(spans) + timedelta(days=7)
        self._reset(first, last, "Person")
        self._drag_info = {}
        width = self.scale.x_end(self.last)

        layout = []
        y = 0.0
        for person in plan.people:
            items = plan.items.get(person, [])
            rows = DS.pack_rows([(i.start, i.end) for i in items]) if items else []
            lanes = (max(rows) + 1) if rows else 1
            h = lanes * ROW_H
            layout.append((person, items, rows, y, h))
            y += h
        height = max(y, self.body.viewport().height())
        self._draw_ruler(width, today)
        self._draw_days(width, height, calendar, today)
        conflicts_by_person: Dict[str, List[DS.PeopleConflict]] = {}
        for c in plan.conflicts:
            conflicts_by_person.setdefault(c.person, []).append(c)

        for person, items, rows, ry, rh in layout:
            shown = names.get(person) or person
            trouble = conflicts_by_person.get(person, [])
            self._row_band(ry, rh, width, header=False, bold=True, label=shown,
                           detail=f"{len(items)}" if items else "",
                           tooltip=f"{shown}: {DS.plural(len(items), 'piece')} of work"
                                   + (f", {DS.plural(len(trouble), 'problem')}" if trouble else ""))
            for a in plan.away.get(person, []):
                r = self.body_scene.addRect(
                    QRectF(self.scale.x(a.start), ry, self.scale.width(a.start, a.end), rh),
                    QPen(Qt.PenStyle.NoPen), QBrush(QColor(_rgba(Gate.tint(Gate.BAD, 0.16)))))
                r.setZValue(2)
                r.setToolTip(f"Away: {a.reason} ({format_range(a.start, a.end)})")
            for item, row in zip(items, rows):
                rect = QRectF(self.scale.x(item.start), ry + row * ROW_H + BAR_PAD,
                              self.scale.width(item.start, item.end), ROW_H - 2 * BAR_PAD)
                if item.kind == "milestone":
                    fill = status_colour(milestone_tone.get(item.ref, "info"))
                    dim = False
                else:
                    fill = Gate.mix(Gate.GROUND, Gate.INFO, 0.45)
                    dim = True
                tip = (f"{item.label}\n{item.project_code}\n{format_range(item.start, item.end)}"
                       + (f"\n{item.days.normalize():f} days of work" if item.days else "")
                       + ("\nFrom the VFX Dashboard (read-only here)" if item.kind == "task" else ""))
                bar = _Bar(self, item.ref if item.kind == "milestone" else ("task", id(item)),
                           rect, fill, item.label, tip, dim_text=dim,
                           selected=item.kind == "milestone" and item.ref in self.selected_ids)
                self.body_scene.addItem(bar)
                if item.kind == "milestone":
                    self._bars[item.ref] = bar
                self.bar_count += 1
            for c in trouble:
                r = self.body_scene.addRect(
                    QRectF(self.scale.x(c.start), ry + rh - 4, self.scale.width(c.start, c.end), 3),
                    QPen(Qt.PenStyle.NoPen), QBrush(_colour(Gate.BAD)))
                r.setZValue(9)
                r.setToolTip(c.text)
        self._finish(width, height)

    # ------------------------------------------------------------ export
    def image(self) -> QImage:
        """Ruler, labels and every bar in one picture (what Export PNG saves)."""
        body = self.body_scene.sceneRect()
        width = int(LABEL_W + body.width())
        height = int(RULER_H + body.height())
        scale = 1.0
        if width * height > 60_000_000:          # keep the file sane for a huge plan
            scale = (60_000_000 / (width * height)) ** 0.5
        image = QImage(max(int(width * scale), 1), max(int(height * scale), 1),
                       QImage.Format.Format_ARGB32)
        image.fill(_colour(Gate.GROUND))
        painter = QPainter(image)
        painter.scale(scale, scale)
        self.ruler_scene.render(painter, QRectF(LABEL_W, 0, body.width(), RULER_H),
                                QRectF(0, 0, body.width(), RULER_H))
        self.label_scene.render(painter, QRectF(0, RULER_H, LABEL_W, body.height()),
                                QRectF(0, 0, LABEL_W, body.height()))
        self.body_scene.render(painter, QRectF(LABEL_W, RULER_H, body.width(), body.height()), body)
        painter.end()
        return image


def _rgba(css: str) -> QColor:
    """'rgba(r, g, b, a)' or '#rrggbb' -> QColor (Gate.tint/overlay give rgba text)."""
    text = str(css).strip()
    if text.startswith("rgba"):
        parts = [p.strip() for p in text[text.index("(") + 1:text.rindex(")")].split(",")]
        r, g, b = (int(float(x)) for x in parts[:3])
        colour = QColor(r, g, b)
        colour.setAlphaF(float(parts[3]))
        return colour
    return QColor(text)


class ScheduleTimeline(QWidget):
    """
    The Timeline / People page: zoom, Fit, Today and Export PNG above the
    drawing; in People mode a list of the problems found beside it.
    """

    def __init__(self, parent=None, *, people_mode: bool = False):
        super().__init__(parent)
        self.people_mode = people_mode
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Gate.SPACE_2)

        bar = QHBoxLayout()
        bar.setSpacing(Gate.SPACE_2)
        self.zoom_cb = QComboBox()
        for name in DS.ZOOMS:
            self.zoom_cb.addItem(name, name)
        self.zoom_cb.setCurrentIndex(self.zoom_cb.findData(DS.ZOOM_WEEK))
        self.zoom_cb.setToolTip("Zoom (or Ctrl + mouse wheel over the chart)")
        bar.addWidget(QLabel("Zoom"))
        bar.addWidget(self.zoom_cb)
        self.fit_button = make_button("Fit", "secondary", tooltip="Fit the whole plan in the window",
                                      on_click=lambda: self.gantt.fit())
        self.today_button = make_button("Today", "secondary", tooltip="Scroll to today",
                                        on_click=lambda: self.gantt.scroll_to(date.today()))
        self.export_button = make_button("Export PNG…", "ghost", icon="download",
                                         on_click=self.export_png)
        bar.addWidget(self.fit_button)
        bar.addWidget(self.today_button)
        self.legend = QLabel("")
        self.legend.setStyleSheet(f"color: {Gate.TEXT_DIM}; font-size: {Gate.SIZE_SM}px;")
        bar.addWidget(self.legend, 1)
        bar.addWidget(self.export_button)
        layout.addLayout(bar)

        self.gantt = GanttView(self)
        self.zoom_cb.currentIndexChanged.connect(lambda *_: self.gantt.set_zoom(self.zoom_cb.currentData()))
        if people_mode:
            split = QSplitter(Qt.Orientation.Horizontal)
            split.addWidget(self.gantt)
            side = QWidget()
            side_layout = QVBoxLayout(side)
            side_layout.setContentsMargins(Gate.SPACE_2, 0, 0, 0)
            self.problems_title = QLabel("Problems")
            self.problems_title.setStyleSheet(f"color: {Gate.TEXT}; font-weight: 600;")
            side_layout.addWidget(self.problems_title)
            self.problems = QListWidget()
            self.problems.setWordWrap(True)
            self.problems.itemActivated.connect(self._problem_activated)
            self.problems.itemClicked.connect(self._problem_activated)
            side_layout.addWidget(self.problems, 1)
            split.addWidget(side)
            split.setStretchFactor(0, 4)
            split.setStretchFactor(1, 1)
            split.setSizes([900, 260])
            layout.addWidget(split, 1)
            self.legend.setText("Shaded: weekly offs, holidays (amber) and approved leave (red). "
                                "Red line: more than a day of work booked per day.")
        else:
            layout.addWidget(self.gantt, 1)
            self.problems = None
            self.legend.setText("Drag a bar to move it, its right edge to change the end. "
                                "Red outline: overdue. Red arrow: starts before what it waits on ends.")

    def set_people_problems(self, plan: DS.PeoplePlan, names: Dict[str, str]) -> None:
        if self.problems is None:
            return
        self.problems.clear()
        for c in plan.conflicts:
            who = names.get(c.person) or c.person
            item = QListWidgetItem(f"{who}: {c.text}")
            item.setData(Qt.ItemDataRole.UserRole, c.start.isoformat())
            item.setForeground(QBrush(_colour(Gate.BAD if c.kind == "overload" else Gate.WARN)))
            self.problems.addItem(item)
        if plan.skipped_tasks:
            note = QListWidgetItem(f"{DS.plural(plan.skipped_tasks, 'dashboard assignment')} "
                                   "without a target date or bid days are not drawn.")
            note.setForeground(QBrush(_colour(Gate.TEXT_DIM)))
            self.problems.addItem(note)
        if not plan.conflicts:
            ok = QListWidgetItem("Nobody is booked over their leave or over a day's work.")
            ok.setForeground(QBrush(_colour(Gate.TEXT_DIM)))
            self.problems.insertItem(0, ok)
        self.problems_title.setText(f"Problems ({len(plan.conflicts)})")

    def _problem_activated(self, item):
        value = item.data(Qt.ItemDataRole.UserRole)
        if value:
            self.gantt.scroll_to(date.fromisoformat(value))

    def export_png(self) -> Optional[str]:
        from PySide6.QtWidgets import QFileDialog
        from pathlib import Path
        default = str(Path.home() / "Documents" / ("people.png" if self.people_mode else "schedule.png"))
        path, _ = QFileDialog.getSaveFileName(self, "Export PNG", default, "PNG image (*.png)")
        if not path:
            return None
        from slate.gui.components.feedback import toast
        if self.gantt.image().save(path, "PNG"):
            toast(self, f"Saved {Path(path).name}.", "success")
            return path
        toast(self, f"Could not write {path}.", "error")
        return None
