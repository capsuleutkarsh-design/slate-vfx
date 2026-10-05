"""
slate_secure: the part of the studio database the workstations cannot change.

Everything in schema public belongs to the workstations' account (ut_vfx_app),
because the workstations migrate their own tables - so nothing in public can be
kept from them, and nothing there is append-only for real. This schema belongs
to the superuser. The server makes it on every start (install(), from
DatabaseEngine._bootstrap) and Recover Slate makes it again before it turns a
switch on. The workstations' account gets exactly what is granted here:

    audit_trail          INSERT and SELECT. A trigger refuses UPDATE, DELETE and
                         TRUNCATE, and stamps every row with the server's clock.
    passwords            nothing at all. While hide_password_hashes is on it holds
                         the password hashes, and ut_users.password_hash holds only
                         'hidden:bcrypt' or 'hidden:legacy'.
    keys                 the public half only (a column grant). keys.private_key
                         of the fleet key leaves only through fleet_signing_key().
    check_password(user, typed)       SECURITY DEFINER: 'bcrypt', 'legacy' or NULL
    fleet_signing_key(user, typed)    SECURITY DEFINER: the private fleet key for
                                      an active administrator's own password

pgcrypto is installed into this schema, so those functions call a crypt() the
workstations' account can neither replace nor shadow (their search_path is
pg_catalog only, and every name is schema-qualified).

Everything here is idempotent and run as the superuser.
"""

from __future__ import annotations

import logging
import secrets
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

SCHEMA = "slate_secure"
HIDDEN = "hidden:"
TRIGGER = "slate_hide_password"
PROBE_USER = "slate-signin-check"

