import logging
import winreg
import sys

class StartupManager:
    def __init__(self, app_name="UTVFX_Gatekeeper"):
        self.app_name = app_name
        self.key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def add_to_startup(self):
        try:
            if getattr(sys, 'frozen', False):
                # Point directly to the Main Executable
                target = f'"{sys.executable}" --startup'
            else:
                # Python script mode
                target = f'"{sys.executable}" "{sys.argv[0]}" --startup'

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_ALL_ACCESS) as key:
                winreg.SetValueEx(key, self.app_name, 0, winreg.REG_SZ, target)
            return True
        except OSError as e:
            logging.error(f"Startup Registry Error: {e}")
            return False

    def remove_from_startup(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key_path, 0, winreg.KEY_ALL_ACCESS) as key:
                try:
                    winreg.DeleteValue(key, self.app_name)
                except FileNotFoundError:
                    return True  # Already gone
            return True
        except OSError as e:
            logging.error(f"Startup Registry Remove Error: {e}")
            return False
