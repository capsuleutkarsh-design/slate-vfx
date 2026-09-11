import sys
import os
import ctypes
import subprocess
import logging
import shutil
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QMessageBox
from PySide6.QtCore import Qt, QTimer

# Windows API Constants
class USER32:
    GWL_STYLE = -16
    WS_CAPTION = 0x00C00000
    WS_THICKFRAME = 0x00040000
    WS_CHILD = 0x40000000
    WS_VISIBLE = 0x10000000
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_FRAMECHANGED = 0x0020

    @staticmethod
    def SetParent(child, parent):
        return ctypes.windll.user32.SetParent(child, parent)

    @staticmethod
    def SetWindowLongA(hwnd, index, new_long):
        return ctypes.windll.user32.SetWindowLongA(hwnd, index, new_long)
    
    @staticmethod
    def GetWindowLongA(hwnd, index):
        return ctypes.windll.user32.GetWindowLongA(hwnd, index)

    @staticmethod
    def MoveWindow(hwnd, x, y, width, height, repaint):
        return ctypes.windll.user32.MoveWindow(hwnd, x, y, width, height, repaint)
    
    @staticmethod
    def EnumWindows(callback, param):
        return ctypes.windll.user32.EnumWindows(callback, param)
    
    @staticmethod
    def GetWindowThreadProcessId(hwnd, process_id_ptr):
        return ctypes.windll.user32.GetWindowThreadProcessId(hwnd, process_id_ptr)

    @staticmethod
    def GetClassNameA(hwnd, lpClassName, nMaxCount):
        return ctypes.windll.user32.GetClassNameA(hwnd, lpClassName, nMaxCount)
    
    @staticmethod
    def GetWindowTextW(hwnd, lpString, nMaxCount):
        return ctypes.windll.user32.GetWindowTextW(hwnd, lpString, nMaxCount)

    @staticmethod
    def GetWindowTextLengthW(hwnd):
        return ctypes.windll.user32.GetWindowTextLengthW(hwnd)

    @staticmethod
    def IsWindowVisible(hwnd):
        return ctypes.windll.user32.IsWindowVisible(hwnd)

logger = logging.getLogger(__name__)

# --- NEW: Process Access Rights ---
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

