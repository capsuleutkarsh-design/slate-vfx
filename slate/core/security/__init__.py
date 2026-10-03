"""
The safety net every security change in Slate stands on.

Earlier hardening locked a studio out of its own database with no way back in
(see tests/test_server_lockout.py). So before any security item is fixed, these
exist and are tested:

    switches     one place where every security feature is turned on or off;
                 all of them start off, and the recovery tool can force any of
                 them off again even when the database cannot be reached
    admin_guard  the last active administrator can never be deleted,
                 deactivated or demoted - in the domain layer, not only on screen
    precheck     can_still_get_in(): the question every hardening step must ask,
                 and get "yes" to, before it is applied

The server-side half (the Recovery Key, the loopback trust window, automatic
snapshots before a change, and the recovery tool) lives in
slate_server/core/recovery. The rules for using all of it are in
AUDIT/SECURITY_FOUNDATION_NOTES.md and docs/RECOVERY.md.
"""
