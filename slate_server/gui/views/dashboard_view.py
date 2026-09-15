from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QPushButton
from PySide6.QtCore import Qt

from ..design_system import C, T
from ..components.toggle_switch import ToggleSwitch
from ..components.status_badge import StatusBadgeWidget
from ..components.stat_card import StatCard

class DashboardView(QWidget):
    """
    Main Dashboard View for Slate Central Server.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def set_cluster_facts(self, facts: dict, pool: dict):
        """
        Say which database is being served, and shout when it is not the one.

        Everything here is read from the running server rather than from the
        configuration, because the difference between the two is exactly the
        failure this is here to catch.
        """
        path = facts.get("running_data_dir") or facts.get("data_dir") or "-"
        self.card_data_dir.set_value(path)
        self.card_cluster.set_value(facts.get("size") or "-")

        tables = facts.get("tables")
        self.card_tables.set_value("-" if tables is None else str(tables))

        if not pool.get("installed"):
            self.card_pool.set_value("not installed")
        elif not pool.get("running"):
            self.card_pool.set_value("stopped")
        elif pool.get("agrees") is False:
            self.card_pool.set_value("wrong database")
        else:
            self.card_pool.set_value("port %s" % (pool.get("listen_port") or "-"))

        troubles = []
        if facts.get("error"):
            # Not "did not answer": the commonest reason it says nothing is
            # that this server has no password to ask with, and calling that a
            # dead database sends somebody to look at the wrong thing.
            troubles.append("The database could not be read: %s" % facts["error"])
        elif tables == 0:
            populated = [o for o in facts.get("others", []) if o.get("populated")]
            if populated:
                # The line that would have ended a day of this on the first
                # morning: the served database is empty and the studio's is
                # right there, in a folder nothing was pointing at.
                troubles.append(
                    "The database '%s' here has no tables, but another database "
                    "on this machine has %s of data in it: %s. That is probably "
                    "the studio's. Point Settings > Database Root Path at it and "
                    "restart."
                    % (facts.get("database") or "?", populated[0]["data"],
                       populated[0]["path"]))
            else:
                troubles.append(
                    "The database '%s' exists but has no tables. That is normal "
                    "for a brand new studio until the first workstation logs in "
                    "and builds the schema. If this studio already has data, "
                    "the Data Directory above is not the folder holding it."
                    % (facts.get("database") or "?"))
        if facts.get("matches_config") is False:
            troubles.append(
                "The server is serving %s, which is not the path in its settings "
                "(%s). The configured path was unreachable, so it fell back."
                % (facts.get("running_data_dir"), facts.get("data_dir")))
        if pool.get("agrees") is False:
            troubles.append(
                "The pool publishes '%s' but clients ask for '%s', so anything "
                "using the pooler port is refused. Restart Pool rewrites it."
                % (pool.get("publishes"), pool.get("expects")))

        if troubles:
            self.lbl_warning.setText("\n".join("- " + t for t in troubles))
            self.lbl_warning.show()
        else:
            self.lbl_warning.hide()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(30)
        
        # --- HEADER ---
        header_layout = QHBoxLayout()
        
        title_layout = QVBoxLayout()
        title_layout.setSpacing(4)
        lbl_title = QLabel("Slate Server")
        lbl_title.setStyleSheet(f"font-size: 28px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY};")
        lbl_subtitle = QLabel("Database engine and master node")
        lbl_subtitle.setStyleSheet(f"font-size: 14px; color: {C.TEXT_SECONDARY};")
        
        title_layout.addWidget(lbl_title)
        title_layout.addWidget(lbl_subtitle)
        
        self.status_badge = StatusBadgeWidget("Server Offline", "error")
        
        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        header_layout.addWidget(self.status_badge, 0, Qt.AlignTop)
        
        main_layout.addLayout(header_layout)
        
        # --- CONTROL PANEL ---
        control_panel = QWidget()
        control_panel.setStyleSheet(f"""
            QWidget {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 12px;
            }}
        """)
        # Two rows, not one. On one row the label, three buttons and the switch
        # needed about 780 pixels, and the window opened with 680 to give them
        # - so the power switch, the one control that matters, was off the
        # right-hand edge of the screen. The switch sits beside its label now,
        # and the buttons take a row of their own.
        cp_layout = QVBoxLayout(control_panel)
        cp_layout.setContentsMargins(24, 20, 24, 20)
        cp_layout.setSpacing(14)
        power_row = QHBoxLayout()
        power_row.setSpacing(14)
        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        cp_layout.addLayout(power_row)
        cp_layout.addLayout(button_row)

        lbl_power = QLabel("Database Server Power")
        lbl_power.setStyleSheet(f"font-size: 16px; font-weight: {T.WEIGHT_SEMI}; border: none;")

        self.toggle_power = ToggleSwitch()
        
        self.btn_force_kill = QPushButton("Force Kill Database")
        self.btn_force_kill.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.STATUS_ERROR};
                color: {C.TEXT_PRIMARY};
                font-weight: {T.WEIGHT_BOLD};
                padding: 6px 12px;
                border-radius: 4px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #D9635F;
            }}
        """)
        
        self.btn_api_dashboard = QPushButton("Open Web Dashboard")
        self.btn_api_dashboard.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.ACCENT_PRIMARY};
                color: white;
                font-weight: {T.WEIGHT_BOLD};
                padding: 6px 16px;
                border-radius: 4px;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #3EA8BF;
            }}
            QPushButton:disabled {{
                background-color: {C.BORDER_DEFAULT};
            }}
        """)
        self.btn_api_dashboard.setEnabled(False)
        
        power_row.addWidget(lbl_power)
        power_row.addWidget(self.toggle_power)
        power_row.addStretch()

        self.btn_restart_pool = QPushButton("Restart Pool")
        self.btn_restart_pool.setToolTip(
            "Rewrites the pool configuration and restarts it. Use this when the "
            "pool publishes a different database name from the one clients ask "
            "for - a pooler left running from an older install keeps its old "
            "config and refuses every connection.")
        self.btn_restart_pool.setStyleSheet(self.btn_api_dashboard.styleSheet())
        button_row.addWidget(self.btn_restart_pool)
        button_row.addWidget(self.btn_api_dashboard)
        button_row.addWidget(self.btn_force_kill)
        button_row.addStretch()

        main_layout.addWidget(control_panel)

        # --- STATS GRID ---
        # Laid out again whenever the width changes: four cards across on a
        # wide window, three or two on a narrow one. A fixed four-across grid
        # needed about 1,000 pixels and simply ran off the edge below that.
        self.grid_layout = QGridLayout()
        self.grid_layout.setSpacing(20)
        grid_layout = self.grid_layout
        self._grid_columns = 0
        
        self.card_ip = StatCard("Server IP (LAN)", "127.0.0.1")
        self.card_port = StatCard("PostgreSQL Port", "-")
        self.card_users = StatCard("Active Connections", "0")

        # Which database, and whether it holds anything.
        #
        # An IP, a port and a connection count can all be correct while the
        # server serves a brand new empty cluster in a fallback location and the
        # studio's real database sits untouched somewhere else. That happened:
        # every figure on this screen agreed with every other one and all of them
        # were about the wrong database. These three say which one it is.
        self.card_data_dir = StatCard("Data Directory", "-")
        # A path is not a statistic. At the card's 28px it would push the grid
        # off the side of the window, so this one reads as text.
        self.card_data_dir.lbl_value.setStyleSheet(
            f"color: {C.TEXT_PRIMARY}; font-size: 13px; font-weight: {T.WEIGHT_SEMI};")
        self.card_data_dir.lbl_value.setWordWrap(True)
        self.card_cluster = StatCard("Cluster Size", "-")
        self.card_tables = StatCard("Tables in Database", "-")
        self.card_pool = StatCard("Connection Pool", "-")

        # In reading order. The data directory card is a path, not a figure,
        # and always takes two columns.
        self._cards = [
            (self.card_ip, 1), (self.card_port, 1), (self.card_users, 1),
            (self.card_pool, 1), (self.card_data_dir, 2), (self.card_cluster, 1),
            (self.card_tables, 1),
        ]
        self._relayout_cards(4)

        main_layout.addLayout(grid_layout)

        # Said in full when something is wrong with the above, because a card is
        # four words wide and "empty" needs a sentence.
        self.lbl_warning = QLabel("")
        self.lbl_warning.setWordWrap(True)
        self.lbl_warning.setStyleSheet(
            f"font-size: 13px; color: #D9635F; font-weight: {T.WEIGHT_SEMI};")
        self.lbl_warning.hide()
        main_layout.addWidget(self.lbl_warning)
        
        # --- LOG CONSOLE ---
        log_panel = QWidget()
        log_panel.setStyleSheet(f"""
            QWidget {{
                background-color: {C.BG_ROOT};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 8px;
            }}
        """)
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(16, 16, 16, 16)
        
        lbl_log_title = QLabel("SYSTEM LOGS")
        lbl_log_title.setStyleSheet(f"color: {C.TEXT_SECONDARY}; font-size: 11px; font-weight: {T.WEIGHT_BOLD}; letter-spacing: 1px; border: none;")
        
        self.lbl_logs = QLabel("Waiting for server to start...")
        self.lbl_logs.setStyleSheet(f"font-family: Consolas, monospace; font-size: 12px; color: {C.TEXT_SECONDARY}; border: none;")
        self.lbl_logs.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        
        log_layout.addWidget(lbl_log_title)
        log_layout.addWidget(self.lbl_logs)
        log_layout.addStretch()
        
        main_layout.addWidget(log_panel, 1) # Give it stretch

    # A card's value is set at 28px, so a figure like "10.100.104.82" wants
    # about 230 pixels with its padding. The column count follows the width
    # that is actually there.
    CARD_MIN_WIDTH = 230

    def _columns_for(self, width: int) -> int:
        margins = self.layout().contentsMargins()
        usable = width - margins.left() - margins.right()
        spacing = self.grid_layout.spacing()
        columns = max(2, (usable + spacing) // (self.CARD_MIN_WIDTH + spacing))
        return min(4, int(columns))

    def _relayout_cards(self, columns: int):
        if columns == self._grid_columns:
            return
        self._grid_columns = columns

        for card, _span in self._cards:
            self.grid_layout.removeWidget(card)
        # Old column stretches would leave a phantom empty column behind.
        for column in range(6):
            self.grid_layout.setColumnStretch(column, 0)

        row, column = 0, 0
        for card, span in self._cards:
            span = min(span, columns)
            if column + span > columns:
                row, column = row + 1, 0
            self.grid_layout.addWidget(card, row, column, 1, span)
            column += span
            if column >= columns:
                row, column = row + 1, 0
        for column in range(columns):
            self.grid_layout.setColumnStretch(column, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Measured against the scroll area's viewport, not this widget. The
        # scroll area never makes this widget narrower than its layout's
        # minimum, so measuring itself always answered "wide enough for four".
        parent = self.parentWidget()
        width = parent.width() if parent is not None else event.size().width()
        self._relayout_cards(self._columns_for(width))
