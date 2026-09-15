import os
import sys
import time
import logging
import subprocess
import zipfile
import urllib.request
from pathlib import Path
import atexit
import threading
import shutil

class NoDatabaseHere(Exception):
    """
    The data directory holds no cluster, and nobody has said to build one.

    Raised instead of running initdb. Building a database is the one thing
    this engine must never do on its own: a new empty cluster is
    indistinguishable from the studio's real one by every figure on the
    dashboard, and four of them were found on one machine in a single day -
    each created because a path resolved to an empty folder.
    """

    def __init__(self, data_dir):
        self.data_dir = str(data_dir)
        super().__init__(
            "There is no database in %s. Choose the folder that holds the "
            "studio's database, or say explicitly that a new empty one should "
            "be created there." % self.data_dir)


class DatabaseEngine:
    """
    Manages the embedded PostgreSQL instance.
    Handles initializing the cluster and starting/stopping.
    Assumes PostgreSQL binaries are bundled in the `bin/pgsql` directory.
    """
    
    def __init__(self, data_dir: str, port: int = 5440, allow_create: bool = False):
        self.data_dir = Path(data_dir)
        self.port = port
        # Off by default. Only the first-run choice in the window sets it, and
        # only after the person has said "create a new empty database here".
        self.allow_create = bool(allow_create)
        
        # Resolve the bundled bin directory
        # When running in dev: slate_server/bin/pgsql/bin
        # When running compiled: _MEIPASS/slate_server/bin/pgsql/bin
        base_dir = Path(__file__).parent.parent
        if getattr(sys, 'frozen', False):
            base_dir = Path(getattr(sys, '_MEIPASS', sys.executable)) / "slate_server"
            
        self.bin_dir = base_dir / "bin" / "pgsql" / "bin"
        
        # Ensure base directories exist
        self.data_dir.parent.mkdir(parents=True, exist_ok=True)
        
    def is_installed(self) -> bool:
        """Check if Postgres binaries exist"""
        return (self.bin_dir / "postgres.exe").exists()

    def is_initialized(self) -> bool:
        """Check if the data directory has been initialized"""
        return (self.data_dir / "PG_VERSION").exists()

    def is_ready(self) -> bool:
        """Checks if PostgreSQL is currently accepting connections on self.port."""
        if not (self.data_dir / "postmaster.pid").exists():
            return False

        pg_isready_exe = self.bin_dir / "pg_isready.exe"
        if not pg_isready_exe.exists():
            return False
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            res = subprocess.run(
                [str(pg_isready_exe), "-h", "127.0.0.1", "-p", str(self.port)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                creationflags=creationflags
            )
            return res.returncode == 0
        except Exception:
            return False

    def _clean_stale_pid_file(self):
        """Removes postmaster.pid if the recorded process is no longer alive."""
        pid_file = self.data_dir / "postmaster.pid"
        if not pid_file.exists():
            return
            
        if self.is_ready():
            return  # Server is actually running and responding

        pid = None
        try:
            with open(pid_file, 'r') as f:
                first_line = f.readline().strip()
                if first_line:
                    pid = int(first_line)
        except Exception:
            pid = None

        is_alive = False
        if pid:
            if sys.platform == 'win32':
                try:
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    if h:
                        kernel32.CloseHandle(h)
                        is_alive = True
                except Exception:
                    is_alive = False
            else:
                try:
                    os.kill(pid, 0)
                    is_alive = True
                except OSError:
                    is_alive = False

        if not is_alive:
            try:
                pid_file.unlink(missing_ok=True)
                logging.info("Removed stale postmaster.pid file.")
            except Exception as e:
                logging.warning(f"Could not remove stale postmaster.pid: {e}")

    def initialize_database(self, progress_callback=None):
        """Runs initdb.exe to create a new cluster."""
        if self.is_initialized():
            return True
            
        if progress_callback:
            progress_callback("Initializing Database Cluster...")

        initdb_exe = str(self.bin_dir / "initdb.exe")
        cmd = [
            initdb_exe,
            "-D", str(self.data_dir),
            "-U", "postgres",
            "-A", "trust", # Trust local connections
            "-E", "utf8"
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self._configure_network_access()
            return True
        except subprocess.CalledProcessError as e:
            raise Exception(f"initdb failed:\n{e.stderr}")

    # Studio machines live on private networks. These are the only addresses
    # allowed in, and every one of them still has to give a password.
    STUDIO_NETWORKS = ("192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12")

    def _configure_network_access(self):
        """
        Write the database's network settings, immediately after initdb.

        Only postgresql.conf. Access rules are not written here, and that is
        deliberate: this runs before the cluster has ever started, so there are
        no accounts and no passwords yet. Demanding a password at this moment
        locks out the step that would go on to set one.

        It used to append the hardened rules here as well, which looked right
        and did nothing - initdb has already written "trust" for local and
        loopback connections, PostgreSQL uses the first matching line, and the
        hardened ones went underneath. The file claimed to require a password
        while the cluster accepted any. _harden_access() replaces the file once
        the accounts exist.
        """
        conf_path = self.data_dir / "postgresql.conf"

        if conf_path.exists():
            with open(conf_path, "a", encoding="utf-8") as f:
                f.write("\n# --- UT CENTRAL SERVER CONFIG ---\n")
                f.write("listen_addresses = '*'\n")
                f.write(f"port = {self.port}\n")
                # Passwords are stored and sent as one-way hashes, never in
                # the clear.
                f.write("password_encryption = scram-sha-256\n")
                # Connections held back so an administrator can always get in
                # during an incident. These only mean anything because the
                # software no longer logs in as a superuser.
                f.write("superuser_reserved_connections = 5\n")

    # The first line of the pg_hba.conf this software writes. Its presence is
    # how a cluster locked out by this software is told apart from one an
    # administrator has secured by hand.
    HBA_SIGNATURE = "# Written by Slate Central Server."

    def _bootstrap(self, progress_callback=None):
        """
        Get a running cluster into a state the software can actually use.

        The order is the whole point, and it is why these are not three
        independent calls at three call sites:

          1. the database has to exist before anything can be granted on it;
          2. the accounts have to be created while the cluster still trusts
             local connections, because setting a password is the one thing a
             password requirement would prevent;
          3. only then is the access hardened, and reloaded.

        Harden first and the cluster locks out the very step that would have
        given it credentials - a fresh install that can never be finished.

        That last sentence described a hypothetical when it was written and an
        installed build the week after. Hardening used to happen whether or not
        the accounts were created, so a server with no password configured -
        which every frozen build was, because it could not find its settings -
        built a cluster, created nothing in it, and then demanded a password
        from everybody including itself. The database survives inside it and
        nothing can reach it. So hardening is now conditional, and a cluster
        already in that state is let back in rather than left there.
        """
        from slate_server.core.db_credentials import admin_password

        def say(message):
            logging.warning(message)
            if progress_callback:
                progress_callback("> " + message)

        if not admin_password():
            # Deliberately not hardened. An unhardened cluster on a studio
            # network is a problem; a hardened one with no accounts is a
            # database nobody will ever open again.
            say("No database password is configured, so the accounts cannot be "
                "created and access has been left as it is. Set the password in "
                "Settings and restart the server.")
            self._ensure_slate_database()
            return False

        if not self._can_authenticate():
            if not self._recover_locked_out_cluster(say):
                say("The database will not accept this server's own password. "
                    "Nothing has been changed. Check the password in Settings.")
                return False

        self._ensure_slate_database()
        if self._ensure_application_role():
            self._harden_access()
            return True

        reason = getattr(self, "_last_role_error", "")
        say("The application account could not be set up, so access has been "
            "left as it is rather than locking the database to credentials it "
            "does not have." + (" The database said: %s" % reason if reason else ""))
        return False

    def _can_authenticate(self) -> bool:
        """Whether this server can log in to its own database at all."""
        try:
            import psycopg2
            from slate_server.core.db_credentials import connect_kwargs
        except ImportError:
            return False

        try:
            conn = psycopg2.connect(
                **connect_kwargs(self.port, "postgres", connect_timeout=5))
        except Exception as exc:
            first = str(exc).strip().splitlines()
            logging.warning("This server cannot log in to its own database: %s",
                            first[0] if first else exc)
            return False
        conn.close()
        return True

    def _trust_rules(self) -> str:
        """Loopback and local only, and only for as long as a repair takes."""
        return "\n".join([
            self.HBA_SIGNATURE,
            "# TEMPORARY - written while the accounts are repaired, and replaced",
            "# with the hardened rules in the same run. If this file is still",
            "# here, the repair did not finish: stop the server and say so.",
            "local   all   all                    trust",
            "host    all   all   127.0.0.1/32     trust",
            "host    all   all   ::1/128          trust",
            "",
        ])

    def _recover_locked_out_cluster(self, say) -> bool:
        """
        Let a cluster back in that was hardened before it had any credentials.

        An install with no password configured created its cluster, created no
        accounts because it had nothing to create them with, and then replaced
        pg_hba.conf with rules demanding one. Nothing can log into the result -
        not this server, not a workstation, not psql - and the studio's data
        sits inside it, intact and unreachable. Reinstalling does not help: the
        cluster outlives the uninstall and the next install adopts it.

        Only attempted on a file this software wrote. An administrator who has
        secured this cluster by hand gets an error message instead, because the
        repair resets the passwords to whatever the settings say, and that is
        not a thing to do to somebody else's decision.

        The window in which loopback connections are trusted lasts as long as
        two ALTER ROLE statements, and closing it again is in a finally.
        """
        hba_path = self.data_dir / "pg_hba.conf"
        try:
            current = hba_path.read_text(encoding="utf-8")
        except OSError as exc:
            logging.error("Could not read %s: %s", hba_path, exc)
            return False

        if not current.startswith(self.HBA_SIGNATURE):
            logging.error("pg_hba.conf was not written by this software, so it "
                          "is left alone.")
            return False

        say("This database was locked before it had any accounts. Repairing it.")
        keep = hba_path.with_suffix(".conf.locked-out")
        recovered = False
        try:
            try:
                if not keep.exists():
                    shutil.copy2(hba_path, keep)
            except OSError as exc:
                logging.warning("Could not keep a copy of the locked-out "
                                "pg_hba.conf: %s", exc)

            hba_path.write_text(self._trust_rules(), encoding="utf-8")
            if not self._reload_configuration():
                return False

            recovered = self._can_authenticate()
            if not recovered:
                say("The database still will not accept a connection after the "
                    "access rules were reset. Nothing else has been changed.")
        except OSError as exc:
            logging.error("Could not repair pg_hba.conf: %s", exc)
            return False
        finally:
            if not recovered:
                # Put it back exactly as it was. A failed repair must not be
                # the thing that leaves a studio database open.
                try:
                    hba_path.write_text(current, encoding="utf-8")
                    self._reload_configuration()
                except OSError as exc:
                    logging.error("Could not restore %s: %s", hba_path, exc)

        return recovered

    def _reload_configuration(self) -> bool:
        """Ask a running cluster to re-read pg_hba.conf. No restart needed."""
        pg_ctl = str(self.bin_dir / "pg_ctl.exe")
        if not os.path.exists(pg_ctl):
            logging.error("pg_ctl is missing, so the access rules cannot be "
                          "reloaded.")
            return False
        try:
            subprocess.run([pg_ctl, "-D", str(self.data_dir), "reload"],
                           capture_output=True, text=True, timeout=15,
                           creationflags=(subprocess.CREATE_NO_WINDOW
                                          if sys.platform == "win32" else 0))
        except (OSError, subprocess.SubprocessError) as exc:
            logging.error("Could not reload the access rules: %s", exc)
            return False
        time.sleep(0.5)
        return True

    def _ensure_application_role(self):
        """
        Create the account the artists' software logs in as, and give it the
        studio's database.

        Without this a fresh install cannot work at all, and says so in the least
        helpful way available: every client reaches the server, authenticates,
        and is told the role does not exist. Five of those trip the circuit
        breaker, and the application then dies at the login screen with a stack
        trace. The database itself is created automatically a few lines above,
        which makes the gap easy to miss - the server looks like it worked.

        deployment/secure_database.sql does this too, for a server that was set
        up before the software created its own accounts. It has to be run by
        hand, which is fine as a migration and useless as a first run.

        Idempotent on purpose: run against a studio that already has the account,
        it re-asserts the password from the settings and the ownership, and
        changes nothing else.
        """
        from slate_server.core.db_credentials import (
            admin_password, admin_user, application_user, database_name)

        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError:
            logging.error("psycopg2 is not available; cannot create the "
                          "application account. Clients will not be able to "
                          "log in until deployment/secure_database.sql is run.")
            return False

        password = admin_password()
        if not password:
            logging.error("No database password is configured, so the "
                          "application account cannot be created. Clients will "
                          "not be able to log in.")
            return False

        role = application_user()
        dbname = database_name()

        try:
            conn = psycopg2.connect(host="127.0.0.1", port=int(self.port),
                                    dbname=dbname, user=admin_user(),
                                    password=password, connect_timeout=10,
                                    application_name="Slate Central Server")
        except Exception as exc:
            logging.error("Could not connect as %s to set up the application "
                          "account: %s", admin_user(), exc)
            return False

        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                ident = sql.Identifier(role)
                literal = sql.Literal(password)

                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                if cur.fetchone():
                    cur.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}")
                                .format(ident, literal))
                    logging.info("Application account %s already existed; "
                                 "password re-asserted from the settings.", role)
                else:
                    cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}")
                                .format(ident, literal))
                    logging.info("Created the application account %s.", role)

                # The point of a separate account. An administrator by accident
                # is the same hole as no account at all.
                cur.execute(sql.SQL(
                    "ALTER ROLE {} NOSUPERUSER NOCREATEROLE NOCREATEDB").format(ident))

                # It has to own the database: the software alters its own tables
                # as it migrates, and a guest cannot.
                cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                            .format(sql.Identifier(dbname), ident))
                cur.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}")
                            .format(sql.Identifier(dbname), ident))
                cur.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(ident))
                cur.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(ident))

                # Tables made earlier - by an older build, or by the superuser -
                # must change hands too, or the next migration is refused.
                # The role is composed into the statement rather than passed
                # as a parameter, and nothing is passed to execute() at all.
                #
                # psycopg2 only substitutes when it is given parameters, and
                # when it does, every % in the statement is a placeholder. This
                # block is PL/pgSQL calling format(), so it is full of %I - and
                # psycopg2 stopped at the first one, every time, on every
                # server. The account was therefore never created, and until
                # the access rules were made conditional the cluster was then
                # hardened against credentials that did not exist.
                cur.execute(sql.SQL("""
                    DO $do$
                    DECLARE r record;
                    BEGIN
                        FOR r IN SELECT tablename FROM pg_tables
                                 WHERE schemaname = 'public' LOOP
                            EXECUTE format('ALTER TABLE public.%I OWNER TO %I',
                                           r.tablename, {owner});
                        END LOOP;
                        FOR r IN SELECT sequencename FROM pg_sequences
                                 WHERE schemaname = 'public' LOOP
                            EXECUTE format('ALTER SEQUENCE public.%I OWNER TO %I',
                                           r.sequencename, {owner});
                        END LOOP;
                    END
                    $do$;
                """).format(owner=sql.Literal(role)))

            # The administrator account is what the hardened settings below will
            # start demanding a password for. Setting it here, while the cluster
            # still trusts local connections, is the only moment this can be done
            # without asking somebody to do it by hand.
            with conn.cursor() as cur:
                cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}")
                            .format(sql.Identifier(admin_user()),
                                    sql.Literal(password)))
            logging.info("Administrator password set from the settings.")
            return True
        except Exception as exc:
            first = str(exc).strip().splitlines()
            self._last_role_error = first[0] if first else str(exc)
            logging.error("Failed to set up the application account %s: %s",
                          role, exc)
            return False
        finally:
            conn.close()

    def _harden_access(self):
        """
        Replace initdb's rules rather than adding to them.

        PostgreSQL uses the first matching line in pg_hba.conf. initdb writes
        "trust" for local and loopback connections, and the hardened rules were
        being appended *below* those - so they never applied to anything, and a
        freshly installed server accepted any password at all on the loopback
        interface. The file said it was secured; the cluster disagreed.

        Only rewritten when a trust rule is actually present, so a studio that
        has edited this file by hand keeps its edits.
        """
        hba_path = self.data_dir / "pg_hba.conf"
        if not hba_path.exists():
            return False

        try:
            existing = hba_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logging.error("Could not read %s: %s", hba_path, exc)
            return False

        live = [l for l in existing if l.strip() and not l.strip().startswith("#")]
        if not any(l.split()[-1].lower() == "trust" for l in live if l.split()):
            return False           # already hardened, or hand-edited

        rules = [
            self.HBA_SIGNATURE,
            "#",
            "# The first matching line wins, which is why this file is replaced",
            "# rather than added to. initdb writes 'trust' rules for local and",
            "# loopback connections - 'trust' means the password is not checked",
            "# at all - and any hardening appended below them never applies.",
            "#",
            "# Studio networks only, and a password every time. Do not add a",
            "# 0.0.0.0/0 rule here, and never use 'trust'.",
            "",
            "local   all   all                    scram-sha-256",
            "host    all   all   127.0.0.1/32     scram-sha-256",
            "host    all   all   ::1/128          scram-sha-256",
        ]
        for network in self.STUDIO_NETWORKS:
            rules.append("host    all   all   %-16s scram-sha-256" % network)
        rules += [
            "",
            "local   replication  all                 scram-sha-256",
            "host    replication  all  127.0.0.1/32   scram-sha-256",
            "host    replication  all  ::1/128        scram-sha-256",
        ]

        try:
            backup = hba_path.with_suffix(".conf.before-hardening")
            if not backup.exists():
                shutil.copy2(hba_path, backup)
            hba_path.write_text("\n".join(rules) + "\n", encoding="utf-8")
        except OSError as exc:
            logging.error("Could not write %s: %s", hba_path, exc)
            return False

        logging.warning("pg_hba.conf still granted trust access; replaced it. "
                        "The previous file is kept as %s", backup.name)

        # A reload is enough - pg_hba.conf does not need a restart - and it has
        # to happen now, or the cluster keeps trusting until something else
        # restarts it.
        self._reload_configuration()
        return True

    def _ensure_pg_directories(self):
        """Ensures directories required by PostgreSQL exist (Git does not track empty folders)."""
        required_dirs = [
            "pg_commit_ts",
            "pg_dynshmem",
            "pg_logical/mappings",
            "pg_logical/snapshots",
            "pg_notify",
            "pg_replslot",
            "pg_serial",
            "pg_snapshots",
            "pg_stat",
            "pg_stat_tmp",
            "pg_tblspc",
            "pg_twophase",
            "pg_wal/archive_status",
        ]
        for rel_dir in required_dirs:
            p = self.data_dir / rel_dir
            p.mkdir(parents=True, exist_ok=True)

    def _ensure_slate_database(self):
        """
        Create the studio's database if this cluster does not have it yet.

        Worth being careful about, because the failure is silent in both
        directions: createdb says nothing useful when the database is already
        there, and the exception handler below discards everything. So if this
        is ever pointed at the wrong name it will not report a problem - it will
        quietly manufacture an empty database and let every screen show zero.
        """
        from slate_server.core.db_credentials import database_name

        createdb_exe = str(self.bin_dir / "createdb.exe")
        if not Path(createdb_exe).exists():
            return
        cmd_createdb = [
            createdb_exe,
            "-h", "127.0.0.1",
            "-U", "postgres",
            "-p", str(self.port),
            database_name(),
        ]
        try:
            from slate_server.core.db_credentials import env_with_password

            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            subprocess.run(
                cmd_createdb,
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=creationflags,
                # The database asks for a password now; without this the tool
                # stops to prompt for one, which nothing is there to answer.
                env=env_with_password(),
            )
        except Exception:
            pass

    def start(self, progress_callback=None):
        """Starts the PostgreSQL server using pg_ctl."""
        if not self.is_installed():
            raise Exception(f"PostgreSQL binaries not found at {self.bin_dir}")

        # If already running and responsive, return immediately
        if self.is_ready():
            if progress_callback:
                progress_callback("Database server already running.")
            self._bootstrap(progress_callback)
            return True
            
        is_first_run = not self.is_initialized()
        if is_first_run:
            if not self.allow_create:
                raise NoDatabaseHere(self.data_dir)
            self.initialize_database(progress_callback)
        else:
            self._ensure_pg_directories()
            self._update_port_in_conf()
            self._clean_stale_pid_file()
            
        if progress_callback:
            progress_callback("Starting Database Server...")

        pg_ctl_exe = str(self.bin_dir / "pg_ctl.exe")
        log_file = str(self.data_dir.parent / "pg_server.log")
        
        cmd = [
            pg_ctl_exe,
            "-D", str(self.data_dir),
            "-l", log_file,
            "start"
        ]
        
        try:
            # Use CREATE_NO_WINDOW without DETACHED_PROCESS to avoid Windows handle hangs
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=creationflags
            )
        except subprocess.TimeoutExpired:
            logging.warning("pg_ctl start timed out after 10s; polling readiness...")
        except Exception as e:
            logging.debug(f"pg_ctl start returned notice: {e}")

        if progress_callback:
            progress_callback("Waiting for database cluster...")

        # Fast readiness polling: check every 200ms up to 6 seconds
        ready = False
        for _ in range(30):
            if self.is_ready():
                ready = True
                break
            time.sleep(0.2)

        if not ready:
            raise Exception(f"Failed to start server. Check log at {log_file}")

        self._bootstrap(progress_callback)
        try:
            atexit.register(self.stop)
        except Exception:
            pass
        return True

    def stop(self, progress_callback=None):
        """Gracefully stops the PostgreSQL server, with fallback force-terminate if needed."""
        if not self.is_installed() or not self.is_initialized():
            return True

        if not self.is_ready():
            self._clean_stale_pid_file()
            return True
            
        if progress_callback:
            progress_callback("Stopping Database Server...")

        pg_ctl_exe = str(self.bin_dir / "pg_ctl.exe")
        log_file = str(self.data_dir.parent / "pg_server.log")
        
        cmd = [
            pg_ctl_exe,
            "-D", str(self.data_dir),
            "stop",
            "-m", "fast"
        ]
        
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                creationflags=creationflags
            )
        except Exception as e:
            logging.debug(f"pg_ctl stop notice: {e}")

        # Wait for connections to close
        for _ in range(25):
            if not self.is_ready():
                self._clean_stale_pid_file()
                return True
            time.sleep(0.2)

        # Fallback force-kill if graceful stop timed out
        pid_file = self.data_dir / "postmaster.pid"
        if pid_file.exists():
            try:
                with open(pid_file, 'r') as f:
                    pid_line = f.readline().strip()
                if pid_line:
                    pid = int(pid_line)
                    if sys.platform == 'win32':
                        creationflags = subprocess.CREATE_NO_WINDOW
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=creationflags
                        )
                    else:
                        os.kill(pid, 9)
            except Exception as exc:
                logging.debug("PostgreSQL fallback kill notice: %s", exc)
            self._clean_stale_pid_file()

        return not self.is_ready()

    def _update_port_in_conf(self):
        import re
        conf_path = self.data_dir / "postgresql.conf"
        if conf_path.exists():
            with open(conf_path, 'r') as f:
                content = f.read()
            content = re.sub(r"port\s*=\s*\d+", f"port = {self.port}", content)
            with open(conf_path, 'w') as f:
                f.write(content)
