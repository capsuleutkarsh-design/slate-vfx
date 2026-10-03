"""
One drawn icon set, in place of 94 emoji.

The product used 485 emoji across 45 files as its iconography. Emoji are drawn
by whichever font Windows picks: some arrive full colour, some monochrome, at
different weights and on different baselines. Side by side in one sidebar they
read as clip art rather than an interface.

These are plain SVG paths on a 24x24 grid, one stroke weight, tinted at draw
time so an icon always matches the text beside it. No files to ship, no font to
install, nothing to fall back to.

    from slate.gui.core.icons import icon
    item.setIcon(icon("home"))
    item.setIcon(icon("home", Gate.ACCENT, 18))
"""

import os

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from slate.core.infra.gate import Gate


# Paths are stroked, not filled - one weight throughout, like a single pen.
_PATHS = {
    "home":        "M3 10.5 12 3l9 7.5V21H3z",
    "folder":      "M3 6h6l1.8 2.4H21V19H3z",
    "tag":         "M3 11.5 11.5 3H21v9.5L12.5 21z M17 7h.01",
    # A strip of film: the frame, two rails, and sprocket holes along both
    # edges. The old path crossed the frame with two lines each way and read as
    # a table wherever it was used.
    "film":        "M4 3h16v18H4z M8 3v18 M16 3v18 M5.5 6h1 M5.5 10h1 M5.5 14h1 M5.5 18h1 M17.5 6h1 M17.5 10h1 M17.5 14h1 M17.5 18h1",
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

    # ---- window and navigation
    "close":          "M6 6l12 12 M18 6 6 18",
    "minus":          "M5 12h14",
    "help":           "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M9.6 9.3a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .9-1 1.6v.6 M12 16.8h.01",
    "sign-out":       "M10 4H5v16h5 M15 8l4 4-4 4 M9 12h10",
    "sign-in":        "M14 4h5v16h-5 M9 8l4 4-4 4 M3 12h10",
    "chevron-up":     "M6 14.5 12 8.5 18 14.5",
    "chevron-left":   "M14.5 6 8.5 12 14.5 18",
    "chevron-right":  "M9.5 6 15.5 12 9.5 18",
    "arrow-left":     "M19 12H5 M11 6l-6 6 6 6",
    "arrow-right":    "M5 12h14 M13 6l6 6-6 6",
    "expand":         "M4 9V4h5 M20 9V4h-5 M4 15v5h5 M20 15v5h-5",
    "collapse":       "M9 4v5H4 M15 4v5h5 M9 20v-5H4 M15 20v-5h5",
    "external":       "M14 4h6v6 M20 4l-9 9 M18 14v6H4V6h6",
    "more":           "M12 5.5h.01 M12 12h.01 M12 18.5h.01",
    "menu":           "M4 6h16 M4 12h16 M4 18h16",
    "grid":           "M4 4h7v7H4z M13 4h7v7h-7z M4 13h7v7H4z M13 13h7v7h-7z",
    "list":           "M9 6h11 M9 12h11 M9 18h11 M4.5 6h.01 M4.5 12h.01 M4.5 18h.01",
    "filter":         "M3 5h18l-7 8.5V20l-4-2v-4.5z",
    "sort":           "M7 4v16 M3.5 16.5 7 20l3.5-3.5 M17 20V4 M13.5 7.5 17 4l3.5 3.5",
    # ---- things
    "user":           "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8",
    "bell":           "M6 16V11a6 6 0 1 1 12 0v5l1.5 2h-15z M10 20.5a2 2 0 0 0 4 0",
    "lock":           "M5 11h14v10H5z M8 11V7.5a4 4 0 0 1 8 0V11",
    "unlock":         "M5 11h14v10H5z M8 11V7.5a4 4 0 0 1 7.7-1.5",
    "eye":            "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    "eye-off":        "M3 3l18 18 M10.6 5.6A9.7 9.7 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-2.9 3.7 M6.6 6.7C4 8.4 2.5 12 2.5 12S6 18.5 12 18.5c1.9 0 3.5-.6 4.9-1.5 M9.9 9.9a3 3 0 0 0 4.2 4.2",
    "copy":           "M9 9h11v11H9z M5 15H4V4h11v1",
    "edit":           "M4 20h4L19 9l-4-4L4 16z M13.5 6.5l4 4",
    "save":           "M5 4h11l3 3v13H5z M8 4v5h7V4 M8 20v-6h8v6",
    "archive":        "M3 4h18v4H3z M5 8v12h14V8 M10 12h4",
    "undo":           "M9 5 4 10l5 5 M4 10h10a6 6 0 0 1 0 12h-3",
    "redo":           "M15 5l5 5-5 5 M20 10H10a6 6 0 0 0 0 12h3",
    "link":           "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1 M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
    "mail":           "M3 5h18v14H3z M3 6l9 7 9-7",
    "send":           "M21 3 10 14 M21 3l-7 18-4-7-7-4z",
    "server":         "M4 4h16v7H4z M4 13h16v7H4z M8 7.5h.01 M8 16.5h.01",
    "cpu":            "M7 7h10v10H7z M10 10h4v4h-4z M10 3v4 M14 3v4 M10 17v4 M14 17v4 M3 10h4 M3 14h4 M17 10h4 M17 14h4",
    "cloud-off":      "M3 3l18 18 M8 7.5A5 5 0 0 1 16.6 10H17a4 4 0 0 1 2.9 6.8 M16 18H7a4 4 0 0 1-1-7.9",
    "wifi-off":       "M3 3l18 18 M8.5 16.5a5 5 0 0 1 7 0 M5 12.9a10 10 0 0 1 4.3-2.4 M19 12.9a10 10 0 0 0-2.5-1.7 M2 9a15 15 0 0 1 4.6-2.9 M22 9a15 15 0 0 0-10-4 M12 20h.01",
    "check-circle":   "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M8 12.5l2.8 2.8L16.5 9.5",
    "x-circle":       "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z M9 9l6 6 M15 9l-6 6",
    "image":          "M3 5h18v14H3z M3 16l5-5 4 4 3-3 6 6 M15.5 9.5h.01",
    "video":          "M3 6h13v12H3z M16 10l5-3v10l-5-3",
    "sequence":       "M7 3h14v14H7z M5 6H3v15h15v-2 M11 7h6 M11 10.5h6 M11 14h4",
    "star":           "M12 3l2.8 5.9 6.2.8-4.6 4.3 1.2 6.2L12 17.2 6.4 20.2l1.2-6.2L3 9.7l6.2-.8z",
    "star-filled":    "M12 3l2.8 5.9 6.2.8-4.6 4.3 1.2 6.2L12 17.2 6.4 20.2l1.2-6.2L3 9.7l6.2-.8z",
    "sparkle":        "M12 3v4 M12 17v4 M3 12h4 M17 12h4 M6 6l2.5 2.5 M15.5 15.5 18 18 M18 6l-2.5 2.5 M8.5 15.5 6 18",
    "rocket":         "M12 15l-3-3c1-4 4-8 10-9-1 6-5 9-9 10z M9 12l-4 1 2-4h4 M12 15l-1 4 4-2v-4 M6 18c-1 0-2 1-2 2 1 0 2-1 2-2z",
    # ---- media transport
    "pause":          "M8 5v14 M16 5v14",
    "stop":           "M6 6h12v12H6z",
    "skip-previous":  "M6 5v14 M19 5 9 12l10 7z",
    "skip-next":      "M18 5v14 M5 5l10 7-10 7z",
    "step-back":      "M18 6 12 12l6 6 M11 6 5 12l6 6",
    "step-forward":   "M6 6l6 6-6 6 M13 6l6 6-6 6",
    "volume":         "M4 9h4l5-4v14l-5-4H4z M16.5 9a4 4 0 0 1 0 6 M19 6.5a8 8 0 0 1 0 11",
    "volume-off":     "M4 9h4l5-4v14l-5-4H4z M17 9.5l5 5 M22 9.5l-5 5",
    "repeat":         "M17 3l3 3-3 3 M4 11V9a3 3 0 0 1 3-3h13 M7 21l-3-3 3-3 M20 13v2a3 3 0 0 1-3 3H4",
    # ---- the few marks a stylesheet needs (see stylesheet_icons below)
    "dot":            "M12 12h.01",
}

