"""
The web API, run inside the server process.

The class lives in slate/api/in_process.py so the Admin Panel's "Start API
gateway" uses the same in-process runner (an installed client has no python
to launch slate/api/main.py with).
"""

from slate.api.in_process import ApiServer  # noqa: F401
