import os
import re
import json
import logging
from typing import Optional, Dict, Any, List, Callable, Tuple
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, Property
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QLabel

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
    def _extract_shot_query(text: str) -> str:
        """Extract normalized shot query token from omnibar text."""
        raw = str(text or "").strip()
        if not raw:
            return ""
        match = re.search(r"\bshot[\s_\-]*([a-zA-Z]*\d+[a-zA-Z]*)\b", raw, flags=re.IGNORECASE)
        if match:
            return f"shot {match.group(1)}"
        if re.search(r"\d", raw):
            return raw
        return ""

    def _jump_to_shot_in_review(self, query_text: str) -> bool:
        """
        Find a shot and select it in the dashboard.

        This used to drive the Shot Checker's own list. The Shot Checker is
        gone - reviewing happens in OpenRV - so the dashboard, which is where
        every shot actually lives, is what gets jumped to.
        """
        if not self._switch_to_tab_label("VFX Dashboard"):
            self.show_feedback("The dashboard is not available for this user.",
                               level="warning", duration=3500)
            return False

        tab = self._get_tab_instance("VFX Dashboard", create=True)
        dashboard = tab if hasattr(tab, "all_shots") else getattr(
            tab, "dashboard_widget", None)
        if dashboard is None:
            self.show_feedback("The dashboard could not be opened.",
                               level="error", duration=3500)
            return False

        shots = list(getattr(dashboard, "all_shots", None) or [])
        if not shots:
            self.show_feedback("No shots are loaded - pick a project first.",
                               level="warning", duration=3500)
            return False

        query = self._extract_shot_query(query_text) or str(query_text or "").strip()

        best_shot = None
        best_score = None
        for shot in shots:
            name = str(getattr(shot, "shot_name", "") or "").strip()
            if not name:
                continue
            score = self._fuzzy_score(query, name)
            if score is None:
                continue
            if best_score is None or score < best_score:
                best_score = score
                best_shot = shot

        if best_shot is None:
            self.show_feedback(f"No shot match found for '{query}'.",
                               level="warning", duration=3500)
            return False

        shot_name = str(getattr(best_shot, "shot_name", "") or "").strip()
        selected = False
        for method in ("select_shot_by_name", "focus_shot", "open_detail_dock"):
            handler = getattr(dashboard, method, None)
            if handler is None:
                continue
            try:
                handler(shot_name if method != "open_detail_dock" else best_shot)
                selected = True
                break
            except Exception as exc:
                logging.debug("Dashboard %s failed: %s", method, exc)

        self._remember_omnibar_shot(shot_name)
        if selected:
            self.show_feedback(f"Jumped to shot: {shot_name}",
                               level="success", duration=2500)
        else:
            self.show_feedback(
                f"Found {shot_name}, but the dashboard could not select it.",
                level="warning", duration=3500)
        return True

    @staticmethod
    def _omnibar_payload_signature(payload: Dict[str, Any]) -> str:
        """Stable signature used to de-duplicate recent command entries."""
        kind = str(payload.get("kind", ""))
        if kind == "action":
            return f"a:{payload.get('label', '')}"
        if kind == "tab":
            return f"t:{payload.get('tab_index', '')}"
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
            tab_index = payload.get("tab_index")
            if isinstance(tab_index, int):
                clean["tab_index"] = tab_index
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
                        tab_index = clean.get("tab_index")
                        if isinstance(tab_index, int) and 0 <= tab_index < len(self.tab_coordinator.tab_labels):
                            clean["label"] = f"Go to Tab: {self.tab_coordinator.tab_labels[tab_index]}"
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
    
                # Base background
                bg_color = QColor("#16323A") # Matching omnibarResults bg
                
                if is_selected:
                    bg_color = QColor("#16323A")
                elif self._hover_index == index.row() or (not is_hovered and self._hover_alpha > 0 and self._hover_index == -1):
                    # Blend hover color
                    alpha = self._hover_alpha if self._hover_index == index.row() else 0.0
                    if alpha > 0:
                        hover_base = QColor("#16323A")
                        r = bg_color.red() + (hover_base.red() - bg_color.red()) * alpha
                        g = bg_color.green() + (hover_base.green() - bg_color.green()) * alpha
                        b = bg_color.blue() + (hover_base.blue() - bg_color.blue()) * alpha
                        bg_color = QColor(int(r), int(g), int(b))
                
                painter.setBrush(bg_color)
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(option.rect.adjusted(2, 2, -2, -2), 6, 6)
                
                # Text
                text = index.data(Qt.ItemDataRole.DisplayRole)
                payload = index.data(Qt.ItemDataRole.UserRole)
                if not payload: # Section header
                    painter.setPen(QColor("#6BA4C9"))
                    font = painter.font()
                    font.setBold(True)
                    font.setPointSize(9)
                    painter.setFont(font)
                    painter.drawText(option.rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, text)
                else:
                    painter.setPen(QColor("#3EA8BF") if is_selected else QColor("#6BA4C9"))
                    painter.drawText(option.rect.adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter, text)
                
                painter.restore()
    
            def sizeHint(self, option, index):
                size = super().sizeHint(option, index)
                size.setHeight(38)
                return size
    
        dialog = QDialog(self)
        dialog.setWindowTitle("Command Palette")
        dialog.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        dialog.setMinimumSize(760, 520)
        dialog.setModal(True)
        dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
    
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
    
        header = QLabel("Command Palette")
        header.setObjectName("omnibarHeader")
        panel_layout.addWidget(header)
    
        search_input = QLineEdit()
        search_input.setObjectName("omnibarInput")
        search_input.setPlaceholderText("Type command, tab name, or 'shot 042'...")
        panel_layout.addWidget(search_input)
    
        results_list = QListWidget()
        results_list.setObjectName("omnibarResults")
        results_list.setItemDelegate(OmnibarResultDelegate(results_list))
        panel_layout.addWidget(results_list)
    
        dialog.setStyleSheet(
            """
            QDialog {
                background: rgba(0, 0, 0, 0);
            }
            QFrame#omnibarPanel {
                background-color: #16323A;
                border: 1px solid #16323A;
                border-radius: 12px;
            }
            QLabel#omnibarHeader {
                color: #6BA4C9;
                font-size: 12pt;
                font-weight: 600;
            }
            QLineEdit#omnibarInput {
                background-color: #16323A;
                color: #6BA4C9;
                border: 1px solid #2C2C34;
                border-radius: 8px;
                padding: 8px 10px;
                font-size: 11pt;
            }
            QLineEdit#omnibarInput:focus {
                border: 1px solid #3EA8BF;
            }
            QListWidget#omnibarResults {
                background-color: #16323A;
                color: #6BA4C9;
                border: 1px solid #16323A;
                border-radius: 8px;
                padding: 4px;
                font-size: 10.5pt;
            }
            QListWidget#omnibarResults::item {
                padding: 8px 10px;
                border-radius: 6px;
            }
            QListWidget#omnibarResults::item:selected {
                background-color: #16323A;
                color: #3EA8BF;
            }
            """
        )
    
        action_items: List[Tuple[str, Callable[[], None]]] = [
            ("Runtime Diagnostics (Ctrl+Shift+D)", self.show_runtime_diagnostics),
            ("Refresh Current Tab (F5 / Ctrl+R)", self.refresh_current_tab),
            ("Open Settings (Ctrl+Shift+S)", self.show_settings_tab),
            ("Open VFX Dashboard", lambda: self._open_named_tab("VFX Dashboard")),
            ("Open Timeline Viewer", lambda: self._open_named_tab("Timeline Viewer")),
            ("Open Stock Viewer", lambda: self._open_named_tab("Stock Viewer")),
            ("Open Help (F1)", self.show_help_dialog),
            ("Rebuild Timeline from Dashboard", self._rebuild_timeline_from_dashboard),
            ("Refresh Stock Viewer", self._refresh_stock_viewer),
            ("Toggle Fullscreen (F11)", self.toggle_fullscreen),

            ("Run Maintenance Sweep", self._run_quick_temp_cleanup),
        ]
        action_lookup = {label: callback for label, callback in action_items}
    
        command_rows: List[Dict[str, Any]] = []
        for label, callback in action_items:
            command_rows.append(
                {
                    "kind": "action",
                    "label": label,
                    "keywords": f"action {label} command maintenance cache diagnostics",
                    "callback": callback,
                }
            )
    
        for i, label in enumerate(self.tab_coordinator.tab_labels):
            item = self.sidebar_nav.item(i)
            if not item or item.isHidden():
                continue
            clean_label = str(label).strip()
            visible_text = item.text().strip()
            command_rows.append(
                {
                    "kind": "tab",
                    "label": f"Go to Tab: {clean_label}",
                    "keywords": f"tab goto switch open {clean_label} {visible_text}",
                    "tab_index": i,
                }
            )
    
        def add_section(title: str):
            section_item = QListWidgetItem(title)
            section_item.setFlags(Qt.NoItemFlags)
            section_item.setData(Qt.ItemDataRole.UserRole, None)
            section_item.setForeground(QColor("#6BA4C9"))
            section_item.setBackground(QColor("#16323A"))
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
                idx = payload.get("tab_index")
                if isinstance(idx, int):
                    self.switch_to_tab_index(idx)
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
                text = str(payload.get("label", "")).replace("Go to Tab: ", "", 1).strip()
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
    
            for row in command_rows:
                score = self._fuzzy_score(q, row.get("keywords", "")) if q else 0
                if score is None:
                    continue
                kind = row.get("kind")
                if kind == "action":
                    ranked_actions.append((score, row))
                elif kind == "tab":
                    ranked_tabs.append((score, row))
    
            shot_query = self._extract_shot_query(q)
            if shot_query:
                shot_row = {
                    "kind": "shot",
                    "label": f"Jump to Shot: {shot_query}",
                    "shot_query": shot_query,
                }
                shot_score = self._fuzzy_score(q, f"jump shot {shot_query}")
                ranked_shots.append((1 if shot_score is None else max(1, shot_score), shot_row))
    
            if not q:
                recent_entries: List[Dict[str, Any]] = list(getattr(self, "_omnibar_recent_entries", []))
                if recent_entries:
                    add_section("Recent Commands")
                    for row in recent_entries[:8]:
                        safe_row = dict(row)
                        if safe_row.get("kind") == "action" and not callable(safe_row.get("callback")):
                            callback = action_lookup.get(str(safe_row.get("label", "")))
                            if callback:
                                safe_row["callback"] = callback
                        item = QListWidgetItem(str(safe_row.get("label", "")))
                        item.setData(Qt.ItemDataRole.UserRole, safe_row)
                        results_list.addItem(item)
    
                recent_shots: List[str] = list(getattr(self, "_omnibar_recent_shots", []))
                if recent_shots:
                    add_section("Recent Shots")
                    for shot_name in recent_shots[:6]:
                        row = {
                            "kind": "shot",
                            "label": f"Jump to Shot: {shot_name}",
                            "shot_query": shot_name,
                        }
                        item = QListWidgetItem(str(row.get("label", "")))
                        item.setData(Qt.ItemDataRole.UserRole, row)
                        results_list.addItem(item)
    
            ranked_actions.sort(key=lambda x: (x[0], str(x[1].get("label", "")).lower()))
            ranked_tabs.sort(key=lambda x: (x[0], str(x[1].get("label", "")).lower()))
            ranked_shots.sort(key=lambda x: (x[0], str(x[1].get("label", "")).lower()))
    
            if ranked_actions:
                add_section("Commands")
                for _score, row in ranked_actions[:36]:
                    item = QListWidgetItem(str(row.get("label", "")))
                    item.setData(Qt.ItemDataRole.UserRole, row)
                    results_list.addItem(item)
    
            if ranked_tabs:
                add_section("Tabs")
                for _score, row in ranked_tabs[:36]:
                    item = QListWidgetItem(str(row.get("label", "")))
                    item.setData(Qt.ItemDataRole.UserRole, row)
                    results_list.addItem(item)
    
            if ranked_shots:
                add_section("Shot Jump")
                for _score, row in ranked_shots[:12]:
                    item = QListWidgetItem(str(row.get("label", "")))
                    item.setData(Qt.ItemDataRole.UserRole, row)
                    results_list.addItem(item)
    
            if results_list.count() > 0:
                for i in range(results_list.count()):
                    payload = results_list.item(i).data(Qt.ItemDataRole.UserRole)
                    if payload:
                        results_list.setCurrentRow(i)
                        break
    
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
