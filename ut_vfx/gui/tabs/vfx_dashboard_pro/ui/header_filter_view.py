from PySide6.QtWidgets import QHeaderView, QMenu
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QPainter, QColor, QPolygon, QAction

class FilterHeaderView(QHeaderView):
    filter_changed = Signal(int, str)  # col_idx, filter_value

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.editors = []
        self.setSectionsClickable(True)
        self.setStretchLastSection(True)
        self.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.sectionResized.connect(self.adjust_positions)
        self.sectionMoved.connect(self.adjust_positions)
        self.geometriesChanged.connect(self.adjust_positions)
        
        # Store unique values for each column
        self.column_values = {}
        self.active_filters = {}

    def set_filter_values(self, col_idx, values):
        self.column_values[col_idx] = sorted(list(set(str(v) for v in values if v)))

    def show_filter_menu(self, logical_index):
        values = self.column_values.get(logical_index, [])
        if not values:
            return

        menu = QMenu(self)
        from ut_vfx.core.infra.design_tokens import ColorTokens as C
        menu.setStyleSheet(f"QMenu {{ background-color: {C.BG_HOVER}; color: {C.TEXT_SECONDARY}; border: 1px solid {C.BORDER_DEFAULT}; }}")
        
        # All action
        all_action = QAction("All", menu)
        all_action.triggered.connect(lambda: self.apply_filter(logical_index, "All"))
        menu.addAction(all_action)
        menu.addSeparator()
        
        for val in values:
            action = QAction(str(val), menu)
            action.triggered.connect(lambda checked, v=val: self.apply_filter(logical_index, v))
            menu.addAction(action)
            
        # Show menu at section position
        header_pos = self.sectionViewportPosition(logical_index)
        global_pos = self.mapToGlobal(self.rect().topLeft())
        global_pos.setX(global_pos.x() + header_pos)
        global_pos.setY(global_pos.y() + self.height())
        
        menu.exec(global_pos)

    def apply_filter(self, col_idx, value):
        if value == "All":
            if col_idx in self.active_filters:
                del self.active_filters[col_idx]
        else:
            self.active_filters[col_idx] = value
            
        self.filter_changed.emit(col_idx, value)
        self.update() # Repaint to show filter icon

    def paintSection(self, painter, rect, logicalIndex):
        painter.save()
        super().paintSection(painter, rect, logicalIndex)
        painter.restore()
        
        # Draw filter icon if filtered
        if logicalIndex in self.active_filters:
            # Draw a funnel shape
            icon_size = 12
            x = rect.right() - 20
            y = rect.center().y() - (icon_size // 2)
            
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor("#3EA8BF"))
            painter.setPen(Qt.PenStyle.NoPen)
            
            # Funnel polygon
            points = [
                QPoint(x, y),
                QPoint(x + icon_size, y),
                QPoint(x + icon_size // 2, y + icon_size)
            ]
            painter.drawPolygon(QPolygon(points))
            painter.restore()
        else:
            # Optional: Draw a subtle hint that filtering is available on hover?
            pass

    def mousePressEvent(self, event):
        logicalIndex = self.logicalIndexAt(event.pos())
        if event.button() == Qt.MouseButton.RightButton and logicalIndex >= 0:
            self.show_filter_menu(logicalIndex)
        else:
            super().mousePressEvent(event)
            
    def adjust_positions(self):
        pass

    def update_filters(self, shots):
        # Access the column getters from the model
        view = self.parent()
        if hasattr(view, 'model') and view.model():
            model = view.model()
            if hasattr(model, 'COLUMNS'):
                columns = model.COLUMNS
                
                for col_idx in range(len(columns)):
                    getter = columns[col_idx][2]
                    values = set()
                    for shot in shots:
                        val = getter(shot)
                        if val:
                            values.add(str(val))
                    
                    self.set_filter_values(col_idx, list(values))
