"""
Refactor v2_store.py for dual SQLite / PostgreSQL support.

This script:
1. Adds try/except psycopg2 import with SQLite fallbacks
2. Adds a _SQLiteCursorWrapper for cursor context-manager compat
3. Overrides get_connection() to wrap SQLite connections  
4. Rewrites ensure_schema() with a complete SQLite branch
5. Replaces execute_values() calls with loop-based inserts
6. Handles ANY(%s) → IN (...) for SQLite
"""

import re
import os
import sys

TARGET = os.path.join(os.path.dirname(__file__), '..', 'ut_messenger', 'server', 'v2_store.py')
TARGET = os.path.abspath(TARGET)

print(f"Refactoring: {TARGET}")
if not os.path.exists(TARGET):
    print(f"ERROR: File not found: {TARGET}")
    sys.exit(1)

with open(TARGET, 'r', encoding='utf-8') as f:
    content = f.read()

# Normalize line endings to \n for processing
content = content.replace('\r\n', '\n').replace('\r', '\n')

# ============================================================================
# 1. Fix imports
# ============================================================================
old_import = "from ut_vfx.core.infra.database_manager import database_manager"
new_imports = """import sqlite3
from contextlib import contextmanager

try:
    from psycopg2.extras import RealDictCursor, execute_values
    HAS_PSYCOPG2 = True
except ImportError:
    RealDictCursor = None
    execute_values = None
    HAS_PSYCOPG2 = False

from ut_vfx.core.infra.database_manager import database_manager"""

content = content.replace(old_import, new_imports)

# ============================================================================
# 2. Add SQLite cursor wrapper + replace get_connection
# ============================================================================
old_get_conn = """    def get_connection(self):
        # Compatibility wrapper for existing with self.get_connection() blocks
        return self.db.get_connection()"""

# Also try the variant without the comment
old_get_conn_alt = """    def get_connection(self):
        return self.db.get_connection()"""

new_get_conn = '''    class _CursorCtx:
        """Makes a cursor usable as a context manager (for SQLite compat)."""
        def __init__(self, cursor):
            self.cursor = cursor
        def __enter__(self):
            return self.cursor
        def __exit__(self, *args):
            pass

    class _ConnWrapper:
        """Wraps sqlite3.Connection so cursor(cursor_factory=...) is accepted."""
        def __init__(self, conn):
            self._conn = conn

        def cursor(self, cursor_factory=None):
            cur = self._conn.cursor()
            return MessengerV2Store._CursorCtx(cur)

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        def executescript(self, sql):
            return self._conn.executescript(sql)

        def execute(self, sql, params=None):
            return self._conn.execute(sql, params or ())

        def __getattr__(self, name):
            return getattr(self._conn, name)

    @contextmanager
    def get_connection(self):
        """Backend-agnostic connection context manager."""
        with self.db.get_connection() as conn:
            if isinstance(conn, sqlite3.Connection):
                yield self._ConnWrapper(conn)
            else:
                yield conn

    @property
    def _is_sqlite(self):
        return hasattr(self.db.backend, '_db_path')'''

if old_get_conn in content:
    content = content.replace(old_get_conn, new_get_conn)
elif old_get_conn_alt in content:
    content = content.replace(old_get_conn_alt, new_get_conn)
else:
    print("WARNING: Could not find get_connection to replace")

# ============================================================================
# 3. Rewrite ensure_schema() — find start and next method
# ============================================================================
schema_start = content.find("    def ensure_schema(self) -> None:")
if schema_start == -1:
    print("WARNING: Could not find ensure_schema")
