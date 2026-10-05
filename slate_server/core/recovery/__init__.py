"""
Getting back into Slate when nobody can sign in.

Unlocked by "the server PC + the Recovery Key":

    * it runs only on the PC that holds the studio's database, by somebody who
      can read and write that database's folder (layout.assert_server_pc);
    * it needs the Recovery Key, printed when the server was set up
      (key.py - stored only as a slow scrypt hash, wrong guesses are slowed
      down, never locked out for good);
    * it never needs a Slate or database password. It gets in through a
      loopback-only, time-boxed trust window (trust_window.py) that is closed
      in a finally, by a watchdog timer, and - if the PC died in the middle -
      at the next start, from a marker file (marker.py).

What it can do (actions.py): reset any account's password and switch it back
on, create or restore an administrator, set the workstations' database
password and the superuser password separately, turn security switches off,
restore the last automatic snapshot (snapshots.py), and run a plain health
check (health.py).

    Recover Slate.bat                 (checkout)  -> slate_recover.py
    Slate_Server.exe --recover        (installed)
    the Recovery page in the Slate Server window

docs/RECOVERY.md is the studio administrator's guide.
"""
