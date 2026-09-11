-- Passwords are supplied when this script is run, not written into it:
--
--     psql -v studio_password="'the password'" -f secure_database.sql
--
-- This repository is public. Do not put a literal back in here.

-- =====================================================================
-- Slate - secure the studio database
-- =====================================================================
--
-- WHAT THIS DOES
--   1. Sets a password on the postgres administrator account. It never had
--      one, because the old settings said "don't ask for a password".
--   2. Creates an ordinary account (ut_vfx_app) for the software to use,
--      instead of the software logging in as the administrator.
--   3. Hands the slate database over to that ordinary account, so the
--      software can still create and change its own tables.
--
--   The password is NOT changed. It stays exactly what every workstation's
--   settings file already says, so nothing on any workstation needs editing.
--
-- WHEN TO RUN IT
--   On the studio server, BEFORE rolling out the updated software.
--   The updated software logs in as ut_vfx_app; if this has not been run,
--   that account will not exist and clients will not connect.
--
-- HOW TO RUN IT
--   From the folder holding this file, on the server:
--
--     slate_server\bin\pgsql\bin\psql.exe -h 127.0.0.1 -p 5440 -U postgres ^
--         -d ut_vfx -f deployment\secure_database.sql
--
--   Run it while the old "no password needed" settings are still in place,
--   or it will not be able to connect. Change pg_hba.conf afterwards.
--
-- ORDER OF THE WHOLE JOB
--   1. Run this script.                     (passwords and accounts exist)
--   2. Replace pg_hba.conf with the hardened one shipped in LocalDatabase.
--   3. Reload:  pg_ctl.exe reload -D <data folder>
--   4. Check:   a wrong password must now be refused.
--   5. Roll out the updated software.
--
-- =====================================================================

\set ON_ERROR_STOP on

\echo ''
\echo '--- 1. give the administrator account the password clients already use'

-- The software's settings file already carries this password. It was simply
-- never checked, so setting it here makes authentication start working with
-- no change anywhere else. If your studio uses a different password, change
-- it in BOTH places (here and the software settings) or clients will fail.
ALTER ROLE postgres PASSWORD :'studio_password';

\echo '--- 2. create the ordinary account the software will use'

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ut_vfx_app') THEN
        CREATE ROLE ut_vfx_app LOGIN PASSWORD :'studio_password';
        RAISE NOTICE 'created ut_vfx_app';
    ELSE
        ALTER ROLE ut_vfx_app LOGIN PASSWORD :'studio_password';
        RAISE NOTICE 'ut_vfx_app already existed - password reset';
    END IF;
END
$do$;

-- Deliberately NOT a superuser. That is the whole point: an ordinary account
-- cannot spend the connection slots the server keeps back for an
-- administrator, and cannot reach any other database on the machine.
ALTER ROLE ut_vfx_app NOSUPERUSER NOCREATEROLE NOCREATEDB;

\echo '--- 3. hand the slate database to that account'

GRANT CONNECT ON DATABASE ut_vfx TO ut_vfx_app;
ALTER DATABASE ut_vfx OWNER TO ut_vfx_app;
ALTER SCHEMA public OWNER TO ut_vfx_app;
GRANT ALL ON SCHEMA public TO ut_vfx_app;

-- Existing tables must change hands too, or the software's own updates
-- (which alter tables as it upgrades) will be refused.
DO $do$
DECLARE
    r record;
    tables_moved int := 0;
    seqs_moved   int := 0;
    views_moved  int := 0;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO ut_vfx_app', r.tablename);
        tables_moved := tables_moved + 1;
    END LOOP;

    FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO ut_vfx_app', r.sequencename);
        seqs_moved := seqs_moved + 1;
    END LOOP;

    FOR r IN SELECT viewname FROM pg_views WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER VIEW public.%I OWNER TO ut_vfx_app', r.viewname);
        views_moved := views_moved + 1;
    END LOOP;

    RAISE NOTICE 'handed over: % tables, % sequences, % views',
                 tables_moved, seqs_moved, views_moved;
END
$do$;

\echo ''
\echo '--- done. checking the result:'

SELECT rolname AS account,
       rolsuper AS is_administrator,
       (rolpassword IS NOT NULL) AS has_a_password
FROM   pg_authid
WHERE  rolname IN ('postgres', 'ut_vfx_app')
ORDER  BY rolname;

\echo ''
\echo 'Expected: postgres = administrator with a password,'
\echo '          ut_vfx_app = NOT an administrator, with a password.'
\echo ''
\echo 'Next: replace pg_hba.conf, reload, then confirm that connecting'
\echo 'with a wrong password is refused.'
\echo ''