else:
    # Find the next method definition at the same indentation level
    rest = content[schema_start + 10:]
    next_def = re.search(r'\n    def ', rest)
    if next_def:
        schema_end = schema_start + 10 + next_def.start()
    else:
        schema_end = len(content)

    new_ensure_schema = '''    def ensure_schema(self) -> None:
        """Create v2 schema tables. Detects backend and uses appropriate DDL."""
        if self._is_sqlite:
            self._ensure_schema_sqlite()
        else:
            self._ensure_schema_postgres()

    def _ensure_schema_sqlite(self) -> None:
        """SQLite-compatible v2 schema."""
        with self.db.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS chat_conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    conversation_type TEXT NOT NULL DEFAULT 'channel',
                    taxonomy TEXT NOT NULL DEFAULT 'user_group',
                    conversation_key TEXT UNIQUE,
                    department TEXT DEFAULT '',
                    project_code TEXT DEFAULT '',
                    created_by TEXT NOT NULL,
                    archived BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    last_activity_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS chat_conversation_members (
                    conversation_id INTEGER NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    joined_at TEXT DEFAULT (datetime('now')),
                    last_read_message_id INTEGER,
                    last_read_at TEXT,
                    pinned BOOLEAN NOT NULL DEFAULT FALSE,
                    muted BOOLEAN NOT NULL DEFAULT FALSE,
                    last_activity_at TEXT,
                    PRIMARY KEY (conversation_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS chat_message_mentions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
                    mentioned_user_id TEXT NOT NULL,
                    mention_text TEXT NOT NULL,
                    is_notified BOOLEAN NOT NULL DEFAULT FALSE,
                    is_read BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE (message_id, mentioned_user_id)
                );

                CREATE TABLE IF NOT EXISTS chat_message_pins (
                    message_id INTEGER PRIMARY KEY REFERENCES chat_messages(id) ON DELETE CASCADE,
                    conversation_id INTEGER NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                    pinned_by TEXT NOT NULL,
                    pinned_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS chat_message_bookmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE (message_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS chat_moderation_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    details_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS chat_retention_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_months INTEGER NOT NULL DEFAULT 12,
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    started_at TEXT,
                    finished_at TEXT,
                    details_json TEXT DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_chat_conversation_members_user
                    ON chat_conversation_members(user_id);
                CREATE INDEX IF NOT EXISTS idx_chat_conversations_taxonomy
                    ON chat_conversations(taxonomy, archived);
            """)

            # Add columns to chat_messages if they don't exist
            cur = conn.cursor()
            existing = {row[1] for row in cur.execute("PRAGMA table_info(chat_messages)").fetchall()}
            migrations = [
                ("conversation_id", "INTEGER"),
                ("thread_root_id", "INTEGER"),
                ("reply_to_id", "INTEGER"),
                ("metadata_json", "TEXT DEFAULT '{}'"),
            ]
            for col_name, col_type in migrations:
                if col_name not in existing:
                    try:
                        conn.execute(f"ALTER TABLE chat_messages ADD COLUMN {col_name} {col_type}")
                    except Exception:
                        pass

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
                ON chat_messages(conversation_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_messages_thread
                ON chat_messages(thread_root_id)
            """)
            conn.commit()

    def _ensure_schema_postgres(self) -> None:
        """Original PostgreSQL schema creation."""
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_conversations (
                        id BIGSERIAL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        conversation_type VARCHAR(32) NOT NULL DEFAULT 'channel',
                        taxonomy VARCHAR(32) NOT NULL DEFAULT 'user_group',
                        conversation_key VARCHAR(255) UNIQUE,
                        department VARCHAR(128) DEFAULT '',
                        project_code VARCHAR(128) DEFAULT '',
                        created_by VARCHAR(255) NOT NULL,
                        archived BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        last_activity_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_conversation_members (
                        conversation_id BIGINT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                        user_id VARCHAR(255) NOT NULL,
                        role VARCHAR(32) NOT NULL DEFAULT 'member',
                        joined_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        last_read_message_id BIGINT,
                        last_read_at TIMESTAMP WITH TIME ZONE,
                        pinned BOOLEAN NOT NULL DEFAULT FALSE,
                        muted BOOLEAN NOT NULL DEFAULT FALSE,
                        last_activity_at TIMESTAMP WITH TIME ZONE,
                        PRIMARY KEY (conversation_id, user_id)
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_message_mentions (
                        id BIGSERIAL PRIMARY KEY,
                        message_id BIGINT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
                        mentioned_user_id VARCHAR(255) NOT NULL,
                        mention_text VARCHAR(255) NOT NULL,
                        is_notified BOOLEAN NOT NULL DEFAULT FALSE,
                        is_read BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (message_id, mentioned_user_id)
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_message_pins (
                        message_id BIGINT PRIMARY KEY REFERENCES chat_messages(id) ON DELETE CASCADE,
                        conversation_id BIGINT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                        pinned_by VARCHAR(255) NOT NULL,
                        pinned_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_message_bookmarks (
                        id BIGSERIAL PRIMARY KEY,
                        message_id BIGINT NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
                        user_id VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (message_id, user_id)
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_moderation_audit (
                        id BIGSERIAL PRIMARY KEY,
                        actor_id VARCHAR(255) NOT NULL,
                        action_type VARCHAR(64) NOT NULL,
                        target_type VARCHAR(64) NOT NULL,
                        target_id VARCHAR(255) NOT NULL,
                        details_json JSONB DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_retention_jobs (
                        id BIGSERIAL PRIMARY KEY,
                        policy_months INTEGER NOT NULL DEFAULT 12,
                        status VARCHAR(32) NOT NULL DEFAULT 'scheduled',
                        started_at TIMESTAMP WITH TIME ZONE,
                        finished_at TIMESTAMP WITH TIME ZONE,
                        details_json JSONB DEFAULT '{}'::jsonb,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                cur.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS conversation_id BIGINT")
                cur.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS thread_root_id BIGINT")
                cur.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS reply_to_id BIGINT")
                cur.execute(
                    "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS metadata_json JSONB DEFAULT '{}'::jsonb"
                )

                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_conversation_members_user ON chat_conversation_members(user_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_conversations_taxonomy ON chat_conversations(taxonomy, archived)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation ON chat_messages(conversation_id, created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_root_id, created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_message_mentions_user ON chat_message_mentions(mentioned_user_id, is_read, created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_message_pins_conversation ON chat_message_pins(conversation_id, pinned_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chat_message_bookmarks_user ON chat_message_bookmarks(user_id, created_at DESC)"
                )

                try:
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_chat_messages_content_fts ON chat_messages USING GIN (to_tsvector('simple', COALESCE(content, '')))"
                    )
                except Exception:
                    pass

                cur.execute("SAVEPOINT sp_chat_messages_trgm")
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_chat_messages_content_trgm ON chat_messages USING GIN (lower(content) gin_trgm_ops)"
                    )
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT sp_chat_messages_trgm")
                finally:
                    cur.execute("RELEASE SAVEPOINT sp_chat_messages_trgm")

                # Backfill legacy groups into v2 channels.
                cur.execute("""
                    INSERT INTO chat_conversations (
                        id, name, conversation_type, taxonomy,
                        conversation_key, created_by, created_at,
                        updated_at, last_activity_at
                    )
                    SELECT
                        g.id::BIGINT, g.name, 'channel', 'user_group',
                        CONCAT('legacy_group:', g.id::TEXT),
                        g.created_by, g.created_at, g.created_at, g.created_at
                    FROM chat_groups g
                    ON CONFLICT (id) DO NOTHING;
                """)
                cur.execute(
                    "SELECT setval(pg_get_serial_sequence('chat_conversations', 'id'), COALESCE((SELECT MAX(id) FROM chat_conversations), 1), true)"
                )
                cur.execute("""
                    INSERT INTO chat_conversation_members (conversation_id, user_id, role, joined_at)
                    SELECT gm.group_id::BIGINT, gm.user_id, 'member', gm.joined_at
                    FROM chat_group_members gm
                    ON CONFLICT (conversation_id, user_id) DO NOTHING;
                """)
                cur.execute("""
                    UPDATE chat_messages m
                    SET conversation_id = c.id
                    FROM chat_conversations c
                    WHERE m.conversation_id IS NULL
                      AND m.group_id IS NOT NULL
                      AND c.conversation_key = CONCAT('legacy_group:', m.group_id::TEXT);
                """)

                # Backfill DM conversations
                cur.execute("""
                    WITH dm_pairs AS (
                        SELECT DISTINCT
                            LEAST(sender_id, recipient_id) AS u1,
                            GREATEST(sender_id, recipient_id) AS u2
                        FROM chat_messages
                        WHERE recipient_id IS NOT NULL
                    )
                    INSERT INTO chat_conversations (
                        name, conversation_type, taxonomy,
                        conversation_key, created_by, last_activity_at
                    )
                    SELECT
                        CONCAT(dm_pairs.u1, ' / ', dm_pairs.u2),
                        'dm', 'dm',
                        CONCAT('dm:', dm_pairs.u1, ':', dm_pairs.u2),
                        dm_pairs.u1, CURRENT_TIMESTAMP
                    FROM dm_pairs
                    ON CONFLICT (conversation_key) DO NOTHING;
                """)
                cur.execute("""
                    INSERT INTO chat_conversation_members (conversation_id, user_id, role)
                    SELECT id, split_part(conversation_key, ':', 2), 'member'
                    FROM chat_conversations
                    WHERE conversation_type = 'dm'
                    ON CONFLICT (conversation_id, user_id) DO NOTHING;
                """)
                cur.execute("""
                    INSERT INTO chat_conversation_members (conversation_id, user_id, role)
                    SELECT id, split_part(conversation_key, ':', 3), 'member'
                    FROM chat_conversations
                    WHERE conversation_type = 'dm'
                    ON CONFLICT (conversation_id, user_id) DO NOTHING;
                """)
                cur.execute("""
                    UPDATE chat_messages m
                    SET conversation_id = c.id
                    FROM chat_conversations c
                    WHERE m.conversation_id IS NULL
                      AND m.recipient_id IS NOT NULL
                      AND c.conversation_key = CONCAT('dm:', LEAST(m.sender_id, m.recipient_id), ':', GREATEST(m.sender_id, m.recipient_id));
                """)

                cur.execute(
                    "SELECT setval(pg_get_serial_sequence('chat_messages', 'id'), COALESCE(MAX(id), 1)) FROM chat_messages"
                )
            conn.commit()
'''

    content = content[:schema_start] + new_ensure_schema + content[schema_end:]

