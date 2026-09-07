# ADR 0003 — Page marks, and what `/api/state` sends now

Date: 2026-09-07
Status: Accepted. Implemented in `prepwright/pagestate.py`, `prepwright/state.py`,
`bridge.py` and `index.html`. `progress/state.json` is imported once and renamed.

## Context

ADR 0002 chose delta writes into one SQLite database per track and said the store
was not yet called by `bridge.py`. Both stores then ran side by side: the page kept
posting a whole `state.json` and the bridge kept writing it, while
`prepwright/state.py` sat unused. Two stores describing the same thing is a defect
that gets worse with every hour of work written against the wrong one.

Wiring the page onto the store surfaced an impedance mismatch. The schema in ADR
0002 models the real flow: a gap becomes a step, a step carries turns, a turn cites
a corpus section. The page holds twenty fields, and only one of them, `stepLog`,
is a transcript. The rest are a topic ticked off, a practice status, a check
result, a banked question, a preference.

## Decision

**A transcript is a `turn`. Everything else is a `mark`.**

`mark` is one append-only table: `(seq, at_utc, kind, key, value, value_bytes,
client_op_id)`, with an index on `(kind, key, seq DESC)` and the same pair of
ABORT triggers that `gap_history` and `card_review` carry. The current value of a
key is the newest row for it. The older rows are its undo history.

`prepwright/pagestate.py` holds the whole mapping in one table, `FIELDS`, and the
bridge serves that table to the page inside the GET reply. The page builds its diff
from what the bridge sent rather than from a second copy of the rules, so adding a
field is one edit.

Three amendments to the schema of ADR 0002 came with it.

`track_meta.bytes_marks`, a counted byte class for marks, charged inside the same
transaction as the row, exactly as `bytes_turns` is. `MARKS_BYTES_CAP` is 256 KiB
and `MARK_MAX_BYTES` is 4 KiB.

`turn.client_meta`, one nullable TEXT column carrying what the page needs to redraw
a turn: which provider answered, the resolved model id, the effort flag, the token
and cost figures, and the page's own role word. `tail()` selects `seq`, `role`,
`body` and `body_bytes` only, so the column has no path into a prompt. It exists
because the page has four role words and the schema has three: an end-of-session
review is stored as `system`, which also keeps it out of reach of
`compact_oldest_completed_step`, and `system` alone cannot say which of the two it
was.

`add_step` allocates `ord` inside its write transaction when the caller passes
`None`. Read outside, two clients adding their first step in the same moment both
see the same maximum and the second dies on `step.ord`'s UNIQUE constraint.

## No monotonicity rule, on purpose

The obvious next move is to refuse a mark that goes backwards: a ticked topic
un-ticking, a practice status returning to `todo`. It is the wrong move.

The page diffs against the document the bridge last acknowledged. A value that goes
backwards in that diff went backwards because the candidate did it, and the UI
offers un-ticking. Refusing it would break a feature to defend against a case
deltas already make impossible: a stale tab cannot emit an op that removes
anything, because no op removes anything. What protects the old value is that it is
still on disk under its own `seq`.

## What the page can no longer do

The Import button merged rather than replaced from the moment this landed. An
append-only store cannot be made to forget, so a document cannot subtract from it
and there is no honest way to make that button mean "throw away what is saved".
The `replace` intent still buys the refusal: a bulk write from a view behind the
store gets a 409 with the real document instead of being quietly folded in. A true
replace belongs to the track switcher, as a new track built from the file, and
that is not built yet.

Two fields the page holds are deliberately not persisted, and `pagestate.FIELDS`
says so beside the table. `assessList` is derivable from `assess` and grows with
the curriculum, so persisting it would put an unbounded value behind a 4 KiB
per-mark cap and freeze saves the day it crossed. `_seq` is a per-page id counter
and `makeId()` already mixes in `Date.now()`. `savedAt` is now derived by the
bridge from the newest stored row, because a client clock written into a field the
store owns is how a stale tab used to win a tie-break it should have lost.

## Consequences

`progress/state.json`, its rotating `backups/`, its `high-water.json`, the revision
hash, `_regression_reason` and `MAX_UNMARK_PER_WRITE` are all gone: 283 lines out of
`bridge.py`. The old file is read once at startup, from `state.json` or from the
newest snapshot that still parses, applied as ops, and renamed. Never deleted.

Each request opens and closes its own handle. `sqlite3` refuses a connection used
from a thread other than the one that made it and this is a threading server, so
the alternative was relaxing `check_same_thread` for the whole package to suit one
caller. The cost is one connection open per save. What it buys, beyond thread
safety, is that `assert_live`, `heartbeat()` and `backup()` all acquired real
callers: every write asserts its generation, which beats the lease, and every
handle that wrote backs up on close under the published interval.

There is no compaction rung for marks. At the cap a write is refused with the cap
named, rather than reclaiming superseded rows the way rung 6 reclaims tutor prose.
The refusal is the same failure the design rejected for turns, and the reason it is
acceptable here is arithmetic rather than principle: a ticked topic charges a
measured 144 bytes, so 256 KiB is roughly 1,800 of those or 800 larger marks, and
a worked track produces a few hundred. If that stops being true, the
rung to build is a receipted compaction of superseded non-append marks, in the
shape `turn_compaction` already has.
