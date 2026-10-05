"""
Header Builder Component.

Creates the application header with:
- Branding (Slate logo)
- Workflow mode selector
- User profile display
- Help button

Extracted from main_window.py for better maintainability.
"""

from pathlib import Path
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QPushButton, QWidget, QSizePolicy
)

import os

from ..core.controls import make_button, style_button
from ...core.infra.gate import Gate
from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QFont, QPixmap, QPainter, QBrush, QColor, QRegion, QFontMetrics
import logging

from ...widgets.db_speed_indicator_compact import DBSpeedIndicatorCompact


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class AccountControl(QFrame):
    """
    Your name, role and avatar, with a chevron: click (or Enter / Space) for
    your account menu. It used to be plain text with a hidden click that only
    offered "Change password", and nothing on screen said it could be clicked.
    """
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("accountControl")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QFrame#accountControl {{ background: transparent; border: 1px solid transparent; "
            f"border-radius: 8px; }}"
            f"QFrame#accountControl:hover {{ background: {Gate.overlay(0.06)}; "
            f"border: 1px solid {Gate.LINE}; }}"
            f"QFrame#accountControl:focus {{ border: 1px solid {Gate.ACCENT}; }}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)


# The badge after the SLATE wordmark, by application. The suite that holds
# everything has no badge: it is not "VFX".
APP_BADGES = {"vfx": "VFX", "ops": "OPS", "all": ""}

# The widest the name and role may be before they are shortened with "...".
PROFILE_TEXT_MAX = 220


def initials(name: str) -> str:
    """
    Two letters for the avatar: the first character of each of the first two
    words, as a reader sees a character - a Devanagari name gives its full
    first syllables, not bare consonants. Latin text is upper-cased.
    """
    from PySide6.QtCore import QTextBoundaryFinder
    words = [w for w in str(name or "").split() if w][:2]
    if not words:
        return "?"
    out = []
    for word in words:
        finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, word)
        end = finder.toNextBoundary()
        out.append(word[:end] if end > 0 else word[:1])
    text = "".join(out)
    return text.upper() if text.isascii() else text


