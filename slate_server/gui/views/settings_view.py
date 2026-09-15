from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFormLayout, QFrame
from PySide6.QtCore import Qt, Signal

from ..design_system import C, T

class SettingsView(QWidget):
    """
    Settings View for Slate Central Server.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(30)

        # --- HEADER ---
        lbl_title = QLabel("Server Configuration")
        lbl_title.setStyleSheet(f"font-size: 28px; font-weight: {T.WEIGHT_BOLD}; color: {C.TEXT_PRIMARY};")
        main_layout.addWidget(lbl_title)

        # --- SETTINGS FORM ---
        form_panel = QWidget()
        form_panel.setObjectName("formPanel")
        # Addressed by name. A bare "QWidget" selector matches every child as
        # well as the panel, so every label in this form was given the panel's
        # background, its border and its rounded corners - and drew as a box
        # over the field beside it.
        form_panel.setStyleSheet(f"""
            QWidget#formPanel {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 12px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
            QLineEdit {{
                background-color: {C.BG_ROOT};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 6px;
                padding: 8px;
                color: {C.TEXT_PRIMARY};
                font-family: {T.FAMILY};
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border: 1px solid {C.ACCENT_PRIMARY};
            }}
        """)
        form_layout = QFormLayout(form_panel)
        form_layout.setContentsMargins(24, 24, 24, 24)
        form_layout.setSpacing(20)

        # Database Path
        self.input_db_path = QLineEdit()
        # A hint, not a value. This field used to be pre-filled with one
        # studio's drive letter, so a server that had never been configured
        # looked configured - and pointed at a drive that did not exist.
        self.input_db_path.setPlaceholderText(
            "Where the database lives, e.g. D:\\Slate_Central\\Database "
            "or \\\\server\\share\\Slate_Central\\Database")
        self.input_db_path.setToolTip(
            "The folder holding the PostgreSQL cluster. Anywhere this machine "
            "can write - a second drive, a share. Point it at an existing "
            "database to adopt it; point it at an empty folder and the server "
            "builds a new one there.")

        lbl_db_path = QLabel("Database Root Path:")
        lbl_db_path.setStyleSheet(f"font-size: 14px; font-weight: {T.WEIGHT_SEMI}; color: {C.TEXT_SECONDARY};")

        form_layout.addRow(lbl_db_path, self.input_db_path)

        # Port
        self.input_port = QLineEdit()
        self.input_port.setText("5440")
        self.input_port.setFixedWidth(100)

        lbl_port = QLabel("PostgreSQL Port:")
        lbl_port.setStyleSheet(f"font-size: 14px; font-weight: {T.WEIGHT_SEMI}; color: {C.TEXT_SECONDARY};")

        form_layout.addRow(lbl_port, self.input_port)

        # The rest of what the server actually runs on. Only the path and the
        # port were editable, so everything else could only be changed by
        # editing a file whose location was not written down anywhere.
        self.input_pooler_port = QLineEdit()
        self.input_pooler_port.setPlaceholderText("6432")
        self.input_pooler_port.setText("6432")
        lbl_pooler = QLabel("Connection Pool Port:")
        lbl_pooler.setStyleSheet(lbl_port.styleSheet())
        form_layout.addRow(lbl_pooler, self.input_pooler_port)

        self.input_db_name = QLineEdit()
        self.input_db_name.setPlaceholderText("ut_vfx")
        self.input_db_name.setToolTip(
            "The database inside the cluster. Clients ask for this name, and "
            "PostgreSQL answers a request for a name that does not exist by "
            "creating an empty one rather than complaining - so a typo here is "
            "how a studio ends up with two databases and its work in the other.")
        lbl_db_name = QLabel("Database Name:")
        lbl_db_name.setStyleSheet(lbl_port.styleSheet())
        form_layout.addRow(lbl_db_name, self.input_db_name)

        # The one setting the server cannot start a usable database without,
        # and the only one that was not editable anywhere. Without it the
        # accounts are never created, so nothing - not a workstation, not the
        # server itself - can log in to the cluster it has just built.
        self.input_db_password = QLineEdit()
        self.input_db_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_db_password.setPlaceholderText("not set")
        self.input_db_password.setToolTip(
            "The password the database is created with, and the one every "
            "workstation uses. It has to match what the clients were installed "
            "with, or they will reach the server and be turned away.")
        lbl_db_password = QLabel("Database Password:")
        lbl_db_password.setStyleSheet(lbl_port.styleSheet())
        form_layout.addRow(lbl_db_password, self.input_db_password)

        self.input_max_conn = QLineEdit()
        self.input_max_conn.setPlaceholderText("100")
        self.input_max_conn.setToolTip(
            "How many connections PostgreSQL itself accepts. With the pool in "
            "front of it this rarely needs raising; the pool's own size is what "
            "a hundred and fifty workstations actually consume.")
        lbl_max_conn = QLabel("Max Connections:")
        lbl_max_conn.setStyleSheet(lbl_port.styleSheet())
        form_layout.addRow(lbl_max_conn, self.input_max_conn)

        # Nothing here may be shrunk below the height its own text needs. A
        # QLineEdit's minimum is smaller than that, so a window a little too
        # short took the difference out of every field on the screen - which
        # looks like a broken theme rather than a window that wants scrolling.
        for field in (self.input_db_path, self.input_port, self.input_pooler_port,
                      self.input_db_name, self.input_db_password,
                      self.input_max_conn):
            field.setMinimumHeight(field.sizeHint().height())

        main_layout.addWidget(form_panel)

        # Deliberately outside the form. A word-wrapped label in a QFormLayout
        # row keeps the height the row was given for one line, so the second and
        # third lines draw straight over whatever is beneath them - and this one
        # carries three file paths. Full width, on its own, wrapping into real
        # space.
        self.lbl_config_source = QLabel("-")
        self.lbl_config_source.setWordWrap(True)
        self.lbl_config_source.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_config_source.setStyleSheet(
            f"font-size: 12px; color: {C.TEXT_SECONDARY}; padding: 0px 4px;")
        main_layout.addWidget(self.lbl_config_source)

        # --- SAVE BUTTON ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.btn_firewall = QPushButton("Allow Firewall")
        self.btn_firewall.setCursor(Qt.CursorShape.PointingHandCursor)
        # Fixed widths clipped their own labels as soon as the text or the
        # font changed. Height is fixed; width is a floor.
        self.btn_firewall.setMinimumWidth(140)
        self.btn_firewall.setFixedHeight(40)
        self.btn_firewall.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.BG_SURFACE_HOVER};
                color: {C.TEXT_PRIMARY};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 6px;
                font-size: 14px;
                font-weight: {T.WEIGHT_BOLD};
            }}
            QPushButton:hover {{
                background-color: {C.BG_SURFACE};
            }}
        """)

        self.btn_save = QPushButton("Save Configuration")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setMinimumWidth(180)
        self.btn_save.setFixedHeight(40)
        self.btn_save.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.ACCENT_PRIMARY};
                color: {C.TEXT_PRIMARY};
                border: none;
                border-radius: 6px;
                padding: 0px 18px;
                font-size: 14px;
                font-weight: {T.WEIGHT_BOLD};
            }}
            QPushButton:hover {{
                background-color: #3EA8BF;
            }}
            QPushButton:pressed {{
                background-color: #3EA8BF;
            }}
        """)

        btn_layout.addWidget(self.btn_firewall)
        btn_layout.addWidget(self.btn_save)
        main_layout.addLayout(btn_layout)

        main_layout.addSpacing(20)

        # --- UPDATE SECTION ---
        update_panel = QFrame()
        update_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {C.BG_SURFACE};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 12px;
            }}
        """)
        update_layout = QHBoxLayout(update_panel)
        update_layout.setContentsMargins(24, 24, 24, 24)

        self.lbl_update_status = QLabel("Ready to check for updates.")
        self.lbl_update_status.setStyleSheet(f"font-size: 14px; color: {C.TEXT_SECONDARY}; border: none;")

        self.btn_check_update = QPushButton("Check for Updates")
        self.btn_check_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_check_update.setMinimumWidth(160)
        self.btn_check_update.setFixedHeight(40)
        self.btn_check_update.setStyleSheet(f"""
            QPushButton {{
                background-color: {C.BG_SURFACE};
                color: {C.TEXT_PRIMARY};
                border: 1px solid {C.BORDER_DEFAULT};
                border-radius: 6px;
                font-size: 14px;
                font-weight: {T.WEIGHT_BOLD};
            }}
            QPushButton:hover {{
                background-color: {C.BG_SURFACE_HOVER};
                border: 1px solid {C.BORDER_FOCUS};
            }}
        """)

        update_layout.addWidget(self.lbl_update_status)
        update_layout.addStretch()
        update_layout.addWidget(self.btn_check_update)

        main_layout.addWidget(update_panel)
        main_layout.addStretch()
