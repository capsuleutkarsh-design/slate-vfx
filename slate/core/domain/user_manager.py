import hashlib
import bcrypt
import logging
import json
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Any
from ..infra.server_hub import ServerHub
from ..infra.audit_logger import AuditLogger
from slate.utils.safe_json import SafeJsonIO

class UserManager:
    """
    Centralized User Management backed by native SQL (PostgreSQL/SQLite).
    No file locking, purely relational with JSON fallback for data migration.
    """
    def __init__(self, db=None):
        """
        db is the backend to use. Left out, it is the studio's own - which is
        what the application always wants. Every repository in core/infra takes
        it the same way, and for the same reason: without it there is no way to
        exercise this class against a database of your own, and the user record
        is not something to learn about in production.
        """
        self._db = db
        self.hub = ServerHub()
        self.audit = AuditLogger()
        self.users_file = self.hub.get_users_file()
        self.roles_file = self.hub.get_config_dir() / "roles.json"

        self._ensure_schema()
        self._run_migration()
        self._ensure_default_roles()
        self._ensure_essential_accounts()

    def _get_db(self):
        if self._db is not None:
            return self._db
        from ..infra.database_manager import database_manager
        return database_manager

    def _ensure_schema(self):
        """Create ut_users and ut_roles tables on the active database engine."""
        db = self._get_db()
        try:
            db.execute_update("""
                CREATE TABLE IF NOT EXISTS ut_roles (
                    role_name TEXT PRIMARY KEY,
                    permissions TEXT
                )
            """)
            db.execute_update("""
                CREATE TABLE IF NOT EXISTS ut_users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    display_name TEXT DEFAULT '',
                    job_title TEXT DEFAULT '',
                    roles TEXT,
                    profile_pic_path TEXT DEFAULT '',
                    last_synced TEXT
                )
            """)
        except Exception as e:
            logging.error(f"Failed to initialize Auth Schema: {e}")

    def _run_migration(self):
        """
        One-time migration from users.json/roles.json to SQL database.
        Wrapped in atomic transaction to prevent data loss.
        """
        db = self._get_db()
        
        # If JSON files don't exist, just ensure admin exists and return
        if not self.users_file.exists() and not self.roles_file.exists():
            res = db.execute_query("SELECT count(*) as c FROM ut_users", fetch="one")
            if res and res.get('c', 0) == 0:
                logging.info("Database is empty and no JSON config found. Creating default users.")
                try:
                    self._create_default_roles_sql(db)
                    self._create_default_users_sql(db)
                except Exception as e:
                    logging.error(f"Failed to create default users: {e}")
                    self._ensure_admin_exists()
            return # Nothing to migrate
            
        logging.info("Starting Auth migration from JSON to SQL (or resuming failed migration)...")


        
        try:
            # 1. Migrate Roles
            if self.roles_file.exists():
                try:
                    with open(self.roles_file, 'r', encoding='utf-8') as f:
                        roles_data = json.load(f)
                except Exception as e:
                    logging.error(f"Failed to parse {self.roles_file}: {e}")
                    roles_data = {}
                    
                roles_config = roles_data.get('roles', {})
                for role_name, permissions in roles_config.items():
                    perm_str = json.dumps(permissions)
                    db.execute_update("DELETE FROM ut_roles WHERE role_name=%s", (role_name,))
                    db.execute_update(
                        "INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)", 
                        (role_name, perm_str)
                    )
            else:
                self._create_default_roles_sql(db)

            # 2. Migrate Users
            if self.users_file.exists():
                try:
                    with open(self.users_file, 'r', encoding='utf-8') as f:
                        users_data = json.load(f)
                except Exception as e:
                    logging.error(f"Failed to parse {self.users_file}: {e}")
                    users_data = {}
                    
                users_dict = users_data.get('users', {})
                
                for username, data in users_dict.items():
                    uid = username.strip()
                    # Extract data with fallbacks
                    display_name = data.get('display_name', uid)
                    job_title = data.get('job_title', '')
                    profile_pic = data.get('profile_pic_path', '')
                    password_hash = data.get('password_hash', '')
                    
                    roles = data.get('roles', [])
                    if 'role' in data and not roles:
                        roles = [data['role']]
                    elif not roles:
                        roles = ["Artist"]
                    
                    roles_str = json.dumps(roles)
                    
                    db.execute_update("DELETE FROM ut_users WHERE username=%s", (uid,))
                    db.execute_update(
                        "INSERT INTO ut_users (username, password_hash, display_name, job_title, roles, profile_pic_path) VALUES (%s, %s, %s, %s, %s, %s)",
                        (uid, password_hash, display_name, job_title, roles_str, profile_pic)
                    )
            else:
                self._create_default_users_sql(db)
            
            # 3. Rename files after successful transaction (Best effort)
            if self.roles_file.exists():
                try:
                    self.roles_file.rename(self.roles_file.with_suffix('.json.migrated'))
                except Exception as e:
                    logging.warning(f"Could not rename roles.json: {e}. Safe to ignore since SQL is populated.")
                    
            if self.users_file.exists():
                try:
                    self.users_file.rename(self.users_file.with_suffix('.json.migrated'))
                except Exception as e:
                    logging.warning(f"Could not rename users.json: {e}. Safe to ignore since SQL is populated.")
                
            logging.info("Auth migration completed successfully.")
            
        except Exception as e:
            logging.error(f"Auth migration failed (rolled back): {e}")
            # If migration fails and tables are empty, populate defaults so we aren't locked out.
            self._ensure_admin_exists()

    # Roles every studio needs. Adding one here makes it appear on existing
    # installations at next start, without touching roles already configured.
    DEFAULT_ROLE_PERMISSIONS = {
        "Coordinator": ["Folder Creator", "Move/Scan", "Dashboard", "Reports",
                        "Stock Browser", "Rename Tool", "Settings",
                        "Attendance", "Shot Review"],
        # A lead runs a department, not the ingest: no Build & Ingest.
        "Lead": ["Dashboard", "Reports", "Stock Browser", "Rename Tool",
                 "Settings", "Attendance", "Shot Review"],
    }

    def _ensure_default_roles(self):
        """
        Create any missing default role. Existing roles are left exactly as
        they are - this never overwrites permissions someone has customised.
        """
        try:
            db = self._get_db()
            rows = db.execute_query("SELECT role_name FROM ut_roles", fetch="all") or []
            existing = {str(r["role_name"]).strip().lower() for r in rows}

            for role_name, permissions in self.DEFAULT_ROLE_PERMISSIONS.items():
                if role_name.lower() in existing:
                    continue
                db.execute_update(
                    "INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                    (role_name, json.dumps(permissions)),
                )
                logging.info("Created missing default role: %s", role_name)
        except Exception as exc:
            logging.warning("Could not ensure default roles: %s", exc)

    def _create_default_roles_sql(self, db):
        defaults = {
            "Developer": ["ALL", "Admin Panel", "Tester Panel"],
            # "Shot Review" is the Timeline Viewer: the supervisor reviews lineups.
            "Supervisor": ["Folder Creator", "Move/Scan", "Reports", "Stock Browser", "Rename Tool", "Dashboard", "Settings", "Attendance", "Admin Panel", "Image Editor", "Shot Review"],
            "Coordinator": ["Folder Creator", "Move/Scan", "Dashboard", "Reports", "Stock Browser", "Rename Tool", "Settings", "Attendance", "Shot Review"],
            # A lead runs a department, not the ingest: no Build & Ingest.
            "Lead": ["Dashboard", "Reports", "Stock Browser", "Rename Tool", "Settings", "Attendance", "Shot Review"],
            "Artist": ["Stock Browser", "Rename Tool", "Dashboard", "Settings", "Attendance", "Image Editor"],
            "Tester": ["Folder Creator", "Move/Scan", "Rename Tool", "Stock Browser", "Tester Panel", "Settings", "Image Editor"]
        }
        for role_name, permissions in defaults.items():
            db.execute_update("DELETE FROM ut_roles WHERE role_name=%s", (role_name,))
            db.execute_update(
                "INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                (role_name, json.dumps(permissions))
            )

    def _create_default_users_sql(self, db):
        defaults = [
            ("admin", "admin123", ["Developer"], "System Admin", "Dev"),
            ("artist", "artist123", ["Artist"], "Test Artist", "Roto"),
            ("tester", "tester123", ["Tester"], "QA Tester", "QA"),
        ]
        for uid, pw, roles, disp, job in defaults:
            pw_hash = self._hash_password(pw)
            db.execute_update("DELETE FROM ut_users WHERE username=%s", (uid,))
            db.execute_update(
                "INSERT INTO ut_users (username, password_hash, display_name, job_title, roles) VALUES (%s, %s, %s, %s, %s)",
                (uid, pw_hash, disp, job, json.dumps(roles))
            )

    def _ensure_essential_accounts(self):
        """Guarantee essential admin/dev accounts exist so developers and administrators are never locked out."""
        db = self._get_db()
        try:
            # Ensure Developer role exists with ALL permissions
            dev_role = db.execute_query("SELECT role_name FROM ut_roles WHERE role_name='Developer'", fetch="one")
            if not dev_role:
                db.execute_update(
                    "INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                    ("Developer", json.dumps(["ALL"]))
                )

            # Check for admin
            admin_row = db.execute_query("SELECT username FROM ut_users WHERE username='admin'", fetch="one")
            if not admin_row:
                logging.info("Injecting default admin user...")
                pw_hash = self._hash_password("admin123")
                db.execute_update(
                    "INSERT INTO ut_users (username, password_hash, display_name, job_title, roles) VALUES (%s, %s, %s, %s, %s)",
                    ("admin", pw_hash, "System Admin", "Dev", json.dumps(["Developer"]))
                )

            # EMP0012 used to be re-created here on every start, with a known
            # password and Developer rights, so deleting it on the Users tab
            # did nothing at all - it was back on the next launch. One
            # administrator account is enough to guarantee nobody is locked
            # out; a second one nobody asked for is an account nobody watches.
        except Exception as e:
            logging.error(f"Failed to ensure essential accounts: {e}")

    def _ensure_admin_exists(self):
        """Backward-compatible alias for _ensure_essential_accounts."""
        self._ensure_essential_accounts()

    # The accounts a brand new database is seeded with, and nothing else.
    SEEDED_ACCOUNTS = frozenset({"admin", "artist", "tester"})

    def is_fresh_seed(self) -> bool:
        """
        Whether nobody has made an account yet.

        True when the user table holds exactly the seeded defaults. The login
        screen uses it to say "this is a new studio, sign in as admin" instead
        of "invalid credentials" - which is what somebody typing the account
        they had on the old server was told, three times, with no way to know
        that the account they needed was a different one.
        """
        try:
            rows = self._get_db().execute_query(
                "SELECT username FROM ut_users", fetch="all") or []
        except Exception as exc:
            logging.debug("Could not tell whether the database is freshly seeded: %s", exc)
            return False
        names = {str(r.get("username") or "").strip().lower() for r in rows}
        return bool(names) and names <= self.SEEDED_ACCOUNTS

    # --- PASSWORD HASHING ---

    def _hash_password(self, password: str) -> str:
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        return hashed.decode('utf-8')

    def _check_password(self, stored_hash: str, password: str) -> bool:
        # 1. Plaintext fallback (for manually edited users.json during migration)
        # Ensure it's not a bcrypt hash or a 64-char hex SHA256 hash
        if not (stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$") or 
                (len(stored_hash) == 64 and all(c in "0123456789abcdefABCDEF" for c in stored_hash))):
            if stored_hash == password:
                return True
            
        # 2. Bcrypt
        try:
            if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
                return bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
        except ValueError:
            pass
            
        # 3. SHA256 legacy
        legacy_hash = hashlib.sha256(str(password).encode()).hexdigest()
        return stored_hash == legacy_hash

    # --- DOMAIN API ---

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        db = self._get_db()
        search_id = username.strip()
        
        # Try exact match first
        user_row = db.execute_query("SELECT * FROM ut_users WHERE username=%s", (search_id,), fetch="one")
        
        if not user_row:
            # Universal case-insensitive match across SQLite and PostgreSQL
            user_row = db.execute_query("SELECT * FROM ut_users WHERE LOWER(username) = LOWER(%s)", (search_id,), fetch="one")
            
        if not user_row:
            logging.warning(f"Authentication failed: User '{search_id}' not found")
            return None
            
        uid = user_row['username']
        stored_hash = user_row['password_hash']
        
        if self._check_password(stored_hash, password):
            logging.info(f"Authentication successful for user '{uid}'")
            try:
                roles_raw = user_row.get('roles', '["Artist"]')
                if isinstance(roles_raw, list):
                    roles = roles_raw
                elif isinstance(roles_raw, str):
                    try:
                        roles = json.loads(roles_raw)
                        if isinstance(roles, str):
                            roles = [roles]
                    except Exception:
                        roles = [roles_raw] if roles_raw else ["Artist"]
                else:
                    roles = ["Artist"]
            except Exception:
                roles = ["Artist"]
                
            return {
                "user_id": uid,
                "username": uid,
                "display_name": user_row.get('display_name') or uid,
                "roles": roles,
                "role": roles[0] if roles else "Artist",
                "job_title": user_row.get('job_title', ''),
                "avatar": user_row.get('profile_pic_path', '')
            }
        else:
            logging.warning(f"Authentication failed: Invalid password for user '{uid}'")
            return None

    @property
    def users(self) -> Dict[str, Dict[str, Any]]:
        """Backward-compatibility property returning dictionary of users."""
        return self.get_all_users()

    def load_users(self):
        """Backward-compatibility stub."""
        pass

    def get_all_users(self) -> Dict[str, Dict[str, Any]]:
        db = self._get_db()
        rows = db.execute_query("SELECT * FROM ut_users", fetch="all") or []
        users_dict = {}
        for r in rows:
            uid = r['username']
            try:
                roles_raw = r.get('roles', '[]')
                if isinstance(roles_raw, list):
                    roles = roles_raw
                elif isinstance(roles_raw, str):
                    try:
                        roles = json.loads(roles_raw)
                        if isinstance(roles, str):
                            roles = [roles]
                    except Exception:
                        roles = [roles_raw] if roles_raw else []
                else:
                    roles = []
            except Exception:
                roles = []
                
            users_dict[uid] = {
                "password_hash": r['password_hash'],
                "display_name": r.get('display_name', ''),
                "job_title": r.get('job_title', ''),
                "roles": roles,
                "role": roles[0] if roles else "Artist",
                "profile_pic_path": r.get('profile_pic_path', ''),
                # The employment record. Read with .get so a database that has
                # not run the migration yet reports empty rather than raising.
                "joined_on": r.get('joined_on'),
                "employment": r.get('employment') or '',
                "reports_to": r.get('reports_to') or '',
                "location": r.get('location') or '',
                "last_day": r.get('last_day'),
            }
        return users_dict

    # The employment record, as opposed to the login. Each of these is read by
    # something that had nowhere to read it from: accrual needs joined_on, the
    # holiday calendar needs location, supervisor scoping needs reports_to, and
    # offboarding needs last_day. They are keyword-only and default to None,
    # which means "leave whatever is there alone" - so the existing callers
    # that know nothing about them cannot blank them out.
    EMPLOYMENT_FIELDS = ("joined_on", "employment", "reports_to", "location", "last_day")

    def add_user(self, u: str, p: str, roles: Any = None, n: str = "", j: str = "",
                 pic: str = "", r: Any = None, *, joined_on=None, employment=None,
                 reports_to=None, location=None, last_day=None) -> bool:
        if roles is None and r is not None:
            roles = r
        if roles is None:
            roles = ["Artist"]
        if isinstance(roles, str):
            roles = [roles]

        db = self._get_db()
        uid = u.strip()

        # Check if user exists (case-insensitive)
        existing = db.execute_query("SELECT username, password_hash, profile_pic_path, display_name, job_title FROM ut_users WHERE LOWER(username)=LOWER(%s)", (uid,), fetch="one")

        if p == "KEEP_OLD":
            if existing:
                pw_hash = existing['password_hash']
                if not pic:
                    pic = existing.get('profile_pic_path', '')
            else:
                pw_hash = self._hash_password("password123")
        else:
            pw_hash = self._hash_password(p)

        roles_str = json.dumps(roles if isinstance(roles, list) else [roles])
        display_name = n.strip() if n and n.strip() else (existing.get('display_name', uid) if existing else uid)
        job_title = j.strip() if j and j.strip() else (existing.get('job_title', '') if existing else '')

        supplied = {
            "joined_on": joined_on,
            "employment": employment,
            "reports_to": reports_to,
            "location": location,
            "last_day": last_day,
        }
        supplied = {field: value for field, value in supplied.items() if value is not None}

        if existing:
            # UPDATE existing row using the stored username
            target_username = existing['username']
            sets = ["password_hash=%s", "display_name=%s", "job_title=%s",
                    "roles=%s", "profile_pic_path=%s"]
            values = [pw_hash, display_name, job_title, roles_str, pic.strip()]
            for field, value in supplied.items():
                sets.append(field + "=%s")
                values.append(value)
            values.append(target_username)
            success = db.execute_update(
                "UPDATE ut_users SET " + ", ".join(sets) + " WHERE username=%s",
                tuple(values)
            )
        else:
            # INSERT
            columns = ["username", "password_hash", "display_name", "job_title",
                       "roles", "profile_pic_path"]
            values = [uid, pw_hash, display_name, job_title, roles_str, pic.strip()]
            for field, value in supplied.items():
                columns.append(field)
                values.append(value)
            success = db.execute_update(
                "INSERT INTO ut_users (" + ", ".join(columns) + ") VALUES ("
                + ", ".join(["%s"] * len(columns)) + ")",
                tuple(values)
            )

        if success:
            self.audit.log_user_change("System", uid, f"Updated roles: {roles}")
        return success

    def update_user(self, username: str, **kwargs) -> bool:
        """Update specific fields of an existing user or create if not exists."""
        users = self.get_all_users()
        existing = users.get(username.strip(), {})
        
        password = kwargs.get("password", "KEEP_OLD")
        roles = kwargs.get("roles", existing.get("roles", ["Artist"]))
        display_name = kwargs.get("display_name", existing.get("display_name", username))
        job_title = kwargs.get("job_title", existing.get("job_title", ""))
        pic = kwargs.get("profile_pic_path", existing.get("profile_pic_path", ""))

        extras = {field: kwargs[field] for field in self.EMPLOYMENT_FIELDS
                  if kwargs.get(field) is not None}

        return self.add_user(username, password, roles, display_name, job_title,
                             pic, **extras)

    def delete_user(self, u: str) -> bool:
        db = self._get_db()
        success = db.execute_update("DELETE FROM ut_users WHERE LOWER(username)=LOWER(%s)", (u.strip(),))
        if success:
            self.audit.log_user_change("System", u, "Deleted")
        return success

    def get_available_roles(self) -> List[str]:
        db = self._get_db()
        rows = db.execute_query("SELECT role_name FROM ut_roles", fetch="all") or []
        return [r['role_name'] for r in rows]

    @property
    def roles_config(self) -> Dict[str, List[str]]:
        """Backward compatibility property for GUI code expecting self.roles_config dictionary mapping"""
        db = self._get_db()
        rows = db.execute_query("SELECT role_name, permissions FROM ut_roles", fetch="all") or []
        config = {}
        for r in rows:
            try:
                config[r['role_name']] = json.loads(r['permissions'])
            except:
                config[r['role_name']] = []
        return config

    def get_allowed_tabs(self, roles: Any) -> List[str]:
        if not roles: return []
        if isinstance(roles, str): roles = [roles]
        
        db = self._get_db()
        allowed_tabs = set()
        
        # We fetch all roles to avoid N+1 queries or complex IN clause for now. It's a small table.
        rows = db.execute_query("SELECT role_name, permissions FROM ut_roles", fetch="all") or []
        role_map = {}
        for r in rows:
            try:
                role_map[r['role_name'].lower()] = json.loads(r['permissions'])
            except Exception:
                pass
                
        for role in roles:
            tabs = role_map.get(role.lower(), [])
            if "ALL" in tabs:
                return ["ALL"]
            allowed_tabs.update(tabs)
            
        return list(allowed_tabs)

    def update_role_permissions(self, role: str, tabs: List[str]) -> bool:
        db = self._get_db()
        if not tabs:
            tabs = ["Settings"]
        tabs_str = json.dumps(tabs)
        # Upsert
        existing = db.execute_query("SELECT 1 FROM ut_roles WHERE role_name=%s", (role,), fetch="one")
        if existing:
            return db.execute_update("UPDATE ut_roles SET permissions=%s WHERE role_name=%s", (tabs_str, role))
        else:
            return db.execute_update("INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)", (role, tabs_str))

    def create_role(self, role: str, tabs: List[str]) -> bool:
        return self.update_role_permissions(role, tabs)

    def delete_role(self, role: str) -> bool:
        db = self._get_db()
        return db.execute_update("DELETE FROM ut_roles WHERE role_name=%s", (role,))

    @property
    def users(self) -> Dict[str, Dict[str, Any]]:
        """Backward compatibility property returning dictionary of all users."""
        return self.get_all_users()

    # STUBS for legacy compatibility if external code calls them
    def _sync_to_postgres(self): pass
    def load_users(self): pass
    def save_users(self, users_dict: Optional[Dict[str, Any]] = None) -> bool:
        db = self._get_db()
        if users_dict:
            return db.sync_users(users_dict)
        return True
    def load_roles(self): pass
    def save_roles(self): return True
