"""Fleet report export service extracted from AdminPanel."""

from __future__ import annotations

import csv
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Tuple

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ..core.domain import fleet_status as fs
from .admin_fleet_export import export_fleet_xlsx
from .admin_widgets import _load_json_with_fallback

logger = logging.getLogger(__name__)

# One set of columns for every format: the record key (kept as it was, so
# anything reading the JSON keeps working - 'ut_user' included) and the header
# people read in the CSV and the workbook. Drive columns (drive_c_total_gb...)
# are added per drive after these.
FIELDS: List[Tuple[str, str]] = [
    ("pc_name", "Machine"),
    ("status", "Status"),
    ("last_seen", "Last report"),
    ("last_seen_age", "Age"),
    ("age_seconds", "Age (s)"),
    ("ut_user", "Slate user"),
    ("os_user", "Windows account"),
    ("ip_address", "IP address"),
    ("mac_address", "MAC address"),
    ("computer_name", "Computer name"),
    ("manufacturer", "Manufacturer"),
    ("model", "Model"),
    ("motherboard", "Motherboard"),
    ("serial_no", "Serial number"),
    ("cpu", "CPU"),
    ("gpu", "GPU"),
    ("ram_gb", "RAM (GB)"),
    ("os", "OS"),
    ("windows_version", "Windows version"),
    ("client_version", "Slate version"),
]
HEADERS = dict(FIELDS)

_DRIVE_PARTS = (("root", "drive"), ("label", "label"), ("total_gb", "total (GB)"),
                ("free_gb", "free (GB)"), ("usage_pct", "used %"), ("alert", "alert"))


def header_for(key: str) -> str:
    """The human header of a record key: 'drive_c_usage_pct' -> 'C: used %'."""
    if key in HEADERS:
        return HEADERS[key]
    if key.startswith("drive_"):
        rest = key[len("drive_"):]
        for suffix, words in _DRIVE_PARTS:
            if rest.endswith("_" + suffix):
                letter = rest[: -len(suffix) - 1]
                if letter.startswith("noletter"):
                    return f"Drive without a letter {letter[len('noletter'):]}: {words}"
                return f"{letter.upper() or '?'}: {words}"
    return key.replace("_", " ").capitalize()


def _age_text(age_seconds: int) -> str:
    if age_seconds < 60:
        return f"{age_seconds} s ago"
    if age_seconds < 3600:
        return f"{age_seconds // 60} min ago"
    return f"{age_seconds // 3600} h {(age_seconds % 3600) // 60} min ago"


def record_for(data: dict, fallback_name: str, now: float) -> dict:
    """One machine's row of the report."""
    seen = fs.last_seen_of(data)
    state = fs.status_for(seen, now)
    last_seen_str = ""
    age_seconds = None
    age_human = ""
    if seen is not None:
        try:
            last_seen_str = datetime.fromtimestamp(seen).strftime("%Y-%m-%d %H:%M:%S")
            age_seconds = max(0, int(now - seen))
            age_human = _age_text(age_seconds)
        except (OverflowError, OSError, ValueError):
            last_seen_str = str(data.get("last_seen"))

    record = {
        "pc_name": data.get("pc_name") or fallback_name,
        "status": fs.label(state),
        "last_seen": last_seen_str,
        "last_seen_age": age_human,
        "age_seconds": age_seconds if age_seconds is not None else "",
        "ut_user": data.get("user", "") or "",
        "os_user": data.get("os_user", "") or "",
        "ip_address": data.get("IPAddress", "") or "",
        "mac_address": data.get("MACAddress", "") or "",
        "computer_name": data.get("ComputerName", "") or "",
        "manufacturer": data.get("Manufacturer", "") or "",
        "model": data.get("Model", "") or "",
        "motherboard": data.get("Motherboard", "") or "",
        "serial_no": data.get("SerialNo", "") or "",
        "cpu": data.get("CPU", "") or "",
        "gpu": data.get("GPU", "") or "",
        "ram_gb": data.get("RAM_GB", "") or "",
        "os": data.get("OS", "") or "",
        "windows_version": data.get("WindowsVersion", "") or "",
        "client_version": data.get("client_version", "") or "",
    }

    unlettered = 0
    for drive in data.get("Drives") or []:
        if not isinstance(drive, dict):
            continue
        # A drive written as {"Root": null} used to crash the whole export.
        root_raw = str(drive.get("Root") or "?")
        root_key = "".join(ch for ch in root_raw.lower() if ch.isalnum())
        if not root_key:
            # A drive reported without a letter gets its own key - 'x' used to
            # overwrite a real X: drive.
            unlettered += 1
            root_key = f"noletter{unlettered}"
        prefix = f"drive_{root_key}"
        usage_pct = fs.disk_percent(drive.get("Usage"))
        level = fs.disk_level(usage_pct)
        record[f"{prefix}_root"] = root_raw
        record[f"{prefix}_label"] = drive.get("Label") or ""
        record[f"{prefix}_total_gb"] = drive.get("Capacity_GB") or ""
        record[f"{prefix}_free_gb"] = drive.get("Free_GB") or ""
        record[f"{prefix}_usage_pct"] = f"{usage_pct:.1f}%" if usage_pct is not None else ""
        record[f"{prefix}_alert"] = {"bad": "CRITICAL", "warn": "WARNING", "ok": "OK"}.get(level, "")
    return record


