"""Fix threading.Thread in main_window.py"""
import pathlib

p = pathlib.Path(r'.\ut_vfx\gui\main_window.py')
text = p.read_bytes().decode('utf-8')

# Find and replace the threading.Thread block
old_variants = [
    "        import threading\r\n        t = threading.Thread(target=_login_task, daemon=True)\r\n        t.start()\r\n",
    "        import threading\n        t = threading.Thread(target=_login_task, daemon=True)\n        t.start()\n",
]

new_text = (
    "        from PySide6.QtCore import QRunnable, QThreadPool\r\n"
    "        class _LoginRunnable(QRunnable):\r\n"
    "            def __init__(self_r, task_fn):\r\n"
    "                super().__init__()\r\n"
    "                self_r.task_fn = task_fn\r\n"
    "            def run(self_r):\r\n"
    "                self_r.task_fn()\r\n"
    "        runnable = _LoginRunnable(_login_task)\r\n"
    "        runnable.setAutoDelete(True)\r\n"
    "        QThreadPool.globalInstance().start(runnable)\r\n"
)

replaced = False
for old in old_variants:
    if old in text:
        text = text.replace(old, new_text)
        replaced = True
        break

if replaced:
    p.write_bytes(text.encode('utf-8'))
    print("SUCCESS: Replaced threading.Thread with QRunnable in main_window.py")
else:
    # Debug: show what's actually around line 698
    lines = text.split('\n')
    for i in range(695, min(705, len(lines))):
        print(f"L{i+1}: {repr(lines[i])}")
