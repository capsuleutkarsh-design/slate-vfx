"""Quick smoke test for the refactored messenger server modules."""
import sys
sys.path.insert(0, '.')

from ut_messenger.server.v2_store import MessengerV2Store
print("1. MessengerV2Store imported OK")

s = MessengerV2Store()
backend = "SQLite" if s._is_sqlite else "PostgreSQL"
print(f"2. Backend detected: {backend}")

s.ensure_schema()
print("3. ensure_schema() completed OK")

from ut_messenger.server.database import ChatDatabaseFunctions, init_chat_tables
print("4. ChatDatabaseFunctions imported OK")

init_chat_tables()
print("5. init_chat_tables() completed OK")

chat_db = ChatDatabaseFunctions()
print("6. ChatDatabaseFunctions instantiated OK")

print("\nAll checks passed! Server should start without crashing.")
