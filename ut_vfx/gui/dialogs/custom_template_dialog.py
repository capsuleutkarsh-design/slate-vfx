"""
SECURE Dialog for creating custom VFX templates with input validation.

Extracted from folder_creator_tab.py for better modularity.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit,
    QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
    QInputDialog, QMessageBox,
)

from ...utils.security import SecurityValidator, SecurityError


class CustomTemplateDialog(QDialog):
    """SECURE Dialog for creating custom templates with input validation and enhanced UX."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.security_validator = SecurityValidator()
        self.setWindowTitle("Create Custom Template")
        self.setModal(True)
        self.resize(600, 500)

        layout = QVBoxLayout(self)

        # Template name input
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Template Name:"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Enter template name...")
        name_layout.addWidget(self.name_input)
        layout.addLayout(name_layout)

        # Tree widget for folder structure
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["Folder Structure", "Type"])
        self.tree_widget.setAlternatingRowColors(True)
        self.tree_widget.setAnimated(True)
        layout.addWidget(self.tree_widget)

        # Buttons for adding folders
        btn_layout = QHBoxLayout()

        self.add_base_btn = QPushButton("Add Base Folder")
        self.add_base_btn.clicked.connect(self.add_base_folder)
        btn_layout.addWidget(self.add_base_btn)

        self.add_sub_btn = QPushButton("Add Sub Folder")
        self.add_sub_btn.clicked.connect(self.add_sub_folder)
        btn_layout.addWidget(self.add_sub_btn)

        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.clicked.connect(self.remove_selected)
        btn_layout.addWidget(self.remove_btn)

        layout.addLayout(btn_layout)

        # OK and Cancel buttons
        ok_cancel_layout = QHBoxLayout()
        self.ok_btn = QPushButton("OK")
        self.ok_btn.clicked.connect(self.accept)
        ok_cancel_layout.addWidget(self.ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_cancel_layout.addWidget(cancel_btn)

        layout.addLayout(ok_cancel_layout)

    def add_base_folder(self):
        """SECURE: Add a base folder with security validation."""
        folder_name, ok = QInputDialog.getText(self, "Add Base Folder", "Enter base folder name:")
        if ok and folder_name:
            is_valid, sanitized_name, error_msg = self.security_validator.sanitize_filename(folder_name)
            if not is_valid:
                QMessageBox.warning(self, "Security Warning",
                                  f"Invalid folder name:\n{error_msg}\n\nPlease use a different name.")
                return
            item = QTreeWidgetItem(self.tree_widget)
            item.setText(0, sanitized_name)
            item.setText(1, "base")

    def add_sub_folder(self):
        """SECURE: Add a sub folder with security validation."""
        selected = self.tree_widget.currentItem()
        if not selected:
            QMessageBox.warning(self, "No Selection", "Please select a parent folder first.")
            return

        folder_name, ok = QInputDialog.getText(self, "Add Sub Folder", "Enter sub folder name:")
        if ok and folder_name:
            is_valid, sanitized_name, error_msg = self.security_validator.sanitize_filename(folder_name)
            if not is_valid:
                QMessageBox.warning(self, "Security Warning",
                                  f"Invalid folder name:\n{error_msg}\n\nPlease use a different name.")
                return
            item = QTreeWidgetItem(selected)
            item.setText(0, sanitized_name)
            item.setText(1, "sub")

    def remove_selected(self):
        """Remove the selected item."""
        selected = self.tree_widget.currentItem()
        if selected:
            parent = selected.parent()
            if parent:
                parent.removeChild(selected)
            else:
                self.tree_widget.takeTopLevelItem(self.tree_widget.indexOfTopLevelItem(selected))

    def get_template_data(self):
        """Get the template data from the tree."""
        template_name = self.name_input.text().strip()

        # SECURITY: Validate template name
        if template_name:
            name_valid, sanitized_name, name_error = self.security_validator.sanitize_filename(template_name)
            if not name_valid:
                raise SecurityError(f"Invalid template name: {name_error}")
        else:
            sanitized_name = "Unnamed_Template"

        template_data = {
            "name": sanitized_name,
            "description": "Custom template created by user",
            "base_folders": [],
            "production_subfolders": [],
            "outsource_subfolders": [],
            "shot_folders": []
        }

        # Extract data from tree
        iterator = QTreeWidgetItemIterator(self.tree_widget)
        while iterator.value():
            item = iterator.value()
            item_text = item.text(0)
            item_type = item.text(1)

            # SECURITY: Validate item text
            if item_text:
                text_valid, sanitized_text, text_error = self.security_validator.sanitize_filename(item_text)
                if text_valid:
                    item_text = sanitized_text
                else:
                    iterator += 1
                    continue

            # Determine parent type if it exists
            parent = item.parent()
            parent_text = parent.text(0) if parent else None

            if item_type == "base":
                template_data["base_folders"].append(item_text)
            elif parent_text and ("production" in parent_text.lower() or "prod" in parent_text.lower()):
                template_data["production_subfolders"].append(item_text)
            elif parent_text and ("outsource" in parent_text.lower() or "out" in parent_text.lower()):
                template_data["outsource_subfolders"].append(item_text)
            elif parent_text and ("reel" in parent_text.lower() or "shot" in item_text.lower() or "scan" in item_text.lower() or "output" in item_text.lower()):
                template_data["shot_folders"].append(item_text)
            elif not parent:
                template_data["base_folders"].append(item_text)

            iterator += 1

        return template_data
