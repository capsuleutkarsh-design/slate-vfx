"""Fleet report export service extracted from AdminPanel."""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import QFileDialog, QMessageBox

from .admin_fleet_export import export_fleet_xlsx
from .admin_widgets import _load_json_with_fallback


def run_fleet_report_export(parent, hub, log_action: Callable[[str], None]) -> None:
    """Export a fleet report snapshot from workstation LiveStatus JSON files."""
    status_dir = hub.get_livestatus_dir()
    files = sorted(status_dir.glob("*.json"))
    if not files:
        QMessageBox.information(
            parent,
            "Fleet Report",
            f"No workstation status files found in:\n{status_dir}",
        )
        return

    default_name = f"fleet_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    path, selected_filter = QFileDialog.getSaveFileName(
        parent,
        "Export Fleet Report",
        default_name,
        "Excel Workbook (*.xlsx);;CSV Files (*.csv);;JSON Files (*.json)",
    )
    if not path:
        return

    if not Path(path).suffix:
        if "json" in selected_filter.lower():
            path += ".json"
        elif "xlsx" in selected_filter.lower() or "excel" in selected_filter.lower():
            path += ".xlsx"
        else:
            path += ".csv"

    now_ts = time.time()
    records = []
    skipped = 0
    summary = {"online": 0, "idle": 0, "offline": 0}

    for report_file in files:
        try:
            data = _load_json_with_fallback(report_file)
            if not isinstance(data, dict):
                skipped += 1
                continue
        except Exception:
            skipped += 1
            continue

        last_seen_raw = data.get("last_seen")
        last_seen_str = ""
        age_seconds = 0
        age_human = ""
        status_text = "Unknown"
        if last_seen_raw is not None:
            try:
                last_seen_val = float(last_seen_raw)
                last_seen_str = datetime.fromtimestamp(last_seen_val).strftime("%Y-%m-%d %H:%M:%S")
                age_seconds = max(0, int(now_ts - last_seen_val))
                if age_seconds < 60:
                    age_human = f"{age_seconds}s ago"
                    status_text = "Online"
                    summary["online"] += 1
                elif age_seconds < 300:
                    age_human = f"{age_seconds // 60}m {age_seconds % 60}s ago"
                    status_text = "Idle"
                    summary["idle"] += 1
                else:
                    if age_seconds < 3600:
                        age_human = f"{age_seconds // 60}m ago"
                    else:
                        age_human = f"{age_seconds // 3600}h {(age_seconds % 3600) // 60}m ago"
                    status_text = "Offline"
                    summary["offline"] += 1
            except (ValueError, TypeError, OSError):
                last_seen_str = str(last_seen_raw)
                status_text = "Unknown"

        record = {
            "pc_name": data.get("pc_name", report_file.stem),
            "status": status_text,
            "last_seen": last_seen_str,
            "last_seen_age": age_human,
            "age_seconds": age_seconds,
            "ut_user": data.get("user", ""),
            "os_user": data.get("os_user", ""),
            "ip_address": data.get("IPAddress", ""),
            "mac_address": data.get("MACAddress", ""),
            "computer_name": data.get("ComputerName", ""),
            "manufacturer": data.get("Manufacturer", ""),
            "model": data.get("Model", ""),
            "motherboard": data.get("Motherboard", ""),
            "serial_no": data.get("SerialNo", ""),
            "cpu": data.get("CPU", ""),
            "gpu": data.get("GPU", ""),
            "ram_gb": data.get("RAM_GB", ""),
            "os": data.get("OS", ""),
            "windows_version": data.get("WindowsVersion", ""),
            "client_version": data.get("client_version", ""),
            "disk_c_total_gb": "",
            "disk_c_free_gb": "",
            "disk_c_usage": "",
            "disk_c_alert": "",
        }

        drives = data.get("Drives", [])
        for drive in drives:
            if not isinstance(drive, dict):
                continue
            root_raw = drive.get("Root", "?")
            root_key = root_raw.replace(":", "").replace("\\", "").strip().lower()
            prefix = f"drive_{root_key}"

            try:
                usage_str = str(drive.get("Usage", "0")).strip().replace("%", "")
                usage_pct = float(usage_str)
            except (ValueError, AttributeError):
                usage_pct = 0.0

            alert = "OK"
            if usage_pct >= 90:
                alert = "CRITICAL"
            elif usage_pct >= 80:
                alert = "WARNING"

            record[f"{prefix}_root"] = root_raw
            record[f"{prefix}_label"] = drive.get("Label", "")
            record[f"{prefix}_total_gb"] = drive.get("Capacity_GB", "")
            record[f"{prefix}_free_gb"] = drive.get("Free_GB", "")
            record[f"{prefix}_usage_pct"] = f"{usage_pct:.1f}%"
            record[f"{prefix}_alert"] = alert

            if root_raw.upper().startswith("C"):
                record["disk_c_total_gb"] = drive.get("Capacity_GB", "")
                record["disk_c_free_gb"] = drive.get("Free_GB", "")
                record["disk_c_usage"] = f"{usage_pct:.1f}%"
                record["disk_c_alert"] = alert

        records.append(record)

    if not records:
        QMessageBox.warning(parent, "Fleet Report", "Could not parse any valid workstation reports.")
        return

    order = {"Online": 0, "Idle": 1, "Offline": 2, "Unknown": 3}
    records.sort(key=lambda r: order.get(r.get("status", "Unknown"), 3))

    output_path = Path(path)
    try:
        if output_path.suffix.lower() == ".xlsx":
            export_fleet_xlsx(output_path, records, summary, skipped)
            log_action(
                f"Exported Excel Fleet Report ({len(records)} PCs, "
                f"{summary['online']} online): {output_path}"
            )
            QMessageBox.information(
                parent,
                "Fleet Report Exported",
                (
                    f"Colored Excel report saved\n\n"
                    f"  Total     : {len(records)}\n"
                    f"  Online    : {summary['online']}\n"
                    f"  Idle      : {summary['idle']}\n"
                    f"  Offline   : {summary['offline']}\n"
                    f"  Skipped   : {skipped}\n\n"
                    f"File: {output_path}"
                ),
            )
            return
        if output_path.suffix.lower() == ".json":
            export_data = {
                "generated_at": datetime.now().isoformat(),
                "summary": {
                    "total": len(records),
                    "online": summary["online"],
                    "idle": summary["idle"],
                    "offline": summary["offline"],
                    "skipped_files": skipped,
                },
                "workstations": records,
            }
            with open(output_path, "w", encoding="utf-8") as fh:
                json.dump(export_data, fh, indent=2)
        else:
            all_keys = list(dict.fromkeys(k for r in records for k in r))
            with open(output_path, "w", newline="", encoding="utf-8-sig") as fh:
                fh.write(
                    f"# UT_VFX Fleet Report | Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                fh.write(
                    f"# Total: {len(records)} | Online: {summary['online']} | Idle: {summary['idle']} | Offline: {summary['offline']} | Skipped: {skipped}\n"
                )
                fh.write("\n")
                writer = csv.DictWriter(fh, fieldnames=all_keys, extrasaction="ignore", restval="")
                writer.writeheader()
                writer.writerows(records)

        log_action(
            f"Exported Fleet Report ({len(records)} PCs, "
            f"{summary['online']} online, {summary['idle']} idle, "
            f"{summary['offline']} offline): {output_path}"
        )
        QMessageBox.information(
            parent,
            "Fleet Report Exported",
            (
                f"Fleet Report saved successfully.\n\n"
                f"  Total Workstations : {len(records)}\n"
                f"  Online             : {summary['online']}\n"
                f"  Idle               : {summary['idle']}\n"
                f"  Offline            : {summary['offline']}\n"
                f"  Skipped (errors)   : {skipped}\n\n"
                f"File: {output_path}"
            ),
        )
    except Exception as exc:
        QMessageBox.critical(parent, "Fleet Report Error", f"Failed to export report:\n{exc}")