class OliveWrapperWidget(QWidget):
    """
    Widgets that launches and embeds 'Olive Video Editor' inside itself.
    """
    def __init__(self, olive_path=None):
        super().__init__()
        self.olive_path = olive_path
        self.process = None
        self.olive_hwnd = None
        self.hunt_counter = 0  # Track attempts
        self.max_hunt_attempts = 120 # 60 seconds at 500ms
        
        # 1. Check for bundled version or system path
        found_path = self._find_olive_executable()
        if found_path and os.path.exists(found_path):
            self.olive_path = found_path
            self._olive_available = True
            logger.info(f"Using Olive Editor at: {self.olive_path}")
        # 2. No bundled version found - mark as unavailable
        elif not self.olive_path:
            logger.error("Olive Editor not found - bundled executable missing from external/olive-editor/")
            self.olive_path = None
            self._olive_available = False

        self.setup_ui()
    
    def _find_olive_executable(self):
        """Find olive-editor.exe using multiple strategies"""
        
        candidates = []
        
        # Strategy 1: Environment Variable Override
        env_path = os.environ.get("OLIVE_EDITOR_PATH") or os.environ.get("OLIVE_EDITOR")
        if env_path and os.path.exists(env_path):
            return os.path.abspath(env_path)

        # Strategy 2: Relative to App/Exe (Bundled or Reposisory)
        # Standalone (.exe)
        if getattr(sys, 'frozen', False):
             base_dir = os.path.dirname(sys.executable)
             # ALSO CHECK _MEIPASS (PyInstaller Temp Dir) for bundled files
             if hasattr(sys, '_MEIPASS'):
                 candidates.append(os.path.join(sys._MEIPASS, "olive-editor", "olive-editor.exe"))
                 candidates.append(os.path.join(sys._MEIPASS, "olive-editor.exe"))
             # Pyinstaller --onedir fallback just in case
             candidates.append(os.path.join(base_dir, "_internal", "olive-editor", "olive-editor.exe"))
        else:
             # Dev mode (running .py)
             base_dir = os.path.dirname(os.path.abspath(__file__))
             # Go up: components/ -> root/
        
        candidates.extend([
            # Common install locations
            os.path.join(base_dir, "olive-editor", "olive-editor.exe"),
            os.path.join(base_dir, "bin", "olive-editor.exe"), 
            os.path.join(base_dir, "bin", "olive-editor", "olive-editor.exe"),
            
            # Dev / Repository structure
            os.path.join(base_dir, "..", "olive-editor", "olive-editor.exe"), 
            os.path.join(base_dir, "lib", "olive-editor", "olive-editor.exe"),
            os.path.join(base_dir, "../../external/olive-editor", "olive-editor.exe") 
        ])
        
        for path in candidates:
            if os.path.exists(path):
                return os.path.abspath(path)
                
        # Strategy 3: System PATH (shutil.which)
        # This works if added to PATH via rez or installer
        system_path = shutil.which("olive-editor") or shutil.which("olive-editor.exe")
        if system_path:
             return os.path.abspath(system_path)
             
        return None
        
    def setup_ui(self):
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        # Placeholder or Control Bar
        self.controls = QWidget()
        self.controls.setFixedHeight(40)
        self.controls.setStyleSheet("background-color: #1D1D22; border-bottom: 1px solid #2C2C34;")
        c_layout = QVBoxLayout(self.controls) # Change to HBox in real impl
        
        self.info_label = QLabel("Olive Video Editor - Integrated Mode")
        self.info_label.setStyleSheet("color: white; font-weight: bold; margin-left: 10px;")
        c_layout.addWidget(self.info_label)
        
        self.layout.addWidget(self.controls)
        
        # Area where Olive will sit
        self.container = QWidget()
        self.container.setStyleSheet("background-color: #0D0D0F;")
        self.layout.addWidget(self.container)
        
        # Launch Button
        self.btn_launch = QPushButton("Launch Editor")
        self.btn_launch.clicked.connect(self.launch_olive)
        
        # Check if Olive is available
        if not hasattr(self, '_olive_available'):
            self._olive_available = True  # Backward compatibility
        
        if not self._olive_available:
            # Olive NOT available - show error state
            self.btn_launch.setText("Olive Editor Not Found")
            self.btn_launch.setEnabled(False)
            self.btn_launch.setStyleSheet("""
                QPushButton { 
                    background-color: #D9635F; 
                    color: white; 
                    padding: 10px; 
                    border-radius: 4px; 
                }
            """)
            self.btn_launch.setToolTip(
                "Olive Editor executable not found.\n"
                "Checked: Bundled, System PATH, and OLIVE_EDITOR env var.\n\n"
                "Tip: Set 'OLIVE_EDITOR' environment variable to the executable path.")
        else:
            # Olive available - normal style
            self.btn_launch.setStyleSheet("""
                QPushButton { 
                    background-color: #3EA8BF; color: white; padding: 10px; border-radius: 4px; 
                }
                QPushButton:hover { background-color: #3EA8BF; }
            """)
        
        # Show button
        self.container.setLayout(QVBoxLayout())
        self.container.layout().addWidget(self.btn_launch)
        self.container.layout().setAlignment(Qt.AlignCenter)

    def launch_olive(self, media_files=None):
        """
        Launch Olive process and start the embedding hunting loop.
        """
        if not os.path.exists(self.olive_path):
            QMessageBox.critical(self, "Error", f"Olive Editor execution not found at:\n{self.olive_path}")
            return

        # 1. FORCE CLEANUP (The Fix for "Does not open")
        # Ensure older instances are dead so we don't attach to a ghost
        logger.info("Cleaning up old olive-editor processes...")
        try:
            subprocess.run(["taskkill", "/F", "/IM", "olive-editor.exe"], 
                         capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            logger.warning(f"Taskkill failed (might be benign): {e}")

        # Clean internally tracked process object
        if self.process:
            try:
                self.process.terminate()
            except (OSError, subprocess.SubprocessError) as e:
                logger.debug(f"Failed to terminate previous Olive process cleanly: {e}")
            
        args = [self.olive_path]
        if media_files:
            args.extend(media_files)
            
        # 2. SANITIZE ENVIRONMENT (Crucial for deployed Qt apps)
        # Prevent "DLL Hell" by stripping parent's QT env vars so child uses its own
        env = os.environ.copy()
        keys_to_remove = [
            "QT_PLUGIN_PATH", 
            "QT_QPA_PLATFORM_PLUGIN_PATH", 
            "QT_FONTS_PATH", 
            "PYTHONPATH", 
            "PYTHONHOME"
        ]
        
        for key in keys_to_remove:
            if key in env:
                del env[key]
                logger.debug(f"Removed env var for child process: {key}")
        
        logger.info(f"Launching Olive: {args}")
        try:
            # Pass sanitized environment
            self.process = subprocess.Popen(args, env=env)
            
            # Switch UI
            self.btn_launch.hide()
            self.info_label.setText("Starting Olive Editor... Please wait.")
            
            # Reset and Start persistent hunt
            self.hunt_counter = 0
            self.hunt_timer = QTimer(self)
            self.hunt_timer.timeout.connect(self._find_and_embed)
            self.hunt_timer.start(500) # Check every 500ms
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch Olive: {e}")

    def _find_and_embed(self):
        """
        Callback to find the Olive window handle.
        Robust strategy: PID -> Title -> ExeName
        """
        if not self.process:
            return
            
        # 1. Timeout Check
        self.hunt_counter += 1
        if self.hunt_counter > self.max_hunt_attempts:
            self.hunt_timer.stop()
            self.info_label.setText("Error: Olive Timeout. Opened externally?")
            QMessageBox.warning(self, "Embedding Timeout", 
                "Timed out waiting for Olive Editor window to appear.\n"
                "It might have opened outside the application or failed to launch.\n\n"
                "Click 'Launch' to try again."
            )
            self.btn_launch.show()
            return

        target_pid = self.process.pid
        found_hwnds = []
        
        def enum_cb(hwnd, _):
            # Check visibility first
            if not USER32.IsWindowVisible(hwnd):
                return True
                
            # A. Check PID match (Direct)
            pid = ctypes.c_ulong()
            USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            
            is_match = (pid.value == target_pid)
            
            # B. Check Title (Fallback for launcher PIDs)
            if not is_match:
                length = USER32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    USER32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value
                    
                    # Looser check: 'olive' anywhere (case-insensitive)
                    if "olive" in title.lower(): 
                         # Verify it's not US (the parent app title usually contains slate/vfx)
                        if "ut" not in title.lower():
                            is_match = True
                            logger.info(f"Found Olive by Title: '{title}' (PID: {pid.value})")

            # C. Check Executable Name (Ultimate Fallback)
            if not is_match:
                 try:
                     # Open process to query name
                     h_process = ctypes.windll.kernel32.OpenProcess(
                         0x0400 | 0x0010, # QUERY_INFO | VM_READ
                         False, pid.value
                     )
                     if h_process:
                         name_buff = ctypes.create_unicode_buffer(1024)
                         # GetModuleBaseNameW is in psapi.dll
                         ctypes.windll.psapi.GetModuleBaseNameW(h_process, 0, name_buff, 1024)
                         exe_name = name_buff.value
                         ctypes.windll.kernel32.CloseHandle(h_process)
                         
                         if exe_name.lower() == "olive-editor.exe":
                             is_match = True
                             logger.info(f"Found Olive by Exe Name: '{exe_name}' (PID: {pid.value})")
                 except Exception as exc:
                     logger.debug("Skipping window handle during Olive detection: %s", exc)

            if is_match:
                found_hwnds.append(hwnd)
                
            return True

        USER32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)(enum_cb), 0)
        
        if found_hwnds:
            # If multiple, take first
            hwnd = found_hwnds[0] 
            
            logger.info(f"Found Olive Window HWND: {hwnd}")
            self.embed_window(hwnd)
            self.hunt_timer.stop() # Success!

    def embed_window(self, hwnd):
        self.olive_hwnd = hwnd
        self.info_label.setText("Olive Editor Active")
        
        # 1. Remove Borders (Make it look like a child widget)
        style = USER32.GetWindowLongA(hwnd, USER32.GWL_STYLE)
        style = style & ~USER32.WS_CAPTION & ~USER32.WS_THICKFRAME # Remove title bar and resizing border
        style = style | USER32.WS_CHILD # Must be a child to be embedded
        USER32.SetWindowLongA(hwnd, USER32.GWL_STYLE, style)
        
        # 2. Re-parent to our container
        # We need the HWND of our container widget
        # PySide6: winId() returns a pointer, we need int
        local_hwnd = int(self.container.winId())
        USER32.SetParent(hwnd, local_hwnd)
        
        # 3. Resize to fill
        # Force a slight delay to allow Windows to process the style change before resizing
        QTimer.singleShot(100, self.resize_embedded)
        
        # 4. Start polling process to detect manual exit and prevent white border glitch
        if not hasattr(self, "monitor_timer"):
            self.monitor_timer = QTimer(self)
            self.monitor_timer.timeout.connect(self._monitor_process)
        self.monitor_timer.start(1000)

    def _monitor_process(self):
        if self.process and self.process.poll() is not None:
            # Process exited (e.g. user closed Olive)
            self.monitor_timer.stop()
            self.olive_hwnd = None
            self.process = None
            self.info_label.setText("Olive Editor - Integrated Mode")
            self.btn_launch.show()
            
            # Force top-level window to update its frame to fix the "white border" glitch on Windows
            top_level = self.window()
            if top_level:
                try:
                    hwnd = int(top_level.winId())
                    # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                    flags = 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010
                    ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, flags)
                except Exception as e:
                    logger.debug(f"Failed to reset window frame: {e}")
        
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_embedded()
        
    def resize_embedded(self):
        if self.olive_hwnd:
            dpr = self.container.devicePixelRatio()
            width = int(self.container.width() * dpr)
            height = int(self.container.height() * dpr)
            # The 'True' flag forces a repaint
            USER32.MoveWindow(self.olive_hwnd, 0, 0, width, height, True)

    def closeEvent(self, event):
        # Kill python process on close
        if self.process:
            self.process.terminate()
        super().closeEvent(event)
