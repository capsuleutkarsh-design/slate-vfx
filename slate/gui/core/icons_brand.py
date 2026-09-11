"""
The Slate mark, as a widget-ready icon.

One definition. The application header, the login screen, the gatekeeper and
the window icon all draw from this, and the installer icons and banners are
generated from the same path data, so none of them can drift out of step with
the others.

The mark is a slate reduced to two parts: the board, and the clapper bar above
it. The board is drawn open along its top edge and the bar carries a short
divider - which is also where the U and the T live, structurally rather than
as a monogram laid over a picture.
"""

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from slate.core.infra.gate import Gate


# viewBox 0 0 64 64. Single colour; the cuts in the clapper bar are holes in
# the path, not a second fill, so the mark works in one ink on any ground.
MARK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <g fill="none" stroke="{colour}" stroke-width="7"
     stroke-linecap="round" stroke-linejoin="round">
    <path d="M11 28 V47 a8 8 0 0 0 8 8 h26 a8 8 0 0 0 8 -8 V28"/>
  </g>
  <g fill="{colour}">
    <g transform="rotate(-11 32 19)">
      <path fill-rule="evenodd" d="
        M10 12 h44 a4 4 0 0 1 4 4 v7 a4 4 0 0 1 -4 4
        h-44 a4 4 0 0 1 -4 -4 v-7 a4 4 0 0 1 4 -4 z
        M21 12 h6 l-7 15 h-6 z
        M38 12 h6 l-7 15 h-6 z
        M55 12 h3 v4 l-5 11 h-6 z"/>
    </g>
    <rect x="28.5" y="30" width="7" height="13" rx="2.5"/>
  </g>
</svg>"""

_cache = {}


def slate_mark(colour: str = None, size: int = 32) -> QIcon:
    """The mark on a transparent ground, tinted and sized."""
    colour = colour or Gate.ACCENT
    key = (colour, size)
    if key in _cache:
        return _cache[key]

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(
            QByteArray(MARK_SVG.format(colour=colour).encode("utf-8"))
        ).render(painter, QRectF(0, 0, size, size))
    finally:
        painter.end()

    icon = QIcon(pixmap)
    _cache[key] = icon
    return icon


def slate_tile(size: int = 64, ground: str = None, ink: str = None) -> QPixmap:
    """
    The mark on its own rounded ground.

    Used where the mark has to hold its own against a background it does not
    control - a desktop, a taskbar, an installer panel.
    """
    ground = ground or Gate.GROUND
    ink = ink or Gate.ACCENT

    from PySide6.QtGui import QColor

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ground))
        radius = size * 0.22
        painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

        inset = size * 0.17
        QSvgRenderer(
            QByteArray(MARK_SVG.format(colour=ink).encode("utf-8"))
        ).render(painter, QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    finally:
        painter.end()

    return pixmap
