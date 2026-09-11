from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QSlider, QLabel, QStyle
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import QUrl, Qt, Signal

class VideoPlayerWidget(QWidget):
    """
    Native Video Player using QtMultimedia.
    Supports Play/Pause, Seeking, Audio.
    """
    
    # Signals
    duration_changed = Signal(int)
    position_changed = Signal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)
        
        # 1. Video Widget (Surface)
        self.video_widget = QVideoWidget()
        self.video_widget.setStyleSheet("background-color: #000;")
        self.main_layout.addWidget(self.video_widget)
        
        # 2. Media Player Backend
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)
        
        # 3. Controls
        self.controls = QWidget()
        self.controls.setFixedHeight(40)
        self.controls.setStyleSheet("background-color: #1D1D22; border-top: 1px solid #26262D;")
        self.c_layout = QHBoxLayout(self.controls)
        self.c_layout.setContentsMargins(5, 0, 5, 0)
        
        # Play Button
        self.play_btn = QPushButton()
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.clicked.connect(self.toggle_play)
        self.c_layout.addWidget(self.play_btn)
        
        # Slider
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderMoved.connect(self.set_position)
        self.slider.sliderPressed.connect(self.player.pause)
        self.slider.sliderReleased.connect(self.player.play)
        self.c_layout.addWidget(self.slider)
        
        # Time Label
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setStyleSheet("color: #B4B1AA; font-family: Consolas;")
        self.c_layout.addWidget(self.time_label)
        
        # Volume
        self.vol_btn = QPushButton("🔊")
        self.vol_btn.setFixedWidth(30)
        self.c_layout.addWidget(self.vol_btn)
        
        # RV Button
        self.rv_btn = QPushButton("RV")
        self.rv_btn.setToolTip("Play in OpenRV")
        self.rv_btn.setFixedWidth(30)
        self.rv_btn.setStyleSheet("background-color: #26262D; color: #E8E6E1; font-weight: bold;")
        self.rv_btn.clicked.connect(self.launch_in_rv)
        self.c_layout.addWidget(self.rv_btn)
        
        self.main_layout.addWidget(self.controls)
        
        self.current_media_path = ""
        
        # Connections
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        
    def load_media(self, file_path):
        """Load video file"""
        self.current_media_path = file_path
        self.player.setSource(QUrl.fromLocalFile(file_path))
        self.play_btn.setEnabled(True)
        # self.player.play() # Auto play?
        
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        else:
            self.player.play()
            self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
            
    def set_position(self, position):
        self.player.setPosition(position)
        
    def on_position_changed(self, position):
        self.slider.setValue(position)
        self.update_time_label(position)
        self.position_changed.emit(position)
        
    def on_duration_changed(self, duration):
        self.slider.setRange(0, duration)
        self.update_time_label(0) # Update total only
        self.duration_changed.emit(duration)
        
    def update_time_label(self, position):
        duration = self.player.duration()
        
        def fmt(ms):
             s = (ms // 1000) % 60
             m = (ms // 60000) % 60
             return f"{m:02}:{s:02}"
             
        self.time_label.setText(f"{fmt(position)} / {fmt(duration)}")

    def on_media_status(self, status):
        if status == QMediaPlayer.EndOfMedia:
            self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
            # Loop?
            # self.player.play()

    def launch_in_rv(self):
        if self.current_media_path:
            from slate.core.rv_integration import RVIntegration
            self.player.pause()
            self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
            rv = RVIntegration()
            rv.launch_media(self.current_media_path)
