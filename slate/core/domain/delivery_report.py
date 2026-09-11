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


# Where reports live inside a project. Beside the client's own delivery folder,
# because that is where a coordinator goes looking for them.
REPORT_DIRNAME = "_ingest_reports"
CLIENT_FOLDER_CANDIDATES = ("01_Frm Client", "01_From Client", "01_Client")


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

    shots: List[Dict] = field(default_factory=list)
    sequences: List[Dict] = field(default_factory=list)
    incomplete: List[Dict] = field(default_factory=list)
    skipped: List[Dict] = field(default_factory=list)
    failed: List[Dict] = field(default_factory=list)

    files_moved: int = 0
    files_skipped: int = 0
    errors: int = 0
    reels: int = 0

    @property
    def total_frames(self) -> int:
        return sum(int(s.get("frames") or 0) for s in self.sequences)

    @property
    def missing_frame_count(self) -> int:
        return sum(len(s.get("missing") or []) for s in self.incomplete)

    @property
    def is_clean(self) -> bool:
        return not self.incomplete and not self.failed

    def headline(self) -> str:
        """One line a coordinator can paste into an email."""
        parts = [f"{len(self.shots)} shot(s) across {self.reels} reel(s)",
                 f"{self.total_frames} frame(s)"]
        if self.incomplete:
            parts.append(f"{len(self.incomplete)} sequence(s) short "
                         f"{self.missing_frame_count} frame(s)")
        if self.failed:
            parts.append(f"{len(self.failed)} file(s) failed")
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
            "totals": {
                "shots": len(self.shots),
                "reels": self.reels,
                "frames": self.total_frames,
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
        }


def build_report(worker, project_name: str, source_path) -> DeliveryReport:
    """Assemble a report from a finished FolderCreationWorker."""
    report = DeliveryReport(
        project=project_name,
        source=str(source_path or ""),
        finished_at=datetime.now().isoformat(timespec="seconds"),
        dry_run=bool(getattr(worker, "dry_run", False)),
        shots=list(getattr(worker, "ingested_shots", []) or []),
        sequences=list(getattr(worker, "sequences_found", []) or []),
        incomplete=list(getattr(worker, "incomplete_sequences", []) or []),
        skipped=list(getattr(worker, "skipped_files", []) or []),
        failed=list(getattr(worker, "failed_files", []) or []),
        files_moved=int(getattr(worker, "files_moved", 0) or 0),
        files_skipped=int(getattr(worker, "files_skipped", 0) or 0),
        errors=int(getattr(worker, "errors", 0) or 0),
        reels=int(getattr(worker, "reels_count", 0) or 0),
    )
    return report


def _report_dir(project_root) -> Path:
    """Where reports go: under the client folder if there is one."""
    root = Path(project_root)
    for candidate in CLIENT_FOLDER_CANDIDATES:
        if (root / candidate).is_dir():
            return root / candidate / REPORT_DIRNAME
    return root / REPORT_DIRNAME


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

    status = "No problems found" if report.is_clean else "Problems found"
    status_class = "ok" if report.is_clean else "bad"

    body = [
        f"<h1>Delivery Report &mdash; {e(report.project)}</h1>",
        f"<p class='meta'>Source: {e(report.source)}<br>"
        f"Ingested: {e(report.finished_at)}"
        + (" <strong>(DRY RUN &mdash; nothing was moved)</strong>" if report.dry_run else "")
        + "</p>",
        f"<p class='status {status_class}'>{e(status)}</p>",
        f"<p class='headline'>{e(report.headline())}</p>",
        table("Shots received", report.shots, [
            ("Reel", lambda s: s.get("reel", "")),
            ("Shot", lambda s: s.get("shot", "")),
            ("Scan version", lambda s: s.get("scan_version", "")),
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
        table("Skipped (already in the project)", report.skipped, [
            ("File", lambda s: s.get("file", "")),
            ("Reason", lambda s: s.get("reason", "")),
        ]),
        table("All sequences", report.sequences, [
            ("Reel", lambda s: s.get("reel", "")),
            ("Shot", lambda s: s.get("shot", "")),
            ("Sequence", lambda s: s.get("name", "")),
            ("Frames", lambda s: s.get("frames", "")),
            ("Range", lambda s: f"{s.get('start')}-{s.get('end')}"
                if s.get("start") is not None else "single file"),
        ]),
    ]

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Delivery Report - {e(report.project)}</title>
<style>
  body {{ font-family: "Segoe UI", system-ui, sans-serif; color: #16323A;
         background: #E8E6E1; margin: 32px auto; max-width: 1000px; padding: 0 20px;
         line-height: 1.55; }}
  h1 {{ font-size: 24px; margin: 0 0 8px; }}
  h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: .08em;
        color: #87857F; margin: 34px 0 8px; }}
  h2.warn {{ color: #D9635F; }}
  .count {{ color: #B4B1AA; font-weight: 400; }}
  .meta {{ color: #87857F; font-size: 13px; margin: 0 0 16px; }}
  .status {{ display: inline-block; padding: 5px 12px; border-radius: 3px;
             font-weight: 600; font-size: 13px; }}
  .status.ok {{ background: #E8E6E1; color: #5FBF8F; }}
  .status.bad {{ background: #D9635F; color: #D9635F; }}
  .headline {{ font-size: 15px; margin: 14px 0 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; border-bottom: 1px solid #16323A; padding: 5px 10px 5px 0;
        font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
        color: #87857F; }}
  td {{ border-bottom: 1px solid #E8E6E1; padding: 6px 10px 6px 0;
        vertical-align: top; }}
</style></head>
<body>{''.join(body)}</body></html>"""


def write_report(report: DeliveryReport, project_root) -> Optional[Dict[str, str]]:
    """
    Write the report and manifest into the project.

    Returns the paths written, or None if they could not be written - a report
    failing must never fail the ingest that produced it.
    """
    try:
        directory = _report_dir(project_root)
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
        return {"report": str(html_path), "manifest": str(json_path)}
    except Exception as exc:
        logging.warning("Could not write the delivery report: %s", exc)
        return None
