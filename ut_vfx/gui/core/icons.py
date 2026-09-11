"""
One drawn icon set, in place of 94 emoji.

The product used 485 emoji across 45 files as its iconography. Emoji are drawn
by whichever font Windows picks: some arrive full colour, some monochrome, at
different weights and on different baselines. Side by side in one sidebar they
read as clip art rather than an interface.

These are plain SVG paths on a 24x24 grid, one stroke weight, tinted at draw
time so an icon always matches the text beside it. No files to ship, no font to
install, nothing to fall back to.

    from ut_vfx.gui.core.icons import icon
    item.setIcon(icon("home"))
    item.setIcon(icon("home", Gate.ACCENT, 18))
"""

import os

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ut_vfx.core.infra.gate import Gate


# Paths are stroked, not filled - one weight throughout, like a single pen.
_PATHS = {
    "home":        "M3 10.5 12 3l9 7.5V21H3z",
    "folder":      "M3 6h6l1.8 2.4H21V19H3z",
    "tag":         "M3 11.5 11.5 3H21v9.5L12.5 21z M17 7h.01",
    "film":        "M3 5h18v14H3z M3 9h18 M3 15h18 M8 5v14 M16 5v14",
    "clapper":     "M3 8h18v12H3z M3 8 6 3h4l-3 5 M11 3h4l-3 5",
    "chart":       "M3 20V10 M9 20V4 M15 20V13 M21 20v-5",
    "calendar":    "M4 6h16v15H4z M4 11h16 M8 3v5 M16 3v5",
    "money":       "M12 3v18 M16.5 7A4 4 0 0 0 9 8.5c0 3.5 7 2 7 5.5A4 4 0 0 1 8 15",
    "clock":       "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 7v5.2l3.2 2",
    "leave":       "M12 21v-8 M12 13c-4-1-7-4-7-8 4 0 7 2 7 8z M12 13c4-1 7-4 7-8-4 0-7 2-7 8z",
    "handshake":   "M3 12l4-4 5 4 5-4 4 4-4.5 5-2.5-2-2.5 2z",
    "monitor":     "M3 5h18v11H3z M9 20h6 M12 16v4",
    "key":         "M14.5 3a6 6 0 1 0-3.6 10.8L9 15.7V18H6.3l-2.6 2.6 1.4 1.4 8-8A6 6 0 0 0 14.5 3z M16 8h.01",
    "ticket":      "M3 8h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4z M12 8v10",
    "package":     "M12 3 3 7.5V17l9 4.5 9-4.5V7.5z M3 7.5 12 12l9-4.5 M12 12v9.5",
    "users":       "M8 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z M2 20c0-3.3 2.7-6 6-6s6 2.7 6 6 M17 20c0-3 -1-5-2.5-6.2 M16 4.5a3.5 3.5 0 0 1 0 6.6",
    "shield":      "M12 3 4.5 6v6c0 4.5 3.2 7.9 7.5 9 4.3-1.1 7.5-4.5 7.5-9V6z",
    "flask":       "M9 3h6 M10.5 3v6L5 19a2 2 0 0 0 1.7 3h10.6A2 2 0 0 0 19 19l-5.5-10V3 M8 15h8",
    "gear":        "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z M19.5 12a7.5 7.5 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7.5 7.5 0 0 0-2-1.2L14.7 3h-4l-.4 2.6a7.5 7.5 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5a7.5 7.5 0 0 0 0 2.5l-2 1.5 2 3.4 2.3-1a7.5 7.5 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7.5 7.5 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5c.06-.4.1-.8.1-1.2z",
    "timeline":    "M3 7h18 M3 12h12 M3 17h15 M7 5v4 M15 10v4 M11 15v4",
    "search":      "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14z M16 16l5 5",
    "refresh":     "M20 12a8 8 0 1 1-2.6-5.9 M20 4v4h-4",
    "plus":        "M12 5v14 M5 12h14",
    "check":       "M4 12.5 9.5 18 20 6.5",
    "chevron-down": "M6 9.5 12 15.5 18 9.5",
    "alert":       "M12 4 2.5 20h19z M12 10v4.5 M12 17.5h.01",
    "info":        "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M12 11v5 M12 7.5h.01",
    "trash":       "M4 7h16 M9 7V4h6v3 M6 7l1 13h10l1-13 M10 11v6 M14 11v6",
    "download":    "M12 4v11 M7.5 10.5 12 15l4.5-4.5 M4 20h16",
    "upload":      "M12 20V9 M7.5 13.5 12 9l4.5 4.5 M4 4h16",
    "play":        "M7 4.5 19 12 7 19.5z",
    "database":    "M12 8c4.4 0 8-1.1 8-2.5S16.4 3 12 3 4 4.1 4 5.5 7.6 8 12 8z M4 5.5v13C4 19.9 7.6 21 12 21s8-1.1 8-2.5v-13 M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5",
}

