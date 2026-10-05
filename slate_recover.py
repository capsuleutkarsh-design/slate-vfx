"""
Recover Slate - for when nobody can sign in.

Run it on the server PC (the one that holds the studio's database):

    Recover Slate.bat                  the window
    Recover Slate.bat health           what is wrong, in plain words
    Recover Slate.bat --help           everything else

It needs the Recovery Key that was printed when the server was set up, and no
other password. See docs/RECOVERY.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    from slate_server.core.recovery.cli import main
    sys.exit(main())
