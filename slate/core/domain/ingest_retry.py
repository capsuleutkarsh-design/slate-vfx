"""
Re-attempt only the files that failed.

A run that dies at minute thirty-five of forty had to be repeated in full. The
manifest written by every ingest records each failure with where it came from
and where it was going, which is all a retry needs. The retry honours how the
run brought files in - copy leaves the client drive alone, move removes the
source once the copy is checked - and rewrites the manifest with the outcome,
so "Retry failed files" offers only what is still failing, also after Slate
was restarted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from slate.core.infra.file_operations import SafeFileOperations


@dataclass
class RetryResult:
    recovered: List[str] = field(default_factory=list)
    still_failing: List[Dict] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    error: Optional[str] = None
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.error:
            return f"Retry failed: {self.error}"
        parts = [f"{len(self.recovered)} file(s) recovered"]
        if self.still_failing:
            parts.append(f"{len(self.still_failing)} still failing")
        if self.missing:
            parts.append(f"{len(self.missing)} no longer on the source drive")
        if self.stopped:
            parts.append("stopped before the end")
        return " | ".join(parts)


def _read(manifest_path) -> Dict:
    try:
        return json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception as exc:
        logging.warning("Could not read manifest %s: %s", manifest_path, exc)
        return {}


def load_failures(manifest_path) -> List[Dict]:
    """The failed entries from an ingest manifest."""
    data = _read(manifest_path)
    return [entry for entry in (data.get("failed") or [])
            if entry.get("source") and entry.get("destination")]


def latest_manifest(project_root) -> Optional[Path]:
    """The most recent manifest of a real run in a project, if there is one."""
    from slate.core.domain.delivery_report import report_dirs

    found = []
    for directory in report_dirs(project_root):
        try:
            found.extend(directory.glob("manifest_*.json"))
        except Exception:
            continue
    if not found:
        return None
    return max(found, key=lambda p: p.name)


def pending_failures(project_root) -> List[Dict]:
    """Files the newest run of this project could not bring in, still waiting."""
    manifest = latest_manifest(project_root)
    return load_failures(manifest) if manifest else []


def retry_failures(manifest_path, fast_mode: bool = False,
                   progress: Callable[[int, int, str], None] = None,
                   should_stop: Callable[[], bool] = None,
                   operation: str = "") -> RetryResult:
    """
    Bring in the files that failed last time, the way that run did (copy or
    move), and record the outcome in the manifest.

    A file that is no longer on the source drive is reported separately from
    one that failed again - somebody may have moved it by hand, and that is not
    the same as a broken copy.
    """
    result = RetryResult()
    data = _read(manifest_path)
    failures = [entry for entry in (data.get("failed") or [])
                if entry.get("source") and entry.get("destination")]
    if not failures:
        return result
    mode = (operation or data.get("operation") or "move").lower()
    stop = should_stop or (lambda: False)

    for index, entry in enumerate(failures, start=1):
        if stop():
            result.stopped = True
            result.still_failing.extend(failures[index - 1:])
            break
        source = Path(entry["source"])
        destination = Path(entry["destination"])

        if progress:
            try:
                progress(index, len(failures), source.name)
            except Exception:
                pass

        if not SafeFileOperations.exists(source):
            if SafeFileOperations.exists(destination):
                result.recovered.append(source.name)       # it got there in the end
            else:
                result.missing.append(source.name)
            continue

        try:
            if mode == "copy":
                outcome = SafeFileOperations.safe_copy_with_verification(
                    source, destination, verify_checksum=not fast_mode)
            else:
                outcome = SafeFileOperations.safe_move_with_verification(
                    source, destination, verify_checksum=not fast_mode)
            success = outcome[0] if isinstance(outcome, tuple) else bool(outcome)
            message = outcome[1] if isinstance(outcome, tuple) and len(outcome) > 1 else ""
        except Exception as exc:
            success, message = False, str(exc)

        if success:
            result.recovered.append(source.name)
        else:
            result.still_failing.append({
                "file": source.name,
                "source": str(source),
                "destination": str(destination),
                "error": message or "unknown error",
            })

    _rewrite(manifest_path, data, result)
    return result


def _rewrite(manifest_path, data: Dict, result: RetryResult) -> None:
    """The manifest now lists only what still failed, with a note of the retry."""
    if not data:
        return
    data["failed"] = list(result.still_failing)
    retries = data.setdefault("retries", [])
    retries.append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "recovered": list(result.recovered),
        "missing": list(result.missing),
        "still_failing": len(result.still_failing),
    })
    totals = data.get("totals")
    if isinstance(totals, dict):
        totals["errors"] = len(result.still_failing)
    try:
        Path(manifest_path).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        logging.warning("Could not update the manifest %s: %s", manifest_path, exc)
