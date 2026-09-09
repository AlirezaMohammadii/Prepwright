# ADR 0005 — A schema call costs two turns, and the app stops claiming what it does not do

Date: 2026-09-09
Status: Accepted. Implemented in `bridge.py`, `index.html`, `prepwright/state.py`
and `tests/test_back_half.py`.

## Context

The back half of this product had never executed. Not once, on any machine, on
any track. Measured on 2026-09-09 across all three live track databases before
anything was changed: `assessment` 0 rows, `card` 0 rows, `card_review` 0 rows,
and `mark` carrying no row of kind `session`, `qa`, `assess` or `card`. Teaching
had produced five turns on one track and two on another. Nothing had ever been
graded, no session had ever been closed, no recap card had ever existed.

The suite was green at 357 tests and had been for two sessions. It was green
over the third of the product that worked.

Driving it in a browser, on the Wingtip track, with real model calls, found the
reason in the first click. `CLI_BASE` pins `--max-turns 1`, and a reply
constrained by `--json-schema` needs two: the model answers, then it emits the
structured output. The CLI exits 1 having printed a complete envelope whose
`subtype` is `error_max_turns`, whose `result` is null, and whose stderr is
empty. `_cli_reason` read only `result`, and only when it was a string, so the
operator's terminal said `Claude tutor CLI failed (exit 1)` with nothing after
it, and the browser said "The grader returned nothing — try again", which is an
instruction that could never work.

Three of the four model-backed features run on `CLI_BASE` with a schema:
`/api/assess`, `/api/review`, and the diagnostic judge. All three were dead. The
fourth, source discovery, runs on `CLI_SEARCH` with `--max-turns 6` and had
always worked, which is why every track has documents and no track has a graded
gap. The evidence is still on disk: 17 of 17 gaps on the Proseware track read
`No answer given`, and 20 of 20 on the Wingtip track read `graded without a model:
length only`, which is the fallback the judge falls back to when it fails.

The walk continued past that fix and found five more things the app claims and
does not do. They are the subject of the second half of this decision.

## Decision

**A schema-constrained call gets exactly two turns, and only that call.**
`CLI_SCHEMA_MAX_TURNS` is `"2"`, applied in `_build_claude_cmd` on the
non-search path only. 2 is measured rather than chosen: on claude 2.1.251 every
schema call reports `num_turns` 2, and raising the cap to 3 does not change it.
The bound stays at 1 for teaching, which has no tools and no schema, because
`--max-turns` is a safety property and relaxing it globally to fix one path
would have widened a guarantee that had nothing to do with the failure. A
schema-validation retry inside the CLI would need a third turn.
`assess_via_cli` and `review_via_cli` already retry the whole batch once, so
that case is covered a level up.

**A failure that happens before the model produces prose still has to name
itself.** `_cli_reason` now falls back to `subtype`, `terminal_reason` and
`api_error_status` when `result` is not a usable string. This is the fix that
matters most for the next bug of this kind, because it is the one that turned a
single wrong flag into three broken features and no way to see why.

**A grade is bounded where it enters, not where it is displayed.**
`ASSESS_SCHEMA` now declares `mastery` as 0 to 1, and `_clean_rows` enforces it
on the way out of every batch. The first real grading call this project ever
made returned `mastery: 45`. A schema is a request, not a guarantee. The three
cases resolve deliberately: 1 to 100 is a percent written where a fraction was
asked for and is divided, so 45 becomes 0.45. Anything else, including a
negative, a value above 100 and NaN, drops the row so the step reads as
ungraded. Clamping 45 upward to 1.0 would have turned a partial answer into a
perfect score and armed the "Mark done" button, which is gated on 0.85. The
grader's own system prompt calls an unearned pass the expensive mistake, so
ambiguity resolves downward.

**A batch that came back empty is a failure.** It used to reach `break` before
the counter that owns `failed`, so `capped` stayed 0 and the toast read "13
steps graded" with no suffix while five steps sat at 0%, indistinguishable from
steps that were never in scope. Usage is now charged before the row check,
because the call was made and billed whether or not it came back usable.

**A route validates its own payload before it consults the machine.** Both
`/api/assess` and `/api/review` selected a provider first, so a malformed step
key on a machine with no model reported "model calls are disabled", which is the
wrong reason, and the same diagnosability failure as the one above. The payload check
is local and free. It goes first.

**The recap drill is a shuffled bank and now says so.** The kicker read "Recap
drill · spaced repetition" and the copy promised that a missed card stays on the
weak list "until you answer it cold". Neither was true. There are no intervals,
no due dates and no ease. `markRecap` is the only writer, `got` is read by no
decision anywhere in the page, and `missed` is only ever incremented. Walked in
a browser: a card was missed three times, then answered correctly twice, and the
weak list still showed it at ×3. The copy now describes the lap, the reshuffle,
and a count that records rather than clears.

**The SM-2 scheduler stays for now, and the reason is a scope correction rather
than a preference.** The decision taken at the start of this session was to drop
the `card` and `card_review` tables and correct the label, on the understanding
that this was the small option. The label was the user-visible half and it is
fixed. The table removal is not the small option, and the evidence is recorded
here so the choice can be made again on real numbers.

Removing it is a 37-point surface across ten files. `card_review.card_id`
references `card(card_id)` and `PRAGMA foreign_keys = ON` is set on every open,
so `DROP TABLE card` fails while any `card_review` row exists. That failure is
data-dependent, which is the trap: with an empty child table the wrong drop
order succeeds, so a migration tested on a fresh database passes and then breaks
for the first person who ever graded a card. The safe order is `card_review`
first. Measured on a throwaway database: `DROP TABLE` does not fire a
`BEFORE DELETE` trigger, so the append-only triggers neither block the drop nor
need dropping first, and a failed drop rolls back completely.

