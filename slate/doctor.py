"""
Ask the software what it thinks its own situation is, and check whether it is right.

Every fault worth chasing in this codebase has had the same shape: nothing
raised, nothing was logged in a way anybody read, and the software carried on
against the wrong database, the wrong cluster or an empty folder. The screens
then showed zero, which looks exactly like a quiet day.

So this does not test the code. It resolves the same settings the application
resolves, then goes and looks at whether the things they name are actually
there - the database, its tables, the cluster the server is pointed at, the pool
in front of it, the folder updates are published to. Each answer is either a
fact or a stated failure; there is no path through here that reports success
because a lookup quietly returned nothing.

    python -m slate.doctor

Exits non-zero if anything is wrong, so it can be the first line of a support
call or the last step of a deployment.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

# Findings, in the order they are reported.
OK, WARN, FAIL = "ok", "warn", "fail"

_SYMBOL = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}


class Report:
    """Collects findings so the exit code can reflect all of them."""

    def __init__(self):
        self.findings = []
        self._section = None

    def section(self, title):
        print()
        print(title)
        print("-" * len(title))
        self._section = title

    def add(self, status, label, detail=""):
        self.findings.append((status, self._section, label, detail))
        line = "[%s] %-34s %s" % (_SYMBOL[status], label, detail)
        print(line.rstrip())

    def note(self, text):
        print("       %s" % text)

    @property
    def failed(self):
        return [f for f in self.findings if f[0] == FAIL]

    @property
    def warned(self):
        return [f for f in self.findings if f[0] == WARN]


# --------------------------------------------------------------------- config

def config_layers():
    """
    Every file GlobalConfig reads, in the order it reads them.

    Deliberately rebuilt here rather than imported, because the point is to show
    the layering - which file exists, and which one had the last word on each
    setting. A single merged dictionary is what hides a stale file.
    """
    local = Path(os.getenv("LOCALAPPDATA", Path.home()))
    source_root = Path(__file__).resolve().parent.parent

    return [
        ("bundled defaults", source_root / "slate" / "default_config.json"),
        ("source config", source_root / "slate" / "config.json"),
        ("client config", source_root / "client_config.json"),
        ("legacy user config", Path.home() / "RuntimeData" / "Slate" / "config.json"),
        ("machine config", local / "Slate" / "config.json"),
    ]


SECRET_HINTS = ("pass", "secret", "token", "key")


def describe(key, value):
    """Show a setting without printing a credential."""
    if any(h in key.lower() for h in SECRET_HINTS):
        return "set (%d characters)" % len(value) if value else "not set"
    return repr(value)


def check_configuration(report):
    report.section("Configuration")

    resolved, source_of = {}, {}
    for name, path in config_layers():
        if not path.exists():
            report.add(OK, name, "absent - %s" % path)
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            report.add(FAIL, name, "unreadable: %s" % exc)
            report.note("at %s" % path)
            continue
        report.add(OK, name, "%d settings - %s" % (len(data), path))
        for k, v in data.items():
            resolved[k] = v
            source_of[k] = name

    print()
    interesting = ("db_mode", "db_host", "db_port", "db_pooler_port", "db_name",
                   "db_user", "db_password", "SERVER_ROOT", "allow_db_fallback")
    for key in interesting:
        if key in resolved:
            print("       %-18s %-28s (from %s)"
                  % (key, describe(key, resolved[key]), source_of[key]))
        else:
            print("       %-18s %s" % (key, "not configured anywhere"))

    if not resolved.get("db_host"):
        report.add(WARN, "db_host", "blank - the client will try network discovery")
    if not resolved.get("db_name"):
        report.add(FAIL, "db_name", "not configured; the software cannot know what to open")

    return resolved


# ------------------------------------------------------------------- database

def check_database(report, cfg):
    report.section("Database")

    host = cfg.get("db_host") or "127.0.0.1"
    port = int(cfg.get("db_port") or 5440)
    name = cfg.get("db_name") or ""
    user = cfg.get("db_user") or ""
    password = cfg.get("db_password") or os.environ.get("SLATE_DB_PASSWORD", "")

    # Is anything listening at all? A refused connection and a wrong password
    # are different problems and should not read the same.
    try:
        with socket.create_connection((host, port), timeout=3):
            report.add(OK, "postgres reachable", "%s:%d" % (host, port))
    except OSError as exc:
        report.add(FAIL, "postgres reachable", "%s:%d - %s" % (host, port, exc))
        report.note("Start Slate Server, or check db_host and db_port.")
        return

    try:
        import psycopg2
    except ImportError:
        report.add(WARN, "psycopg2", "not installed; skipping the database checks")
        return

    def connect(dbname):
        return psycopg2.connect(host=host, port=port, dbname=dbname, user=user,
                                password=password, connect_timeout=5,
                                application_name="slate doctor")

    # Ask the cluster what databases it has. This is the check that matters:
    # connecting successfully to a database that was created empty by mistake
    # looks identical to connecting to the real one.
    try:
        with connect("postgres") as conn, conn.cursor() as cur:
            cur.execute("SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1")
            present = [r[0] for r in cur.fetchall()]
    except Exception as exc:
        report.add(WARN, "cluster inventory", "could not list databases: %s" % exc)
        present = None

    if present is not None:
        report.add(OK, "databases in the cluster", ", ".join(present))
        if name not in present:
            report.add(FAIL, "configured database", "%r is NOT in this cluster" % name)
            report.note("The software would ask for it, and the server's createdb "
                        "would make an empty one rather than refuse.")
            return

    try:
        with connect(name) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
            tables = cur.fetchone()[0]
            cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
            size = cur.fetchone()[0]
    except Exception as exc:
        report.add(FAIL, "connect to %s" % name, str(exc).strip().splitlines()[0])
        return

    if tables == 0:
        report.add(FAIL, "database %r" % name, "exists but has no tables - it is empty")
        report.note("This is what a database created by mistake looks like.")
        return
    report.add(OK, "database %r" % name, "%d tables, %s" % (tables, size))

    # A few tables whose emptiness would be a real problem rather than a quiet
    # Tuesday. Counted individually so a missing table is named, not summarised.
    CORE = ("ut_users", "tracking_projects", "leave_requests", "it_tickets",
            "hardware_inventory", "onboarding_workflows", "software_licenses")
    try:
        with connect(name) as conn, conn.cursor() as cur:
            for table in CORE:
                try:
                    cur.execute("SELECT count(*) FROM %s" % table)
                    print("       %-26s %d row(s)" % (table, cur.fetchone()[0]))
                except Exception:
                    conn.rollback()
                    report.add(FAIL, table, "table missing")
    except Exception as exc:
        report.add(WARN, "row counts", str(exc).strip().splitlines()[0])


# --------------------------------------------------------------------- server

def check_server(report, cfg):
    report.section("Server")

    appdata = Path(os.getenv("LOCALAPPDATA", Path.home())) / "Slate_Central"
    settings = appdata / "slate_server_config.json"

    if not settings.exists():
        report.add(FAIL, "server settings", "absent - %s" % settings)
        report.note("With no settings the server treats this as a first run: it "
                    "falls back to a default path and builds an empty cluster.")
        return

    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.add(FAIL, "server settings", "unreadable: %s" % exc)
        return

    report.add(OK, "server settings", str(settings))
    db_path = Path(data.get("db_path", ""))
    print("       %-18s %s" % ("db_path", db_path))
    print("       %-18s %s" % ("port", data.get("port")))

    if not db_path.exists():
        report.add(FAIL, "data directory", "does not exist: %s" % db_path)
        return
    if not (db_path / "base").is_dir() or not (db_path / "PG_VERSION").exists():
        report.add(FAIL, "data directory", "not a PostgreSQL cluster: %s" % db_path)
        return

    version = (db_path / "PG_VERSION").read_text(encoding="utf-8").strip()
    databases = len(list((db_path / "base").iterdir()))
    report.add(OK, "data directory", "PostgreSQL %s, %d database(s)" % (version, databases))

    # Three base directories is an untouched initdb: template0, template1 and
    # postgres, and nothing of anybody's.
    if databases <= 3:
        report.add(WARN, "data directory", "holds only the PostgreSQL templates")

    if int(data.get("port", 0)) != int(cfg.get("db_port") or 0):
        report.add(FAIL, "port agreement",
                   "server serves %s, clients are configured for %s"
                   % (data.get("port"), cfg.get("db_port")))


# ----------------------------------------------------------------------- pool

def check_pool(report, cfg):
    report.section("Connection pool")

    ini = Path(__file__).resolve().parent.parent / "pgbouncer" / "pgbouncer.ini"
    if not ini.exists():
        report.add(WARN, "pgbouncer.ini", "not written yet - the server writes it on start")
        return

    published, backend = None, None
    in_databases = False
    for line in ini.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_databases = stripped.lower() == "[databases]"
            continue
        if in_databases and "=" in stripped and not stripped.startswith(";"):
            published = stripped.split("=", 1)[0].strip()
            for part in stripped.split():
                if part.startswith("dbname="):
                    backend = part.split("=", 1)[1]
            break

    if published is None:
        report.add(WARN, "pgbouncer.ini", "no [databases] entry found")
        return

    report.add(OK, "pool publishes", "%s -> dbname=%s" % (published, backend))

    wanted = cfg.get("db_name")
    if wanted and published != wanted:
        report.add(FAIL, "pool agreement",
                   "clients ask for %r, the pool publishes %r" % (wanted, published))
        report.note("A client using the pooler port would be refused.")
        report.note("This file is rewritten every time Slate Server starts, so "
                    "a stale one usually means the server has not been started "
                    "since the setting changed.")
    if backend and wanted and backend != wanted:
        report.add(FAIL, "pool backend",
                   "the pool connects to %r, not %r" % (backend, wanted))


# -------------------------------------------------------------------- updates

def check_updates(report, cfg):
    report.section("Updates")

    root = cfg.get("SERVER_ROOT") or ""
    if not root:
        report.add(WARN, "SERVER_ROOT", "not configured")
        return
    root = Path(root)
    if not root.exists():
        report.add(FAIL, "central folder", "unreachable: %s" % root)
        return
    report.add(OK, "central folder", str(root))

    releases = root / "Updates" / "releases"
    if not releases.is_dir():
        report.add(WARN, "update channel", "%s does not exist" % releases)
        report.note("Nothing has been published; clients will report no update "
                    "available, which is correct but indistinguishable from "
                    "being up to date.")
        return
    report.add(OK, "update channel", str(releases))

    try:
        from slate.core.updater.manifest import TARGETS, manifest_name, problems
    except ImportError as exc:
        report.add(WARN, "manifest contract", "could not import: %s" % exc)
        return

    for target in TARGETS:
        path = releases / manifest_name(target)
        if not path.exists():
            report.add(WARN, "%s manifest" % target, "not published")
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            report.add(FAIL, "%s manifest" % target, "not valid JSON: %s" % exc)
            continue

        faults = problems(manifest)
        if faults:
            report.add(FAIL, "%s manifest" % target, "; ".join(faults))
            continue

        package = releases / manifest["package_name"]
        if not package.exists():
            report.add(FAIL, "%s package" % target,
                       "the manifest names %s, which is not there" % manifest["package_name"])
            continue
        report.add(OK, "%s update" % target,
                   "v%s, %s" % (manifest["version"], manifest["package_name"]))

    # Without this the update stages and can never apply.
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    updater = here / "SlateUpdater.exe"
    if getattr(sys, "frozen", False) and not updater.exists():
        report.add(FAIL, "SlateUpdater.exe", "missing from %s" % here)
        report.note("Updates will download and verify, then fail to install.")


# ----------------------------------------------------------------------- main

def main():
    print("Slate - configuration and connection check")
    print("=" * 60)
    print("Reports what the software resolves, then checks it is really there.")

    report = Report()
    cfg = check_configuration(report)

    for check in (check_database, check_server, check_pool, check_updates):
        try:
            check(report, cfg)
        except Exception as exc:                       # noqa: BLE001
            # A check that breaks is itself a finding. Swallowing it here would
            # reproduce the exact habit this command exists to expose.
            report.add(FAIL, check.__name__, "the check itself failed: %r" % exc)

    print()
    print("=" * 60)
    if report.failed:
        print("%d problem(s) found:" % len(report.failed))
        for _, section, label, detail in report.failed:
            print("  - %s: %s %s" % (section, label, detail))
        return 1
    if report.warned:
        print("No faults. %d thing(s) worth knowing about:" % len(report.warned))
        for _, section, label, detail in report.warned:
            print("  - %s: %s %s" % (section, label, detail))
        return 0
    print("Everything checked out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