# What the sidebar and sub-tabs used to say with an emoji.
ALIASES = {
    "🏠": "home", "📁": "folder", "🏷️": "tag", "🏷": "tag",
    "🎞️": "film", "🎞": "film", "🎬": "clapper", "📊": "chart",
    "📅": "calendar", "💰": "money", "⏱️": "clock", "⏱": "clock",
    "⏰": "clock", "🌴": "leave", "🤝": "handshake", "🖥️": "monitor",
    "🖥": "monitor", "🔑": "key", "🎫": "ticket", "📦": "package",
    "👥": "users", "🛡️": "shield", "🛡": "shield", "🧪": "flask",
    "⚙️": "gear", "⚙": "gear", "🔧": "gear", "🔍": "search",
    "🔄": "refresh", "➕": "plus", "✅": "check", "⚠️": "alert",
    "ℹ️": "info", "🗑️": "trash", "🗑": "trash", "⬇️": "download",
    "⬆️": "upload", "▶️": "play", "🗄️": "database", "💾": "database",
}

_cache = {}


def icon(name: str, colour: str = None, size: int = 18) -> QIcon:
    """
    An icon by name, or by the emoji it replaces.

    Unknown names give back an empty QIcon rather than raising - a missing icon
    should never be the reason a tab will not open.
    """
    key = ALIASES.get(name, name)
    path = _PATHS.get(key)
    if path is None:
        return QIcon()

    colour = colour or Gate.TEXT_2
    cache_key = (key, colour, size)
    if cache_key in _cache:
        return _cache[cache_key]

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}" fill="none" stroke="{colour}" '
        f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{path}"/></svg>'
    )

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
    finally:
        painter.end()

    result = QIcon(pixmap)
    _cache[cache_key] = result
    return result


def svg_file(name: str, colour: str, out_dir: str, stroke: float = 2.0) -> str:
    """
    Write one icon out as an SVG file, and give back its path.

    Qt stylesheets can only reach an icon through a url() on disk - they cannot
    take a data URI and they cannot call into this module. So the few icons the
    global sheet needs (a combo box arrow, a checkbox tick) are materialised
    once at theme time from the same paths every other icon comes from, rather
    than being a pair of PNGs somebody has to keep in step with the palette.

    Returns "" if the name is unknown or the file cannot be written, and the
    caller leaves the stylesheet to fall back to Qt's own drawing.
    """
    key = ALIASES.get(name, name)
    path = _PATHS.get(key)
    if path is None:
        return ""

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="24" height="24" fill="none" stroke="{colour}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{path}"/></svg>'
    )
    try:
        os.makedirs(out_dir, exist_ok=True)
        target = os.path.join(out_dir, "%s.svg" % key)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(svg)
        return target
    except OSError:
        return ""


def has_icon(name: str) -> bool:
    return (ALIASES.get(name, name)) in _PATHS
