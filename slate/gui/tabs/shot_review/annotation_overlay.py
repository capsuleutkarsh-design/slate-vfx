from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QPointF, QEvent, Signal
from PySide6.QtGui import QPainter, QPen, QColor, QTabletEvent
import logging

logger = logging.getLogger(__name__)

class AnnotationOverlay(QWidget):
    """
    Transparent overlay for drawing annotations on top of an image.
    Supports Wacom pressure sensitivity via QTabletEvent.
    """
    
    # Signal emitted when a stroke is finished (for undo/redo stacks in future)
    stroke_finished = Signal()

    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Transparent background
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False) # Catch events
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Enable tablet tracking
        self.setAttribute(Qt.WidgetAttribute.WA_TabletTracking, True)
        self.setMouseTracking(True)
        
        self.viewer = parent # Start assuming parent is the viewer
        
        # Internal state
        self.strokes = []       # List of all strokes (each stroke is a dict)
        self.current_stroke = None
        self.is_drawing = False
        
        # Pen settings
        self.pen_color = QColor(255, 0, 0) # Red default
        self.base_width = 3.0
        
    def _to_image(self, pos: QPointF):
        if hasattr(self.viewer, 'screen_to_image'):
            return self.viewer.screen_to_image(pos)
        return pos
        
    def _to_screen(self, pos: QPointF):
        if hasattr(self.viewer, 'image_to_screen'):
            return self.viewer.image_to_screen(pos)
        return pos

    def set_color(self, color: QColor):
        self.pen_color = color
        
    def set_width(self, width: float):
        self.base_width = width

    def clear(self):
        """Clear all annotations"""
        self.strokes = []
        self.current_stroke = None
        self.update()

    def undo(self):
        """Remove last stroke"""
        if self.strokes:
            self.strokes.pop()
            self.update()

    def tabletEvent(self, event: QTabletEvent):
        """Handle Wacom Tablet Events"""
        
        pos = event.position()
        pressure = event.pressure()
        
        # Filter out 0 pressure (hover)
        if pressure == 0 and event.type() != QEvent.TabletRelease:
            return
            
        # Convert to Image Space
        image_pos = self._to_image(pos)

        if event.type() == QEvent.TabletPress:
            self.is_drawing = True
            self.current_stroke = {
                'points': [image_pos],
                'pressures': [pressure],
                'color': self.pen_color,
                'width': self.base_width
            }
            event.accept()
            
        elif event.type() == QEvent.TabletMove and self.is_drawing:
            if self.current_stroke:
                self.current_stroke['points'].append(image_pos)
                self.current_stroke['pressures'].append(pressure)
                self.update()
            event.accept()
            
        elif event.type() == QEvent.TabletRelease:
            if self.is_drawing and self.current_stroke:
                self.strokes.append(self.current_stroke)
                self.current_stroke = None
                self.is_drawing = False
                self.stroke_finished.emit()
                self.update()
            event.accept()

    def mousePressEvent(self, event):
        """Fallback for Mouse"""
        if self.is_drawing: return
        
        if event.button() == Qt.LeftButton:
            self.is_drawing = True
            image_pos = self._to_image(event.position())
            
            self.current_stroke = {
                'points': [image_pos],
                'pressures': [1.0], # Full pressure for mouse
                'color': self.pen_color,
                'width': self.base_width
            }
            self.update()

    def mouseMoveEvent(self, event):
        """Fallback for Mouse"""
        if self.is_drawing and self.current_stroke:
            image_pos = self._to_image(event.position())
            self.current_stroke['points'].append(image_pos)
            self.current_stroke['pressures'].append(1.0)
            self.update()

    def mouseReleaseEvent(self, event):
        """Fallback for Mouse"""
        if self.is_drawing and self.current_stroke:
            self.strokes.append(self.current_stroke)
            self.current_stroke = None
            self.is_drawing = False
            self.stroke_finished.emit()
            self.update()

    def paintEvent(self, event):
        """Draw strokes"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw existing strokes
        for stroke in self.strokes:
            self._draw_stroke(painter, stroke)
            
        # Draw current stroke
        if self.current_stroke:
            self._draw_stroke(painter, self.current_stroke)

    def _draw_stroke(self, painter, stroke):
        """Draw a single stroke with variable pressure"""
        points = stroke['points']
        pressures = stroke['pressures']
        color = stroke['color']
        base_width = stroke['width']
        
        if not points: return
        
        # Convert points to Screen Space for drawing
        screen_points = [self._to_screen(p) for p in points]
        
        # Optimization: If all pressures are 1.0 (Mouse), use fast Polyline
        if all(p == 1.0 for p in pressures):
            pen = QPen(color, base_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            
            from PySide6.QtGui import QPolygonF
            poly = QPolygonF(screen_points)
            painter.drawPolyline(poly)
            return

        # Variable width logic
        for i in range(len(screen_points) - 1):
            p1 = screen_points[i]
            p2 = screen_points[i+1]
            pres = pressures[i]
            
            width = base_width * (0.2 + pres * 0.8) 
            
            pen = QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(p1, p2)