`CARDS_BYTES_CAP` is 327,680 and is one of four terms in the `TRACK_DB_CAP`
budget, which a test asserts by arithmetic. `archive_track` selects from
`due_card` in the middle of archiving. `keep.recount` writes `track.n_cards`.
And `DESIGN-state-corpus.md` presents cross-track spaced repetition as a named
architectural layer, with the archive design writing `cards.jsonl` outside the
tar specifically so cold cards stay reviewable. Removing the tables invalidates
a documented layer, which is a different decision from deleting dead code.

Five questions have no recorded answer and are not this session's to invent:
whether `track.n_cards` and `track_meta.bytes_cards` are physically dropped or
left dormant, whether `TRACK_DB_CAP` falls to 4,390,912 or keeps its value with
a corrected comment, whether the design document is rewritten or annotated,
whether any `cards.jsonl` already exists in an archive, and whether the `card`
mark kind that the live drill uses should be renamed once the table of that name
is gone.

Nothing was half-removed. The tables carry zero rows on all three live tracks,
no production code can write one, and a comment now says so at the DDL so the
next reader does not wire them by accident.

**A track still cannot end, for the same reason.** The decision was to derive a
terminal stage from a fact on disk. The fact exists and this session found it:
the newest `mark` of kind `topic` under a gap id, joined to `step.gap_id`, which
is the join `adoptCurriculum` already makes and the one that survives a re-cut
of the plan. Every other candidate is dead. Nothing in production writes
`step.status`, `step.score`, `step.review`, `step.opened_utc` or
`step.completed_utc`, the `assessment` table's only callers are two tests, and
`flow.curriculum.done` counts `step.status == 'done'` and is therefore a
permanent zero sitting beside a comment that says the ladder is derived from
facts on disk.

Building it is a stage-ladder branch, a `STAGES` entry, the derivation itself, a
fix to that permanent zero, a screen for a stage the page currently assumes is
teachable, and a case in the tests that pin the ladder. It also needs one
decision: whether finished means the written plan, which the server can derive
from step rows, or what the page shows, which includes practice and check items
that `adoptCurriculum` invents client-side and that have no step row to join to.
Those two disagree on any track where a practice item was never ticked.

**The store is append-only, so the page stops offering to delete.** The Session
log carried a Delete button under a header reading "An append-only log", and the
Q&A log carried one too. Walked in a browser: the row vanished, the header pill
said "Saved to disk", the `session` mark count in SQLite stayed at 1, and a
reload brought the row back. `computeOps` emits only items present in `cur` and
absent from `ack`, no op removes anything, and `validate_ops` accepts exactly
two op kinds. The delete could never have persisted. Both affordances are gone.

**A Word table is read as rows, not as a column of loose lines.** This one came
from the nineteen round-2 findings that had never been verified, and the
reported symptom was not the defect. The report said `strip_running` deletes a
repeated capitalised status cell, and proposed excluding the weakest heading
rule, which would have let a Title Case running header survive forty times. The
cause is one layer down. `read_docx` walked every `w:p` and emitted each as its
own line, so a three-column register became a column of short lines and a Status
cell repeating "Not Applicable" six times looked exactly like page furniture.
`read_xlsx` and `read_table` have always joined cells with " | ", and
`looks_like_heading` refuses any line containing " | " for precisely this
reason, with a comment naming the artefacts it protects. Only `.docx` never
produced one. Joining the row fixes the reported symptom with no heuristic
trade at all.

## What is deliberately not fixed, and why

**`state.assessList` is still not persisted.** After a re-check, reload the page
and the "Last checked" hint survives while the graded rows under it do not. The
hint is built from `assessAt` and `assessCost`, which are `pref` scalars in
`pagestate.FIELDS`. The list is excluded on purpose, but the reason written
beside the table is wrong and was corrected: it says the list grows with the
curriculum, and it does not. `MAX_ASSESS_STEPS` is 18 and is enforced twice, so
one run returns at most 18 rows whatever the curriculum size. Measured with the
store's own serializer, 18 typical rows are 1,693 bytes against a 4,096-byte
per-mark cap. The real objection is the tail: 18 rows carrying 200-character
reasons are 4,753 bytes and would be refused, and the refusal rejects the whole
delta, so a verbose grading run would wedge every later save until the tab was
reloaded. Only the false comment in `index.html` claiming the opposite was
corrected. The right fix is the one below, and it is larger than this session.

**The `assessment` table is still dead, and this is now the clearest open
question in the project.** `/api/assess` opens no handle and writes nothing. The
grade lives only in a `mark` of kind `assess`, which is durable but is page
state. Writing the grade to the `assessment` table instead would give it a
history rather than a single superseded value, would let the panel be rebuilt
from the store after a reload, and would make the table stop being dead code.
That is three changes across the route, the store and the page, and it is not
work to start at the end of a session that has already changed this much. It is
named here so it is not rediscovered.

## Consequences

The suite went from 357 tests to 393. The 36 new ones are the first coverage
`/api/assess` and `/api/review` have ever had, and they are written against the
contract the walk revealed rather than the one the source implies. One of them
exists because reverting a fix left its own test green: `_clean_rows` had a
direct unit test, and nothing proved `_assess_batch` actually called it.

Every fix in this ADR was proven by reverting it and confirming its own test
fails. That is seven reverts, run mechanically, with `bridge.py` restored and
hash-checked afterwards.

The diagnostic judge is now reachable for the first time. Nothing in this
session re-ran it, so every gap on every existing track still carries the
length-only fallback. A track diagnosed from here will be graded by a model. The
three that already exist will not be, until they are re-run.
