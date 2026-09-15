"""
Central Attendance Manager — works with both PostgreSQL and SQLite backends.
"""
import datetime
import json
import logging
import socket
from ..infra.database_manager import DatabaseManager

logger = logging.getLogger(__name__)


def _is_postgres(db) -> bool:
    """Check whether the active backend resolves to PostgresManager."""
    backend = getattr(db, "backend", db)
    return type(backend).__name__ == "PostgresManager"


class CentralAttendance:
    """
    Manages Attendance using the centralized database.
    Works with both PostgreSQL and SQLite backends.
    """
    def __init__(self):
        self.db = DatabaseManager()
        self.pc_name = socket.gethostname()
        
        # Configuration
        # The studio's start time, from the one place that holds it. These two
        # were literals here, and the leave policy had its own idea of the
        # working day - so the two halves of the same module disagreed.
        from slate.core.domain.leave_policy import late_cutoff
        self.LATE_CUTOFF_HOUR, self.LATE_CUTOFF_MINUTE = late_cutoff()

    def _json_merge_sql(self, column: str, param_placeholder: str = "%s") -> str:
        """
        Generate backend-appropriate JSON merge expression.
        PostgreSQL: column || %s::jsonb
        SQLite:     json_patch(column, %s)  — or Python-side merge via update
        """
        if _is_postgres(self.db):
            return f"{column} || {param_placeholder}::jsonb"
        else:
            return f"json_patch(COALESCE({column}, '{{}}'), {param_placeholder})"

    def log_action(self, user_name, action, metadata=None):
        """
        Log 'in' or 'out' action to the database.
        """
        try:
            if not user_name:
                logging.warning("Attendance log_action called with None/empty user_name, skipping")
                return
                
            user_id = user_name.lower().strip()
            
            # Get current date/time from DB for consistency
            if _is_postgres(self.db):
                now_res = self.db.execute_query("SELECT CURRENT_DATE, CURRENT_TIME", fetch="all")
            else:
                now_res = self.db.execute_query("SELECT date('now') AS current_date, time('now') AS current_time", fetch="all")
            
            if not now_res:
                raise Exception("Could not fetch server time")
            	
            if isinstance(now_res[0], dict):
                today_date = now_res[0]['current_date']
                now_time = now_res[0]['current_time']
            else:
                today_date = now_res[0][0]
                now_time = now_res[0][1]

            # Ensure today_date is a string
            if hasattr(today_date, "isoformat"):
                today_date = today_date.isoformat()
            else:
                today_date = str(today_date)
            if isinstance(now_time, str):
                # Parse time string "HH:MM:SS"
                parts = now_time.split(":")
                now_time = datetime.time(int(parts[0]), int(parts[1]), int(parts[2].split(".")[0]) if len(parts) > 2 else 0)
            
            # SMART AUTO-LOGOUT: Check previous day
            if action == "in":
                self._check_and_fix_previous_day(user_id, today_date)

            time_str = now_time.strftime("%H:%M:%S") if hasattr(now_time, 'strftime') else str(now_time)

            # 1. PUNCH IN
            if action == 'in':
                check_sql = "SELECT id FROM attendance_log WHERE user_id = %s AND day_date = %s"
                existing = self.db.execute_query(check_sql, (user_id, today_date), fetch="all")
                
                if existing and len(existing) > 0:
                    logger.warning(f"Duplicate punch-in attempt for {user_id} on {today_date}")
                    return

                insert_sql = """
                INSERT INTO attendance_log (user_id, day_date, punch_in, pc_name, metadata)
                VALUES (%s, %s, %s, %s, %s)
                """
                meta_json = json.dumps(metadata) if metadata else "{}"
                self.db.execute_update(insert_sql, (user_id, today_date, time_str, self.pc_name, meta_json))
                logger.info(f"Punch IN success: {user_id} at {time_str}")

            # 2. PUNCH OUT
            elif action == 'out':
                meta_json = json.dumps(metadata) if metadata else "{}"
                
                if _is_postgres(self.db):
                    update_sql = """
                    UPDATE attendance_log 
                    SET punch_out = %s, metadata = metadata || %s::jsonb
                    WHERE user_id = %s AND day_date = %s
                    """
                else:
                    update_sql = """
                    UPDATE attendance_log 
                    SET punch_out = %s, metadata = json_patch(COALESCE(metadata, '{}'), %s)
                    WHERE user_id = %s AND day_date = %s
                    """
                
                success = self.db.execute_update(update_sql, (time_str, meta_json, user_id, today_date))
                
                if not success:
                    logger.warning(f"Punch OUT without IN for {user_id}. creating partial record.")
                    insert_sql = """
                    INSERT INTO attendance_log (user_id, day_date, punch_out, pc_name, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    """
                    self.db.execute_update(insert_sql, (user_id, today_date, time_str, self.pc_name, meta_json))
                
                logger.info(f"Punch OUT success: {user_id} at {time_str}")

        except Exception as e:
            logger.error(f"Attendance DB Error: {e}", exc_info=True)
            raise

    def _check_and_fix_previous_day(self, user_id, today_date):
        """
        Check if previous working day has IN but no OUT.
        Auto-logout at configured shift time (default: 19:30:00) if missing.
        """
        try:
            from ..infra.global_config import GlobalConfig
            auto_logout_time = GlobalConfig.get("default_auto_logout_time", "19:30:00")
            if not auto_logout_time or len(str(auto_logout_time).split(":")) < 2:
                auto_logout_time = "19:30:00"

            sql = """
            SELECT day_date FROM attendance_log 
            WHERE user_id = %s AND day_date < %s AND punch_out IS NULL
            ORDER BY day_date DESC LIMIT 1
            """
            res = self.db.execute_query(sql, (user_id, today_date), fetch="all")
            
            if res and len(res) > 0:
                if isinstance(res[0], dict):
                    missed_date = res[0]['day_date']
                else:
                    missed_date = res[0][0]
                
                logger.info(f"Auto-closing missing punch-out for {user_id} on {missed_date} at {auto_logout_time}")
                
                auto_meta = json.dumps({"auto_logout": True, "cutoff": auto_logout_time})
                
                if _is_postgres(self.db):
                    update_sql = """
                    UPDATE attendance_log 
                    SET punch_out = %s, metadata = metadata || %s::jsonb
                    WHERE user_id = %s AND day_date = %s
                    """
                else:
                    update_sql = """
                    UPDATE attendance_log 
                    SET punch_out = %s, metadata = json_patch(COALESCE(metadata, '{}'), %s)
                    WHERE user_id = %s AND day_date = %s
                    """
                self.db.execute_update(update_sql, (auto_logout_time, auto_meta, user_id, missed_date))
                
        except Exception as e:
            logger.error(f"Auto-logout check failed: {e}")

    def get_full_month_data(self, year=None, month=None):
        """
        Retrieve monthly data in a format compatible with legacy UI.
        """
        if not year or not month:
            now = datetime.datetime.now()
            year, month = now.year, now.month
        
        if _is_postgres(self.db):
            sql = """
            SELECT user_id, day_date, punch_in, punch_out, pc_name, metadata
            FROM attendance_log 
            WHERE EXTRACT(YEAR FROM CAST(day_date AS DATE)) = %s AND EXTRACT(MONTH FROM CAST(day_date AS DATE)) = %s
            """
        else:
            # SQLite: use strftime for date extraction
            sql = """
            SELECT user_id, day_date, punch_in, punch_out, pc_name, metadata
            FROM attendance_log 
            WHERE CAST(strftime('%Y', day_date) AS INTEGER) = %s 
              AND CAST(strftime('%m', day_date) AS INTEGER) = %s
            """
        
        rows = self.db.execute_query(sql, (year, month), fetch="all")
        
        data = {}
        if not rows:
            return data
        
        for row in rows:
            if isinstance(row, dict):
                uid = row['user_id']
                d = row['day_date']
                pi = row['punch_in']
                po = row['punch_out']
                pc = row['pc_name']
                meta = row.get('metadata')
            else:
                uid = row[0]
                d = row[1]
                pi = row[2]
                po = row[3]
                pc = row[4]
                meta = row[5] if len(row) > 5 else None

            # Handle string dates from SQLite
            if isinstance(d, str):
                d = datetime.date.fromisoformat(d)

            day_key = f"{d.day:02d}"
            
            if uid not in data:
                data[uid] = {}

            meta_dict = {}
            if isinstance(meta, dict):
                meta_dict = meta
            elif isinstance(meta, str) and meta.strip():
                try:
                    meta_dict = json.loads(meta)
                except json.JSONDecodeError:
                    meta_dict = {}
            
            # Format time strings
            if hasattr(pi, 'strftime'):
                in_str = pi.strftime("%H:%M")
            elif isinstance(pi, str) and pi:
                in_str = pi[:5]
            else:
                in_str = ""

            if hasattr(po, 'strftime'):
                out_str = po.strftime("%H:%M")
            elif isinstance(po, str) and po:
                out_str = po[:5]
            else:
                out_str = ""

            data[uid][day_key] = {
                "in": in_str,
                "out": out_str,
                "pc": pc,
                "wfh": bool(meta_dict.get("wfh", False)),
                "auto_logout": bool(meta_dict.get("auto_logout", False))
            }
            
        return data

    def update_record(self, user_name, year, month, day, in_time, out_time):
        """Admin update of a record."""
        try:
            user_id = str(user_name).lower().strip()
            if not user_id:
                return False, "User is required"

            target_date = datetime.date(int(year), int(month), int(day))

            p_in = in_time.strip() if isinstance(in_time, str) and in_time.strip() else None
            p_out = out_time.strip() if isinstance(out_time, str) and out_time.strip() else None

            meta_json = json.dumps({
                "admin_edit": True,
                "edited_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

            if _is_postgres(self.db):
                update_sql = """
                UPDATE attendance_log
                SET punch_in = %s,
                    punch_out = %s,
                    pc_name = 'ADMIN_EDIT',
                    metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                WHERE user_id = %s AND day_date = %s
                """
            else:
                update_sql = """
                UPDATE attendance_log
                SET punch_in = %s,
                    punch_out = %s,
                    pc_name = 'ADMIN_EDIT',
                    metadata = json_patch(COALESCE(metadata, '{}'), %s)
                WHERE user_id = %s AND day_date = %s
                """

            updated_rows = self.db.execute_query(
                update_sql,
                (p_in, p_out, meta_json, user_id, target_date),
                fetch="rowcount",
            )
            if updated_rows and int(updated_rows) > 0:
                return True, "Updated"

            insert_sql = """
            INSERT INTO attendance_log (user_id, day_date, punch_in, punch_out, pc_name, metadata)
            VALUES (%s, %s, %s, %s, 'ADMIN_EDIT', %s)
            """
            inserted = self.db.execute_update(insert_sql, (user_id, target_date, p_in, p_out, meta_json))
            if inserted:
                return True, "Inserted"
            return False, "Database rejected attendance edit"
        except Exception as e:
            logger.error(f"Update failed: {e}")
            return False, str(e)

    def sync_attendance(self, user_id, user_name, action, timestamp=None) -> bool:
        """Sync attendance event to central database."""
        try:
            act = "in" if str(action).lower() in ("login", "in") else "out"
            self.log_action(user_name=user_id or user_name, action=act)
            return True
        except Exception as e:
            logger.error(f"sync_attendance error: {e}")
            return False

    def get_team_overview(self):
        """Get overview of today's attendance across the team."""
        try:
            today_str = datetime.date.today().isoformat()
            rows = self.db.execute_query(
                "SELECT user_id, day_date, punch_in, punch_out, pc_name FROM attendance_log WHERE day_date = %s",
                (today_str,),
                fetch="all"
            ) or []
            return rows
        except Exception as e:
            logger.error(f"get_team_overview error: {e}")
            return []

    def is_user_active(self, user_id: str) -> bool:
        """Check if user has punched in and not yet punched out today."""
        try:
            today_str = datetime.date.today().isoformat()
            uid = str(user_id).lower().strip()
            row = self.db.execute_query(
                "SELECT id FROM attendance_log WHERE user_id = %s AND day_date = %s AND punch_out IS NULL",
                (uid, today_str),
                fetch="one"
            )
            return bool(row)
        except Exception as e:
            logger.error(f"is_user_active error: {e}")
            return False