# ============================================================================
# 4. Fix create_message: replace execute_values with loop for SQLite
#    and fix %s::jsonb cast
# ============================================================================
# Replace the execute_values call in create_message with a backend check
old_execute_values = """                if mention_targets:
                    execute_values(
                        cur,
                        \"\"\"
                        INSERT INTO chat_message_mentions (
                            message_id,
                            mentioned_user_id,
                            mention_text,
                            is_notified,
                            is_read
                        )
                        VALUES %s
                        ON CONFLICT (message_id, mentioned_user_id) DO NOTHING
                        \"\"\",
                        [
                            (int(message["id"]), uid, f"@{uid}", False, False)
                            for uid in mention_targets
                        ],
                    )"""

new_execute_mentions = """                if mention_targets:
                    if HAS_PSYCOPG2 and not self._is_sqlite:
                        execute_values(
                            cur,
                            \"\"\"
                            INSERT INTO chat_message_mentions (
                                message_id,
                                mentioned_user_id,
                                mention_text,
                                is_notified,
                                is_read
                            )
                            VALUES %s
                            ON CONFLICT (message_id, mentioned_user_id) DO NOTHING
                            \"\"\",
                            [
                                (int(message["id"]), uid, f"@{uid}", False, False)
                                for uid in mention_targets
                            ],
                        )
                    else:
                        for uid in mention_targets:
                            cur.execute(
                                \"\"\"INSERT OR IGNORE INTO chat_message_mentions
                                (message_id, mentioned_user_id, mention_text, is_notified, is_read)
                                VALUES (%s, %s, %s, %s, %s)\"\"\",
                                (int(message["id"]), uid, f"@{uid}", False, False),
                            )"""

