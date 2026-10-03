"""
Re-attempt only the files that failed - and finish a run that was stopped.

A run that dies at minute thirty-five of forty had to be repeated in full. The
manifest written by every ingest records each failure, and each file a stopped
run never reached, with where it came from and where it was going (its shot
and scan version), which is all a retry needs. The retry honours how the run
brought files in - copy leaves the client drive alone, move removes the
source once the copy is checked - fills the same scan version the run was
filling, adds that version's Denoise (etc.) folders, puts a shot that only now
received its first file on the Dashboard, and rewrites the manifest and the
report with the outcome. "Retry failed files" offers what is still waiting in
every run of the project, also after Slate was restarted.
"""

from __future__ import annotations

import json
import logging
import os
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
    registered: List[str] = field(default_factory=list)   # shots put on the Dashboard by the retry
    reports: List[str] = field(default_factory=list)      # the reports written again

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
        if self.registered:
            parts.append(f"{len(self.registered)} shot(s) added to the Dashboard")
        return " | ".join(parts)

    def merge(self, other: "RetryResult") -> None:
        self.recovered += other.recovered
        self.still_failing += other.still_failing
        self.missing += other.missing
        self.registered += other.registered
        self.reports += other.reports
        self.stopped = self.stopped or other.stopped
        self.error = self.error or other.error


def _read(manifest_path) -> Dict:
    try:
        return json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception as exc:
        logging.warning("Could not read manifest %s: %s", manifest_path, exc)
        return {}


def _waiting(data: Dict) -> List[Dict]:
    return [entry for entry in list(data.get("failed") or []) + list(data.get("pending") or [])
            if entry.get("source") and entry.get("destination")]


def load_failures(manifest_path) -> List[Dict]:
    """The files of an ingest manifest still waiting: failed, and not reached by a stopped run."""
    return _waiting(_read(manifest_path))


def _manifests(project_root) -> List[Path]:
    from slate.core.domain.delivery_report import report_dirs

    found = []
    for directory in report_dirs(project_root):
        try:
            found.extend(directory.glob("manifest_*.json"))
        except Exception:
            continue
    return sorted(found, key=lambda p: p.name, reverse=True)


def latest_manifest(project_root) -> Optional[Path]:
    """The most recent manifest of a real run in a project, if there is one."""
    found = _manifests(project_root)
    return found[0] if found else None


# {manifest: (modified time, waiting entries)}: the button is refreshed as a
# person types, and a project's manifests are only re-read when they change.
_waiting_cache: Dict[str, tuple] = {}


def manifests_with_failures(project_root) -> List[Dict]:
    """
    Every run of this project with files still waiting, newest first:
    [{'manifest': path, 'failed': n, 'pending': n}]. An older run's failures
    are not hidden by a newer run.
    """
    out = []
    for path in _manifests(project_root):
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        hit = _waiting_cache.get(str(path))
        if hit is None or hit[0] != mtime:
            entries = _waiting(_read(path))
            hit = (mtime, sum(1 for e in entries if not e.get("pending")),
                   sum(1 for e in entries if e.get("pending")))
            _waiting_cache[str(path)] = hit
        if hit[1] or hit[2]:
            out.append({"manifest": path, "failed": hit[1], "pending": hit[2]})
    return out


def pending_failures(project_root) -> List[Dict]:
    """Files still waiting in every run of this project."""
    out = []
    for item in manifests_with_failures(project_root):
        out.extend(load_failures(item["manifest"]))
    return out


def retry_failures(manifest_path, fast_mode: bool = False,
                   progress: Callable[[int, int, str], None] = None,
                   should_stop: Callable[[], bool] = None,
                   operation: str = "") -> RetryResult:
    """
    Bring in the files that failed last time (and those a stopped run never
    reached), the way that run did (copy or move), and record the outcome in
    the manifest and the report.

    A file that is no longer on the source drive is reported separately from
    one that failed again - somebody may have moved it by hand, and that is not
    the same as a broken copy.
    """
    result = RetryResult()
    data = _read(manifest_path)
    failures = _waiting(data)
    if not failures:
        return result
    mode = (operation or data.get("operation") or "move").lower()
    stop = should_stop or (lambda: False)
    landed: List[Dict] = []

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
                landed.append(entry)
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
            landed.append(entry)
        else:
            result.still_failing.append({**entry, "error": message or "unknown error", "pending": False})

    _finish_versions(landed)
    new_shots = _new_shots(data, landed)
    if new_shots and data.get("register_shots") and not data.get("dry_run"):
        result.registered = _register(data, new_shots)
    _rewrite(manifest_path, data, result, landed)
    from slate.core.domain.delivery_report import rewrite_html
    html = rewrite_html(manifest_path)
    if html:
        result.reports.append(str(html))
    return result


