import os
import re
import json
import logging
from typing import Optional, Dict, Any, List, Callable, Tuple
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, Property
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QLabel
from slate.core.infra.gate import Gate

class QuickSearchControllerMixin:
    """
    Mixin for VFXFolderCreatorApp that handles the Command Palette / Omnibar.
    """

    @staticmethod
    def _fuzzy_score(query: str, text: str) -> Optional[int]:
        """
        Lightweight fuzzy scorer.
        Lower score is better. Returns None when there is no match.
        """
        q = re.sub(r"[_\-]+", " ", str(query or "").strip().lower())
        t = re.sub(r"[_\-]+", " ", str(text or "").strip().lower())
        q = re.sub(r"\s+", " ", q).strip()
        t = re.sub(r"\s+", " ", t).strip()
        if not q:
            return 0
        if t.startswith(q):
            return 0
        if q in t:
            return 3 + max(0, t.index(q))
    
        qi = 0
        gaps = 0
        last = -1
        for idx, ch in enumerate(t):
            if qi < len(q) and ch == q[qi]:
                if last >= 0:
                    gaps += max(0, idx - last - 1)
                last = idx
                qi += 1
                if qi == len(q):
                    return 12 + gaps
        return None

    @staticmethod
    def _word_start_score(query: str, text: str) -> Optional[int]:
        """
        Every word of the query starts a word of the text ("cache" -> "Clear
        cache"). Lower is better; None when it does not match that way.
        """
        q_words = re.sub(r"[_\-]+", " ", str(query or "").lower()).split()
        t_words = re.sub(r"[_\-&/()]+", " ", str(text or "").lower()).split()
        if not q_words:
            return 0
        score = 0
        for q in q_words:
            hit = next((i for i, w in enumerate(t_words) if w.startswith(q)), None)
            if hit is None:
                return None
            score += hit
        return score

    @classmethod
    def match_rows(cls, query: str, rows):
        """
        The rows that match, best first. Words that start a word in the label
        or keywords win; letters scattered through a label ("head" in "Admin
        Panel") count only when nothing matches properly.
        """
        q = str(query or "").strip().lower()
        if not q:
            return [(0, row) for row in rows]
        strong, weak = [], []
        for row in rows:
            haystack = f"{row.get('label', '')} {row.get('keywords', '')}"
            score = cls._word_start_score(q, haystack)
            if score is not None:
                strong.append((score, row))
                continue
            score = cls._fuzzy_score(q, row.get("label", ""))
            if score is not None:
                weak.append((score + 100, row))
        return strong if strong else weak

    @staticmethod
    def _extract_shot_query(text: str) -> str:
        """
        The shot part of what was typed: 'shot 042' -> '042', 'SH010' -> 'SH010'.
        The word 'shot' used to be kept, so the placeholder's own example
        ('shot 042') could never match SH042.
        """
        raw = str(text or "").strip()
        if not raw:
            return ""
        match = re.search(r"\bshot[\s_\-]*([a-zA-Z]*\d+\w*)", raw, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        if re.search(r"\d", raw):
            return raw
        return ""

    @classmethod
    def rank_shot_names(cls, query: str, names) -> list:
        """
        Shot names that match, best first: the exact name, then the same number
        ('042' -> SH042), then a prefix ending where a number ends, then a
        longer number (SH010 -> SH0100) and scattered letters. SH0100 used to
        tie with SH010, and whichever came first in the list won.
        """
        q = str(query or "").strip().lower()
        if not q:
            return []
        ranked = []
        for name in names:
            n = str(name or "").strip().lower()
            if not n:
                continue
            numbers = [int(d) for d in re.findall(r"\d+", n)]
            at = n.find(q)
            cut_short = q[-1].isdigit() and at >= 0 and n[at + len(q):at + len(q) + 1].isdigit()
            if n == q:
                score = 0
            elif q.isdigit() and int(q) in numbers:
                score = 1
            elif n.startswith(q) and not cut_short:
                score = 2
            elif cut_short:
                score = 5
            else:
                fuzzy = cls._fuzzy_score(q, n)
                if fuzzy is None:
                    continue
                score = 10 + fuzzy
            ranked.append((score, str(name)))
        ranked.sort(key=lambda item: (item[0], item[1].lower()))
        return [name for _score, name in ranked]

    def _dashboard_widget(self):
        tab = self._get_tab_instance("VFX Dashboard", create=True)
        return tab if hasattr(tab, "all_shots") else getattr(tab, "dashboard_widget", None)

    def _open_dashboard_project(self, project_code: str) -> bool:
        """
        The VFX Dashboard, on this project. Goes through the project selector,
        so unsaved changes to another project are asked about first.
        """
        if not self._switch_to_tab_label("VFX Dashboard"):
            self.show_feedback("The dashboard is not available for this user.",
                               level="warning", duration=3500)
            return False
        dashboard = self._dashboard_widget()
        if dashboard is None:
            self.show_feedback("The dashboard could not be opened.", level="error", duration=3500)
            return False
        current = getattr(dashboard, "current_project", None)
        if current is not None and getattr(current, "code", None) == project_code:
            return True
        combo = getattr(dashboard, "project_combo", None)
        index = combo.findData(project_code) if combo is not None else -1
        if index < 0 and hasattr(dashboard, "load_projects"):
            dashboard.load_projects()           # a project made since the dashboard opened
            index = combo.findData(project_code) if combo is not None else -1
        if index >= 0:
            combo.setCurrentIndex(index)
        elif hasattr(dashboard, "switch_project"):
            dashboard.switch_project(project_code)
        current = getattr(dashboard, "current_project", None)
        return current is not None and getattr(current, "code", None) == project_code

    def _shot_locations(self, query: str, project_code: str = ""):
        """[(shot name, project code)] in active projects, best match first."""
        from slate.core.infra.database_manager import database_manager
        sql = ("SELECT s.shot_name, s.project_code FROM tracking_shots s "
               "JOIN tracking_projects p ON p.code = s.project_code AND p.active = 1")
        params = ()
        if project_code:
            sql += " WHERE s.project_code = %s"
            params = (project_code,)
        rows = database_manager.execute_query(sql, params or None, fetch="all") or []
        pairs = []
        for row in rows:
            name = row["shot_name"] if isinstance(row, dict) else row[0]
            code = row["project_code"] if isinstance(row, dict) else row[1]
            if name:
                pairs.append((str(name), str(code or "")))
        best = self.rank_shot_names(query, {name for name, _code in pairs})
        if not best:
            return []
        top = best[0].lower()
        return [(name, code) for name, code in pairs if name.lower() == top]

    def _jump_to_shot_in_review(self, query_text: str, project_code: str = "") -> bool:
        """
        Find a shot, open its project in the dashboard and select it.

        The dashboard used to be searched as it stood - and right after
        sign-in no project is open there, so every jump (from the palette or
        from Home) ended on "No shots are loaded - pick a project first".
        """
        query = self._extract_shot_query(query_text) or str(query_text or "").strip()
        if not query:
            return False
        try:
            found = self._shot_locations(query, project_code)
        except Exception as exc:
            logging.warning("Shot lookup failed: %s", exc)
            self.show_feedback("Shots could not be looked up just now.", level="error",
                               duration=4000, details=str(exc))
            return False
        if not found:
            self.show_feedback(f"No shot matches '{query}' in the active projects.",
                               level="warning", duration=3500)
            return False

        name, code = found[0]
        if len({c for _n, c in found}) > 1:
            dashboard = self._dashboard_widget() if "VFX Dashboard" in self._palette_tab_labels() else None
            open_code = getattr(getattr(dashboard, "current_project", None), "code", None)
            here = [pair for pair in found if pair[1] == open_code]
            if here:
                name, code = here[0]
            else:
                from PySide6.QtWidgets import QInputDialog
                choices = [f"{c} \u00b7 {n}" for n, c in found]
                picked, ok = QInputDialog.getItem(
                    self, "Which shot?", f"{found[0][0]} is in more than one project.",
                    choices, 0, False)
                if not ok:
                    return False
                name, code = found[choices.index(picked)]

        if not self._open_dashboard_project(code):
            return False
        dashboard = self._dashboard_widget()
        shot = next((s for s in list(getattr(dashboard, "all_shots", None) or [])
                     if str(getattr(s, "shot_name", "") or "").strip().lower() == name.lower()), None)
        if shot is None:
            # Artists see only their own shots there.
            self.show_feedback(f"{name} is in {code}, but not among the shots you can see there.",
                               level="warning", duration=4000)
            return False
        try:
            if hasattr(dashboard, "_select_shot_ids"):
                dashboard._select_shot_ids([id(shot)])
            dashboard.open_detail_dock(shot)
        except Exception as exc:
            logging.debug("Dashboard could not select %s: %s", name, exc)
            self.show_feedback(f"Found {name}, but the dashboard could not select it.",
                               level="warning", duration=3500)
            return True
        self._remember_omnibar_shot(name)
        self.show_feedback(f"Opened {name} in {code}.", level="success", duration=2500)
        return True

    @staticmethod
    def _omnibar_payload_signature(payload: Dict[str, Any]) -> str:
        """Stable signature used to de-duplicate recent command entries."""
        kind = str(payload.get("kind", ""))
        if kind == "action":
            return f"a:{payload.get('label', '')}"
        if kind == "tab":
            return f"t:{payload.get('tab_label') or payload.get('tab_index', '')}"
        if kind == "shot":
            return f"s:{payload.get('shot_query', '')}"
        return f"x:{payload.get('label', '')}"

    @staticmethod
    def _sanitize_omnibar_entry(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return a JSON-safe subset of an omnibar payload."""
        if not isinstance(payload, dict):
            return {}
        kind = str(payload.get("kind", "")).strip().lower()
        clean = {
            "kind": kind,
            "label": str(payload.get("label", "")).strip(),
        }
        if kind == "tab":
            # By name: row numbers differ between the VFX, Operations and full
            # windows and between roles, so a remembered row opened another tab.
            label = str(payload.get("tab_label", "")).strip()
            if label:
                clean["tab_label"] = label
        if kind == "shot":
            clean["shot_query"] = str(payload.get("shot_query", "")).strip()
        return clean

    def _load_omnibar_state(self) -> None:
        """Load persisted omnibar recents from global settings."""
        try:
            gs = self.settings.get("global_settings", self.global_settings) or {}
            stored_entries = gs.get("omnibar_recent_entries", [])
            if isinstance(stored_entries, list):
                cleaned_entries = []
                for row in stored_entries[:12]:
                    clean = self._sanitize_omnibar_entry(row)
                    label = str(clean.get("label", ""))
                    if any(token in label for token in ("â", "ðŸ", "Ã", "�")):
                        continue
                    if clean.get("kind") == "tab":
                        tab_label = clean.get("tab_label")
                        # Old entries kept only a row number; a screen this
                        # person does not have here is left out.
                        if not tab_label or tab_label not in self._palette_tab_labels():
                            continue
                        clean["label"] = f"Go to {tab_label}"
                    if clean.get("label"):
                        cleaned_entries.append(clean)
                self._omnibar_recent_entries = cleaned_entries
    
            stored_shots = gs.get("omnibar_recent_shots", [])
            if isinstance(stored_shots, list):
                cleaned_shots = []
                for shot_name in stored_shots[:10]:
                    shot = str(shot_name or "").strip()
                    if shot and shot.lower() not in {s.lower() for s in cleaned_shots}:
                        cleaned_shots.append(shot)
                self._omnibar_recent_shots = cleaned_shots
        except Exception as exc:
            logging.debug("Omnibar state load skipped: %s", exc)

    def _save_omnibar_state(self) -> None:
        """Persist omnibar recents into global settings."""
        try:
            self.global_settings["omnibar_recent_entries"] = list(getattr(self, "_omnibar_recent_entries", []))[:12]
            self.global_settings["omnibar_recent_shots"] = list(getattr(self, "_omnibar_recent_shots", []))[:10]
            self.settings["global_settings"] = dict(self.global_settings)
            self.config_manager.update_global_settings(self.global_settings)
        except Exception as exc:
            logging.debug("Omnibar state save skipped: %s", exc)

    def _remember_omnibar_entry(self, payload: Dict[str, Any], limit: int = 10) -> None:
        """Remember a recently executed command entry."""
        if not isinstance(payload, dict):
            return
        clean_payload = self._sanitize_omnibar_entry(payload)
        if not clean_payload.get("label"):
            return
        entries: List[Dict[str, Any]] = list(getattr(self, "_omnibar_recent_entries", []))
        sig = self._omnibar_payload_signature(clean_payload)
        deduped = [row for row in entries if self._omnibar_payload_signature(row) != sig]
        deduped.insert(0, clean_payload)
        setattr(self, "_omnibar_recent_entries", deduped[:max(1, int(limit))])
        self._save_omnibar_state()

    def _remember_omnibar_shot(self, shot_name: str, limit: int = 8) -> None:
        """Remember recently jumped shots for quick reuse."""
        clean = str(shot_name or "").strip()
        if not clean:
            return
        shots: List[str] = list(getattr(self, "_omnibar_recent_shots", []))
        shots = [s for s in shots if str(s).strip().lower() != clean.lower()]
        shots.insert(0, clean)
        setattr(self, "_omnibar_recent_shots", shots[:max(1, int(limit))])
        self._save_omnibar_state()

    def _palette_tab_labels(self) -> List[str]:
        """The screens this person has, in sidebar order - never a heading."""
        tc = getattr(self, "tab_coordinator", None)
        if tc is None:
            return []
        labels = []
        for entry in tc.nav_items:
            label = str(entry.get("label") or "").strip()
            if not label or label.startswith("__HEADER__") or not entry.get("permitted", True):
                continue
            if label not in labels:
                labels.append(label)
        return labels

    def palette_commands(self) -> List[Dict[str, Any]]:
        """
        The palette's commands and screens for this person.

        A command for a screen is offered only when that screen is here for
        them (an artist was offered Timeline Viewer commands that then said
        "not available"); the maintenance sweep only to people with the Admin
        Panel. Each command has its own keywords - they all shared "command
        maintenance cache diagnostics", so "cache" listed every command.
        """
        tabs = self._palette_tab_labels()
        allowed = list(getattr(self, "allowed_tabs", []) or [])
        is_dev = str(getattr(self, "user_role", "") or "").strip().lower() == "developer"
        maintenance = is_dev or "ALL" in allowed or "Admin Panel" in allowed

        actions = [
            ("Diagnostics (Ctrl+Shift+D)", self.show_runtime_diagnostics,
             "diagnostics version database support it", None),
            ("Refresh this screen (F5)", self.refresh_current_tab, "refresh reload update", None),
            ("Open Help (F1)", self.show_help_dialog, "help manual documentation", None),
            ("Keyboard shortcuts", getattr(self, "show_shortcuts", None), "keys shortcuts keyboard", None),
            ("Rebuild Timeline from Dashboard", self._rebuild_timeline_from_dashboard,
             "timeline rebuild lineup rv edl", "Timeline Viewer"),
            ("Refresh Stock Viewer", self._refresh_stock_viewer, "stock rescan library refresh", "Stock Viewer"),
            ("Full screen (F11)", self.toggle_fullscreen, "fullscreen window maximise", None),
            ("Clear temporary files (maintenance)", self._run_quick_temp_cleanup,
             "maintenance cache temp clean sweep", "__maintenance__"),
        ]
        rows = []
        for label, callback, keywords, needs in actions:
            if not callable(callback):
                continue
            if needs == "__maintenance__":
                if not maintenance:
                    continue
            elif needs and needs not in tabs:
                continue
            rows.append({"kind": "action", "label": label, "keywords": keywords, "callback": callback,
                         # Never the row Enter runs without the person choosing it.
                         "careful": needs == "__maintenance__"})
        keys = {"Settings": " (Ctrl+Shift+S)"}
        for label in tabs:
            rows.append({"kind": "tab", "label": f"Go to {label}{keys.get(label, '')}",
                         "keywords": f"go open {label} preferences options" if label == "Settings"
                         else f"go open {label}", "tab_label": label})
        return rows

    @staticmethod
    def palette_preselect(payloads):
        """
        The row Enter would run: the first real row that is not a maintenance
        or destructive command (an admin's Ctrl+K, Enter started a sweep).
        """
        for i, payload in enumerate(payloads):
            if payload and not payload.get("careful"):
                return i
        return None

    def _go_to_tab(self, label: str) -> bool:
        """Open a screen by name, unfolding its sidebar group first."""
        tc = self.tab_coordinator
        for entry in tc.nav_items:
            if entry.get("label") == label and entry.get("item") is not None:
                row = tc.sidebar_nav.row(entry["item"])
                tc._reveal_row(row)
                self.switch_to_tab_index(row)
                return True
        return False

    def show_quick_search(self):
        """Show global quick search / command palette."""
        from PySide6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QFrame,
            QGraphicsDropShadowEffect,
            QStyledItemDelegate,
            QStyle
        )
        from PySide6.QtGui import QColor, QPainter
        from PySide6.QtCore import QPropertyAnimation, QEasingCurve, Property
    
        class OmnibarResultDelegate(QStyledItemDelegate):
            """Delegate to handle smooth background transitions on hover/selection."""
            def __init__(self, parent=None):
                super().__init__(parent)
                self.parent_list = parent
                self._hover_alpha = 0.0
                self._hover_index = -1
                self._anim = QPropertyAnimation(self, b"hover_alpha", self)
                self._anim.setDuration(150)
                self._anim.setEasingCurve(QEasingCurve.OutQuad)
    
            @Property(float)
            def hover_alpha(self):
                return self._hover_alpha
    
            @hover_alpha.setter
            def hover_alpha(self, value):
                self._hover_alpha = value
                if self.parent_list:
                    self.parent_list.viewport().update()
    
            def paint(self, painter, option, index):
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                
                is_selected = option.state & QStyle.StateFlag.State_Selected
                is_hovered = option.state & QStyle.StateFlag.State_MouseOver
                
                # Update animation state
                if is_hovered:
                    if self._hover_index != index.row():
                        self._hover_index = index.row()
                        self._anim.stop()
                        self._anim.setEndValue(1.0)
                        self._anim.start()
                elif self._hover_index == index.row():
                    self._hover_index = -1
                    self._anim.stop()
                    self._anim.setEndValue(0.0)
                    self._anim.start()
    
                # The selected row has a fill of its own and an accent bar:
                # selection, hover and the panel were one colour, so arrow
                # keys moved an invisible cursor.
                bg_color = QColor(Gate.PANEL)
                payload_here = index.data(Qt.ItemDataRole.UserRole)
                if is_selected and payload_here:
                    bg_color = QColor(Gate.SELECTION)
                elif self._hover_index == index.row() and payload_here:
                    hover = QColor(Gate.mix(Gate.PANEL, Gate.TEXT, 0.08))
                    alpha = max(0.0, min(1.0, self._hover_alpha))
                    bg_color = QColor(
                        int(bg_color.red() + (hover.red() - bg_color.red()) * alpha),
                        int(bg_color.green() + (hover.green() - bg_color.green()) * alpha),
                        int(bg_color.blue() + (hover.blue() - bg_color.blue()) * alpha))

                painter.setBrush(bg_color)
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(option.rect.adjusted(2, 2, -2, -2), 6, 6)
                if is_selected and payload_here:
                    painter.setBrush(QColor(Gate.ACCENT))
                    bar = option.rect.adjusted(2, 6, 0, -6)
                    bar.setWidth(3)
                    painter.drawRoundedRect(bar, 1.5, 1.5)
                
                # Text
                text = index.data(Qt.ItemDataRole.DisplayRole)
                payload = index.data(Qt.ItemDataRole.UserRole)
                if not payload: # Section header
                    painter.setPen(QColor(Gate.TEXT_DIM))
                    font = painter.font()
                    font.setBold(True)
                    font.setPointSize(9)
                    painter.setFont(font)
                    painter.drawText(option.rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, text)
                else:
                    painter.setPen(QColor(Gate.TEXT) if is_selected else QColor(Gate.TEXT_2))
                    painter.drawText(option.rect.adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter, text)
                
                painter.restore()
    
            def sizeHint(self, option, index):
                size = super().sizeHint(option, index)
                size.setHeight(38)
                return size
    
        dialog = QDialog(self)
        dialog.setWindowTitle("Command Palette")
        # A popup: a click outside closes it, as palettes do (a modal frameless
        # dialog swallowed the click and stayed).
        dialog.setWindowFlags(Qt.WindowType.Popup)
        dialog.setMinimumSize(760, 520)
        # The rounded panel and its shadow margin are drawn on nothing: without
        # this the corners and margin were painted as an opaque square.
        dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._palette_dialog = dialog
        
        outer_layout = QVBoxLayout(dialog)
        outer_layout.setContentsMargins(20, 20, 20, 20)
        outer_layout.setSpacing(0)
    
        panel = QFrame(dialog)
        panel.setObjectName("omnibarPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 14, 14, 14)
        panel_layout.setSpacing(10)
    
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(32)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 10)
        panel.setGraphicsEffect(shadow)
    
        outer_layout.addWidget(panel)
    
        header = QLabel("Search or jump to")
        header.setObjectName("omnibarHeader")
        panel_layout.addWidget(header)
    
        search_input = QLineEdit()
        search_input.setObjectName("omnibarInput")
        search_input.setPlaceholderText("Search commands, screens or shots (e.g. shot 042)\u2026")
        panel_layout.addWidget(search_input)
    
        results_list = QListWidget()
        results_list.setObjectName("omnibarResults")
        results_list.setItemDelegate(OmnibarResultDelegate(results_list))
        panel_layout.addWidget(results_list)
    
        dialog.setStyleSheet(
            f"""
            QDialog {{
                background: transparent;
            }}
            QFrame#omnibarPanel {{
                background-color: {Gate.PANEL};
                border: 1px solid {Gate.LINE};
                border-radius: 12px;
            }}
            QLabel#omnibarHeader {{
                color: {Gate.TEXT_2};
                font-size: 12pt;
                font-weight: 600;
            }}
            QLineEdit#omnibarInput {{
                background-color: {Gate.INPUT};
                color: {Gate.TEXT};
                border: 1px solid {Gate.LINE};
                border-radius: 8px;
                padding: 8px 10px;
                font-size: 11pt;
            }}
            QLineEdit#omnibarInput:focus {{
                border: 1px solid {Gate.ACCENT};
            }}
            QListWidget#omnibarResults {{
                background-color: {Gate.PANEL};
                color: {Gate.TEXT_2};
                border: none;
                border-radius: 8px;
                padding: 4px;
                font-size: 10.5pt;
            }}
            QListWidget#omnibarResults::item {{
                padding: 8px 10px;
                border-radius: 6px;
            }}
            QListWidget#omnibarResults::item:selected {{
                background-color: {Gate.SELECTION};
                color: {Gate.TEXT};
            }}
            """
        )
    
        command_rows: List[Dict[str, Any]] = self.palette_commands()
        action_lookup = {row["label"]: row["callback"] for row in command_rows if row["kind"] == "action"}
        careful_labels = {row["label"] for row in command_rows if row.get("careful")}

        def add_section(title: str):
            section_item = QListWidgetItem(title)
            section_item.setFlags(Qt.NoItemFlags)
            section_item.setData(Qt.ItemDataRole.UserRole, None)
            section_item.setForeground(QColor(Gate.TEXT_DIM))
            results_list.addItem(section_item)
    
        def accept_current_item():
            current = results_list.currentItem()
            if not current:
                return
            payload = current.data(Qt.ItemDataRole.UserRole) or {}
            if not payload:
                return
            kind = payload.get("kind")
            if kind == "action":
                callback = payload.get("callback")
                if callable(callback):
                    callback()
                self._remember_omnibar_entry(payload)
                dialog.accept()
                return
            if kind == "tab":
                label = payload.get("tab_label")
                if label:
                    self._go_to_tab(label)
                self._remember_omnibar_entry(payload)
                dialog.accept()
                return
            if kind == "shot":
                query_text = str(payload.get("shot_query", "")).strip()
                jumped = self._jump_to_shot_in_review(query_text)
                if jumped:
                    self._remember_omnibar_entry(payload)
                dialog.accept()
                return
    
        def select_next_selectable(step: int) -> bool:
            """Move selection while skipping non-selectable section rows."""
            count = results_list.count()
            if count <= 0:
                return False
            current_row = results_list.currentRow()
            idx = 0 if current_row < 0 else current_row
            for _ in range(count):
                idx = (idx + step) % count
                item = results_list.item(idx)
                payload = item.data(Qt.ItemDataRole.UserRole) if item else None
                if payload:
                    results_list.setCurrentRow(idx)
                    results_list.scrollToItem(item)
                    return True
            return False
    
        def autocomplete_from_selection():
            """Fill the input with selected result text/target."""
            current = results_list.currentItem()
            if not current:
                return
            payload = current.data(Qt.ItemDataRole.UserRole) or {}
            if not payload:
                return
    
            kind = str(payload.get("kind", "")).strip().lower()
            if kind == "tab":
                text = str(payload.get("tab_label") or payload.get("label", "")).strip()
            elif kind == "shot":
                text = str(payload.get("shot_query", "")).strip()
            else:
                text = str(payload.get("label", "")).strip()
    
            if text:
                search_input.setText(text)
                search_input.setCursorPosition(len(text))
                search_input.setFocus()
    
        def update_results(query=""):
            results_list.clear()
            q = str(query or "").strip().lower()
            ranked_actions: List[Tuple[int, Dict[str, Any]]] = []
            ranked_tabs: List[Tuple[int, Dict[str, Any]]] = []
            ranked_shots: List[Tuple[int, Dict[str, Any]]] = []
    
            for score, row in self.match_rows(q, command_rows):
                kind = row.get("kind")
                if kind == "action":
                    ranked_actions.append((score, row))
                elif kind == "tab":
                    ranked_tabs.append((score, row))
    
            shot_query = self._extract_shot_query(q)
            if shot_query:
                shot_row = {
                    "kind": "shot",
                    "label": f"Jump to shot {shot_query.upper() if shot_query.isalnum() else shot_query}",
                    "shot_query": shot_query,
                }
                shot_score = self._fuzzy_score(q, f"jump shot {shot_query}")
                ranked_shots.append((1 if shot_score is None else max(1, shot_score), shot_row))
    
            if not q:
                recent_entries: List[Dict[str, Any]] = list(getattr(self, "_omnibar_recent_entries", []))
                if recent_entries:
                    add_section("Recent")
                    for row in recent_entries[:8]:
                        safe_row = dict(row)
                        if safe_row.get("kind") == "action" and not callable(safe_row.get("callback")):
                            callback = action_lookup.get(str(safe_row.get("label", "")))
                            if not callback:
                                continue        # not offered to this person here
                            safe_row["callback"] = callback
                            safe_row["careful"] = safe_row.get("label") in careful_labels
                        item = QListWidgetItem(str(safe_row.get("label", "")))
                        item.setData(Qt.ItemDataRole.UserRole, safe_row)
                        results_list.addItem(item)
    
                recent_shots: List[str] = list(getattr(self, "_omnibar_recent_shots", []))
                if recent_shots:
                    add_section("Recent shots")
                    for shot_name in recent_shots[:6]:
                        row = {
                            "kind": "shot",
                            "label": f"Jump to shot {shot_name}",
                            "shot_query": shot_name,
                        }
                        item = QListWidgetItem(str(row.get("label", "")))
                        item.setData(Qt.ItemDataRole.UserRole, row)
                        results_list.addItem(item)
    
            if q:
                ranked_actions.sort(key=lambda x: (x[0], str(x[1].get("label", "")).lower()))
                ranked_tabs.sort(key=lambda x: (x[0], str(x[1].get("label", "")).lower()))
            # Nothing typed: the sidebar's own order (they are already in it).
            ranked_shots.sort(key=lambda x: (x[0], str(x[1].get("label", "")).lower()))
    
            # Screens first: getting somewhere is what the palette is mostly for.
            if ranked_tabs:
                add_section("Screens")
                for _score, row in ranked_tabs[:36]:
                    item = QListWidgetItem(str(row.get("label", "")))
                    item.setData(Qt.ItemDataRole.UserRole, row)
                    results_list.addItem(item)
    
            if ranked_actions:
                add_section("Commands")
                for _score, row in ranked_actions[:36]:
                    item = QListWidgetItem(str(row.get("label", "")))
                    item.setData(Qt.ItemDataRole.UserRole, row)
                    results_list.addItem(item)
    
            if ranked_shots:
                add_section("Shots")
                for _score, row in ranked_shots[:12]:
                    item = QListWidgetItem(str(row.get("label", "")))
                    item.setData(Qt.ItemDataRole.UserRole, row)
                    results_list.addItem(item)
    
            if q and results_list.count() == 0:
                add_section(f'Nothing matches "{query.strip()}". Try a screen name, a command '
                            "or a shot number.")
            payloads = [results_list.item(i).data(Qt.ItemDataRole.UserRole)
                        for i in range(results_list.count())]
            # Ctrl+K, Enter used to open Admin Panel (first alphabetically).
            # Empty, only a Recent row is offered to Enter.
            if not q:
                recent_first = results_list.count() and results_list.item(0).text() == "Recent"
                payloads = payloads[:2] if recent_first else []
            preselect_row = self.palette_preselect(payloads)
            if preselect_row is not None:
                results_list.setCurrentRow(preselect_row)
    
        search_input.textChanged.connect(update_results)
        results_list.itemActivated.connect(lambda _item: accept_current_item())
        search_input.returnPressed.connect(accept_current_item)
    
        # Keyboard polish: header-safe navigation + autocomplete + instant escape.
        search_input_orig_keypress = search_input.keyPressEvent
        def search_input_keypress(event):
            key = event.key()
            if key == Qt.Key.Key_Down:
                select_next_selectable(1)
                return
            if key == Qt.Key.Key_Up:
                select_next_selectable(-1)
                return
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                autocomplete_from_selection()
                return
            if key == Qt.Key.Key_Escape:
                dialog.reject()
                return
            search_input_orig_keypress(event)
        search_input.keyPressEvent = search_input_keypress
    
        results_list_orig_keypress = results_list.keyPressEvent
        def results_list_keypress(event):
            key = event.key()
            if key == Qt.Key.Key_Down:
                select_next_selectable(1)
                return
            if key == Qt.Key.Key_Up:
                select_next_selectable(-1)
                return
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                autocomplete_from_selection()
                return
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                accept_current_item()
                return
            if key == Qt.Key.Key_Escape:
                dialog.reject()
                return
            results_list_orig_keypress(event)
        results_list.keyPressEvent = results_list_keypress
    
        dialog.adjustSize()
        frame_geo = self.frameGeometry()
        dialog.move(frame_geo.center() - dialog.rect().center())
    
        update_results()
        search_input.setFocus()
        dialog.exec()
