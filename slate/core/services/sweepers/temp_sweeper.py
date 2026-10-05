import os
import re
import logging
import tempfile
import time
from pathlib import Path
from ..sweeper_engine import BaseSweeper

logger = logging.getLogger(__name__)

# ProxyManager._partial_name: "<stem>.part<pid>-<thread><ext>", renamed into
# place when finished. One left a day later is an interrupted write's stump.
_PARTIAL = re.compile(r"\.part\d+-\d+(\.[^.]*)?$")


class TempFileSweeper(BaseSweeper):
    """
    Removes Slate's own temporary files once they are a day old.

    It used to empty %LOCALAPPDATA%\Slate\Cache of anything over a day old -
    the thumbnail cache, local-only proxies, the RV playlist and the library
    cache, all still in use - while the Slate\Temp folder it named was never
    written by anything. Only files that are temporary by construction go now.
    """

    def __init__(self, name="TempFileSweeper", max_age_days=1):
        super().__init__(name)
        self.max_age_days = max_age_days

    @staticmethod
    def candidates():
        """Every Slate temporary file, whatever its age."""
        # The player's frame lists (sequence_engine), one per player.
        yield from Path(tempfile.gettempdir()).glob("slate-frames-*.txt")
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            cache = Path(local_appdata) / "Slate" / "Cache"
            if cache.is_dir():
                yield from (p for p in cache.rglob("*.part*") if _PARTIAL.search(p.name))

    def run(self, dry_run=False):
        freed = count = 0
        errors = []
        oldest = time.time() - self.max_age_days * 86400
        for path in self.candidates():
            try:
                stat = path.stat()
                if stat.st_mtime >= oldest or not path.is_file():
                    continue
                if not dry_run:
                    path.unlink()
                freed += stat.st_size
                count += 1
            except OSError as exc:
                errors.append(str(exc))
        return {'freed_bytes': freed, 'files_deleted': count, 'errors': errors}