content = content.replace(old_execute_values, new_execute_mentions)

# Fix the ::jsonb cast in the INSERT INTO chat_messages VALUES
content = content.replace("%s::jsonb)", "%s)")
content = content.replace("::jsonb", "")

# ============================================================================
# 5. Fix ANY(%s) patterns used in _get_mentions_map and _get_attachments_map
# ============================================================================
# Replace _get_mentions_map
old_mentions_map = '''    def _get_mentions_map(self, message_ids: list[int]) -> dict[int, list[str]]:
        ids = [int(mid) for mid in message_ids if int(mid) > 0]
        if not ids:
            return {}

        out: dict[int, list[str]] = {mid: [] for mid in ids}
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT message_id, mentioned_user_id
                    FROM chat_message_mentions
                    WHERE message_id = ANY(%s)
                    ORDER BY message_id ASC, mentioned_user_id ASC
                    """,
                    (ids,),
                )
                for row in cur.fetchall():
                    msg_id = int(row[0])
                    out.setdefault(msg_id, []).append(str(row[1]))
        return out'''

new_mentions_map = '''    def _get_mentions_map(self, message_ids: list[int]) -> dict[int, list[str]]:
        ids = [int(mid) for mid in message_ids if int(mid) > 0]
        if not ids:
            return {}

        out: dict[int, list[str]] = {mid: [] for mid in ids}
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                if self._is_sqlite:
                    placeholders = ','.join(['?'] * len(ids))
                    cur.execute(
                        f"SELECT message_id, mentioned_user_id FROM chat_message_mentions WHERE message_id IN ({placeholders}) ORDER BY message_id ASC, mentioned_user_id ASC",
                        tuple(ids),
                    )
                else:
                    cur.execute(
                        """
                        SELECT message_id, mentioned_user_id
                        FROM chat_message_mentions
                        WHERE message_id = ANY(%s)
                        ORDER BY message_id ASC, mentioned_user_id ASC
                        """,
                        (ids,),
                    )
                for row in cur.fetchall():
                    msg_id = int(row["message_id"] if isinstance(row, dict) else row[0])
                    uid = str(row["mentioned_user_id"] if isinstance(row, dict) else row[1])
                    out.setdefault(msg_id, []).append(uid)
        return out'''

