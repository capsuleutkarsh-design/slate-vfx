from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QPainter, QColor, QFontMetrics, QFont

GROUP_HEADER_ROLE = Qt.ItemDataRole.UserRole + 200

class GroupHeaderDelegate(QStyledItemDelegate):
    """
    Paints a ShotGrid / Flow-style section header row spanning the table.
    Displays group title, expand/collapse indicator, shot counts,
    and aggregate progress metrics.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter: QPainter, option, index):
        group_data = index.data(GROUP_HEADER_ROLE)
        if not group_data:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = option.rect

        # 1. Background
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg_color = QColor("#16323A") if is_hovered else QColor("#16323A")
        painter.fillRect(rect, bg_color)

        # 2. Bottom separator & left accent bar
        painter.setPen(QColor("#16323A"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        accent_bar = QRect(rect.left(), rect.top(), 4, rect.height())
        painter.fillRect(accent_bar, QColor("#3EA8BF"))

        # 3. Disclosure triangle
        is_collapsed = bool(group_data.get("is_collapsed", False))
        arrow = "▶" if is_collapsed else "▼"
        arrow_font = QFont("Segoe UI", 9)
        painter.setFont(arrow_font)
        painter.setPen(QColor("#6BA4C9"))
        arrow_rect = QRect(rect.left() + 12, rect.top(), 16, rect.height())
        painter.drawText(arrow_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, arrow)

        # 4. Group Title
        title_text = str(group_data.get("title", "Group")).upper()
        title_font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        painter.setFont(title_font)
        painter.setPen(QColor("#E8E6E1"))
        fm = QFontMetrics(title_font)
        title_width = fm.horizontalAdvance(title_text) + 8
        title_rect = QRect(rect.left() + 32, rect.top(), title_width, rect.height())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title_text)

        # 5. Shot Count Badge
        count = group_data.get("count", 0)
        count_text = f"{count} Shots" if count != 1 else "1 Shot"
        badge_font = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        painter.setFont(badge_font)
        fm_badge = QFontMetrics(badge_font)
        b_width = fm_badge.horizontalAdvance(count_text) + 16
        b_height = 20
        b_x = title_rect.right() + 8
        b_y = rect.top() + (rect.height() - b_height) // 2

        badge_rect = QRect(b_x, b_y, b_width, b_height)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("rgba(255, 255, 255, 0.08)"))
        painter.drawRoundedRect(badge_rect, 10, 10)

        painter.setPen(QColor("#B4B1AA"))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, count_text)

        # 6. Progress Pill (% Approved)
        approved_count = group_data.get("approved_count", 0)
        pct = int((approved_count / count * 100)) if count > 0 else 0
        pct_text = f"{approved_count}/{count} Approved ({pct}%)"
        pct_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(pct_font)
        fm_pct = QFontMetrics(pct_font)
        pct_width = fm_pct.horizontalAdvance(pct_text) + 20
        pct_x = badge_rect.right() + 10
        pct_rect = QRect(pct_x, b_y, pct_width, b_height)

        # Pill background
        painter.setBrush(QColor("rgba(95, 191, 143, 0.25)"))
        painter.drawRoundedRect(pct_rect, 10, 10)

        # Mini fill indicator
        fill_w = max(0, int((pct_rect.width() - 4) * (pct / 100.0)))
        if fill_w > 0:
            fill_rect = QRect(pct_rect.left() + 2, pct_rect.top() + 2, fill_w, b_height - 4)
            painter.setBrush(QColor("rgba(95, 191, 143, 0.35)"))
            painter.drawRoundedRect(fill_rect, 8, 8)

        painter.setPen(QColor("#5FBF8F"))
        painter.drawText(pct_rect, Qt.AlignmentFlag.AlignCenter, pct_text)

        # 7. Total frames and bid days on the right side
        frames = group_data.get("total_frames", 0)
        bids = group_data.get("total_bids", 0.0)
        metrics_text = f"{frames} frames  •  {bids:.1f} bid days"
        m_font = QFont("Segoe UI", 9)
        painter.setFont(m_font)
        painter.setPen(QColor("#6BA4C9"))
        fm_m = QFontMetrics(m_font)
        m_width = fm_m.horizontalAdvance(metrics_text) + 16
        m_rect = QRect(rect.right() - m_width - 12, rect.top(), m_width, rect.height())
        painter.drawText(m_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, metrics_text)

        painter.restore()