# Glyphs drawn solid rather than stroked.
_FILLED = {"star-filled"}

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
    "⬆️": "upload", "▶️": "play", "🗄️": "database", "💾": "save",
    "❌": "close", "✖": "close", "✕": "close", "❓": "help", "⭐": "star-filled",
    "☆": "star", "★": "star-filled", "⏸": "pause", "⏸️": "pause", "⏹": "stop",
    "⏹️": "stop", "⏮": "skip-previous", "⏭": "skip-next", "🔔": "bell",
    "🔒": "lock", "🔓": "unlock", "👁": "eye", "👁️": "eye", "📋": "copy",
    "✏️": "edit", "✏": "edit", "📂": "folder", "📄": "sequence", "🖼": "image",
    "🖼️": "image", "🎥": "video", "📹": "video", "🔊": "volume", "🔇": "volume-off",
    "👤": "user", "📧": "mail", "✉️": "mail", "🔗": "link", "🚀": "rocket",
    "⋮": "more", "☰": "menu", "↩": "undo", "↪": "redo",
}

_cache = {}


def _forget(_mode=None):
    # Icons drawn in the default colour belong to the old palette.
    _cache.clear()


Gate.on_change(_forget)


def _svg(key: str, colour: str, size: int, stroke: float) -> str:
    path = _PATHS[key]
    if key in _FILLED:
        paint = f'fill="{colour}" stroke="{colour}" stroke-width="{stroke}"'
    else:
        paint = f'fill="none" stroke="{colour}" stroke-width="{stroke}"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{size}" height="{size}" {paint} '
        f'stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{path}"/></svg>'
    )