def _finish_versions(landed: List[Dict]) -> None:
    """The scan version's own folders (Denoise ...), which the run adds when a version gets its first file."""
    done = set()
    for entry in landed:
        version_dir = entry.get("version_dir")
        if not version_dir or version_dir in done:
            continue
        done.add(version_dir)
        for derived in entry.get("derived") or []:
            SafeFileOperations.safe_create_directory(Path(version_dir) / derived)


def _new_shots(data: Dict, landed: List[Dict]) -> List[Dict]:
    """Shots (and scan versions) that received their first file in this retry."""
    have = {(str(s.get("reel", "")).lower(), str(s.get("shot", "")).lower(), str(s.get("scan_version", "")))
            for s in data.get("shots") or []}
    out = []
    for entry in landed:
        if not entry.get("shot"):
            continue
        key = (str(entry["reel"]).lower(), str(entry["shot"]).lower(), str(entry.get("scan_version", "")))
        if key in have:
            continue
        have.add(key)
        shot = {k: entry.get(k, "") for k in ("reel", "shot", "path", "scan_version", "source_folder",
                                              "client_version")}
        shot["is_media"] = True
        frames = [s for s in data.get("sequences") or []
                  if s.get("reel") == entry["reel"] and s.get("shot") == entry["shot"]
                  and s.get("start") is not None]
        if frames:
            shot["first_frame"] = min(int(s["start"]) for s in frames)
            shot["last_frame"] = max(int(s["end"]) for s in frames)
        out.append(shot)
    return out


def _register(data: Dict, shots: List[Dict]) -> List[str]:
    try:
        from slate.core.domain.shot_registry import register_ingested_shots
        registration = register_ingested_shots(project_code=data.get("project", ""), shots=shots,
                                               project_name=data.get("project", ""))
        if registration.error:
            logging.warning("Retry could not add shots to the Dashboard: %s", registration.error)
        return list(registration.created) + list(registration.new_scans)
    except Exception as exc:
        logging.warning("Retry could not add shots to the Dashboard: %s", exc)
        return []


def _rewrite(manifest_path, data: Dict, result: RetryResult, landed: List[Dict] = ()) -> None:
    """The manifest now lists only what is still waiting, with a note of the retry."""
    if not data:
        return
    still = list(result.still_failing)
    data["failed"] = [e for e in still if not e.get("pending")]
    data["pending"] = [e for e in still if e.get("pending")]
    data.setdefault("shots", []).extend(_new_shots(data, landed))
    # Frames that landed now count in their sequence.
    sequences = {(s.get("reel"), s.get("shot"), s.get("name")): s for s in data.get("sequences") or []}
    for entry in landed:
        seq = sequences.get((entry.get("reel"), entry.get("shot"), entry.get("sequence")))
        if seq is not None and entry.get("frame") in (seq.get("not_landed") or []):
            seq["not_landed"].remove(entry["frame"])
            seq["frames"] = int(seq.get("frames") or 0) + 1
    retries = data.setdefault("retries", [])
    retries.append({
        "at": datetime.now().isoformat(timespec="seconds"),
        "recovered": list(result.recovered),
        "missing": list(result.missing),
        "still_failing": len(result.still_failing),
    })
    totals = data.get("totals")
    if isinstance(totals, dict):
        totals["errors"] = len(data["failed"])
        totals["files_moved"] = int(totals.get("files_moved") or 0) + len(result.recovered)
    if not still and data.get("status") == "stopped":
        data["status"] = "completed"          # the stopped run is finished now
    try:
        Path(manifest_path).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        logging.warning("Could not update the manifest %s: %s", manifest_path, exc)
