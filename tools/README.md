# tools/

Maintenance and seeding scripts. Run them with Slate's own Python so they see
the same dependencies the application does:

```
runtime\python\python.exe tools\<script>.py
```

Each reads its database settings through
`slate/core/infra/local_secrets.py`, so none of them carries a password.
Set `SLATE_DB_PASSWORD` for a one-off session, or let it read the local config
`setup.bat` wrote.

---

## seed_workplace_demo.py

Fills Leave, Tickets, Hardware, Joining & Leaving and Licences with realistic
data so the screens can be looked at before a studio has its own.

```
runtime\python\python.exe tools\seed_workplace_demo.py
runtime\python\python.exe tools\seed_workplace_demo.py --clear
```

Everything it writes is tagged `[demo]`, and `--clear` removes exactly what it
wrote and nothing else. The licences it creates are deliberately one of each
finding — over-subscribed, expired, renewing soon, under-used, never measured —
so the screen opens showing all five kinds of answer rather than five rows of
"fine".

Safe to run twice: it clears its own records first.

---

## utilities/

Smaller one-off scripts. `create_client_config.py` writes a per-site
`client_config.json` for a machine that needs settings different from the
shipped defaults.
