"""
What arrived, and what was wrong with it.

An ingest used to end in a set of counts. This turns the same run into two
artefacts kept inside the project:

  * a report a coordinator can read and send to the client the same afternoon
  * a manifest recording every file, so a disputed delivery has evidence and an
    interrupted run has somewhere to resume from

Both are written next to each other under the incoming-client folder.
"""

from __future__ import annotations

import html
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


# Where reports live inside a project. Beside the client's own delivery folder
# (ingest_survey.client_folder_for), because that is where a coordinator goes
# looking for them.
REPORT_DIRNAME = "_ingest_reports"


def frame_summary(frames, limit: int = 12) -> str:
    """Render frame numbers as ranges: [1043, 1044, 1045, 1060] -> '1043-1045, 1060'."""
    if not frames:
        return ""
    ordered = sorted(set(frames))
    spans = []
    start = previous = ordered[0]
    for frame in ordered[1:]:
        if frame == previous + 1:
            previous = frame
            continue
        spans.append((start, previous))
        start = previous = frame
    spans.append((start, previous))

    rendered = [str(a) if a == b else f"{a}-{b}" for a, b in spans]
    if len(rendered) > limit:
        return ", ".join(rendered[:limit]) + f" (+{len(rendered) - limit} more)"
    return ", ".join(rendered)


@dataclass
class DeliveryReport:
    project: str = ""
    source: str = ""
    started_at: str = ""
    finished_at: str = ""
    dry_run: bool = False
    operation: str = "copy"           # copy / move
    status: str = "completed"         # completed / stopped / failed

    shots: List[Dict] = field(default_factory=list)
    sequences: List[Dict] = field(default_factory=list)
    incomplete: List[Dict] = field(default_factory=list)
    skipped: List[Dict] = field(default_factory=list)
    failed: List[Dict] = field(default_factory=list)
    pending: List[Dict] = field(default_factory=list)      # a stopped run never reached these
    documents: List[Dict] = field(default_factory=list)
    ignored: List[Dict] = field(default_factory=list)
    unchanged: List[Dict] = field(default_factory=list)

    files_moved: int = 0
    files_skipped: int = 0
    errors: int = 0
    reels: int = 0
    register_shots: bool = False       # the run added its shots to the Dashboard
    retries: List[Dict] = field(default_factory=list)

    @staticmethod
    def distinct_shots(entries) -> int:
        """
        How many shots: one per destination (reel, shot). Two deliveries of
        SH_050 (ScanA, ScanB) are two entries - two scan versions - but one
        shot, the way the pre-flight, the result and the dashboard count.
        """
        return len({(str(e.get("reel", "")).lower(), str(e.get("shot", "")).lower())
                    for e in entries or []})

    @property
    def shot_count(self) -> int:
        return self.distinct_shots(self.shots)

    @property
    def real_sequences(self) -> List[Dict]:
        out = []
        for s in self.sequences:
            kind = s.get("kind") or ("sequence" if int(s.get("frames") or 0) > 1 else "file")
            if kind == "sequence":
                out.append(s)
        return out

    @property
    def total_frames(self) -> int:
        """Frames of real sequences only - a MOV or a PDF is not a frame."""
        return sum(int(s.get("frames") or 0) for s in self.real_sequences)

    @property
    def single_files(self) -> int:
        return len(self.sequences) - len(self.real_sequences)

    @property
    def missing_frame_count(self) -> int:
        return sum(len(s.get("missing") or []) for s in self.incomplete)

    @property
    def not_landed(self) -> List[Dict]:
        """Sequences some of whose frames failed or were never reached."""
        return [s for s in self.real_sequences if s.get("not_landed")]

    @property
    def stopped(self) -> bool:
        return self.status != "completed"

    @property
    def is_clean(self) -> bool:
        return not self.incomplete and not self.failed and not self.stopped

    def headline(self) -> str:
        """One line a coordinator can paste into an email."""
        parts = []
        if self.status == "stopped":
            parts.append("STOPPED before the end - incomplete")
        elif self.status == "failed":
            parts.append("FAILED - incomplete")
        parts += [f"{self.shot_count} shot(s) across {self.reels} reel(s)",
                  f"{self.total_frames} frame(s)"]
        if self.single_files:
            parts.append(f"{self.single_files} single file(s)")
        if self.incomplete:
            parts.append(f"{len(self.incomplete)} sequence(s) short "
                         f"{self.missing_frame_count} frame(s)")
        if self.failed:
            parts.append(f"{len(self.failed)} file(s) failed")
        if self.pending:
            parts.append(f"{len(self.pending)} file(s) not reached")
        if self.is_clean:
            parts.append("no problems found")
        return " | ".join(parts)

    def to_dict(self) -> Dict:
        return {
            "project": self.project,
            "source": self.source,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "operation": self.operation,
            "status": self.status,
            "register_shots": self.register_shots,
            "totals": {
                "shots": self.shot_count,
                "unchanged_shots": self.distinct_shots(self.unchanged),
                "reels": self.reels,
                "frames": self.total_frames,
                "single_files": self.single_files,
                "files_moved": self.files_moved,
                "files_skipped": self.files_skipped,
                "errors": self.errors,
                "missing_frames": self.missing_frame_count,
            },
            "shots": self.shots,
            "sequences": self.sequences,
            "incomplete": self.incomplete,
            "skipped": self.skipped,
            "failed": self.failed,
            "pending": self.pending,
            "documents": self.documents,
            "ignored": self.ignored,
            "unchanged": self.unchanged,
            "retries": self.retries,
        }

    @classmethod
    def from_manifest(cls, data: Dict) -> "DeliveryReport":
        """The report a manifest describes - so a retry can write the report again."""
        totals = data.get("totals") or {}
        return cls(
            project=data.get("project", ""), source=data.get("source", ""),
            started_at=data.get("started_at", ""), finished_at=data.get("finished_at", ""),
            dry_run=bool(data.get("dry_run")), operation=data.get("operation", "copy"),
            status=data.get("status", "completed"),
            shots=list(data.get("shots") or []), sequences=list(data.get("sequences") or []),
            incomplete=list(data.get("incomplete") or []), skipped=list(data.get("skipped") or []),
            failed=list(data.get("failed") or []), pending=list(data.get("pending") or []),
            documents=list(data.get("documents") or []), ignored=list(data.get("ignored") or []),
            unchanged=list(data.get("unchanged") or []),
            files_moved=int(totals.get("files_moved") or 0), files_skipped=int(totals.get("files_skipped") or 0),
            errors=int(totals.get("errors") or 0), reels=int(totals.get("reels") or 0),
            register_shots=bool(data.get("register_shots")), retries=list(data.get("retries") or []),
        )


