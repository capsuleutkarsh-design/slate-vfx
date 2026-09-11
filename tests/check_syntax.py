"""Quick syntax validation for modified files."""
import ast
import sys

files = [
    "slate/gui/stock_model.py",
    "slate/gui/tabs/stock_browser_tab.py",
    "slate/gui/widgets/advanced_player.py",
]

ok = 0
for f in files:
    try:
        with open(f, encoding="utf-8") as fh:
            ast.parse(fh.read())
        print(f"OK  {f}")
        ok += 1
    except SyntaxError as e:
        print(f"FAIL {f}: {e}")

print(f"\n{ok}/{len(files)} passed")
sys.exit(0 if ok == len(files) else 1)
