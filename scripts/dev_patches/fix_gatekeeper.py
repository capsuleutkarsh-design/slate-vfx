"""Fix the processEvents loop in gatekeeper_main.py"""
import pathlib

p = pathlib.Path(__file__).resolve().parent.parent.parent / 'ut_vfx' / 'gatekeeper_main.py'
text = p.read_text(encoding='utf-8')

old = """            # Force UI Event Processing to ensure window vanishes visually
            for _ in range(5): 
                QApplication.processEvents()"""

new = """            # Single event processing pass to flush pending deletions
            QApplication.processEvents()"""

if old in text:
    text = text.replace(old, new)
    p.write_text(text, encoding='utf-8')
    print("SUCCESS: Fixed processEvents loop in gatekeeper_main.py")
else:
    # Try with different line endings
    old2 = old.replace('\n', '\r\n')
    if old2 in text:
        text = text.replace(old2, new.replace('\n', '\r\n'))
        p.write_text(text, encoding='utf-8')
        print("SUCCESS: Fixed processEvents loop (CRLF) in gatekeeper_main.py")
    else:
        print("ERROR: Target text not found. Printing context around 'processEvents':")
        idx = text.find('processEvents')
        while idx != -1:
            start = max(0, idx - 100)
            end = min(len(text), idx + 100)
            print(f"  ... {repr(text[start:end])} ...")
            idx = text.find('processEvents', idx + 1)
