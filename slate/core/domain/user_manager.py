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

        # Who is making changes through this manager (set_acting_user). None
        # means Slate itself - seeding, imports run by code, tests - and is not
        # checked; the Users & Roles screen always sets it.
        self.acting_user = None
        self.last_error = ""

        self._ensure_schema()
        self._run_migration()
        self._ensure_default_roles()
        self._upgrade_role_abilities()
        self._upgrade_producer_dashboard()
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
        # Normally added by the workplace migration when the database opens;
        # ut_users may not have existed yet at that moment on a new database.
        try:
            from ..infra.migrations.workplace_schema import _column_exists
            if not _column_exists(db, "ut_users", "must_change_password"):
                db.execute_update("ALTER TABLE ut_users ADD COLUMN must_change_password INTEGER")
        except Exception as e:
            logging.warning("Could not add must_change_password: %s", e)
        # Deactivation (see deactivate_user). Additive, and NULL means active,
        # so every existing account stays exactly as it was.
        try:
            from ..infra.migrations.workplace_schema import _column_exists
            for column, kind in (("active", "INTEGER"), ("deactivated_on", "TEXT"),
                                 ("deactivated_by", "TEXT")):
                if not _column_exists(db, "ut_users", column):
                    db.execute_update(f"ALTER TABLE ut_users ADD COLUMN {column} {kind}")
        except Exception as e:
            logging.warning("Could not add the account status columns: %s", e)

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
        Create each default role once. Existing roles are left exactly as they
        are - this never overwrites permissions someone has customised - and a
        role the studio deleted stays deleted: ut_role_seeds remembers which
        roles were already created, so they are not brought back at next start.
        """
        from slate.core.domain.permissions_catalog import STUDIO_ROLES
        defaults = {**self.DEFAULT_ROLE_PERMISSIONS, **STUDIO_ROLES}
        try:
            db = self._get_db()
            db.execute_update("CREATE TABLE IF NOT EXISTS ut_role_seeds (role_name TEXT PRIMARY KEY)")
            rows = db.execute_query("SELECT role_name FROM ut_roles", fetch="all") or []
            existing = {str(r["role_name"]).strip().lower() for r in rows}
            seeded_rows = db.execute_query("SELECT role_name FROM ut_role_seeds", fetch="all") or []
            seeded = {str(r["role_name"]).strip().lower() for r in seeded_rows}

            for role_name, permissions in defaults.items():
                key = role_name.lower()
                if key in seeded:
                    continue
                if key not in existing:
                    db.execute_update(
                        "INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)",
                        (role_name, json.dumps(permissions)),
                    )
                    logging.info("Created default role: %s", role_name)
                db.execute_update("INSERT INTO ut_role_seeds (role_name) VALUES (%s)", (key,))
        except Exception as exc:
            logging.warning("Could not ensure default roles: %s", exc)

    # ------------------------------------------------------ role upgrades
    #
    # New abilities split rights that used to come with a tab or a role name.
    # Each upgrade runs once per database (ut_role_upgrades remembers it) and
    # only ever adds, so no role loses anything it could do before. A role the
    # studio changes afterwards is left alone.
    ROLE_UPGRADES = ("2026-09-abilities",)

    # Role names (lower-case) of the artist kind, and of supervisors, heads and
    # coordinators, as far as a name can tell.
    _ARTIST_WORDS = ("artist", "compositor", "dmp", "generalist", "lead")
    _OVERSEER_WORDS = ("supervisor", "coordinator", "producer", "head", "editor")

    def _upgrade_role_abilities(self):
        from slate.core.domain.permissions_catalog import ability_key, has_all
        try:
            db = self._get_db()
            db.execute_update(
                "CREATE TABLE IF NOT EXISTS ut_role_upgrades ("
                "name TEXT PRIMARY KEY, applied_at TEXT)")
            done = {str(r["name"]) for r in
                    (db.execute_query("SELECT name FROM ut_role_upgrades", fetch="all") or [])}
            if "2026-09-abilities" in done:
                return
            rows = db.execute_query("SELECT role_name, permissions FROM ut_roles", fetch="all") or []
            for row in rows:
                role = str(row["role_name"])
                try:
                    perms = list(json.loads(row["permissions"] or "[]"))
                except Exception:
                    continue
                if has_all(perms):
                    continue
                added = self.upgraded_permissions(role, perms)
                if added != perms:
                    db.execute_update("UPDATE ut_roles SET permissions=%s WHERE role_name=%s",
                                      (json.dumps(added), role))
                    logging.info("Role %s upgraded: %s", role,
                                 [p for p in added if p not in perms])
            from datetime import datetime
            db.execute_update("INSERT INTO ut_role_upgrades (name, applied_at) VALUES (%s, %s)",
                              ("2026-09-abilities", datetime.now().isoformat(timespec="seconds")))
            self._forget_cached_abilities()
        except Exception as exc:
            logging.warning("Could not upgrade role abilities: %s", exc)

    def _upgrade_producer_dashboard(self):
        """
        Once per database: a role named Producer gets the Dashboard tab.
        access.json gives producers dashboard_write, but a Producer role made
        before had no Dashboard tab, so they never saw it. Only adds.
        """
        from slate.core.domain.permissions_catalog import has_all
        try:
            db = self._get_db()
            db.execute_update(
                "CREATE TABLE IF NOT EXISTS ut_role_upgrades ("
                "name TEXT PRIMARY KEY, applied_at TEXT)")
            done = {str(r["name"]) for r in
                    (db.execute_query("SELECT name FROM ut_role_upgrades", fetch="all") or [])}
            if "2026-10-producer-dashboard" in done:
                return
            rows = db.execute_query("SELECT role_name, permissions FROM ut_roles", fetch="all") or []
            for row in rows:
                role = str(row["role_name"])
                if "producer" not in role.strip().lower():
                    continue
                try:
                    perms = list(json.loads(row["permissions"] or "[]"))
                except Exception:
                    continue
                if has_all(perms) or "Dashboard" in perms:
                    continue
                db.execute_update("UPDATE ut_roles SET permissions=%s WHERE role_name=%s",
                                  (json.dumps(perms + ["Dashboard"]), role))
                logging.info("Role %s upgraded: Dashboard tab", role)
            from datetime import datetime
            db.execute_update("INSERT INTO ut_role_upgrades (name, applied_at) VALUES (%s, %s)",
                              ("2026-10-producer-dashboard", datetime.now().isoformat(timespec="seconds")))
        except Exception as exc:
            logging.warning("Could not give producer roles the Dashboard tab: %s", exc)

    @classmethod
    def upgraded_permissions(cls, role, perms):
        """
        What a role's stored list becomes in the 2026-09 upgrade. Only adds:

          Scheduling tab     -> can:schedule_write  (it could edit before)
          Bidding tab        -> can:approve_bid     (it could mark Won/Lost)
          IT tab             -> can:manage_it       (the tab key used to give the IT queue)
          artist-type roles  -> can:assignable      (they were in the Artist pickers)
          supervisors, coordinators, producers, heads, editors
                             -> can:dashboard_view_all (they saw every shot)
        """
        from slate.core.domain.permissions_catalog import ability_key, abilities_in
        result = list(perms or [])
        have = abilities_in(result)
        name = str(role or "").strip().lower()

        def add(ability):
            if ability not in have:
                result.append(ability_key(ability))
                have.add(ability)

        if "Scheduling" in result:
            add("schedule_write")
        if "Bidding" in result:
            add("approve_bid")
        if "IT" in result:
            add("manage_it")
        if any(word in name for word in cls._ARTIST_WORDS) or name == "cg":
            add("assignable")
        if any(word in name for word in cls._OVERSEER_WORDS):
            add("dashboard_view_all")
        return result

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

    # ------------------------------------------------------------ passwords
    #
    # One rule for spaces, everywhere a password is typed or set: spaces at the
    # start or end are trimmed. Sign-in used to trim and changing a password did
    # not, so "secret99 " could be saved and then never typed again - and a
    # password of six spaces was accepted and locked the account out for good.

    @staticmethod
    def clean_password(password) -> str:
        """The password as it is stored and checked: trimmed of outer spaces."""
        return str(password or "").strip()

    def password_problem(self, password) -> Optional[str]:
        """Why this new password cannot be used, or None when it is fine."""
        cleaned = self.clean_password(password)
        if not cleaned:
            return "A password cannot be empty or only spaces."
        if len(cleaned) < self.MIN_PASSWORD_LENGTH:
            return f"Use at least {self.MIN_PASSWORD_LENGTH} characters."
        return None

    def _password_matches(self, stored_hash, password) -> bool:
        cleaned = self.clean_password(password)
        if cleaned and self._check_password(stored_hash, cleaned):
            return True
        # A password saved with outer spaces before the rule above existed
        # still opens its account when typed exactly.
        raw = str(password or "")
        return bool(raw.strip()) and raw != cleaned and self._check_password(stored_hash, raw)

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
        
        if self._password_matches(stored_hash, password):
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
                "avatar": user_row.get('profile_pic_path', ''),
                # Imported with a shared first password: choose a new one now.
                "must_change_password": bool(user_row.get('must_change_password') or 0),
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
                # Deactivated, or past their last day (see deactivate_user).
                "active": self._flag_active(r),
                "deactivated_on": r.get('deactivated_on'),
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

        # Giving roles, or changing an account, only within what the acting
        # person may grant (Users & Roles sets acting_user).
        self._check_account_change(uid, roles)

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
            # Trimmed like every other password, and never empty: an account
            # whose password is spaces can never be signed in to.
            if not self.clean_password(p):
                raise ValueError("A password cannot be empty or only spaces.")
            pw_hash = self._hash_password(self.clean_password(p))

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
            why = self._last_admin_refusal(
                self._set_fields(target_username, roles=list(roles if isinstance(roles, list) else [roles]),
                                 **({"last_day": supplied["last_day"]} if "last_day" in supplied else {})))
            if why:
                self._refuse_last_admin(why)
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
            self.audit.log_user_change(self._actor(), uid, f"Updated roles: {roles}")
        return bool(success)

    MIN_PASSWORD_LENGTH = 6

    def set_must_change_password(self, username: str, required: bool) -> bool:
        db = self._get_db()
        return bool(db.execute_update(
            "UPDATE ut_users SET must_change_password=%s WHERE LOWER(username)=LOWER(%s)",
            (1 if required else 0, username.strip())))

    def change_own_password(self, username: str, current: str, new: str):
        """
        A person choosing their own password. Returns (ok, message).

        Needs the current one, so a workstation left signed in cannot be used
        to take over the account. Clears "must change password".
        """
        if not self.authenticate(username, current):
            return False, "The current password is not right."
        problem = self.password_problem(new)
        if problem:
            return False, problem
        new = self.clean_password(new)
        if new == self.clean_password(current):
            return False, "Choose a password different from the current one."
        db = self._get_db()
        ok = db.execute_update(
            "UPDATE ut_users SET password_hash=%s, must_change_password=0 WHERE LOWER(username)=LOWER(%s)",
            (self._hash_password(new), username.strip()))
        if ok:
            self.audit.log_user_change(username, username, "Changed own password")
            return True, "Password changed."
        return False, "The password could not be saved. Try again."

    # "Clear this field" for update_user, as opposed to None ("leave it alone").
    # Reports to, Employment, Joined and Location could never be emptied: the
    # dialog sent None for "Nobody" / "Not recorded", which meant "unchanged".
    CLEAR = "\x00clear"

    PROFILE_FIELDS = ("display_name", "job_title", "profile_pic_path") + EMPLOYMENT_FIELDS

    def _row(self, username):
        row = self._get_db().execute_query(
            "SELECT * FROM ut_users WHERE LOWER(username)=LOWER(%s)",
            (str(username or "").strip(),), fetch="one")
        return dict(row) if row else None

    def username_problem(self, username: str) -> str:
        """Why this cannot be a new username, or ''. The import's rule, everywhere."""
        from slate.core.domain.user_import import USERNAME_PATTERN, USERNAME_RULE
        uid = str(username or "").strip().lower()
        if not uid:
            return "Enter a username."
        if not USERNAME_PATTERN.match(uid):
            return "A username is " + USERNAME_RULE + "."
        if self._row(uid):
            return "Username already taken."
        return ""

    def create_user(self, username: str, password: str, roles, display_name: str = "",
                    job_title: str = "", **fields):
        """
        Add a new account - and only that. Returns (ok, message).

        'Add New User' used add_user, which updates an account that already
        exists: adding 'HR.Kavya' with role Developer took over hr.kavya,
        password included. A username in use (in any case) is refused here,
        and the username must follow the same rule as an import.
        """
        uid = str(username or "").strip().lower()
        self.last_error = ""
        problem = self.username_problem(uid)
        if problem:
            self.last_error = problem
            return False, problem
        roles = [roles] if isinstance(roles, str) else list(roles or [])
        if not roles:
            return False, "Pick at least one role."
        self._check_account_change(uid, roles)
        manager = fields.get("reports_to")
        if manager not in (None, self.CLEAR) and str(manager).strip():
            why = self.reports_to_problem(uid, manager)
            if why:
                return False, why
        from slate.core.domain.onboarding_service import employment_value
        if fields.get("employment") not in (None, self.CLEAR):
            fields["employment"] = employment_value(fields["employment"])
        columns = ["username", "password_hash", "display_name", "job_title", "roles",
                   "profile_pic_path"]
        values = [uid, self._hash_password(password), (display_name or "").strip() or uid,
                  (job_title or "").strip(), json.dumps(roles), ""]
        for field in self.EMPLOYMENT_FIELDS:
            value = fields.get(field)
            if value in (None, self.CLEAR) or str(value).strip() == "":
                continue
            columns.append(field)
            values.append(value)
        result = self._get_db().execute_update(
            "INSERT INTO ut_users (" + ", ".join(columns) + ") VALUES ("
            + ", ".join(["%s"] * len(columns)) + ")", tuple(values))
        if not result:
            message = "The account could not be saved: %s" % (getattr(result, "error", "") or "refused")
            self.last_error = message
            return False, message
        self.audit.log_user_change(self._actor(), uid, "Created with roles %s" % ", ".join(roles))
        self._forget_people()
        return True, "Added %s (%s)" % ((display_name or "").strip() or uid, uid)

    def update_user(self, username: str, **kwargs) -> bool:
        """
        Change an existing person's profile: name, department, roles, and the
        employment record. Never the password (that is Reset Password).

        A field left out, or None, is left alone; UserManager.CLEAR empties
        it. Every change is written to the audit log with the old and new
        values and who made it. An account that does not exist is created
        through add_user, as before.
        """
        uid = str(username or "").strip()
        existing = self._row(uid)
        if existing is None:
            password = kwargs.pop("password", "KEEP_OLD")
            return self.add_user(uid, password, kwargs.get("roles"), kwargs.get("display_name", ""),
                                 kwargs.get("job_title", ""), kwargs.get("profile_pic_path", ""),
                                 **{f: kwargs[f] for f in self.EMPLOYMENT_FIELDS
                                    if kwargs.get(f) not in (None, self.CLEAR)})
        if kwargs.get("password") not in (None, "KEEP_OLD"):
            raise ValueError("update_user never changes a password.")
        target = existing["username"]

        sets, values, changes = [], [], []
        roles = kwargs.get("roles")
        if roles is not None:
            roles = [roles] if isinstance(roles, str) else list(roles)
            self._check_account_change(target, roles)
            old_roles = self._parse_roles(existing.get("roles"))
            if [str(r) for r in old_roles] != [str(r) for r in roles]:
                sets.append("roles=%s")
                values.append(json.dumps(roles))
                changes.append("roles %s -> %s" % (", ".join(map(str, old_roles)) or "-",
                                                   ", ".join(map(str, roles)) or "-"))
        else:
            self._check_account_change(target)

        manager = kwargs.get("reports_to")
        if manager not in (None, self.CLEAR) and str(manager).strip():
            why = self.reports_to_problem(target, manager)
            if why:
                self.last_error = why
                from slate.core.domain.access import GrantRefused
                raise GrantRefused(why)
        if kwargs.get("employment") not in (None, self.CLEAR):
            from slate.core.domain.onboarding_service import employment_value
            kwargs["employment"] = employment_value(kwargs["employment"])

        for field in self.PROFILE_FIELDS:
            if field not in kwargs or kwargs[field] is None:
                continue
            new = None if kwargs[field] == self.CLEAR else kwargs[field]
            if isinstance(new, str):
                new = new.strip()
                if field in ("display_name",) and not new:
                    continue                     # a name is never blanked
                if new == "" and field in self.EMPLOYMENT_FIELDS:
                    new = None
            old = existing.get(field)
            if str(old or "")[:10 if field in ("joined_on", "last_day") else None] == str(new or "")[
                    :10 if field in ("joined_on", "last_day") else None]:
                continue
            sets.append(field + "=%s")
            values.append(new)
            changes.append("%s %r -> %r" % (field, str(old or "")[:40], str(new or "")[:40]))

        if not sets:
            return True
        guarded = {}
        if roles is not None:
            guarded["roles"] = roles
        if "last_day" in kwargs and kwargs["last_day"] is not None:
            guarded["last_day"] = None if kwargs["last_day"] == self.CLEAR else kwargs["last_day"]
        if guarded:
            why = self._last_admin_refusal(self._set_fields(target, **guarded))
            if why:
                self._refuse_last_admin(why)
        values.append(target)
        result = self._get_db().execute_update(
            "UPDATE ut_users SET " + ", ".join(sets) + " WHERE username=%s", tuple(values))
        if result:
            self.audit.log_user_change(self._actor(), target, "Changed: " + "; ".join(changes))
            self._forget_people()
        return bool(result)

    @staticmethod
    def _forget_people():
        try:
            from slate.core.domain import people
            people.refresh()
        except Exception:
            pass

    # --------------------------------------------------------- reports to
    def approvers(self) -> List[str]:
        """
        Who can be somebody's 'Reports to': active people who approve leave
        (a supervisor or lead) or keep it (HR). Anybody else - an artist -
        left that person's leave waiting for ever.
        """
        from slate.core.domain import access
        from slate.core.domain.workplace_access import manages_leave
        stored = access._role_permission_lists()
        out = []
        for username, data in (self.get_all_users() or {}).items():
            if not data.get("active", True):
                continue
            roles = data.get("roles") or []
            tabs = set()
            for role in roles:
                tabs.update(stored.get(str(role).strip().lower(), []))
            if access.can(roles, "approve_leave") or manages_leave(roles, tabs):
                out.append(username)
        return sorted(out, key=str.lower)

    def would_create_cycle(self, username: str, manager: str) -> bool:
        """True when manager reports (directly or through others) to username."""
        target = str(username or "").strip().lower()
        seen = set()
        current = str(manager or "").strip().lower()
        users = {u.lower(): d for u, d in (self.get_all_users() or {}).items()}
        while current and current not in seen:
            if current == target:
                return True
            seen.add(current)
            current = str((users.get(current) or {}).get("reports_to") or "").strip().lower()
        return False

    def reports_to_problem(self, username: str, manager: str) -> str:
        """Why manager cannot be username's Reports to, or ''."""
        uid = str(username or "").strip().lower()
        boss = str(manager or "").strip()
        if not boss:
            return ""
        if boss.lower() == uid:
            return "Somebody cannot report to themselves."
        if not self._row(boss):
            return "There is no account called %s." % boss
        if self.would_create_cycle(uid, boss):
            return ("%s already reports to %s (directly or through others), so this "
                    "would make a loop." % (boss, username))
        return ""

    def delete_user(self, u: str) -> bool:
        """
        Remove an account for good - only one that has no history at all (a
        typo, a duplicate). Anyone who has worked here is deactivated instead
        (deactivate_user), so their attendance and leave keep an owner.
        The reason for a refusal is in last_error.
        """
        uid = str(u or "").strip()
        self.last_error = ""
        if self.acting_user and uid.lower() == self.acting_user.lower():
            self.last_error = "You cannot delete your own account."
            return False
        if uid.lower() in self.PROTECTED_ACCOUNTS:
            self.last_error = f"{uid} is a system account and cannot be deleted."
            return False
        try:
            self._check_account_change(uid)
        except PermissionError as exc:
            self.last_error = str(exc)
            return False
        if self.has_history(uid):
            self.last_error = (f"{uid} has attendance, leave or other records, so the account "
                               "is kept. Deactivate it instead.")
            return False
        why = self._last_admin_refusal(self._remove_user(uid))
        if why:
            self.last_error = why
            return False
        db = self._get_db()
        success = db.execute_update("DELETE FROM ut_users WHERE LOWER(username)=LOWER(%s)", (uid,))
        if success:
            self.audit.log_user_change(self._actor(), uid, "Deleted")
        return bool(success)

    # ------------------------------------------- the last administrator
    #
    # Every change that could take away the last way in is checked against the
    # state it would produce (slate.core.security.admin_guard). Here, in the
    # domain layer, so the Users screen, an import and code all meet the same
    # rule. A refusal says why and changes nothing.

    def _last_admin_refusal(self, change) -> str:
        from slate.core.security import admin_guard
        return admin_guard.check(self._get_db(), change)

    def _refuse_last_admin(self, message):
        from slate.core.security.admin_guard import LastAdminRefused
        self.last_error = message
        logging.warning("Refused (last administrator): %s", message)
        raise LastAdminRefused(message)

    @staticmethod
    def _set_fields(username, **fields):
        def change(users, perms):
            from slate.core.security.admin_guard import find
            key = find(users, username)
            if key is not None:
                users[key].update({k: (json.dumps(v) if k == "roles" else v)
                                   for k, v in fields.items()})
        return change

    @staticmethod
    def _remove_user(username):
        def change(users, perms):
            from slate.core.security.admin_guard import find
            key = find(users, username)
            if key is not None:
                users.pop(key)
        return change

    @staticmethod
    def _set_role(role, permissions):
        def change(users, perms):
            perms[str(role).strip().lower()] = list(permissions or [])
        return change

    @staticmethod
    def _rename_role_change(old, new):
        def change(users, perms):
            from slate.core.security.admin_guard import parse_roles
            old_key = str(old).strip().lower()
            perms[str(new).strip().lower()] = perms.pop(old_key, [])
            for record in users.values():
                roles = parse_roles(record.get("roles"))
                record["roles"] = json.dumps(
                    [new if str(r).strip().lower() == old_key else r for r in roles])
        return change

    # ------------------------------------------------------- who is editing
    def set_acting_user(self, username):
        """
        Say who is making changes through this manager. From then on every
        change to accounts and roles is checked against what that person may
        grant (access.can_grant and friends) - here, not only on screen.
        """
        self.acting_user = str(username).strip() if username else None

    def _acting_roles(self):
        if not self.acting_user:
            return None
        row = self._get_db().execute_query(
            "SELECT roles FROM ut_users WHERE LOWER(username)=LOWER(%s)",
            (self.acting_user,), fetch="one")
        return self._parse_roles(row.get("roles") if row else None) if row else []

    @staticmethod
    def _parse_roles(raw):
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                value = json.loads(raw)
                return [value] if isinstance(value, str) else list(value or [])
            except Exception:
                return [raw]
        return []

    def _refuse(self, message):
        from slate.core.domain.access import GrantRefused
        self.last_error = message
        logging.warning("Refused for %s: %s", self.acting_user, message)
        raise GrantRefused(message)

    def _check_account_change(self, username, new_roles=None):
        """
        Raise GrantRefused when the acting person may not change this account
        (or give it these roles). No acting person: no check.
        """
        editor_roles = self._acting_roles()
        if editor_roles is None:
            return
        from slate.core.domain import access
        existing = self._get_db().execute_query(
            "SELECT roles FROM ut_users WHERE LOWER(username)=LOWER(%s)",
            (str(username).strip(),), fetch="one")
        old_roles = self._parse_roles(existing.get("roles")) if existing else []
        target = old_roles if new_roles is None else list(new_roles)
        why = access.role_assignment_refusal(
            editor_roles, old_roles, target, self.get_available_roles(), self.roles_config)
        if why:
            self._refuse(why)

    def _actor(self):
        return self.acting_user or "System"

    def assignable_roles(self) -> List[str]:
        """The roles the acting person may give to somebody (all of them for Slate itself)."""
        roles = self.get_available_roles()
        editor_roles = self._acting_roles()
        if editor_roles is None:
            return roles
        from slate.core.domain import access
        return access.assignable_roles(editor_roles, roles, self.roles_config)

    # ---------------------------------------------------- account lifecycle
    #
    # People leave. Deleting their account (the only option there was) left
    # their attendance and leave rows pointing at nobody, still showing in HR
    # and supervisor queues, and you could delete yourself. An account is now
    # deactivated instead: history kept, hidden from lists and pickers, and
    # restorable. A person whose last day has passed counts as inactive too.
    #
    # Refusing a deactivated person at sign-in belongs to the sign-in code and
    # is not done here.

    # The accounts Slate itself relies on. (Users & Roles also shields EMP0012
    # from a mis-click; here it may still be removed deliberately.)
    PROTECTED_ACCOUNTS = frozenset({"admin", "developer"})

    @staticmethod
    def _flag_active(record) -> bool:
        """Not deactivated and not past the last day - the rule the
        last-administrator guard uses too (admin_guard.account_active)."""
        from slate.core.security.admin_guard import account_active
        return account_active(record)

    def is_active(self, username: str) -> bool:
        row = self._get_db().execute_query(
            "SELECT * FROM ut_users WHERE LOWER(username)=LOWER(%s)",
            (str(username or "").strip(),), fetch="one")
        return bool(row) and self._flag_active(dict(row))

    def active_users(self) -> Dict[str, Dict[str, Any]]:
        """Everybody who has not been deactivated or passed their last day."""
        return {name: data for name, data in self.get_all_users().items() if data.get("active", True)}

    def open_items(self, username: str) -> List[str]:
        """
        What must be handled before somebody is deactivated: leave requests
        still waiting for a decision, and machines still issued to them.
        """
        from slate.core.domain import leave_policy as lp
        db = self._get_db()
        found = []
        wanted = str(username or "").strip()
        try:
            rows = db.execute_query(
                "SELECT status FROM leave_requests WHERE LOWER(user_id)=LOWER(%s)",
                (wanted,), fetch="all") or []
            waiting = sum(1 for r in rows if lp.normalise_status(r["status"]) in (
                lp.STATUS_PENDING_SUPERVISOR, lp.STATUS_PENDING_HR))
            if waiting:
                found.append(f"{waiting} leave request(s) waiting for a decision")
        except Exception as exc:
            logging.debug("Leave requests not checked for %s: %s", wanted, exc)
        try:
            rows = db.execute_query(
                "SELECT machine_name FROM asset_assignments "
                "WHERE LOWER(user_id)=LOWER(%s) AND returned_on IS NULL",
                (wanted,), fetch="all") or []
            machines = sorted({str(r["machine_name"]) for r in rows})
            if machines:
                found.append("machine(s) still issued: " + ", ".join(machines))
        except Exception as exc:
            logging.debug("Issued machines not checked for %s: %s", wanted, exc)
        return found

    def deactivate_user(self, username: str, by: str = None):
        """
        Switch an account off, keeping everything it did. Returns (ok, message).

        Refused for yourself, for the protected system accounts and while the
        person still has leave waiting for a decision or a machine issued.
        """
        uid = str(username or "").strip()
        actor = (by or self.acting_user or "").strip()
        if not uid:
            return False, "Choose somebody to deactivate."
        if actor and uid.lower() == actor.lower():
            return False, "You cannot deactivate your own account."
        if uid.lower() in self.PROTECTED_ACCOUNTS:
            return False, f"{uid} is a system account and cannot be deactivated."
        try:
            self._check_account_change(uid)
        except PermissionError as exc:
            return False, str(exc)
        why = self._last_admin_refusal(self._set_fields(uid, active=0))
        if why:
            self.last_error = why
            return False, why
        waiting = self.open_items(uid)
        if waiting:
            return False, (f"{uid} still has " + "; ".join(waiting)
                           + ". Handle those first, then deactivate the account.")
        from datetime import date
        ok = self._get_db().execute_update(
            "UPDATE ut_users SET active=0, deactivated_on=%s, deactivated_by=%s "
            "WHERE LOWER(username)=LOWER(%s)",
            (date.today().isoformat(), actor or "System", uid))
        if not ok:
            return False, f"{uid} could not be deactivated. Try again."
        self.audit.log_user_change(actor or "System", uid, "Deactivated")
        return True, (f"{uid} is deactivated. Their history is kept, and the account "
                      "can be reactivated at any time.")

    def reactivate_user(self, username: str, by: str = None):
        """Switch a deactivated account back on. Returns (ok, message)."""
        uid = str(username or "").strip()
        actor = (by or self.acting_user or "").strip()
        try:
            self._check_account_change(uid)
        except PermissionError as exc:
            return False, str(exc)
        ok = self._get_db().execute_update(
            "UPDATE ut_users SET active=1, deactivated_on=NULL, deactivated_by=NULL "
            "WHERE LOWER(username)=LOWER(%s)", (uid,))
        if not ok:
            return False, f"{uid} could not be reactivated. Try again."
        self.audit.log_user_change(actor or "System", uid, "Reactivated")
        note = ""
        try:
            row = self._get_db().execute_query(
                "SELECT last_day FROM ut_users WHERE LOWER(username)=LOWER(%s)", (uid,), fetch="one")
            if row and row.get("last_day") and not self._flag_active({"last_day": row.get("last_day")}):
                note = " Their last day is in the past - clear it on Edit User if they are back."
        except Exception:
            pass
        return True, f"{uid} is active again.{note}"

    def has_history(self, username: str) -> bool:
        """Whether anything in the studio's records points at this account."""
        db = self._get_db()
        wanted = str(username or "").strip()
        for table in ("attendance_log", "leave_requests", "asset_assignments",
                      "onboarding_workflows", "it_tickets"):
            column = "submitted_by" if table == "it_tickets" else "user_id"
            try:
                row = db.execute_query(
                    f"SELECT 1 AS x FROM {table} WHERE LOWER({column})=LOWER(%s) LIMIT 1",
                    (wanted,), fetch="one")
            except Exception:
                continue
            if row:
                return True
        return False

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
        editor_roles = self._acting_roles()
        before = self.role_permissions(role)
        if editor_roles is not None:
            from slate.core.domain import access
            why = access.role_change_refusal(
                editor_roles, role, before, tabs, self.roles_config)
            if why:
                self._refuse(why)
        why = self._last_admin_refusal(self._set_role(role, tabs))
        if why:
            self._refuse_last_admin(why)
        tabs_str = json.dumps(tabs)
        # Upsert
        existing = db.execute_query("SELECT 1 FROM ut_roles WHERE role_name=%s", (role,), fetch="one")
        if existing:
            ok = db.execute_update("UPDATE ut_roles SET permissions=%s WHERE role_name=%s", (tabs_str, role))
        else:
            ok = db.execute_update("INSERT INTO ut_roles (role_name, permissions) VALUES (%s, %s)", (role, tabs_str))
        self._forget_cached_abilities()
        if ok:
            # Who gave a role what is the first question after a surprise.
            added = sorted(set(tabs) - set(before))
            removed = sorted(set(before) - set(tabs))
            if added or removed or not existing:
                self.audit.log_event(
                    "ROLE_MGMT", self._actor(),
                    "%s role %s: added %s; removed %s" % (
                        "Changed" if existing else "Created", role,
                        ", ".join(added) or "nothing", ", ".join(removed) or "nothing"))
        return bool(ok)

    def create_role(self, role: str, tabs: List[str]) -> bool:
        return self.update_role_permissions(role, tabs)

    def role_permissions(self, role: str) -> List[str]:
        """Exactly what is stored for this role: tab keys, "can:" abilities and anything else."""
        db = self._get_db()
        row = db.execute_query("SELECT permissions FROM ut_roles WHERE LOWER(role_name)=LOWER(%s)",
                               (role,), fetch="one")
        if not row:
            return []
        try:
            return list(json.loads(row["permissions"] or "[]"))
        except Exception:
            return []

    def role_exists(self, role: str) -> bool:
        wanted = str(role or "").strip().lower()
        return any(str(r).strip().lower() == wanted for r in self.get_available_roles())

    def users_with_role(self, role: str) -> List[str]:
        """Usernames of everybody who holds this role (compared case-insensitively)."""
        wanted = str(role or "").strip().lower()
        holders = []
        for username, data in (self.get_all_users() or {}).items():
            roles = data.get("roles") or []
            if isinstance(roles, str):
                roles = [roles]
            if any(str(r).strip().lower() == wanted for r in roles):
                holders.append(username)
        return sorted(holders)

    def rename_role(self, old: str, new: str):
        """
        Rename a role, keeping who holds it and what it grants. Returns (ok, message).

        There was no rename: a new role, everybody moved across by hand, the
        old one deleted. One transaction here: the role, every person's role
        list, and the record of seeded defaults (so a renamed default is not
        recreated under its old name at the next start).
        """
        old_name = str(old or "").strip()
        new_name = str(new or "").strip()
        if not new_name:
            return False, "Enter the new name."
        if old_name.lower() == "developer":
            return False, "The Developer role cannot be renamed."
        if not self.role_exists(old_name):
            return False, "There is no role called %s." % old_name
        if new_name.lower() != old_name.lower() and self.role_exists(new_name):
            return False, "A role called %s already exists." % new_name
        editor_roles = self._acting_roles()
        perms = self.role_permissions(old_name)
        if editor_roles is not None:
            from slate.core.domain import access
            why = access.role_change_refusal(editor_roles, old_name, perms, perms, self.roles_config)
            if why:
                return False, why
        stored = next(r for r in self.get_available_roles() if str(r).lower() == old_name.lower())
        why = self._last_admin_refusal(self._rename_role_change(stored, new_name))
        if why:
            self.last_error = why
            return False, why
        holders = self.users_with_role(stored)
        from ..infra.transaction import atomic
        try:
            with atomic(self._get_db()) as tx:
                tx.write("UPDATE ut_roles SET role_name=%s WHERE role_name=%s",
                         (new_name, stored), expect_rows=True)
                for username in holders:
                    row = tx.one("SELECT roles FROM ut_users WHERE username=%s", (username,))
                    roles = self._parse_roles((row or {}).get("roles"))
                    roles = [new_name if str(r).lower() == stored.lower() else r for r in roles]
                    tx.write("UPDATE ut_users SET roles=%s WHERE username=%s",
                             (json.dumps(roles), username))
                tx.write("DELETE FROM ut_role_seeds WHERE role_name=%s", (new_name.lower(),))
                tx.write("UPDATE ut_role_seeds SET role_name=%s WHERE role_name=%s",
                         (new_name.lower(), stored.lower()))
        except Exception as exc:
            logging.warning("Role rename %s -> %s failed: %s", stored, new_name, exc)
            return False, "The role could not be renamed: %s" % exc
        self._forget_cached_abilities()
        self.audit.log_event("ROLE_MGMT", self._actor(),
                             "Renamed role %s to %s (%d holder(s))" % (stored, new_name, len(holders)))
        return True, "Renamed %s to %s." % (stored, new_name)

    def delete_role(self, role: str) -> bool:
        """
        Delete a role nobody holds. Refused while anyone still has it: their
        access would silently change, so move them to another role first.
        """
        editor_roles = self._acting_roles()
        if editor_roles is not None:
            from slate.core.domain import access
            why = access.role_change_refusal(
                editor_roles, role, self.role_permissions(role), [], self.roles_config)
            if why:
                self._refuse(why)
        if self.users_with_role(role):
            logging.warning("Role %s not deleted: still held by %s", role, self.users_with_role(role))
            return False
        db = self._get_db()
        ok = db.execute_update("DELETE FROM ut_roles WHERE role_name=%s", (role,))
        self._forget_cached_abilities()
        if ok:
            self.audit.log_event("ROLE_MGMT", self._actor(), "Deleted role %s" % role)
        return bool(ok)

    @staticmethod
    def _forget_cached_abilities():
        try:
            from slate.core.domain import access
            access.reset_cache()
        except Exception:
            pass

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
            from slate.core.security.admin_guard import sync_change
            why = self._last_admin_refusal(sync_change(users_dict))
            if why:
                self._refuse_last_admin(why)
            return db.sync_users(users_dict)
        return True
    def load_roles(self): pass
    def save_roles(self): return True