def build_report(worker, project_name: str, source_path) -> DeliveryReport:
    """Assemble a report from a finished FolderCreationWorker."""
    survey = getattr(worker, "survey", None)
    ignored = []
    if survey is not None:
        ignored += [{"item": name, "reason": "empty folder"} for name in survey.empty_folders]
        ignored += [{"item": name, "reason": "system file"} for name in survey.junk_files]
        ignored += [{"item": name, "reason": "could not be read"} for name in survey.unreadable]
    report = DeliveryReport(
        project=project_name,
        source=str(source_path or ""),
        started_at=str(getattr(worker, "started_at", "") or ""),
        finished_at=(str(getattr(worker, "finished_at", "") or "")
                     or datetime.now().isoformat(timespec="seconds")),
        dry_run=bool(getattr(worker, "dry_run", False)),
        operation=str(getattr(worker, "operation", "copy") or "copy"),
        status=str(getattr(worker, "outcome", "completed") or "completed"),
        shots=list(getattr(worker, "ingested_shots", []) or []),
        sequences=list(getattr(worker, "sequences_found", []) or []),
        incomplete=list(getattr(worker, "incomplete_sequences", []) or []),
        skipped=list(getattr(worker, "skipped_files", []) or []),
        failed=list(getattr(worker, "failed_files", []) or []),
        pending=list(getattr(worker, "pending_files", []) or []),
        documents=list(getattr(worker, "documents_filed", []) or []),
        ignored=ignored,
        unchanged=list(getattr(worker, "skipped_shots", []) or []),
        files_moved=int(getattr(worker, "files_moved", 0) or 0),
        files_skipped=int(getattr(worker, "files_skipped", 0) or 0),
        errors=int(getattr(worker, "errors", 0) or 0),
        reels=int(getattr(worker, "reels_count", 0) or 0),
        register_shots=bool(getattr(worker, "register_shots", False)),
    )
    return report


def _report_dir(project_root, client_folder: str = "") -> Path:
    """
    Where a project's reports go: always <project>/<client folder>/_ingest_reports.

    The folder used to depend on whether the client folder happened to exist
    yet, so the first report of a project landed somewhere else from the rest.
    """
    from slate.core.domain.ingest_survey import client_folder_for
    return Path(project_root) / (client_folder or client_folder_for(None)) / REPORT_DIRNAME