class HeaderBuilder:
    """
    Builds the application header section.
    
    Creates a professional header with branding, controls, and user info.
    """
    
    def __init__(self, parent_window, user_data=None):
        """
        Initialize header builder.
        
        Args:
            parent_window: Reference to main window
            user_data: User information dict with display_name, role, profile_pic_path
        """
        self.parent = parent_window
        self.user_data = user_data or {}
        self.logout_button = None
        self.header_widget = None
        self.mark_label = None
        self.vfx_label = None
        self.badge_label = None
        self.profile_text_widget = None
        self.header_layout = None
        self.branding_widget = None
        self.local_mode_label = None
    
    def create_header(self):
        """
        Create the complete header widget.
        
        Returns:
            QFrame: The header widget with all components
        """
        logging.info("Building header structure")
        
        # Root Header (Command Center Gradient)
        header_widget = QFrame()
        header_widget.setObjectName("header")
        header_widget.setMinimumHeight(80)
        header_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.header_widget = header_widget
        
        # Gradient styling
        header_widget.setStyleSheet(f"""
            QFrame#header {{
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {Gate.ACCENT_SURFACE}, stop:1 {Gate.ACCENT_SURFACE});
                border-bottom: 1px solid {Gate.INFO};
            }}
            QLabel {{ background: transparent; border: none; }} 
        """)
        
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(24, 10, 24, 10)
        header_layout.setSpacing(12)
        self.header_layout = header_layout
        
        # 1. LEFT: BRANDING
        logo_widget = self._create_branding()
        self.branding_widget = logo_widget
        header_layout.addWidget(logo_widget)
        header_layout.addStretch()

        # 2. Search or jump to... - the command palette, which had no visible
        # way in (only Ctrl+P / Ctrl+K, listed nowhere).
        self.search_button = self._create_search_button()
        header_layout.addWidget(self.search_button)
        
        # 3. RIGHT: HELP BUTTON
        self.help_button = self._create_help_button()  # Store reference
        header_layout.addWidget(self.help_button)

        # 3a. RIGHT: LOGOUT BUTTON
        self.logout_button = self._create_logout_button()
        header_layout.addWidget(self.logout_button)

        # 3a-1. RIGHT: DEV ROLE SWITCHER
        #
        # This is a development control - it lets you pretend to be another role -
        # and it was shown to anyone holding 'admin' or 'superuser', which is to
        # say it shipped to the studio. It is now limited to an actual developer
        # account, and even then only when Slate_DEV_TOOLS is set.
        roles_data = self.user_data.get('roles', self.user_data.get('role', []))
        user_roles = [r.lower() for r in (roles_data if isinstance(roles_data, list) else [roles_data])]
        is_developer = any(r in ('dev', 'developer') for r in user_roles)
        dev_tools_on = os.environ.get("Slate_DEV_TOOLS", "").strip().lower() in ("1", "true", "yes")
        if is_developer and dev_tools_on:
            self.dev_role_btn = make_button("Switch role", "ghost",
                                            tooltip="Developer tool: view the app as another role")
            self.dev_role_btn.clicked.connect(self._on_dev_switch_role)
            header_layout.addWidget(self.dev_role_btn)

        # 3a-2. RIGHT: SYNC BUTTON
        self.sync_button = self._create_sync_button()
        header_layout.addWidget(self.sync_button)
        
        # 3b. DB SPEED INDICATOR (Real-time monitoring)
        self.db_speed_indicator = DBSpeedIndicatorCompact()
        header_layout.addWidget(self.db_speed_indicator)
        
        # 3d. LOCAL MODE BADGE (always visible in fallback mode)
        self.local_mode_label = QLabel("LOCAL MODE")
        self.local_mode_label.setStyleSheet(
            f"""
            QLabel {{
                color: {Gate.WARN};
                font-size: 10px;
                font-weight: 800;
                background: {Gate.WARN_SURFACE};
                border: 1px solid {Gate.tint(Gate.WARN, 0.55)};
                border-radius: 4px;
                padding: 2px 8px;
                letter-spacing: 0.4px;
            }}
            """
        )
        self.local_mode_label.setToolTip("LOCAL MODE: central sync features are limited")
        self.local_mode_label.setVisible(False)
        # Sized to its text: left to the row it stretched into a tall slab.
        self.local_mode_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        header_layout.addWidget(self.local_mode_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # Which database and the shared folder are in Settings and
        # Diagnostics. LOCAL MODE shows whenever Slate works on its local
        # copy, because Help and several screens point at it "in the header".
        self.local_mode_label.setVisible(False)
        self.local_mode_label.setToolTip(
            "LOCAL MODE: the studio database cannot be reached, so Slate is working on "
            "this machine's copy.\nTeam views, fleet monitoring and remote actions are "
            "limited. Use Sync to send your changes once the connection is back.")

        # 4. RIGHT: USER PROFILE
        profile_widget = self._create_user_profile()
        header_layout.addWidget(profile_widget)
        
        # Apply initial responsive layout state.
        self.update_responsive_layout(self.parent.width())
        return header_widget

    def update_db_status(self, is_connected, latency_ms):
        """The DatabaseMonitor's reading (taken on its own thread) for the latency dot."""
        indicator = getattr(self, "db_speed_indicator", None)
        if indicator is None:
            return
        try:
            indicator.set_status(is_connected, latency_ms)
        except RuntimeError:
            pass

    def _on_dev_switch_role(self):
        """Secret Dev feature: switch user role dynamically to test UI layout variations."""
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        roles = ["Developer", "HR", "Admin", "Supervisor", "Artist", "Producer", "Manager"]
        current = getattr(self.parent, "user_role", "Developer")
        try:
            default_idx = roles.index(current)
        except ValueError:
            default_idx = 0
            
        role, ok = QInputDialog.getItem(
            self.parent, "Dev Mode: Switch Role", "Select Role to simulate:",
            roles, default_idx, False
        )
        if ok and role:
            self.parent.user_role = role
            QMessageBox.information(
                self.parent, "Role Switched", 
                f"Simulated role changed to: {role}\n\nPlease restart the application for the new tabs and layout to fully take effect."
            )

    def set_db_runtime_status(self, active_mode: str, fallback_used: bool = False):
        """LOCAL MODE in the header while Slate works on this machine's copy."""
        self._local_mode = bool(fallback_used)
        if self.local_mode_label:
            self.local_mode_label.setVisible(self._local_mode)

    def _create_branding(self):
        """Create the Slate branding section."""
        self.mark_label = None
        self.vfx_label = None
        self.badge_label = None

        logo_widget = QWidget()
        logo_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        logo_widget.setStyleSheet("background: transparent; border: none;")
        logo_widget.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        logo_layout = QHBoxLayout(logo_widget)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setSpacing(8)

        # The mark, drawn from the same vector the icons and the installer use.
        from ..core.icons_brand import slate_mark
        mark_icon = QLabel()
        mark_icon.setPixmap(slate_mark(Gate.ACCENT, 30).pixmap(30, 30))
        mark_icon.setStyleSheet("background: transparent; border: none;")
        mark_icon.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.mark_icon = mark_icon

        mark_label = QLabel("SLATE")
        mark_label.setObjectName("appLogo")
        mark_label.setStyleSheet(f"""
            font-family: {Gate.FONT_LABEL_STRONG};
            font-size: 27px;
            font-weight: 600;
            letter-spacing: 3px;
            color: {Gate.TEXT};
            background: transparent;
        """)
        mark_label.setWordWrap(False)
        mark_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        mark_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.mark_label = mark_label

        # The category sits in a badge rather than in the name, which is where
        # the old "PRO" badge already was.
        app_mode = str(getattr(self.parent, "app_mode", "vfx") or "vfx").lower()
        self.badge_text = APP_BADGES.get(app_mode, "")
        vfx_label = QLabel(self.badge_text)
        vfx_label.setObjectName("appDescription")
        vfx_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vfx_label.setStyleSheet(f"""
            background-color: {Gate.ACCENT};
            color: {Gate.TEXT_ON_ACCENT};
            font-family: {Gate.FONT_LABEL_STRONG};
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 1.5px;
            padding: 2px 6px;
            border-radius: 3px;
        """)
        # Sized to the text, not to the row. Left on Preferred it stretched to
        # the full height of the header and read as a coloured slab.
        vfx_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        vfx_label.setFixedHeight(19)
        self.vfx_label = vfx_label
        # Kept so anything that still reaches for badge_label keeps working.
        self.badge_label = vfx_label

        logo_layout.addWidget(mark_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        logo_layout.addWidget(mark_label, 0, Qt.AlignmentFlag.AlignVCenter)
        logo_layout.addWidget(vfx_label, 0, Qt.AlignmentFlag.AlignVCenter)
        vfx_label.setVisible(bool(self.badge_text))

        # The studio's own logo, if one is set (Settings > Studio Logo). It has
        # its own box beside the wordmark. It used to share the wordmark's
        # slot, and after a settings change the new logo was painted straight
        # over the "S" of SLATE.
        self.studio_logo_divider = QFrame()
        self.studio_logo_divider.setFrameShape(QFrame.Shape.VLine)
        self.studio_logo_divider.setFixedSize(1, 28)
        self.studio_logo_divider.setStyleSheet(f"background: {Gate.LINE}; border: none;")
        self.studio_logo_label = QLabel()
        self.studio_logo_label.setObjectName("studioLogo")
        self.studio_logo_label.setStyleSheet("background: transparent; border: none;")
        self.studio_logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.studio_logo_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        logo_layout.addSpacing(6)
        logo_layout.addWidget(self.studio_logo_divider, 0, Qt.AlignmentFlag.AlignVCenter)
        logo_layout.addSpacing(6)
        logo_layout.addWidget(self.studio_logo_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._apply_studio_logo()

        return logo_widget

    # The box the studio logo is fitted into, aspect ratio kept.
    STUDIO_LOGO_MAX = (160, 40)

    def _studio_logo_path(self) -> str:
        global_settings = {}
        if hasattr(self.parent, "config_manager"):
            try:
                global_settings = self.parent.config_manager.settings.get("global_settings", {}) or {}
            except Exception:
                global_settings = {}
        from slate.core.infra.studio_settings import studio_logo
        return studio_logo(global_settings.get("branding_logo_path", ""))

    def _apply_studio_logo(self):
        """Put the configured studio logo in its box, or hide the box."""
        label = getattr(self, "studio_logo_label", None)
        if label is None:
            return
        path = self._studio_logo_path()
        pix = QPixmap(path) if path and Path(path).exists() else QPixmap()
        shown = not pix.isNull()
        if shown:
            max_w, max_h = self.STUDIO_LOGO_MAX
            pix = pix.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
            label.setPixmap(pix)
            label.setFixedSize(pix.size())
            label.setToolTip("Studio logo (change it in Settings)")
        else:
            label.clear()
        label.setVisible(shown)
        divider = getattr(self, "studio_logo_divider", None)
        if divider is not None:
            divider.setVisible(shown)

    def reload_branding(self):
        """
        Show a changed studio logo (used after settings change).

        Only the logo's own box changes. The whole branding block used to be
        rebuilt and swapped, which is how a new logo ended up drawn over the
        wordmark.
        """
        try:
            self._apply_studio_logo()
            self.update_responsive_layout(self.parent.width())
        except Exception as exc:
            logging.warning("Header branding reload failed: %s", exc)
    
    def _create_help_button(self):
        """Reference material - present, but never competing for attention."""
        help_btn = make_button("Help", "ghost", icon="help",
                               tooltip="Help for the screen you are on (F1)")
        help_btn.setMinimumHeight(28)
        help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._outline(help_btn)
        return help_btn

    @staticmethod
    def _outline(button):
        """A quiet outline, so a ghost button in the header reads as a button."""
        button.setStyleSheet(button.styleSheet() + (
            f"QPushButton {{ border: 1px solid {Gate.LINE}; }}"
            f"QPushButton:hover {{ border: 1px solid {Gate.TEXT_DIM}; }}"))

    def _create_search_button(self):
        """'Search or jump to...  Ctrl+K' - opens the command palette."""
        button = make_button("Search or jump to\u2026   Ctrl+K", "ghost", icon="search",
                             tooltip="Find a screen, a command or a shot (Ctrl+K)")
        button.setMinimumHeight(28)
        button.setMinimumWidth(200)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(button.styleSheet() + (
            f"QPushButton {{ text-align: left; padding-left: 10px; padding-right: 12px; "
            f"background: {Gate.INPUT}; border: 1px solid {Gate.LINE}; color: {Gate.TEXT_DIM}; "
            f"font-weight: normal; }}"
            f"QPushButton:hover {{ border: 1px solid {Gate.TEXT_DIM}; color: {Gate.TEXT_2}; }}"))
        button.clicked.connect(self._open_palette)
        return button

    def _open_palette(self):
        open_palette = getattr(self.parent, "show_quick_search", None)
        if callable(open_palette):
            open_palette()

    def _create_logout_button(self):
        """
        Ends the session, but does not destroy anything, so it is quiet rather
        than red. It used to be styled the same as a delete.
        """
        logout_btn = make_button("Sign out", "ghost", icon="sign-out",
                                 tooltip="Sign out so somebody else can sign in")
        logout_btn.setMinimumHeight(28)
        logout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._outline(logout_btn)
        return logout_btn

    def _create_sync_button(self):
        """
        Push the changes made while working offline up to the studio database.

        Shown only while Slate is in local mode (set_sync_available): with the
        studio database connected every change is shared when it is saved, and
        a Sync button then does nothing - which is exactly what it used to do,
        for everybody, connected to nothing.
        """
        sync_btn = make_button(
            "Sync", "primary",
            tooltip=("Slate is working offline on this machine's copy.\n"
                     "Sync sends the changes made here to the studio database "
                     "and brings the studio's changes back."))
        sync_btn.setMinimumHeight(28)
        sync_btn.setMinimumWidth(70)
        sync_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sync_btn.setVisible(False)
        return sync_btn

    def insert_before_help(self, widget):
        """Place a header control (the notification bell) just left of Help."""
        if not self.header_layout:
            return
        idx = self.header_layout.indexOf(getattr(self, "help_button", None))
        if idx < 0:
            self.header_layout.addWidget(widget)
        else:
            self.header_layout.insertWidget(idx, widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_sync_available(self, available: bool):
        """Show Sync only while offline (local/fallback) mode is active."""
        button = getattr(self, "sync_button", None)
        if button is None:
            return
        try:
            button.setVisible(bool(available))
        except RuntimeError:
            pass

    def user_subtitle(self, role: str = "") -> str:
        """
        The line under the name: the job title, or the role when there is
        none. The window title shows the same thing (MainWindow.user_subtitle).
        """
        helper = getattr(self.parent, "user_subtitle", None)
        if callable(helper):
            try:
                return helper()
            except Exception:
                pass
        job = str(self.user_data.get("job_title") or "").strip()
        return job if job and job.lower() != "none" else str(role or "")

    def _create_user_profile(self):
        """Your account: name, job title, avatar and a chevron; click for the menu."""
        # Get user data
        display_name = self.user_data.get('display_name', 'Guest User')
        # Handle roles array (new format) and role string (legacy)
        roles_data = self.user_data.get('roles', self.user_data.get('role', ['Guest']))
        if isinstance(roles_data, list):
            role = roles_data[0] if roles_data else 'Guest'
        else:
            role = roles_data
        profile_pic = self.user_data.get('profile_pic_path', '')

        # Try to get fresh data from user_manager if available
        if hasattr(self.parent, 'user_manager') and hasattr(self.parent, 'current_user'):
            try:
                fresh_data = self.parent.user_manager.users.get(self.parent.current_user)
                if fresh_data:
                    display_name = fresh_data.get('display_name', display_name)
                    # Extract role from roles array
                    fresh_roles = fresh_data.get('roles', fresh_data.get('role', [role]))
                    if isinstance(fresh_roles, list):
                        role = fresh_roles[0] if fresh_roles else role
                    else:
                        role = fresh_roles
                    profile_pic = fresh_data.get('profile_pic_path', profile_pic)
            except Exception as exc:
                logging.debug("User profile refresh failed, using cached data: %s", exc)
        subtitle = self.user_subtitle(role)
        self.full_name = str(display_name or "")
        self.full_subtitle = str(subtitle or "")

        profile_widget = AccountControl()
        profile_layout = QHBoxLayout(profile_widget)
        profile_layout.setContentsMargins(8, 2, 6, 2)
        profile_layout.setSpacing(10)

        # Name/Role text
        text_info = QWidget()
        text_info.setStyleSheet("background: transparent; border: none;")
        ti_layout = QVBoxLayout(text_info)
        ti_layout.setContentsMargins(0, 0, 0, 0)
        ti_layout.setSpacing(0)
        ti_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # The name in the main text colour and weight; the role under it,
        # quieter. They were the same blue at the same weight.
        self.name_label = QLabel()
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.name_label.setStyleSheet(
            f"color: {Gate.TEXT}; font-weight: 600; font-size: 13px; "
            "background: transparent; border: none;"
        )
        self.role_label = QLabel()
        self.role_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.role_label.setStyleSheet(
            f"color: {Gate.TEXT_DIM}; font-weight: normal; font-size: 11px; "
            "background: transparent; border: none;"
        )
        ti_layout.addWidget(self.name_label)
        ti_layout.addWidget(self.role_label)
        self._set_profile_text(PROFILE_TEXT_MAX)

        # Avatar
        avatar_label = self._create_avatar(display_name, profile_pic)

        from ..core.icons import pixmap as icon_pixmap
        chevron = QLabel()
        chevron.setPixmap(icon_pixmap("chevron-down", Gate.TEXT_DIM, 14))
        chevron.setStyleSheet("background: transparent; border: none;")

        profile_layout.addWidget(text_info)
        profile_layout.addWidget(avatar_label)
        profile_layout.addWidget(chevron)
        self.profile_text_widget = text_info
        self.profile_widget = profile_widget

        tip = self.full_name + (f"\n{self.full_subtitle}" if self.full_subtitle else "")
        profile_widget.setToolTip(tip + "\n\nYour account: change password, keyboard shortcuts, sign out")
        profile_widget.clicked.connect(lambda: self._show_account_menu(profile_widget))

        return profile_widget

    def _set_profile_text(self, max_width: int):
        """Name and role, shortened with '...' to fit max_width (full text in the tooltip)."""
        for label, text in ((self.name_label, getattr(self, "full_name", "")),
                            (self.role_label, getattr(self, "full_subtitle", ""))):
            metrics = QFontMetrics(label.font())
            label.ensurePolished()
            metrics = QFontMetrics(label.font())
            label.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, max_width))
            label.setMaximumWidth(max_width)

    def account_actions(self):
        """(label, callback) for the account menu, in order; None is a separator."""
        actions = [("Change password\u2026", self._change_password)]
        shortcuts = getattr(self.parent, "show_shortcuts", None)
        if callable(shortcuts):
            actions.append(("Keyboard shortcuts", shortcuts))
        from ..login_dialog import version_text
        actions.append((f"About Slate \u00b7 {version_text()}", self._show_about))
        logout = getattr(self.parent, "logout_user", None)
        if callable(logout):
            actions.append(None)
            actions.append(("Sign out", logout))
        return actions

    def _show_account_menu(self, anchor):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(anchor)
        for entry in self.account_actions():
            if entry is None:
                menu.addSeparator()
            else:
                menu.addAction(entry[0], entry[1])
        # popup, not exec: no nested event loop, and the menu (kept on self)
        # works the same for the mouse and the keyboard.
        self.account_menu = menu
        menu.popup(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _show_about(self):
        """The Credits screen (licence section 5, unchanged); the version is on the menu entry."""
        from slate import licence
        licence.show_credits(self.parent)

    def _change_password(self):
        from ..dialogs.change_password_dialog import ChangePasswordDialog
        username = (getattr(self.parent, "current_user", None)
                    or self.user_data.get("user_id") or self.user_data.get("username"))
        manager = getattr(self.parent, "user_manager", None)
        if not (username and manager):
            return
        dialog = ChangePasswordDialog(manager, username, parent=self.parent,
                                      display_name=self.user_data.get("display_name", ""))
        if dialog.exec() == dialog.DialogCode.Accepted:
            from .feedback import toast
            toast(self.parent, "Password changed. Use it the next time you sign in.", "success")

    def _create_avatar(self, display_name, profile_pic):
        """Create circular avatar with image or initials."""
        avatar_label = QLabel()
        avatar_label.setObjectName("userAvatar")
        avatar_size = 36
        avatar_label.setFixedSize(avatar_size, avatar_size)
        avatar_label.setStyleSheet("background: transparent; border: none;")
        
        pixmap = QPixmap(avatar_size, avatar_size)
        pixmap.fill(Qt.transparent)
        
        source_pix = QPixmap(profile_pic) if profile_pic and Path(profile_pic).exists() else QPixmap()
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QRegion(0, 0, avatar_size, avatar_size, QRegion.Ellipse)
        painter.setClipRegion(path)
        
        if not source_pix.isNull():
            # Use profile picture
            scaled = source_pix.scaled(
                avatar_size, avatar_size, 
                Qt.KeepAspectRatioByExpanding, 
                Qt.SmoothTransformation
            )
            x_off = (scaled.width() - avatar_size) // 2
            y_off = (scaled.height() - avatar_size) // 2
            painter.drawPixmap(-x_off, -y_off, scaled)
        else:
            # Generate initials avatar
            painter.setBrush(QBrush(QColor(Gate.ACCENT)))  # Sky Blue
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(0, 0, avatar_size, avatar_size)
            painter.setPen(QColor(Gate.TEXT_ON_ACCENT))
            painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            painter.drawText(QRect(0, 0, avatar_size, avatar_size), Qt.AlignmentFlag.AlignCenter,
                             initials(display_name))
        
        painter.end()
        avatar_label.setPixmap(pixmap)
        
        return avatar_label
    
    def update_responsive_layout(self, window_width: int):
        """
        Adjust header density for narrow window widths so branding remains readable.
        """
        if window_width <= 0:
            return

        if self.vfx_label:
            # One badge (vfx_label and badge_label are the same widget): shown
            # from 980 px. A second rule hid it again below 1180.
            self.vfx_label.setVisible(bool(getattr(self, "badge_text", "")) and window_width >= 980)

        # Who is signed in stays on screen at every width: the name is
        # shortened rather than hidden (it disappeared below 1120 px).
        if self.profile_text_widget and hasattr(self, "name_label"):
            self.profile_text_widget.setVisible(True)
            self._set_profile_text(PROFILE_TEXT_MAX if window_width >= 1280 else 140)

        search = getattr(self, "search_button", None)
        if search is not None:
            compact = window_width < 1180
            search.setText("" if compact else "Search or jump to\u2026   Ctrl+K")
            search.setMinimumWidth(32 if compact else 200)

        if self.local_mode_label:
            self.local_mode_label.setVisible(bool(getattr(self, "_local_mode", False)))