def _device_ratio() -> float:
    try:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        return max(1.0, float(screen.devicePixelRatio())) if screen else 1.0
    except Exception:
        return 1.0


def icon(name: str, colour: str = None, size: int = 18) -> QIcon:
    """
    An icon by name, or by the emoji it replaces.

    Unknown names give back an empty QIcon rather than raising - a missing icon
    should never be the reason a tab will not open. The default colour is the
    active theme's secondary text, so an icon matches the words beside it in
    Light as well as the dark themes.
    """
    key = ALIASES.get(name, name)
    if key not in _PATHS:
        return QIcon()

    colour = colour or Gate.TEXT_2
    ratio = _device_ratio()
    cache_key = (key, colour, size, ratio)
    if cache_key in _cache:
        return _cache[cache_key]

    # Drawn at the screen's pixel density, so it stays sharp at 125% and 150%.
    pixels = max(1, int(round(size * ratio)))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # The dot is a zero-length path: only a thick round stroke makes it a
        # visible disc (as the stylesheet's radio dot draws it). At 1.6 it was
        # nothing at all.
        stroke = 11.0 if key == "dot" else 1.6
        QSvgRenderer(QByteArray(_svg(key, colour, pixels, stroke).encode("utf-8"))).render(painter)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(ratio)

    result = QIcon(pixmap)
    _cache[cache_key] = result
    return result


def pixmap(name: str, colour: str = None, size: int = 18) -> QPixmap:
    """The icon as a pixmap, for a QLabel."""
    return icon(name, colour, size).pixmap(size, size)


def svg_file(name: str, colour: str, out_dir: str, stroke: float = 2.0, file_name: str = "") -> str:
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
    if key not in _PATHS:
        return ""

    svg = _svg(key, colour, 24, stroke)
    try:
        os.makedirs(out_dir, exist_ok=True)
        target = os.path.join(out_dir, "%s.svg" % (file_name or key))
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(svg)
        return target
    except OSError:
        return ""


# The marks the application stylesheet reaches through url(@ICONS/<file>.svg):
# file name -> (glyph, colour attribute on Gate, stroke). "-off" files are the
# disabled / at-the-limit versions.
STYLESHEET_ICONS = {
    "chevron-down": ("chevron-down", "TEXT_DIM", 2.2),
    "chevron-down-off": ("chevron-down", "IDLE", 2.2),
    "chevron-up": ("chevron-up", "TEXT_DIM", 2.2),
    "chevron-up-off": ("chevron-up", "IDLE", 2.2),
    "chevron-left": ("chevron-left", "TEXT_2", 2.2),
    "chevron-right": ("chevron-right", "TEXT_DIM", 2.2),
    "sort-up": ("chevron-up", "TEXT_2", 2.4),
    "sort-down": ("chevron-down", "TEXT_2", 2.4),
    # The tick and the dash sit on the accent fill, so they take the colour
    # that reads on the accent rather than text grey.
    "check": ("check", "TEXT_ON_ACCENT", 2.8),
    "check-off": ("check", "IDLE", 2.8),
    "indeterminate": ("minus", "TEXT_ON_ACCENT", 2.8),
    "radio-dot": ("dot", "ACCENT", 11.0),
    "radio-dot-off": ("dot", "IDLE", 11.0),
}


def stylesheet_icons(out_dir: str) -> bool:
    """
    Write every icon the application stylesheet uses into out_dir, in the
    active theme's colours. True when all of them were written.
    """
    ok = True
    for file_name, (glyph, attribute, stroke) in STYLESHEET_ICONS.items():
        if not svg_file(glyph, getattr(Gate, attribute), out_dir, stroke=stroke, file_name=file_name):
            ok = False
    return ok


def has_icon(name: str) -> bool:
    return (ALIASES.get(name, name)) in _PATHS


def names():
    """Every glyph this set can draw."""
    return sorted(_PATHS)
