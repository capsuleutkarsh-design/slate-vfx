import logging
import json
import threading
import time
import queue
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from contextlib import contextmanager
from collections import defaultdict

class TelemetryManager:
    """
    Enhanced Telemetry System (Improvement #7).
    Tracks feature usage, events, and performance metrics to local JSON logs.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TelemetryManager, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self):
        if self.initialized: return
        self.initialized = True
        
        self.enabled = True # Can be toggled via settings
        self.session_id = f"SES_{int(datetime.now().timestamp())}"
        self.log_dir = self._get_log_dir()
        self.current_log_file = self.log_dir / f"telemetry_{datetime.now().strftime('%Y-%m')}.jsonl"
        
        self.lock = threading.Lock()
        self._event_queue: "queue.Queue[dict]" = queue.Queue(maxsize=10000)
        self._writer_running = True
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="utvfx-telemetry-writer",
            daemon=True,
        )
        self._writer_thread.start()
        
        # Performance tracking (Improvement #7)
        self.performance_metrics = defaultdict(list)
        self.slow_operation_threshold = 1000  # milliseconds
        
    def _get_log_dir(self) -> Path:
        try:
            import sys
            import os
            if sys.platform == "win32":
                p = Path(os.environ.get('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "UTVFX" / "telemetry"
            else:
                p = Path.home() / ".ut_vfx" / "telemetry"
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            return Path.cwd() / "telemetry"

    def track_event(self, event_name: str, properties: Optional[Dict[str, Any]] = None):
        """Log a user event."""
        if not self.enabled: return
        
        event_data = {
            "event": event_name,
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "properties": properties or {}
        }
        
        try:
            self._event_queue.put_nowait(event_data)
        except queue.Full:
            # Drop oldest event to keep UI thread non-blocking.
            try:
                _ = self._event_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._event_queue.put_nowait(event_data)
            except queue.Full:
                logging.debug("Telemetry queue overflow; dropping event %s", event_name)

    def _write_event(self, data: Dict[str, Any]):
        try:
            with self.lock:
                with open(self.current_log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(data) + "\n")
        except Exception as e:
            logging.debug(f"Telemetry write failed: {e}")

    def _writer_loop(self):
        """Single background writer for telemetry JSONL appends."""
        while self._writer_running:
            try:
                event = self._event_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if event is None:
                break
            self._write_event(event)

    def shutdown(self, timeout_sec: float = 1.5):
        """Flush and stop telemetry writer thread."""
        if not getattr(self, "_writer_running", False):
            return
        self._writer_running = False
        try:
            self._event_queue.put_nowait(None)
        except Exception:
            pass
        try:
            if self._writer_thread and self._writer_thread.is_alive():
                self._writer_thread.join(timeout=max(0.1, float(timeout_sec)))
        except Exception as exc:
            logging.debug("Telemetry shutdown join skipped: %s", exc)
    
    # ===== PERFORMANCE TRACKING (Improvement #7) =====
    
    @contextmanager
    def measure(self, operation_name: str):
        """
        Context manager to measure operation performance.
        
        Usage:
            with telemetry.measure("stock_browser_scan"):
                scan_directory(path)
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self.track_performance(operation_name, duration_ms)
    
    def track_performance(self, operation: str, duration_ms: float):
        """Track operation performance and log slow operations"""
        # Store metrics
        with self.lock:
            self.performance_metrics[operation].append(duration_ms)
        
        # Log slow operations
        if duration_ms > self.slow_operation_threshold:
            logging.warning(f"⏱️  SLOW OPERATION: {operation} took {duration_ms:.0f}ms")
            
            # Track as telemetry event
            self.track_event(f"performance_{operation}", {
                "duration_ms": duration_ms,
                "slow": True,
                "threshold": self.slow_operation_threshold
            })
        else:
            # Track normal performance
            self.track_event(f"performance_{operation}", {
                "duration_ms": duration_ms,
                "slow": False
            })
    
    def get_stats(self, operation: str) -> Optional[Dict[str, float]]:
        """Get performance statistics for an operation"""
        if operation not in self.performance_metrics:
            return None
        
        durations = self.performance_metrics[operation]
        if not durations:
            return None
        
        return {
            "count": len(durations),
            "avg_ms": sum(durations) / len(durations),
            "min_ms": min(durations),
            "max_ms": max(durations),
            "total_ms": sum(durations)
        }
    
    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all tracked operations"""
        stats = {}
        for operation in self.performance_metrics.keys():
            stats[operation] = self.get_stats(operation)
        return stats
    
    def get_slow_operations(self) -> Dict[str, float]:
        """Get operations that exceeded the threshold"""
        slow_ops = {}
        for operation, durations in self.performance_metrics.items():
            slow_count = sum(1 for d in durations if d > self.slow_operation_threshold)
            if slow_count > 0:
                slow_ops[operation] = {
                    "slow_count": slow_count,
                    "total_count": len(durations),
                    "slow_percentage": (slow_count / len(durations)) * 100,
                    "avg_duration": sum(durations) / len(durations)
                }
        return slow_ops

    # ===== TELEMETRY READER API =====

    def list_log_files(self) -> List[Path]:
        """Return available telemetry log files in ascending order."""
        try:
            files = sorted(self.log_dir.glob("telemetry_*.jsonl"))
            return [p for p in files if p.is_file()]
        except Exception:
            return []

    def read_events(self, month: Optional[str] = None, limit: int = 5000) -> List[Dict[str, Any]]:
        """
        Read telemetry events from disk.

        Args:
            month: Optional month key in YYYY-MM format. Defaults to current month file.
            limit: Max events to return (newest first in memory after load order).
        """
        limit = max(1, int(limit or 1))
        target = self._resolve_log_file(month)
        if not target.exists():
            return []

        events: List[Dict[str, Any]] = []
        try:
            with open(target, "r", encoding="utf-8") as fh:
                for line in fh:
                    payload = self._parse_event_line(line)
                    if payload is None:
                        continue
                    events.append(payload)
            if len(events) > limit:
                events = events[-limit:]
            return events
        except Exception as exc:
            logging.debug("Telemetry read failed for %s: %s", target, exc)
            return []

    def summarize_events(self, month: Optional[str] = None, limit: int = 5000) -> Dict[str, Any]:
        """Build a compact summary for dashboards/CLI tools."""
        events = self.read_events(month=month, limit=limit)
        by_event = defaultdict(int)
        by_day = defaultdict(int)
        sessions = set()
        latest_timestamp = ""

        for item in events:
            event_name = str(item.get("event", "unknown"))
            by_event[event_name] += 1

            ts = str(item.get("timestamp", ""))
            if ts:
                by_day[ts[:10]] += 1
                if ts > latest_timestamp:
                    latest_timestamp = ts

            session_id = item.get("session_id")
            if session_id:
                sessions.add(str(session_id))

        top_events = sorted(by_event.items(), key=lambda pair: pair[1], reverse=True)[:10]
        return {
            "month": month or datetime.now().strftime("%Y-%m"),
            "total_events": len(events),
            "sessions": len(sessions),
            "latest_timestamp": latest_timestamp,
            "top_events": [{"event": name, "count": count} for name, count in top_events],
            "events_by_day": dict(sorted(by_day.items())),
        }

    def _resolve_log_file(self, month: Optional[str]) -> Path:
        if month:
            safe_month = str(month).strip()
            return self.log_dir / f"telemetry_{safe_month}.jsonl"
        return self.current_log_file

    @staticmethod
    def _parse_event_line(line: str) -> Optional[Dict[str, Any]]:
        raw = str(line or "").strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            return None
        return None

# Global Instance
telemetry = TelemetryManager()