# {app} becomes the workstations' role, quoted. Run without params, so % is literal.
_INSTALL = r"""
CREATE SCHEMA IF NOT EXISTS slate_secure;
REVOKE ALL ON SCHEMA slate_secure FROM PUBLIC;
GRANT USAGE ON SCHEMA slate_secure TO {app};
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA slate_secure;

-- ---------------------------------------------------------- the audit trail
CREATE TABLE IF NOT EXISTS slate_secure.audit_trail (
    id bigserial PRIMARY KEY,
    at timestamptz NOT NULL DEFAULT now(),
    kind text NOT NULL DEFAULT '',
    username text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT '',
    details text NOT NULL DEFAULT '',
    pc text NOT NULL DEFAULT '');
REVOKE ALL ON slate_secure.audit_trail FROM PUBLIC;
GRANT SELECT, INSERT ON slate_secure.audit_trail TO {app};
GRANT USAGE ON SEQUENCE slate_secure.audit_trail_id_seq TO {app};

CREATE OR REPLACE FUNCTION slate_secure.append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.at := now();
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'The audit trail can only be added to (% refused).', TG_OP;
END $fn$;
CREATE OR REPLACE TRIGGER audit_trail_append_only
    BEFORE INSERT OR UPDATE OR DELETE ON slate_secure.audit_trail
    FOR EACH ROW EXECUTE FUNCTION slate_secure.append_only();
CREATE OR REPLACE TRIGGER audit_trail_no_truncate
    BEFORE TRUNCATE ON slate_secure.audit_trail
    FOR EACH STATEMENT EXECUTE FUNCTION slate_secure.append_only();

-- ------------------------------------------------------------ passwords
CREATE TABLE IF NOT EXISTS slate_secure.passwords (
    username text PRIMARY KEY,
    hash text NOT NULL);
REVOKE ALL ON slate_secure.passwords FROM PUBLIC;

-- The rule UserManager._check_password has always had: bcrypt ($2a$/$2b$),
-- else an unsalted SHA-256 in hex, else the plain text. pgcrypto knows only
-- $2a$, which is the same computation as $2b$ for any real password.
CREATE OR REPLACE FUNCTION slate_secure.password_kind(stored text, typed text) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $fn$
    SELECT CASE
        WHEN stored IS NULL OR stored = '' OR typed IS NULL OR typed = '' THEN NULL
        WHEN stored ~ '^\$2[ab]\$' THEN
            CASE WHEN slate_secure.crypt(typed, '$2a$' || substr(stored, 5))
                      = '$2a$' || substr(stored, 5) THEN 'bcrypt' END
        WHEN stored ~ '^[0-9a-fA-F]{64}$' THEN
            CASE WHEN stored = encode(slate_secure.digest(typed, 'sha256'), 'hex')
                 THEN 'legacy' END
        WHEN stored = typed THEN 'legacy'
    END
$fn$;
REVOKE ALL ON FUNCTION slate_secure.password_kind(text, text) FROM PUBLIC;

CREATE OR REPLACE FUNCTION slate_secure.check_password(who text, typed text) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
    SELECT slate_secure.password_kind(p.hash, typed)
    FROM slate_secure.passwords p WHERE p.username = who
$fn$;
REVOKE ALL ON FUNCTION slate_secure.check_password(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION slate_secure.check_password(text, text) TO {app};

-- Fired by the trigger hide_passwords() puts on ut_users: whatever writes a
-- password (Users & Roles, the rehash at sign-in, an import, Recover Slate),
-- the hash goes here and ut_users keeps only the marker.
CREATE OR REPLACE FUNCTION slate_secure.hide_hash() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
BEGIN
    IF NEW.password_hash IS NULL OR NEW.password_hash = ''
            OR NEW.password_hash LIKE 'hidden:%' THEN
        IF TG_OP = 'UPDATE' AND NEW.username <> OLD.username THEN
            UPDATE slate_secure.passwords SET username = NEW.username
            WHERE username = OLD.username;
        END IF;
        RETURN NEW;
    END IF;
    INSERT INTO slate_secure.passwords (username, hash)
        VALUES (NEW.username, NEW.password_hash)
        ON CONFLICT (username) DO UPDATE SET hash = EXCLUDED.hash;
    IF TG_OP = 'UPDATE' AND NEW.username <> OLD.username THEN
        DELETE FROM slate_secure.passwords WHERE username = OLD.username;
    END IF;
    NEW.password_hash := 'hidden:' || CASE WHEN NEW.password_hash ~ '^\$2[ab]\$'
                                           THEN 'bcrypt' ELSE 'legacy' END;
    RETURN NEW;
END $fn$;

-- -------------------------------------------------------- the fleet key
CREATE TABLE IF NOT EXISTS slate_secure.keys (
    name text PRIMARY KEY,
    public_key text NOT NULL,
    private_key text NOT NULL,
    made_at timestamptz NOT NULL DEFAULT now());
REVOKE ALL ON slate_secure.keys FROM PUBLIC;
GRANT SELECT (name, public_key, made_at) ON slate_secure.keys TO {app};

-- Full access (a role holding ALL, or named admin / developer) or
-- manage_system: who may restart or shut down workstations in the Admin Panel.
CREATE OR REPLACE FUNCTION slate_secure.is_administrator(account jsonb) RETURNS boolean
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $fn$
DECLARE held jsonb;
BEGIN
    IF lower(trim(coalesce(account->>'active', ''))) IN ('0', '0.0', 'f', 'false', 'no', 'off')
       OR (coalesce(account->>'last_day', '') ~ '^\d{4}-\d{2}-\d{2}'
           AND left(account->>'last_day', 10) < to_char(current_date, 'YYYY-MM-DD')) THEN
        RETURN false;
    END IF;
    BEGIN
        held := (account->>'roles')::jsonb;
    EXCEPTION WHEN others THEN
        held := to_jsonb(account->>'roles');
    END;
    IF jsonb_typeof(held) IS DISTINCT FROM 'array' THEN
        held := jsonb_build_array(held);
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM jsonb_array_elements_text(held) AS r(role)
        LEFT JOIN public.ut_roles g ON lower(g.role_name) = lower(r.role)
        WHERE lower(r.role) IN ('admin', 'developer')
           OR (g.permissions ~ '^\s*\[' AND EXISTS (
                SELECT 1 FROM jsonb_array_elements_text(g.permissions::jsonb) AS p(perm)
                WHERE upper(p.perm) = 'ALL' OR lower(p.perm) = 'manage_system')));
EXCEPTION WHEN others THEN
    RETURN false;
END $fn$;
REVOKE ALL ON FUNCTION slate_secure.is_administrator(jsonb) FROM PUBLIC;

CREATE OR REPLACE FUNCTION slate_secure.fleet_signing_key(who text, typed text) RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$
DECLARE account jsonb; stored text;
BEGIN
    SELECT to_jsonb(u) INTO account FROM public.ut_users u WHERE u.username = who;
    stored := account->>'password_hash';
    IF stored LIKE 'hidden:%' THEN
        SELECT p.hash INTO stored FROM slate_secure.passwords p WHERE p.username = who;
    END IF;
    IF slate_secure.password_kind(stored, typed) IS NULL
       OR NOT slate_secure.is_administrator(account) THEN
        RETURN NULL;
    END IF;
    RETURN (SELECT k.private_key FROM slate_secure.keys k WHERE k.name = 'fleet');
END $fn$;
REVOKE ALL ON FUNCTION slate_secure.fleet_signing_key(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION slate_secure.fleet_signing_key(text, text) TO {app};
"""


