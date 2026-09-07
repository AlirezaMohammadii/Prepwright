# ADR 0002 — SQLite per track, with published caps and a published eviction order

Date: 2026-09-07
Status: Accepted. Implemented in `prepwright/{config,state,track,keep}.py`.
Amended by ADR 0003, which wired the store into `bridge.py` and replaced
`progress/state.json` with per-track delta writes. The Status field below the
Context describes the position before that cutover.

## Context

One curriculum in a single `state.json`, guarded by a revision hash and
snapshotted into one global backup directory, is the scheme `bridge.py` runs today.
It fails in two ways even at one track. Snapshots of a whole document accumulate
faster than the work inside them grows, so the backup directory becomes the largest
thing on disk. And a revision counter answers only "did you read the current file",
never "is what you are sending poorer than what is already there", so a browser tab
holding a stale copy can write it over a much richer one and be told the save
succeeded.

Multiply both by the number of job applications and neither is survivable. The
candidate set the constraint directly: storage must not grow without bound, stale
tracks must be killable under their control, and no track's material may reach a
prompt built for another.

## Decision

SQLite, which is standard library, one database per track plus a small library
registry. Writes are deltas rather than whole documents, so a stale client cannot
express a loss rather than merely being detected making one.

Caps are enforced at the write, inside the same transaction, against counters the
write itself maintains. A cap checked against a number refreshed at startup is not
a cap.

Every byte class is budgeted with arithmetic that can be checked: corpus 640 KiB
per track, turns 3 MiB, cards 320 KiB, and a hot track ceiling of 13 MiB with WAL,
index, spool and backups counted. The three-year projection at 25 tracks a year is
91.6 MiB.

Eviction runs in a published order. Age drives the ladder and pressure is only a
backstop, because a pressure threshold alone on a library this size would rarely
fire, which would leave the bound resting on the candidate's diligence.

Cross-track isolation is a path mechanism, not a convention. A prompt is assembled
from exactly one track's subtree, resolved with realpath, refusing symlinks.

The full design is in `DESIGN-state-corpus.md`, produced by a four-way design panel
with two adversarial judges per design. Judge means were 5.5 to 6.5 out of 10, so
it is a strong draft rather than a settled answer.

## Consequences

The cutover happened in ADR 0003. `read_state` and `write_state` are gone from
`bridge.py`, along with the revision hash, the rotating snapshots and the
high-water file, and `/api/state` now reads and writes `track.db` through
`prepwright/pagestate.py`. Prepwright still serves one track at a time, chosen by
`current_track_id()`, even though the store beside it already holds many; the
track switcher is the piece that is still missing.

Anything not written by the candidate can be regenerated, so it is not backed up.
That is the whole reason the numbers stay small, and it is a bet: it assumes research
and generation stay cheap enough to repeat.

## Alternatives rejected

JSON files with a revision hash. A revision proves the client read the current file
and proves nothing about what it is carrying.

A shared content-addressed blob store across tracks. Deduplication saves single-digit
megabytes and buys a refcount table, a collector, and a live pointer from one track's
prompt into another track's bytes. That is the isolation property, sold for a rounding
error.

Storing raw fetched sources next to the distilled ones. Ten to forty times the entire
per-track budget for PDFs. Source URL, fetch date and content hash falsify a citation
years later just as well.