def build_records(files: Iterable[Path], now: float = None):
    """(records, summary, skipped) for a set of status files."""
    now = time.time() if now is None else now
    records, raw, skipped = [], [], 0
    for report_file in files:
        try:
            data = _load_json_with_fallback(report_file)
        except Exception:
            skipped += 1
            continue
        if not isinstance(data, dict):
            skipped += 1
            continue
        raw.append(data)
        records.append(record_for(data, Path(report_file).stem, now))
    counts = fs.summarise(raw, now)
    summary = {"online": counts[fs.ONLINE], "not_responding": counts[fs.NOT_RESPONDING],
               "offline": counts[fs.OFFLINE], "unknown": counts[fs.UNKNOWN]}
    # Kept for anything that read the old key.
    summary["idle"] = summary["not_responding"]
    order = {fs.label(s): i for s, i in fs.ORDER.items()}
    records.sort(key=lambda r: (order.get(r.get("status"), 9), str(r.get("pc_name", "")).lower()))
    return records, summary, skipped


def columns_of(records) -> List[str]:
    keys = [key for key, _header in FIELDS]
    extra = list(dict.fromkeys(k for r in records for k in r if k not in HEADERS))
    return keys + extra


def summary_sentence(records, summary, skipped) -> str:
    """'13 machines: 8 online, 2 not responding, 2 offline, 1 unknown.'"""
    total = len(records)
    text = (f"{total} machine{'s' if total != 1 else ''}: {summary['online']} online, "
            f"{summary['not_responding']} not responding, {summary['offline']} offline, "
            f"{summary['unknown']} unknown.")
    if skipped:
        text += f" {skipped} report file{'s' if skipped != 1 else ''} could not be read."
    return text


def write_csv(path: Path, records, summary, skipped) -> Path:
    """
    The CSV is data only, starting with the header row - comment lines above
    it put the header on row 4 for Excel and pandas. The summary goes into a
    '<name>_summary.txt' beside it.
    """
    columns = columns_of(records)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([header_for(k) for k in columns])
        for record in records:
            writer.writerow([record.get(k, "") for k in columns])
    sidecar = path.with_name(path.stem + "_summary.txt")
    sidecar.write_text(
        f"Slate fleet report - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"{summary_sentence(records, summary, skipped)}\n", encoding="utf-8")
    return sidecar


def write_json(path: Path, records, summary, skipped) -> None:
    export_data = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total": len(records),
            "online": summary["online"],
            "not_responding": summary["not_responding"],
            "idle": summary["not_responding"],
            "offline": summary["offline"],
            "unknown": summary["unknown"],
            "skipped_files": skipped,
        },
        "workstations": records,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(export_data, fh, indent=2)


def write_report(output_path: Path, files, now=None) -> dict:
    """Read the reports and write the chosen format. Runs on a worker thread."""
    output_path = Path(output_path)
    records, summary, skipped = build_records(files, now)
    if not records:
        return {"ok": False, "message": "None of the workstation reports could be read."}
    suffix = output_path.suffix.lower()
    if suffix == ".xlsx":
        export_fleet_xlsx(output_path, records, summary, skipped,
                          columns=columns_of(records), header_for=header_for)
        kind = "Coloured Excel report"
    elif suffix == ".json":
        write_json(output_path, records, summary, skipped)
        kind = "JSON report"
    else:
        write_csv(output_path, records, summary, skipped)
        kind = "CSV report (the summary is in a _summary.txt beside it)"
    return {"ok": True, "records": len(records), "summary": summary, "skipped": skipped,
            "message": f"{kind} saved.\n\n{summary_sentence(records, summary, skipped)}\n\n"
                       f"File: {output_path}"}


class FleetReportWorker(QThread):
    """Reads every status file and writes the report without freezing the window."""
    done = Signal(dict)

    def __init__(self, output_path, files, parent=None):
        super().__init__(parent)
        self.output_path = Path(output_path)
        self.files = list(files)

    def run(self):
        try:
            self.done.emit(write_report(self.output_path, self.files))
        except Exception as exc:
            logger.exception("Fleet report failed")
            self.done.emit({"ok": False, "message": f"The report could not be written:\n{exc}"})


def run_fleet_report_export(parent, hub, log_action: Callable[[str], None]):
    """
    Export a fleet report snapshot from workstation LiveStatus JSON files.
    Reading hundreds of reports from the share happens on a worker thread;
    returns that worker (or None when nothing was started).
    """
    status_dir = hub.get_livestatus_dir()
    files = sorted(Path(status_dir).glob("*.json"))
    if not files:
        QMessageBox.information(parent, "Fleet report",
                                "No workstation has reported yet, so there is nothing to export.")
        return None

    default_name = f"fleet_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    path, selected_filter = QFileDialog.getSaveFileName(
        parent,
        "Export fleet report",
        default_name,
        "Excel Workbook (*.xlsx);;CSV Files (*.csv);;JSON Files (*.json)",
    )
    if not path:
        return None

    if not Path(path).suffix:
        if "json" in selected_filter.lower():
            path += ".json"
        elif "xlsx" in selected_filter.lower() or "excel" in selected_filter.lower():
            path += ".xlsx"
        else:
            path += ".csv"

    previous = getattr(parent, "_fleet_report_worker", None)
    try:
        busy = previous is not None and previous.isRunning()
    except RuntimeError:
        busy = False
    if busy:
        QMessageBox.information(parent, "Fleet report", "A fleet report is already being written.")
        return None

    worker = FleetReportWorker(path, files, parent)
    parent._fleet_report_worker = worker
    QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)

    def finished(result):
        QApplication.restoreOverrideCursor()
        if result.get("ok"):
            summary = result["summary"]
            log_action(f"Exported fleet report ({result['records']} machines, "
                       f"{summary['online']} online, {summary['not_responding']} not responding, "
                       f"{summary['offline']} offline): {path}")
            QMessageBox.information(parent, "Fleet report", result["message"])
        else:
            QMessageBox.warning(parent, "Fleet report", result["message"])

    worker.done.connect(finished)
    worker.start()
    return worker