content = content.replace(old_mentions_map, new_mentions_map)

# Replace _get_attachments_map
old_attachments_map = '''    def _get_attachments_map(self, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        ids = [int(mid) for mid in message_ids if int(mid) > 0]
        if not ids:
            return {}

        out: dict[int, list[dict[str, Any]]] = {mid: [] for mid in ids}
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        message_id,
                        id,
                        file_name,
                        file_path,
                        file_size,
                        mime_type,
                        uploaded_by,
                        uploaded_at
                    FROM chat_file_attachments
                    WHERE message_id = ANY(%s)
                    ORDER BY message_id ASC, uploaded_at ASC
                    """,
                    (ids,),
                )
                for row in cur.fetchall():
                    msg_id = int(row.get("message_id") or 0)
                    if msg_id <= 0:
                        continue
                    payload = dict(row)
                    payload.pop("message_id", None)
                    out.setdefault(msg_id, []).append(payload)
        return out'''

new_attachments_map = '''    def _get_attachments_map(self, message_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        ids = [int(mid) for mid in message_ids if int(mid) > 0]
        if not ids:
            return {}

        out: dict[int, list[dict[str, Any]]] = {mid: [] for mid in ids}
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if self._is_sqlite:
                    placeholders = ','.join(['?'] * len(ids))
                    cur.execute(
                        f"""SELECT message_id, id, file_name, file_path, file_size,
                            mime_type, uploaded_by, uploaded_at
                        FROM chat_file_attachments
                        WHERE message_id IN ({placeholders})
                        ORDER BY message_id ASC, uploaded_at ASC""",
                        tuple(ids),
                    )
                else:
                    cur.execute(
                        """
                        SELECT
                            message_id, id, file_name, file_path, file_size,
                            mime_type, uploaded_by, uploaded_at
                        FROM chat_file_attachments
                        WHERE message_id = ANY(%s)
                        ORDER BY message_id ASC, uploaded_at ASC
                        """,
                        (ids,),
                    )
                for row in cur.fetchall():
                    msg_id = int(row.get("message_id") or 0) if isinstance(row, dict) else int(row[0] or 0)
                    if msg_id <= 0:
                        continue
                    payload = dict(row) if isinstance(row, dict) else dict(zip(
                        ["message_id", "id", "file_name", "file_path", "file_size", "mime_type", "uploaded_by", "uploaded_at"], row
                    ))
                    payload.pop("message_id", None)
                    out.setdefault(msg_id, []).append(payload)
        return out'''

