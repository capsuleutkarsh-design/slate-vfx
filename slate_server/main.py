import sys
import os
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from PySide6.QtWidgets import QApplication
from slate_server.gui.app_window import UTServerWindow
import logging

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # "Slate_Server.exe --recover [command ...]": the recovery tool, for when
    # nobody can sign in. It runs instead of the server window, so it works
    # when that window cannot start. See docs/RECOVERY.md.
    if "--recover" in sys.argv[1:]:
        from slate_server.core.recovery.cli import main as recover
        rest = [a for a in sys.argv[1:] if a != "--recover"]
        commands = [a for a in rest if not a.startswith("-")]
        if commands and getattr(sys, "frozen", False):
            # The installed server has no console of its own; a command-line
            # recovery needs one for the key prompt and the answers.
            try:
                import ctypes
                ctypes.windll.kernel32.AllocConsole()
                sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
                sys.stderr = sys.stdout
                sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
            except Exception:
                pass
        code = recover(rest) or 0
        if commands and getattr(sys, "frozen", False):
            try:
                input("
Press Enter to close.")
            except Exception:
                pass
        os._exit(code)

    # The server installer asks where the studio's shared folder is and writes
    # the answer beside the program. The clients pick that up through
    # GlobalConfig; the server never constructs one, so on a machine that runs
    # only the server the answer went nowhere - which is the same "I set it and
    # it was ignored" the clients had, in the one place where the reply decides
    # where the database gets built.
    try:
        from slate.core.infra.install_handoff import apply_install_answers
        taken = apply_install_answers()
        if taken:
            logging.info("Took %s from the install into this machine's settings.",
                         ", ".join(sorted(taken)))
    except Exception as exc:
        logging.debug("Install answers were not applied: %s", exc)

    app = QApplication(sys.argv)

    # Set global application info
    app.setApplicationName("Slate Central Server")
    app.setOrganizationName("UT Studio")

    # On the application, not only on the window, so a dialog opened without
    # a parent is drawn in the same dark theme as everything else.
    from slate_server.gui.design_system import GLOBAL_STYLESHEET
    app.setStyleSheet(GLOBAL_STYLESHEET)

    from slate import licence
    if not licence.check_startup():
        os._exit(3)
    window = UTServerWindow()
    if not licence.check_window(window):
        os._exit(3)
    window.show()
    
    ret = app.exec()
    try:
        if hasattr(window, 'db_engine'):
            window.db_engine.stop()
    except Exception:
        pass
    os._exit(ret)

if __name__ == "__main__":
    main()
