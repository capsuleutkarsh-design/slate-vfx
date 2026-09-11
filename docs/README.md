# Slate documentation

Two audiences, kept apart on purpose.

## If you use Slate

**[The studio guide](guide/)** — written for the people who use it, not the
people who build it. Slate shows you what your job needs and hides what it does
not, so each page is a complete account of one person's side of the software.

| | |
|---|---|
| **[Artists](guide/artists.md)** | Asking for leave, why a day off sometimes costs three, raising a ticket, attendance |
| **[Supervisors and leads](guide/supervisors.md)** | The first of the two leave approvals, and the production screens |
| **[HR](guide/hr.md)** | The approval queue, the holiday calendar, the year end, joining and leaving |
| **[IT](guide/it.md)** | The service desk, provisioning, machines, licences, the server |

## If you run or build Slate

| | |
|---|---|
| **[Installing it](install.md)** | `setup.bat`, what it downloads, the server, and what to do when it goes wrong |
| **[Architecture](architecture.md)** | How it is actually put together — layers, the database, threads, the design system |
| **[Working on it](development.md)** | The four conventions, adding a screen, changing the schema, testing |

---

### A note on these pages

The documentation that used to sit here described a different application:
PyQt6 rather than PySide6, SQLite as the primary backend rather than a fallback,
a FastAPI server that no longer exists, and tab names nobody would recognise.
It was deleted rather than patched.

These pages were written against the code as it stands. If one of them
disagrees with the source, the source is right and the page is a bug — please
raise it.
