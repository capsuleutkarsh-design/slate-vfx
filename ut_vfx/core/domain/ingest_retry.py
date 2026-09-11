"""
Re-attempt only the files that failed.

A run that dies at minute thirty-five of forty had to be repeated in full.
Nothing was ever corrupted - files are copied, verified, then the source is
removed, so the client drive is never left short - but the time was spent again.

The manifest written by every ingest records each failure with where it came
from and where it was going, which is all a retry needs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ut_vfx.core.infra.file_operations import SafeFileOperations


@dataclass
class RetryResult:
    recovered: List[str] = field(default_factory=list)
    still_failing: List[Dict] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    error: Optional[str] = None

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
        return " | ".join(parts)


def load_failures(manifest_path) -> List[Dict]:
    """The failed entries from an ingest manifest."""
    try:
        data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception as exc:
        logging.warning("Could not read manifest %s: %s", manifest_path, exc)
        return []

    return [entry for entry in (data.get("failed") or [])
            if entry.get("source") and entry.get("destination")]


def latest_manifest(project_root) -> Optional[Path]:
    """The most recent manifest in a project, if there is one."""
    from ut_vfx.core.domain.delivery_report import REPORT_DIRNAME, _report_dir

    try:
        directory = _report_dir(project_root)
        manifests = sorted(directory.glob("manifest_*.json"))
        return manifests[-1] if manifests else None
    except Exception:
        return None


def retry_failures(manifest_path, fast_mode: bool = False,
                   progress=None) -> RetryResult:
    """
    Move the files that failed last time.

    A file that is no longer on the source drive is reported separately from
    one that failed again - somebody may have moved it by hand, and that is not
    the same as a broken copy.
    """
    result = RetryResult()

    failures = load_failures(manifest_path)
    if not failures:
        return result

    for index, entry in enumerate(failures, start=1):
        source = Path(entry["source"])
        destination = Path(entry["destination"])

        if progress:
            try:
                progress(index, len(failures), source.name)
            except Exception:
                pass

        if not source.exists():
            if destination.exists():
                # It got there in the end; nothing to do.
                result.recovered.append(source.name)
            else:
                result.missing.append(source.name)
            continue

        try:
            outcome = SafeFileOperations.safe_move_with_verification(
                source, destination, verify_checksum=not fast_mode,
            )
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

    return result
