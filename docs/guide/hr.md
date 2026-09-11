# Slate, for HR

You have the second half of every leave decision, the holiday calendar, the year end,
and the paperwork half of joining and leaving. IT have their own half and you will not
see it — nobody is shown a checklist they cannot act on.

---

## The leave queue

**Leave** opens on *Waiting on me*. Requests arrive here once the supervisor has
already approved them, so what you are deciding is the policy question, not the
"can we spare them" question.

Sorting is deliberate: yours first, then by start date. Somebody leaving on Monday
needs an answer before somebody leaving next month.

Four figures across the top:

| | |
|---|---|
| **Waiting on you** | your decision, nobody else's |
| **Elsewhere in the chain** | sitting with a supervisor |
| **Away today** | approved and actually out right now |
| **Requests in total** | all time |

**Approve** confirms what your approval means before it happens — at your stage it is
final and the days are deducted. **Reject** requires a reason, and the person who
asked will read it.

You can only act on rows at your stage. Selecting a request still with the supervisor
leaves the buttons off rather than failing when you press them.

---

## The holiday calendar

**Leave → Calendar.**

Every date on this list is free for the studio, and the sandwich rule is measured
against it. Getting it wrong changes what leave costs everybody, so it is worth doing
in one sitting at the start of the year.

Add a date, a name, and who it applies to (*All*, or a single location for a
site-specific holiday). Past holidays are dimmed so your eye stays on the rest of the
year.

Removing a holiday does not rewrite history: leave already taken keeps the day count
it was charged at. Only future requests change.

---

## The year end

**Leave → Year end.**

This is the one destructive thing in the module, so it is a preview first and a
confirmation second.

Closing a year draws a line under it. Up to the carry-forward cap (12 days by
default) moves into the next year; anything above it is lost. The table shows, per
person, the balance at year end, what carries and what lapses — with the lapsing
column in red — *before* anything is written.

Some things to know:

- **Default to the year just gone.** Closing the year you are still in lapses leave people have not had a chance to take.
- **A year already closed is never closed twice.** Those people show as *already closed* and are skipped.
- **After a close, balances count from the line.** Accrual restarts, the carried days are the opening balance, and leave taken before the line is not deducted again.

Without a close, the carry-forward cap never actually applies — it is a policy you
believe you have rather than one the software enforces.

---

## Joining and leaving

**Joining & Leaving** is one record read in two directions, and you see the paperwork
half of it.

### Starting somebody

**Start joining** puts them on the list. Pick the person, whether they are staff or
freelance, and their department.

Freelancers skip payroll and final settlement automatically — the rest of the
checklist is the same.

Your lines are the ones only HR can do:

- Signed contract received
- ID and address proof on file
- Added to payroll *(staff only)*
- Studio induction and policies
- Introduced to the team

IT's lines — accounts, email, workstation, software, shares, render farm — appear on
their screen, not yours. The **All** column tells you how much is outstanding across
both teams, so you can see whether somebody is actually ready to start.

### When somebody leaves

**Start leaving** lays down the mirror image. It is not a different list — it is the
same list undone, which is the only reason offboarding can be systematic rather than
approximate.

If a machine is still out with somebody being offboarded, a red **Kit not returned**
card appears at the top of the screen. It is only there when it is true.

---

## The policy Slate is enforcing

| | |
|---|---|
| Working week | Monday to Saturday; Sunday is the weekly off |
| Accrual | 2 days per completed month, from the joining date |
| A month completes | on the joining day's anniversary, not at month end |
| Carry forward | capped at 12 days; the rest lapses at the year end |
| Sandwich rule | on — a working day skipped between two non-working days charges the block |
| Comp off | off by default; the engine exists for studios that operate it |
| Approval | supervisor, then HR |

These live in `slate/core/domain/leave_policy.py`, defined once. Changing a number
there changes it everywhere — the artist's preview, your queue and the year end all
read the same rules.