def install(conn, app_role: str) -> None:
    """Make (or bring up to date) slate_secure, and the fleet key if there is none."""
    from psycopg2 import sql
    from slate.core.security.signing import FLEET_KEY, new_keypair
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(_INSTALL.replace("{app}", sql.Identifier(app_role).as_string(conn)))
        cur.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n "
                    "ON n.oid = e.extnamespace WHERE e.extname = 'pgcrypto'")
        where = (cur.fetchone() or ["?"])[0]
        if where != SCHEMA:
            raise RuntimeError("pgcrypto is installed in schema %s, not %s. Move it as the "
                               "superuser: ALTER EXTENSION pgcrypto SET SCHEMA %s"
                               % (where, SCHEMA, SCHEMA))
        cur.execute("SELECT 1 FROM slate_secure.keys WHERE name = %s", (FLEET_KEY,))
        if not cur.fetchone():
            private, public = new_keypair()
            cur.execute("INSERT INTO slate_secure.keys (name, public_key, private_key) "
                        "VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING",
                        (FLEET_KEY, public, private))
            logger.warning("Made the studio's fleet signing key.")


def is_installed(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('slate_secure.audit_trail') IS NOT NULL")
        return bool(cur.fetchone()[0])


# ------------------------------------------------------- hide_password_hashes

def hide_passwords(conn) -> int:
    """The trigger on, and every hash moved out of ut_users. One transaction."""
    with conn.cursor() as cur:
        cur.execute(
            "CREATE OR REPLACE TRIGGER %s BEFORE INSERT OR UPDATE ON public.ut_users "
            "FOR EACH ROW EXECUTE FUNCTION slate_secure.hide_hash();"
            "UPDATE public.ut_users SET password_hash = password_hash "
            "WHERE password_hash <> '' AND password_hash NOT LIKE 'hidden:%%'" % TRIGGER)
        return max(cur.rowcount, 0)


def unhide_passwords(conn) -> int:
    """Exactly as before: the trigger gone, every hash back in ut_users. One transaction."""
    with conn.cursor() as cur:
        cur.execute(
            "DROP TRIGGER IF EXISTS %s ON public.ut_users;"
            "UPDATE public.ut_users u SET password_hash = p.hash FROM slate_secure.passwords p "
            "WHERE p.username = u.username AND u.password_hash LIKE 'hidden:%%';"
            "DELETE FROM slate_secure.passwords" % TRIGGER)
        cur.execute("SELECT count(*) FROM public.ut_users WHERE password_hash LIKE 'hidden:%'")
        return int(cur.fetchone()[0])


def is_hiding(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_trigger WHERE tgname = %s "
                    "AND tgrelid = to_regclass('public.ut_users')", (TRIGGER,))
        return bool(cur.fetchone())


def effective_hashes(conn) -> Dict[str, str]:
    """{username: the hash that signs them in}, wherever it is kept."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('slate_secure.passwords') IS NOT NULL")
        secure = cur.fetchone()[0]
        cur.execute(
            "SELECT u.username, %s FROM public.ut_users u %s" % (
                "COALESCE(CASE WHEN u.password_hash LIKE 'hidden:%%' THEN p.hash END, "
                "u.password_hash)" if secure else "u.password_hash",
                "LEFT JOIN slate_secure.passwords p ON p.username = u.username"
                if secure else ""))
        return {name: value for name, value in cur.fetchall()}


# --------------------------------------------- proving a real sign-in works

def make_probe(conn) -> Tuple[str, str]:
    """
    A throwaway account with a random password, switched off, so a real
    sign-in check can be made without knowing anybody's password. drop_probe()
    removes it; one left behind by a crash opens nothing (nobody knows it).
    """
    import bcrypt
    password = secrets.token_urlsafe(18)
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.ut_users (username, password_hash, display_name, roles) "
                    "VALUES (%s, %s, 'Slate sign-in check', '[]') ON CONFLICT (username) "
                    "DO UPDATE SET password_hash = EXCLUDED.password_hash",
                    (PROBE_USER, hashed))
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='ut_users' AND column_name='active'")
        if cur.fetchone():
            cur.execute("UPDATE public.ut_users SET active = 0 WHERE username = %s",
                        (PROBE_USER,))
    return PROBE_USER, password


def drop_probe(conn, name: str = PROBE_USER) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.ut_users WHERE username = %s", (name,))
        cur.execute("SELECT to_regclass('slate_secure.passwords') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("DELETE FROM slate_secure.passwords WHERE username = %s", (name,))


def signin_problem(client: dict, name: str, password: str) -> str:
    """
    '' when the workstations' sign-in check, run over the workstations' own
    login, accepts this password and refuses a wrong one; otherwise why not.
    """
    import psycopg2
    from slate.core.domain.user_manager import UserManager
    from slate.core.security.dbapi import ConnectionDB
    try:
        conn = psycopg2.connect(connect_timeout=5, application_name="Slate pre-check", **client)
    except Exception as exc:
        return "the workstations' login failed: %s" % str(exc).strip().splitlines()[0]
    try:
        # The very check the sign-in window runs, without the rest of
        # UserManager's start-up (migrations, seeding), which is not ours to run.
        users = UserManager.__new__(UserManager)
        users._db = ConnectionDB(conn)
        row = users._db.execute_query("SELECT * FROM ut_users WHERE username=%s", (name,),
                                      fetch="one")
        if not row:
            return "the check account could not be read"
        if not users._password_matches(row["password_hash"], password, name):
            return "its right password was refused"
        if users._password_matches(row["password_hash"], password + "x", name):
            return "a wrong password was accepted"
        return ""
    except Exception as exc:
        return "the sign-in check failed: %s" % str(exc).strip().splitlines()[0]
    finally:
        conn.close()


def readable_hashes(client: dict) -> str:
    """'' when the workstations' login can read no password hash at all; else what it can."""
    import psycopg2
    try:
        conn = psycopg2.connect(connect_timeout=5, application_name="Slate pre-check", **client)
    except Exception as exc:
        return "the workstations' login failed: %s" % str(exc).strip().splitlines()[0]
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ut_users WHERE password_hash <> '' "
                        "AND password_hash NOT LIKE 'hidden:%'")
            visible = cur.fetchone()[0]
            if visible:
                return "%d password hash(es) are still in ut_users" % visible
            try:
                cur.execute("SELECT hash FROM slate_secure.passwords LIMIT 1")
                return "the workstations can read slate_secure.passwords"
            except psycopg2.errors.InsufficientPrivilege:
                return ""
    finally:
        conn.close()


def fleet_key_problem(client: dict) -> str:
    """'' when the workstations' login reads the fleet key's public half and not its private one."""
    import psycopg2
    from slate.core.security.dbapi import ConnectionDB
    from slate.core.security.signing import fleet_public_key
    try:
        conn = psycopg2.connect(connect_timeout=5, application_name="Slate pre-check", **client)
    except Exception as exc:
        return "the workstations' login failed: %s" % str(exc).strip().splitlines()[0]
    try:
        if not fleet_public_key(ConnectionDB(conn)):
            return "the workstations cannot read the fleet key's public half"
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT private_key FROM slate_secure.keys")
            return "the workstations can read the fleet key's private half"
        except psycopg2.errors.InsufficientPrivilege:
            return ""
    finally:
        conn.close()
