import logging

try:
    import ctypes
except ImportError:
    ctypes = None

# What each lock is called on screen. The message used to show the internal
# mutex name: "The application 'Slate_Process_vfx' is already running."
PRODUCT_NAMES = {
    "Slate_Process_vfx": "Slate VFX",
    "Slate_Process_ops": "Slate Operations",
    "Slate_Process": "Slate",
}


def lock_name_for(mode: str) -> str:
    """The single-instance lock for an application mode (vfx / ops / all)."""
    mode = str(mode or "all").lower()
    return f"Slate_Process_{mode}" if mode != "all" else "Slate_Process"


def product_name(app_name: str) -> str:
    return PRODUCT_NAMES.get(str(app_name), "Slate")


def already_open_message(app_name: str) -> str:
    return f"{product_name(app_name)} is already open."


def _server_name(app_name: str) -> str:
    return f"{app_name}_activate"


def notify_running(app_name: str, timeout_ms: int = 1000) -> bool:
    """Ask the instance that is already open to come to the front."""
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtNetwork import QLocalSocket
        if QCoreApplication.instance() is None:
            QCoreApplication([])
        socket = QLocalSocket()
        socket.connectToServer(_server_name(app_name))
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(b"activate")
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        return True
    except Exception as exc:
        logging.debug("Could not reach the open instance: %s", exc)
        return False


def listen(app_name: str, on_activate):
    """
    In the instance that is open: call on_activate when a second start asks
    for it, so starting Slate again brings this window forward. Returns the
    server (keep a reference), or None.
    """
    try:
        from PySide6.QtNetwork import QLocalServer
        server = QLocalServer()
        QLocalServer.removeServer(_server_name(app_name))
        if not server.listen(_server_name(app_name)):
            return None

        def accept():
            while server.hasPendingConnections():
                connection = server.nextPendingConnection()
                connection.readyRead.connect(lambda c=connection: (c.readAll(), on_activate()))
                connection.disconnected.connect(connection.deleteLater)

        server.newConnection.connect(accept)
        return server
    except Exception as exc:
        logging.debug("Single-instance listener not started: %s", exc)
        return None


class SingleInstance:
    """
    Windows Single Instance Lock using CreateMutex.
    Non-blocking. If locked, returns False immediately.
    """
    def __init__(self, app_name):
        self.app_name = app_name
        self.mutex_name = f"Global\\{app_name}_SingleInstance_Mutex"
        self.mutex_handle = None
        self.is_running = False

    def check(self):
        """Check if an instance is already running. Returns True if we are the FIRST instance."""
        if not ctypes:
            return True  # Fallback for non-Windows

        ERROR_ALREADY_EXISTS = 183

        self.mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, self.mutex_name)
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            self.is_running = True
            # The open window comes forward; only when it cannot be reached
            # is there a message, in words.
            if not notify_running(self.app_name):
                try:
                    ctypes.windll.user32.MessageBoxW(
                        0, already_open_message(self.app_name),
                        product_name(self.app_name), 0x40 | 0x1000)
                except OSError as exc:
                    logging.warning("SingleInstance alert display failed: %s", exc)
            return False

        return True  # We are the first!

    def cleanup(self):
        """Release mutex handling optional cleanup."""
        if self.mutex_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self.mutex_handle)
            except OSError as exc:
                logging.warning("SingleInstance cleanup failed: %s", exc)
            self.mutex_handle = None

    def __del__(self):
        try:
            self.cleanup()
        except OSError as exc:
            logging.debug("SingleInstance __del__ cleanup warning: %s", exc)
