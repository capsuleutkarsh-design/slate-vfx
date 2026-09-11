# Database & Concurrency Architecture

UT_VFX is designed to handle extremely high-throughput media ingestion and metadata syncing while keeping the PyQt6 user interface buttery smooth. To achieve this, the architecture relies heavily on multithreaded SQLite access and specialized asynchronous execution wrappers.

---

## 1. The Database Backend (`SQLiteManager`)

While the original system was designed with `PostgresManager` in mind, `SQLiteManager` is implemented as a direct, drop-in replacement that relies exclusively on the standard library.

### Location & Setup
The database file `ut_vfx.db` is dynamically resolved in the following priority:
1. `db_path` override in `GlobalConfig` (for remote shared drives in studio environments).
2. `%LOCALAPPDATA%/UTVFX/ut_vfx.db` (Default fallback for local installations).

### On-The-Fly SQL Translation
To maintain 100% compatibility with PostgreSQL logic without changing queries at the domain layer, `SQLiteManager._translate_sql()` parses incoming queries and performs dynamic translation:
- Converts positional parameters `%s` to `?`.
- Converts `ILIKE` to `LIKE` (which is case-insensitive for ASCII in SQLite).
- Converts `TRUNCATE TABLE x` to `DELETE FROM x`.
- Strips PostgreSQL typecasting like `::jsonb` or `::JSONB`.
- Removes `RETURNING id` clauses using regex, and intercepts the result via `cursor.lastrowid` instead.

### Dictionary-like Access
To replicate the behavior of `psycopg2.extras.RealDictCursor`, `SQLiteManager` overrides the SQLite connection row factory with a custom `_dict_factory` that zips the cursor description (column names) with the row tuple. This guarantees that all repository methods return `dict` objects instead of raw tuples.

---

## 2. Concurrency & Thread Safety

SQLite notoriously throws `database is locked` errors when accessed by multiple threads. UT_VFX mitigates this using three distinct strategies:

### A. Thread-Local Connection Pooling
Instead of sharing a single database connection across the application, `SQLiteManager` leverages Python's `threading.local()`. 
Every thread (e.g., the Main GUI thread, background scanning threads, async network listeners) instantiates its own `sqlite3.Connection` object when it first calls `_get_conn()`. 
- `check_same_thread=False` allows the connection to persist inside the thread pool without throwing errors.
- `timeout=30` ensures threads wait gracefully for locks rather than immediately faulting.

### B. Write-Ahead Logging (WAL)
Upon connection, the database executes:
```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=10000;
```
Write-Ahead Logging allows readers to continue reading the database *while* a writer is writing to it. This means the UI can populate lists of shots at the exact same moment a background worker is ingesting 5,000 new image sequence paths.

### C. Context-Managed Transactions
Explicit transaction blocks are defined using the `@contextmanager def transaction()` decorator. When a repository method calls `with db.transaction() as conn:`, it guarantees that exceptions cause a safe `conn.rollback()` before propagating, preventing dirty writes from holding the database lock.

---

## 3. Asynchronous GUI Execution (`DatabaseWorker`)

To prevent database latency from freezing the Qt Event Loop, UT_VFX uses `db_worker.py`. This module provides the `run_db_async` function, which wraps any database callable into a `QRunnable` and sends it to the `QThreadPool.globalInstance()`.

### The `wrapped C++ object has been deleted` Problem
A common bug in PyQt applications occurs when an async thread finishes and tries to emit a signal to a UI widget that the user has already closed. The underlying C++ object is destroyed, but the Python wrapper still exists, resulting in a fatal crash.

UT_VFX solves this cleanly using **Safe Callbacks**:
- `_wrap_safe_callback()` inspects the callback receiver (`on_success` or `on_error`) using `__self__`.
- It creates a `weakref` to the `QObject`.
- Before invoking the callback, it verifies that the widget is still alive using Shiboken: `shiboken6.isValid(owner)`.
- If the widget is destroyed, the async thread silently drops the data, guaranteeing absolute stability during rapid tab switching or application teardowns.
