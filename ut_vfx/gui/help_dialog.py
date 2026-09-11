# -*- coding: utf-8 -*-
"""
Help Dialog - Interactive documentation system for UT_VFX.

Provides tabbed, searchable help content with rich formatting and emoji support.
"""

import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTextBrowser, QLineEdit, QPushButton, QLabel,
    QWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ..core.help_content import HELP_CONTENT, get_all_tabs, search_help
from ..core.infra.gate import Gate
from .core.icons import icon as drawn_icon


class HelpDialog(QDialog):
    """
    Main help dialog featuring tabbed documentation with search.
    """
    
    def __init__(self, parent=None, initial_tab="getting_started", mode=None):
        # Which application this is: "vfx", "ops", or None for both.
        # The two shells do not share a sidebar, so they do not share help.
        self.mode = mode
        super().__init__(parent)
        self.setWindowTitle("Slate Help")
        self.setMinimumSize(1000, 800)
        self.resize(1200, 850)
        
        self.setup_ui()
        self.load_content()
        self.set_active_tab(initial_tab)
        
        # Apply polished dark theme
        self.setStyleSheet("""
            QDialog {
                background-color: #16161A;
                color: #E8E6E1;
            }
            QTabWidget::pane {
                border: 1px solid #1D1D22;
                background: #1D1D22;
                border-radius: 8px;
            }
            QTabBar::tab {
                background: #1D1D22;
                color: #B4B1AA;
                padding: 12px 24px;
                border: 1px solid #1D1D22;
                border-bottom: none;
                margin-right: 3px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-size: 13px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background: #1D1D22;
                color: #E8E6E1;
                border-bottom: 3px solid #3EA8BF;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background: #26262D;
                color: #E8E6E1;
            }
            QTextBrowser {
                background: #1D1D22;
                color: #E8E6E1;
                border: 1px solid #1D1D22;
                border-radius: 6px;
                padding: 20px;
                font-size: 14px;
                line-height: 1.6;
            }
            QLineEdit {
                background: #1D1D22;
                color: #E8E6E1;
                border: 2px solid #26262D;
                border-radius: 6px;
                padding: 10px 14px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 2px solid #3EA8BF;
                background: #1D1D22;
            }
            QPushButton {
                background: #26262D;
                color: #E8E6E1;
                border: 1px solid #2C2C34;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover {
                background: #26262D;
                border: 1px solid #3EA8BF;
            }
            QPushButton:pressed {
                background: #1D1D22;
            }
        """)
    
    def setup_ui(self):
        """Create the main UI layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header with search
        header = self.create_header()
        layout.addWidget(header)
        
        # Tab widget for different help sections
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.North)
        layout.addWidget(self.tab_widget)
        
        # Footer with buttons
        footer = self.create_footer()
        layout.addWidget(footer)
    
    def create_header(self):
        """Create header with title and search."""
        header = QWidget()
        header.setStyleSheet("""
            QWidget {
                background: #1D1D22;
                border-bottom: 1px solid #3EA8BF;
            }
        """)
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(24, 16, 24, 16)
        h_layout.setSpacing(20)
        
        # Title - Clean text only
        title = QLabel("Slate Help")
        title.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title.setStyleSheet("color: #3EA8BF; border: none;")
        h_layout.addWidget(title)
        
        h_layout.addStretch()
        
        # Search container with integrated clear button
        search_container = QWidget()
        search_container.setStyleSheet("background: transparent; border: none;")
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(0)
        
        # Search box with icon
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search documentation...")
        self.search_box.setFixedWidth(320)
        self.search_box.setFixedHeight(36)
        self.search_box.setStyleSheet("""
            QLineEdit {
                background: #1D1D22;
                color: #E8E6E1;
                border: 1px solid #26262D;
                border-radius: 4px;
                padding: 8px 40px 8px 12px;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #3EA8BF;
                background: #26262D;
            }
        """)
        self.search_box.textChanged.connect(self.on_search)
        search_layout.addWidget(self.search_box)
        
        # Clear button (icon style, overlaid on search box)
        clear_btn = QPushButton("×")
        clear_btn.setFixedSize(28, 28)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #87857F;
                border: none;
                border-radius: 14px;
                font-size: 20px;
                font-weight: bold;
                margin-right: 4px;
            }
            QPushButton:hover {
                background: #26262D;
                color: #3EA8BF;
            }
            QPushButton:pressed {
                background: #2C2C34;
            }
        """)
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(lambda: self.search_box.clear())
        
        # Position clear button over search box (right side)
        search_layout.addWidget(clear_btn)
        search_layout.setAlignment(clear_btn, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        clear_btn.move(-32, 0)  # Overlay on search box
        
        h_layout.addWidget(search_container)
        
        return header
    
    def create_footer(self):
        """Create footer with action buttons."""
        footer = QWidget()
        footer.setStyleSheet("""
            QWidget {
                background: #1D1D22;
                border-top: 1px solid #26262D;
            }
        """)
        f_layout = QHBoxLayout(footer)
        f_layout.setContentsMargins(24, 14, 24, 14)
        
        # Info label
        info = QLabel("Press <b>F1</b> anytime to open help")
        info.setStyleSheet("color: #87857F; font-size: 12px; border: none;")
        f_layout.addWidget(info)
        
        f_layout.addStretch()
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(100)
        close_btn.setFixedHeight(34)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #3EA8BF;
                border: none;
                border-radius: 4px;
                padding: 8px 20px;
                font-weight: 600;
                font-size: 13px;
                color: #E8E6E1;
            }
            QPushButton:hover {
                background: #3EA8BF;
            }
            QPushButton:pressed {
                background: #3EA8BF;
            }
        """)
        close_btn.clicked.connect(self.accept)
        f_layout.addWidget(close_btn)
        
        return footer
    
    def load_content(self):
        """Load all help content into tabs."""
        try:
            tabs_data = get_all_tabs(getattr(self, "mode", None))
            
            if not tabs_data:
                logging.error("No help tabs found - HELP_CONTENT may be empty")
                self._show_error_tab("No Help Content", 
                    "Help system is not available. The help content database is empty.")
                return
                
            for tab_data in tabs_data:
                tab_id = tab_data["id"]
                tab_title = tab_data["title"]
                
                # Create text browser for this tab
                browser = QTextBrowser()
                browser.setOpenExternalLinks(True)
                browser.setObjectName(tab_id)
                
                # Load HTML content
                try:
                    content_data = HELP_CONTENT[tab_id]
                    html_content = self.format_html(content_data.get("content", ""))
                    browser.setHtml(html_content)
                except Exception as e:
                    logging.exception(f"Error loading content for tab {tab_id}")
                    browser.setHtml(self.format_html(f"<h2>Error Loading Content</h2><p>{str(e)}</p>"))
                
                # Labelled with the drawn icon set, like the rest of the
                # product. These used to be emoji baked into the title string,
                # which Windows rendered in whatever font it fancied.
                # Qt treats a single "&" in tab text as a keyboard mnemonic,
                # so "Build & Ingest" would render as "Build _Ingest".
                tab_title = tab_title.replace("&", "&&")
                glyph = tab_data.get("icon") or ""
                if glyph:
                    self.tab_widget.addTab(browser, drawn_icon(glyph, Gate.TEXT_2, 16), tab_title)
                else:
                    self.tab_widget.addTab(browser, tab_title)
                
        except ImportError as e:
            logging.exception("Failed to import help_content module")
            self._show_error_tab("Import Error", 
                f"Failed to load help system: {str(e)}<br><br>Please contact IT support.")
        except Exception as e:
            logging.exception("Error loading help content")
            self._show_error_tab("System Error", 
                f"An error occurred while loading help: {str(e)}")
    
    def _show_error_tab(self, title, message):
        """Display error message in help dialog"""
        error_html = self.format_html(f"""
            <h1 style="color: #D9635F;">&#9888; {title}</h1>
            <p style="font-size: 14px;">{message}</p>
            <hr style="border: 1px solid #2C2C34; margin: 20px 0;">
            <p style="color: #87857F;">If this problem persists, please contact your supervisor or IT team for support.</p>
        """)
        browser = QTextBrowser()
        browser.setHtml(error_html)
        self.tab_widget.addTab(browser, f"Warning: {title}")
    
    def format_html(self, content):
        """Wrap content in HTML template with styling."""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    font-family: 'Segoe UI', Arial, sans-serif;
                    font-size: 13px;
                    line-height: 1.6;
                    color: #E8E6E1;
                    margin: 0;
                    padding: 0;
                }}
                h1 {{
                    color: #3EA8BF;
                    border-bottom: 2px solid #3EA8BF;
                    padding-bottom: 10px;
                    margin-top: 0;
                }}
                h2 {{
                    color: #5FBF8F;
                    border-bottom: 1px solid #2C2C34;
                    padding-bottom: 5px;
                    margin-top: 25px;
                }}
                h3 {{
                    color: #D9A441;
                    margin-top: 20px;
                }}
                code {{
                    background: #16161A;
                    padding: 2px 6px;
                    border-radius: 3px;
                    font-family: 'Consolas', monospace;
                    color: #5FBF8F;
                }}
                pre {{
                    background: #16161A;
                    padding: 15px;
                    border-radius: 5px;
                    border-left: 3px solid #3EA8BF;
                    overflow-x: auto;
                    font-family: 'Consolas', monospace;
                    font-size: 12px;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 15px 0;
                }}
                th {{
                    background: #26262D;
                    color: #3EA8BF;
                    font-weight: bold;
                    text-align: left;
                    padding: 10px;
                    border: 1px solid #2C2C34;
                }}
                td {{
                    padding: 8px;
                    border: 1px solid #2C2C34;
                }}
                tr:nth-child(even) {{
                    background: #1D1D22;
                }}
                ul, ol {{
                    margin: 10px 0;
                    padding-left: 25px;
                }}
                li {{
                    margin: 5px 0;
                }}
                a {{
                    color: #3EA8BF;
                    text-decoration: none;
                }}
                a:hover {{
                    text-decoration: underline;
                }}
                p {{
                    margin: 10px 0;
                }}
            </style>
        </head>
        <body>
            {content}
        </body>
        </html>
        """
    
    def set_active_tab(self, tab_id):
        """Set active tab by ID."""
        for i in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(i)
            if widget.objectName() == tab_id:
                self.tab_widget.setCurrentIndex(i)
                break
    
    def on_search(self, query):
        """Handle search query."""
        if not query.strip():
            # Reset to normal content
            self.load_content()
            return
        
        # Perform search
        results = search_help(query)
        
        if not results:
            # No results - show message
            no_results_html = self.format_html(f"""
                <h2>🔍 No Results Found</h2>
                <p>No help content matches your search for <b>"{query}"</b></p>
                <p>Try different keywords or check spelling.</p>
            """)
            current_browser = self.tab_widget.currentWidget()
            if isinstance(current_browser, QTextBrowser):
                current_browser.setHtml(no_results_html)
        else:
            # Show results in current tab
            results_html = "<h2>🔍 Search Results</h2>"
            results_html += f"<p>Found <b>{len(results)}</b> result(s) for <b>\"{query}\"</b></p>"
            
            for tab_id, title, snippet in results:
                results_html += f"""
                    <div style="background: #1D1D22; padding: 10px; margin: 10px 0; border-left: 3px solid #3EA8BF; border-radius: 3px;">
                        <h3>{title}</h3>
                        <p>{snippet}</p>
                    </div>
                """
            
            results_html += """
                <p style="margin-top: 20px; color: #87857F; font-size: 11px;">
                💡 Tip: Clear search to return to full documentation
                </p>
            """
            
            current_browser = self.tab_widget.currentWidget()
            if isinstance(current_browser, QTextBrowser):
                current_browser.setHtml(self.format_html(results_html))
    
    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)


def show_help(parent=None, tab_id="getting_started", mode=None):
    """
    Show help dialog.
    
    Args:
        parent: Parent widget
        tab_id: ID of tab to show (default: getting_started)
        mode: "vfx", "ops" or None for everything. Decides which sections
              are offered, so each application's help matches its sidebar.
    """
    dialog = HelpDialog(parent, initial_tab=tab_id, mode=mode)
    dialog.exec()
