# Slate, for IT

Three screens are yours: the service desk, the provisioning half of joining and
leaving, and licences. You take leave like anyone else, and you reach that through
your own **Leave** screen.

---

## The service desk

**Tickets** opens on the whole queue, sorted the way you should work it: breached
first, then at risk, then by priority, then by how long is left.

Five figures across the top — **Open**, **Breached**, **At risk**, **Unassigned**,
**Mine**.

### Priority is derived, not chosen

Nobody picks P1. The person raising the ticket answers two questions in plain English
— who is affected, and whether they can carry on — and the priority falls out of an
impact × urgency grid.

| | Cannot work | Workaround, costing time | Can wait |
|---|---|---|---|
| **Whole studio** | P1 | P2 | P2 |
| **A department** | P2 | P3 | P3 |
| **One person** | P3 | P3 | P4 |

This is why the queue is trustworthy: it is not a negotiation.

### Two clocks

**Response** is attention — you have looked at it and said something. **Resolution**
is a fix.

| | Response | Resolution |
|---|---|---|
| P1 | 15 min | 4 hours |
| P2 | 30 min | 8 hours |
| P3 | 2 hours | 3 days |
| P4 | 4 hours | 5 days |

**Take** assigns it to you. **Mark responded** stops the response clock — do it when
you actually reply, not when you open the ticket.

Moving a ticket to **Waiting on you** hands it back to the person who raised it, and
they will see it.

---

## Joining and leaving

**Joining & Leaving** shows you the provisioning half. HR start people; you do not.
That division is the point — IT provisioning somebody nobody hired is how ghost
accounts happen.

Your lines:

- Domain account created
- Email and calendar set up
- Workstation issued
- DCC software installed
- Project shares mounted
- Render farm access

And on the way out, the same list undone: render farm revoked, shares unmounted,
workstation returned, mailbox archived and forwarded, account disabled.

### Issuing a machine

Select the person, press **Issue machine**, pick from what is free. Slate then:

- writes the assignment, with the date and your name
- marks the machine **Active** in Hardware and records who holds it
- ticks *Workstation issued* and stamps the machine name on that row

That last part is why offboarding works. When that person leaves, the leaving
checklist already knows which machine to ask for — nobody has to remember.

A machine flagged **Repair** is never offered, and collecting one back does not clear
that flag: it is free of its owner but not free to hand to somebody else.

### Kit not returned

A red card at the top of the screen, shown only when it is true: machines still out
with people who are being offboarded. A machine nobody asked for back is invisible
until something asks this question.

---

## Licences

**Licences** is a compliance and renewal read, not an inventory. The question it
answers is the one you get asked: *are we licensed, and are we paying for seats
nobody uses?*

Both cost money, in opposite directions.

### Peak, not total

The honest measure of a floating licence is **peak concurrent use**. Twenty artists
and eight Nuke seats is fine if never more than eight are comping at once — and the
only way to know is to have written the number down repeatedly.

**Record usage** does that. Take the reading when the studio is busy; a
quiet-afternoon number makes an over-subscribed licence look fine.

### The findings

| | |
|---|---|
| **Over-subscribed** | more in use than we own. This is the one that ends in an audit letter, and it outranks everything else — an expiry is a diary entry, a shortfall is people unable to work. |
| **Expired** | already gone. Anyone relying on it is stuck. |
| **Renews soon** | inside 45 days. Decide before purchasing needs lead time. |
| **Under-used** | peak never reaches 60% of what was bought. Seats to drop at renewal. |
| **No readings** | bought, never measured. Nothing to renew against except the invoice. |

Every row carries a sentence saying what it means in numbers, because a status word
on its own is not something you can take to purchasing.

---

## Running the server

`Slate Server.bat` starts Central Server — PostgreSQL, the PgBouncer pooler and sync.

Workstations do not need PostgreSQL installed. `setup.bat` skips it unless you pass
`/server`, and a workstation with no reachable database falls back to a local SQLite
copy rather than refusing to start.

`pgbouncer.ini` and `userlist.txt` are written by the server at startup and are
git-ignored. Do not edit them by hand; they are overwritten.