def report_dirs(project_root) -> List[Path]:
    """Every place a report of this project may be: under any client folder a template named, or the project."""
    root = Path(project_root)
    try:
        places = [d / REPORT_DIRNAME for d in sorted(root.iterdir()) if d.is_dir()]
    except OSError:
        places = []
    places.append(root / REPORT_DIRNAME)          # where early reports went
    return places


def dry_run_dir(project_name: str) -> Path:
    """
    Dry-run reports live in Slate's own folder: a dry run must not create
    anything in the projects folder - not even the project's own folder.
    """
    import os
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Slate"
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(project_name or "project"))
    return base / "ingest_dry_runs" / safe


def _render_html(report: DeliveryReport) -> str:
    e = html.escape

    def rows(items, columns):
        out = []
        for item in items:
            cells = "".join(f"<td>{e(str(fn(item)))}</td>" for _, fn in columns)
            out.append(f"<tr>{cells}</tr>")
        return "\n".join(out) or f'<tr><td colspan="{len(columns)}">None</td></tr>'

    def table(title, items, columns, warn=False):
        head = "".join(f"<th>{e(name)}</th>" for name, _ in columns)
        cls = ' class="warn"' if warn and items else ""
        return (f"<h2{cls}>{e(title)} <span class='count'>{len(items)}</span></h2>"
                f"<table><thead><tr>{head}</tr></thead>"
                f"<tbody>{rows(items, columns)}</tbody></table>")

    if report.status == "stopped":
        status = "Stopped before the end - this delivery is incomplete"
    elif report.status == "failed":
        status = "The ingest failed - this delivery is incomplete"
    else:
        status = "No problems found" if report.is_clean else "Problems found"
    status_class = "ok" if report.is_clean else "bad"
    if report.dry_run:
        what = (f"Would {'move' if report.operation == 'move' else 'copy'} into the project "
                "<strong>(dry run - nothing was copied, moved or created)</strong>")
    else:
        verb = {"move": "Moved", "copy": "Copied"}.get(report.operation, "Brought in")
        drive = "emptied" if report.operation == "move" else "left as it was"
        what = e(f"{verb} into the project (the client drive was {drive})")
    retried = ""
    if report.retries:
        last = report.retries[-1]
        retried = (f"<br>Retried {len(report.retries)} time(s), last at {e(str(last.get('at', '')))}: "
                   f"{len(last.get('recovered') or [])} file(s) recovered")

    body = [
        f"<h1>Delivery Report &mdash; {e(report.project)}</h1>",
        f"<p class='meta'>Source: {e(report.source)}<br>{what}<br>"
        f"Started: {e(report.started_at or '-')} &middot; finished: {e(report.finished_at)}{retried}</p>",
        f"<p class='status {status_class}'>{e(status)}</p>",
        f"<p class='headline'>{e(report.headline())}</p>",
        table("Shots received", report.shots, [
            ("Reel", lambda s: s.get("reel", "")),
            ("Shot", lambda s: s.get("shot", "")),
            ("Scan version", lambda s: s.get("scan_version", "")),
            ("Client version", lambda s: s.get("client_version", "") or "-"),
            ("Delivered as", lambda s: s.get("source_folder", "")),
        ]),
        table("Sequences short of frames", report.incomplete, [
            ("Reel", lambda s: s.get("reel", "")),
            ("Shot", lambda s: s.get("shot", "")),
            ("Sequence", lambda s: s.get("name", "")),
            ("Range", lambda s: f"{s.get('start')}-{s.get('end')}"),
            ("Missing", lambda s: frame_summary(s.get("missing"))),
        ], warn=True),
        table("Files that failed", report.failed, [
            ("File", lambda s: s.get("file", "")),
            ("Reason", lambda s: s.get("error", "")),
        ], warn=True),
        table("Sequences not fully brought in (failed or stopped)", report.not_landed, [
            ("Reel", lambda s: s.get("reel", "")),
            ("Shot", lambda s: s.get("shot", "")),
            ("Sequence", lambda s: s.get("name", "")),
            ("Frames in", lambda s: f"{s.get('frames', 0)} of {s.get('planned', s.get('frames', 0))}"),
            ("Not brought in", lambda s: frame_summary(s.get("not_landed"))),
        ], warn=True),
        table("Shots already in the project (not brought in again)", report.unchanged, [
            ("Reel", lambda s: s.get("reel", "")),
            ("Shot", lambda s: s.get("shot", "")),
            ("Delivered as", lambda s: s.get("source", "")),
            ("Why", lambda s: s.get("reason", "")),
        ]),
        table("Documents filed", report.documents, [
            ("File", lambda s: s.get("file", "")),
            ("Filed in", lambda s: s.get("destination", "")),
        ]),
        table("Ignored on the drive", report.ignored, [
            ("Item", lambda s: s.get("item", "")),
            ("Why", lambda s: s.get("reason", "")),
        ]),
        table("Skipped (already in the project)", report.skipped, [
            ("File", lambda s: s.get("file", "")),
            ("Reason", lambda s: s.get("reason", "")),
        ]),
        table("All sequences", report.sequences, [
            ("Reel", lambda s: s.get("reel", "")),
            ("Shot", lambda s: s.get("shot", "")),
            ("Sequence", lambda s: s.get("name", "")),
            ("Frames", lambda s: s.get("frames", "")),
            ("Range", lambda s: (f"{s.get('start')}-{s.get('end')}"
                                 + (" (irregular numbering)" if s.get("irregular") else ""))
                if s.get("start") is not None else "single file"),
        ]),
    ]

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Delivery Report - {e(report.project)}</title>
<style>
  body {{ font-family: "Segoe UI", system-ui, sans-serif; color: #16323A;
         background: #FFFFFF; margin: 32px auto; max-width: 1000px; padding: 0 20px;
         line-height: 1.55; }}
  h1 {{ font-size: 24px; margin: 0 0 8px; }}
  h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: .08em;
        color: #5E5C57; margin: 34px 0 8px; }}
  h2.warn {{ color: #B23A36; }}
  .count {{ color: #87857F; font-weight: 400; }}
  .meta {{ color: #5E5C57; font-size: 13px; margin: 0 0 16px; }}
  .status {{ display: inline-block; padding: 5px 12px; border-radius: 3px;
             font-weight: 600; font-size: 13px; }}
  .status.ok {{ background: #DDF2E6; color: #1E6B43; }}
  .status.bad {{ background: #D9635F; color: #FFFFFF; }}
  .headline {{ font-size: 15px; margin: 14px 0 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; background: #E8E6E1; border-bottom: 1px solid #16323A;
        padding: 5px 10px; font-size: 11px; text-transform: uppercase;
        letter-spacing: .06em; color: #4A4843; }}
  td {{ border-bottom: 1px solid #D3D0C9; padding: 6px 10px;
        vertical-align: top; }}
</style></head>
<body>{''.join(body)}</body></html>"""


def rewrite_html(manifest_path) -> Optional[Path]:
    """
    Write the report of a manifest again, after a retry changed it, so the
    report a coordinator sends matches what is in the project now.
    """
    try:
        manifest_path = Path(manifest_path)
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        html_path = manifest_path.with_name(manifest_path.name.replace("manifest_", "delivery_", 1))
        html_path.write_text(_render_html(DeliveryReport.from_manifest(data)), encoding="utf-8")
        return html_path
    except Exception as exc:
        logging.warning("Could not rewrite the delivery report for %s: %s", manifest_path, exc)
        return None


def write_report(report: DeliveryReport, project_root, client_folder: str = "") -> Optional[Dict[str, str]]:
    """
    Write the report and manifest.

    A real run writes into the project (<project>/<client folder>/
    _ingest_reports); a dry run writes into Slate's own folder, so a
    simulation creates nothing under the projects folder. Returns the paths
    written, or None if they could not be written - a report failing must
    never fail the ingest that produced it.
    """
    try:
        if report.dry_run:
            directory = dry_run_dir(report.project or Path(str(project_root)).name)
        else:
            directory = _report_dir(project_root, client_folder)
        directory.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = "dryrun_" if report.dry_run else ""
        html_path = directory / f"{prefix}delivery_{stamp}.html"
        json_path = directory / f"{prefix}manifest_{stamp}.json"

        html_path.write_text(_render_html(report), encoding="utf-8")
        json_path.write_text(
            json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8"
        )

        logging.info("Delivery report written to %s", html_path)
        return {"report": str(html_path), "manifest": str(json_path), "folder": str(directory)}
    except Exception as exc:
        logging.warning("Could not write the delivery report: %s", exc)
        return None
