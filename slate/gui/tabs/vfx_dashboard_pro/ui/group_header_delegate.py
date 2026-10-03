from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QColor, QFontMetrics, QFont
from slate.core.infra.gate import Gate

GROUP_HEADER_ROLE = Qt.ItemDataRole.UserRole + 200


class GroupHeaderDelegate(QStyledItemDelegate):
    """
    Paints a group header row: disclosure arrow, the group's name (as it is
    written - "Vikram Singh", not "VIKRAM SINGH"), the shot count, how many are
    approved, and the group's frames and bid days.

    Everything is drawn from the left edge of what is on screen, not across
    the full 24-column span: the totals used to sit at the far right of a
    2,200 px row, visible only after scrolling. The frozen Reel/Shot columns
    draw the same thing at the same place, so the row reads as one.
    """

    def __init__(self, parent=None, frozen_width=None, frozen=False):
        """
        frozen_width: callable giving the frozen columns' width (0 when none).
        The name and count sit inside the frozen part; the progress pill and
        the totals start after it, so the frozen edge never cuts through them.
        frozen=True is the overlay's own copy: it draws only the name part.
        """
        super().__init__(parent)
        self._frozen_width = frozen_width
        self._is_frozen = frozen

    @staticmethod
    def colours(hovered: bool) -> dict:
        """The header's colours (theme tokens), normal or under the pointer."""
        return {
            "background": QColor(Gate.mix(Gate.PANEL, Gate.ACCENT, 0.16 if hovered else 0.08)),
            "separator": QColor(Gate.LINE),
            "accent": QColor(Gate.ACCENT),
            "title": QColor(Gate.TEXT),
            "muted": QColor(Gate.TEXT_2),
            "badge": Gate.qcolor(Gate.TEXT, 0.08),
            "progress": Gate.qcolor(Gate.OK, 0.22),
            "progress_fill": Gate.qcolor(Gate.OK, 0.38),
            "progress_text": QColor(Gate.OK),
        }

    def paint(self, painter: QPainter, option, index):
        group_data = index.data(GROUP_HEADER_ROLE)
        if not group_data:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        c = self.colours(hovered)

        painter.fillRect(rect, c["background"])
        painter.setPen(c["separator"])
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        # Anchor at the visible left edge: when the grid is scrolled the
        # span starts off screen, but its text should not.
        left = max(rect.left(), 0)
        painter.fillRect(QRect(left, rect.top(), 4, rect.height()), c["accent"])

        base = QFont(option.font)

        # Disclosure arrow.
        is_collapsed = bool(group_data.get("is_collapsed", False))
        arrow_font = QFont(base)
        painter.setFont(arrow_font)
        painter.setPen(c["muted"])
        arrow_rect = QRect(left + 12, rect.top(), 16, rect.height())
        painter.drawText(arrow_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         "▸" if is_collapsed else "▾")

        # Group name, in its own case.
        title_text = str(group_data.get("title", "Group"))
        title_font = QFont(base)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(c["title"])
        fm = QFontMetrics(title_font)
        fw = 0
        if callable(self._frozen_width):
            try:
                fw = int(self._frozen_width() or 0)
            except Exception:
                fw = 0
        count_preview = int(group_data.get("count", 0) or 0)
        count_width = QFontMetrics(base).horizontalAdvance(
            "1 shot" if count_preview == 1 else f"{count_preview} shots") + 16
        limit = 420 if fw <= 0 else max(30, fw - 32 - 8 - count_width - 8)
        title_width = min(fm.horizontalAdvance(title_text) + 8, limit)
        title_rect = QRect(left + 32, rect.top(), title_width, rect.height())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         fm.elidedText(title_text, Qt.TextElideMode.ElideRight, title_width))

        # Shot count.
        count = int(group_data.get("count", 0) or 0)
        count_text = "1 shot" if count == 1 else f"{count} shots"
        badge_font = QFont(base)
        painter.setFont(badge_font)
        fm_badge = QFontMetrics(badge_font)
        b_height = min(20, rect.height() - 6)
        b_y = rect.top() + (rect.height() - b_height) // 2
        badge_rect = QRect(title_rect.right() + 8, b_y,
                           fm_badge.horizontalAdvance(count_text) + 16, b_height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c["badge"])
        painter.drawRoundedRect(badge_rect, b_height / 2, b_height / 2)
        painter.setPen(c["muted"])
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, count_text)
        cursor = badge_rect.right() + 10
        if self._is_frozen:
            painter.restore()
            return
        if fw > 0:
            cursor = max(cursor, left + fw + 10)

        # Approved so far (omitted shots are not counted). Not shown when the
        # grouping is by status - every group would read 0/33 or 33/33.
        if group_data.get("show_progress", True):
            counted = int(group_data.get("counted", count) or 0)
            approved = int(group_data.get("approved_count", 0) or 0)
            pct = int(approved / counted * 100) if counted else 0
            omitted = int(group_data.get("count", 0) or 0) - counted
            # Done or approved, the rule the production summary uses; the
            # difference from the shot count is said, not left to guess.
            pct_text = f"{approved}/{counted} done or approved ({pct}%)" +                 (f" · {omitted} omitted" if omitted > 0 else "")
            pct_font = QFont(base)
            pct_font.setBold(True)
            painter.setFont(pct_font)
            fm_pct = QFontMetrics(pct_font)
            pct_rect = QRect(cursor, b_y, fm_pct.horizontalAdvance(pct_text) + 20, b_height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c["progress"])
            painter.drawRoundedRect(pct_rect, b_height / 2, b_height / 2)
            fill_w = max(0, int((pct_rect.width() - 4) * (pct / 100.0)))
            if fill_w > 0:
                painter.setBrush(c["progress_fill"])
                painter.drawRoundedRect(QRect(pct_rect.left() + 2, pct_rect.top() + 2,
                                              fill_w, b_height - 4), (b_height - 4) / 2, (b_height - 4) / 2)
            painter.setPen(c["progress_text"])
            painter.drawText(pct_rect, Qt.AlignmentFlag.AlignCenter, pct_text)
            cursor = pct_rect.right() + 12

        # Frames and bid days, right after the rest - on screen.
        frames = int(group_data.get("total_frames", 0) or 0)
        bids = float(group_data.get("total_bids", 0.0) or 0.0)
        bids_text = f"{bids:,.1f}".rstrip("0").rstrip(".")
        metrics_text = f"{frames:,} frames  ·  {bids_text} bid days"
        painter.setFont(base)
        painter.setPen(c["muted"])
        fm_m = QFontMetrics(base)
        m_rect = QRect(cursor, rect.top(), fm_m.horizontalAdvance(metrics_text) + 8, rect.height())
        painter.drawText(m_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, metrics_text)

        painter.restore()
