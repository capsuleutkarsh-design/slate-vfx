"""
Filters: show only the shots that match some rules (field, condition, value).

The feature had three names (button "Advanced...", window "Advanced Query
Builder", heading "Advanced Filters"); it is "Filters" everywhere now. "Clear
All" used to apply and close at once and there was no Cancel; a rule with an
empty value was dropped without a word; four of the nine fields named things a
shot does not have, so they could never match.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QToolButton,
    QVBoxLayout, QWidget,
)

from slate.core.infra.gate import Gate
from slate.gui.core.controls import make_button, set_default_button
from slate.gui.core.icons import icon as draw_icon
from .date_fields import scaled_font

OPERATORS = ["contains", "is", "is not", "does not contain", "is empty", "is not empty"]
NEEDS_NO_VALUE = ("is empty", "is not empty", "Is Empty", "Is Not Empty")


def _fields():
    from slate.gui.tabs.vfx_dashboard_pro.controllers.filter_mixin import ADVANCED_FIELDS
    return list(ADVANCED_FIELDS)


class QueryRuleWidget(QWidget):
    remove_requested = Signal(QWidget)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.field_combo = QComboBox()
        self.field_combo.addItems(_fields())
        self.field_combo.setMinimumWidth(150)

        self.op_combo = QComboBox()
        self.op_combo.addItems(OPERATORS)
        self.op_combo.setMinimumWidth(130)
        self.op_combo.currentTextChanged.connect(self._sync_value)

        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("Value…")

        self.remove_btn = QToolButton()
        self.remove_btn.setIcon(draw_icon("close", Gate.TEXT_2, 16))
        self.remove_btn.setAutoRaise(True)
        self.remove_btn.setToolTip("Remove rule")
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))

        layout.addWidget(self.field_combo)
        layout.addWidget(self.op_combo)
        layout.addWidget(self.value_input, 1)
        layout.addWidget(self.remove_btn)

    def _sync_value(self, op):
        needs = op not in NEEDS_NO_VALUE
        self.value_input.setEnabled(needs)
        self.value_input.setPlaceholderText("Value…" if needs else "(no value needed)")

    def set_rule(self, rule):
        from slate.gui.tabs.vfx_dashboard_pro.controllers.filter_mixin import _OLD_FIELD_NAMES
        field = _OLD_FIELD_NAMES.get(rule.get("field"), rule.get("field"))
        index = self.field_combo.findText(field or "")
        if index >= 0:
            self.field_combo.setCurrentIndex(index)
        from ..controllers.filter_mixin import OPERATOR_NAMES
        op = rule.get("operator") or OPERATORS[0]
        self.op_combo.setCurrentText(OPERATOR_NAMES.get(op, op))
        self.value_input.setText(str(rule.get("value", "")))

    def get_rule(self):
        return {
            "field": self.field_combo.currentText(),
            "operator": self.op_combo.currentText(),
            "value": self.value_input.text(),
        }

    def is_complete(self) -> bool:
        rule = self.get_rule()
        return rule["operator"] in NEEDS_NO_VALUE or bool(rule["value"].strip())


class QueryBuilderDialog(QDialog):
    query_applied = Signal(list, str)  # (rules, "AND" | "OR")

    def __init__(self, parent=None, rules=None, match_type="AND"):
        super().__init__(parent)
        self.setWindowTitle("Filters")
        self.resize(640, 400)
        self.rules = []
        self.setup_ui()
        if rules:
            for rule in rules:
                self.add_rule().set_rule(rule)
            self.match_combo.setCurrentIndex(0 if match_type == "AND" else 1)
        else:
            self.add_rule()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QHBoxLayout()
        title = QLabel("Filters")
        font = title.font()
        font.setBold(True)
        font = scaled_font(font, 1.25)
        title.setFont(font)
        self.match_combo = QComboBox()
        self.match_combo.addItems(["Match all rules", "Match any rule"])
        header_layout.addWidget(title)
        header_layout.addStretch()
        header_layout.addWidget(QLabel("Show shots that"))
        header_layout.addWidget(self.match_combo)
        main_layout.addLayout(header_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.rules_container = QWidget()
        self.rules_layout = QVBoxLayout(self.rules_container)
        self.rules_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.rules_layout.setSpacing(8)
        self.scroll_area.setWidget(self.rules_container)
        main_layout.addWidget(self.scroll_area, 1)

        add_row = QHBoxLayout()
        self.add_rule_btn = make_button("Add rule", "secondary", icon="plus", on_click=self.add_rule)
        add_row.addWidget(self.add_rule_btn)
        self.clear_btn = make_button("Clear all rules", "ghost", on_click=self.clear_rules,
                                     tooltip="Remove every rule here; nothing changes until you apply")
        add_row.addWidget(self.clear_btn)
        add_row.addStretch(1)
        main_layout.addLayout(add_row)

        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet(f"color: {Gate.WARN};")
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()
        main_layout.addWidget(self.warning_label)

        footer_layout = QHBoxLayout()
        footer_layout.addStretch(1)
        self.cancel_btn = make_button("Cancel", "secondary", on_click=self.reject)
        self.apply_btn = make_button("Apply filters", "primary", on_click=self.apply_query)
        footer_layout.addWidget(self.cancel_btn)
        footer_layout.addWidget(self.apply_btn)
        main_layout.addLayout(footer_layout)
        set_default_button(self, self.apply_btn)

    def add_rule(self):
        rule_widget = QueryRuleWidget(self)
        rule_widget.remove_requested.connect(self.remove_rule)
        self.rules_layout.addWidget(rule_widget)
        self.rules.append(rule_widget)
        return rule_widget

    def remove_rule(self, widget):
        if widget in self.rules:
            self.rules.remove(widget)
            self.rules_layout.removeWidget(widget)
            widget.deleteLater()

    def clear_rules(self):
        """Empty the dialog. Nothing is applied until Apply."""
        for widget in list(self.rules):
            self.remove_rule(widget)
        self.warning_label.hide()

    def apply_query(self):
        incomplete = [w for w in self.rules if not w.is_complete()]
        if incomplete:
            self.warning_label.setText(
                f"{len(incomplete)} rule{'s have' if len(incomplete) != 1 else ' has'} no value. "
                "Give it one, choose "is empty" or "is not empty", or remove it.")
            self.warning_label.show()
            incomplete[0].value_input.setFocus()
            return
        query_data = [w.get_rule() for w in self.rules]
        match_type = "AND" if self.match_combo.currentIndex() == 0 else "OR"
        self.query_applied.emit(query_data, match_type)
        self.accept()