content = content.replace(old_attachments_map, new_attachments_map)

# ============================================================================
# 6. Fix search_messages: to_tsvector/plainto_tsquery → LIKE for SQLite
# ============================================================================
old_search_filter = '''        if query:
            normalized_query = str(query).strip()
            if normalized_query:
                where.append(
                    "("
                    "to_tsvector('simple', COALESCE(m.content, '')) @@ plainto_tsquery('simple', %s) "
                    "OR m.content ILIKE %s"
                    ")"
                )
                params.append(normalized_query)
                params.append(f"%{normalized_query}%")'''

new_search_filter = '''        if query:
            normalized_query = str(query).strip()
            if normalized_query:
                if self._is_sqlite:
                    where.append("m.content LIKE %s")
                    params.append(f"%{normalized_query}%")
                else:
                    where.append(
                        "("
                        "to_tsvector('simple', COALESCE(m.content, '')) @@ plainto_tsquery('simple', %s) "
                        "OR m.content ILIKE %s"
                        ")"
                    )
                    params.append(normalized_query)
                    params.append(f"%{normalized_query}%")'''

content = content.replace(old_search_filter, new_search_filter)

# ============================================================================
# 7. Fix mark_read: GREATEST function and UPDATE ... FROM syntax
# ============================================================================
old_mark_read_insert = """                    INSERT INTO chat_conversation_members (
                        conversation_id,
                        user_id,
                        role,
                        last_read_message_id,
                        last_read_at
                    )
                    VALUES (%s, %s, 'member', %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (conversation_id, user_id) DO UPDATE
                    SET last_read_message_id = GREATEST(COALESCE(chat_conversation_members.last_read_message_id, 0), EXCLUDED.last_read_message_id),
                        last_read_at = CURRENT_TIMESTAMP
                    RETURNING conversation_id, user_id, last_read_message_id, last_read_at"""

new_mark_read_insert = """                    INSERT INTO chat_conversation_members (
                        conversation_id,
                        user_id,
                        role,
                        last_read_message_id,
                        last_read_at
                    )
                    VALUES (%s, %s, 'member', %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (conversation_id, user_id) DO UPDATE
                    SET last_read_message_id = MAX(COALESCE(chat_conversation_members.last_read_message_id, 0), EXCLUDED.last_read_message_id),
                        last_read_at = CURRENT_TIMESTAMP
                    RETURNING conversation_id, user_id, last_read_message_id, last_read_at"""

content = content.replace(old_mark_read_insert, new_mark_read_insert)

# Fix the UPDATE ... FROM (PostgreSQL-specific) for mark_read mentions
old_update_mentions = """                    UPDATE chat_message_mentions mm
                    SET is_read = TRUE
                    FROM chat_messages m
                    WHERE mm.message_id = m.id
                      AND m.conversation_id = %s
                      AND m.id <= %s
                      AND mm.mentioned_user_id = %s"""

new_update_mentions = """                    UPDATE chat_message_mentions
                    SET is_read = TRUE
                    WHERE message_id IN (
                        SELECT m.id FROM chat_messages m
                        WHERE m.conversation_id = %s AND m.id <= %s
                    )
                    AND mentioned_user_id = %s"""

content = content.replace(old_update_mentions, new_update_mentions)

# ============================================================================
# Write the result
# ============================================================================
with open(TARGET, 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)

print(f"SUCCESS: Refactored {TARGET}")
print(f"  - File size: {len(content)} bytes")
print(f"  - Lines: {content.count(chr(10)) + 1}")
