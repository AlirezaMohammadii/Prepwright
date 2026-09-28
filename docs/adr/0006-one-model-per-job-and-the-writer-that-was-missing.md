# ADR 0006 — One model per job, and the writer that was missing

Date: 2026-09-09
Status: accepted
Supersedes the open questions in ADR 0005 that the owner ruled on.

## Context

Session 10 began with three owner rulings and three new requests. The rulings
closed questions ADR 0005 had costed but not decided. The requests were about
making the pair of local tools feel like one product.

Everything below was verified by running it. Where a number appears, the command
that produced it is named.

## The rulings

### 1. SM-2 stays, dormant and annotated

ADR 0005 costed the removal at 37 points across ten files and listed five
questions with no recorded answer. The owner ruled: keep the tables.

The reasoning is short enough to state in full. The user-visible defect was the
Recap kicker claiming "spaced repetition" over a shuffled bank, and that was
already fixed in c0c66a7. The tables carry zero rows on all three live tracks
and cost nothing at rest. `prepwright/state.py` already carries an UNREACHABLE
banner at the DDL. Dropping them would invalidate "Isolation layer 8" in
`DESIGN-state-corpus.md` for no change any user can see.

Both places are now annotated as dormant rather than live. The five open
questions in ADR 0005 are moot while the answer is "keep".

If it is ever reversed, one fact decides the migration and it is a trap:
`card_review.card_id` references `card(card_id)` with `PRAGMA foreign_keys` ON,
so `card_review` must be dropped FIRST. The wrong order succeeds on an empty
database and fails only for someone who has graded a card, which is a migration
that passes every test and breaks in the field.

### 2. "Finished" means the written plan

ADR 0005 left this as the one question blocking a terminal stage: does finished
mean the plan the server can derive, or what the page shows, which includes
practice and check items the page invents client-side with no step row?

The owner ruled: the written plan. So the denominator is the `step` table and a
mark with no step to join to is ignored.

That ruling disposed of something it was not asked about. The live Wingtip track
carries 24 `topic` mark keys against 20 step rows: `g01`-`g20` plus `T1`-`T4`,
residue from an earlier cut that an append-only store can never remove.
Plan-as-denominator ignores them for free. Page-as-denominator would have had to
decide what they meant.

`prepared` is now the last rung of `STAGES`. `skipped` counts as settled,
because a plan that can never end once anything is skipped punishes the
candidate for deciding a step is not for them; but at least one step must be
actually delivered, so a track where everything was skipped does not get to
announce that you are ready.

### 3. MIN_TERMS scales to the goal

A flat floor of 2 is unsatisfiable for a one-word goal: no section can share two
words of a one-word query. Every section was dropped, `select` refused the whole
file, and it told the candidate their own resource "shares no vocabulary with
the goal" — false, and unactionable, because rewording cannot add a second word
to a goal that is legitimately one word.

`term_floor()` is `min(MIN_TERMS, len(terms(goal)))`. One root cause, so this
also closed the separate finding that `plan()` deferred a gap the corpus does
teach: both ran through `score_sections`.

## The decisions this session took on its own

### The step-lifecycle writer

Nothing in production ever wrote `step.status`, `step.score`, `step.review`,
`step.opened_utc` or `step.completed_utc`. Three measured consequences:

1. **The transcript cap was unrecoverable.** `append_turn` catches
   `CapExceeded` and calls `compact_oldest_completed_step`, whose selector needs
   `status='done' AND review IS NOT NULL`. It could never match. 12,438 B is the
   worst legal single turn, so 252 turns is the floor and 1,300 to 2,700 is
   typical. This was data loss on a timer.
2. **The `assessment` table was dead.** `/api/assess` opened no handle at all.
3. **`flow.curriculum.done` was a permanent 0.**

`sync_step_lifecycle` is RECONCILIATION, not an event hook, and that is the
load-bearing choice. Every fact it needs was already durable in the `mark`
table, so a step row rebuilds from marks written by a build that predates the
method. An event hook would have left every existing track wrong forever.

The three marks join on two different keys, which is silent to get wrong: a
`topic` mark is keyed by GAP id, `assess` and `session` by STEP id.

Proven on the live Wingtip track: ticking g08 in a browser moved `8:topic:S08`
through `ready → open → done` with both timestamps, score 0.2 from its assess
mark and review from its session mark; `flow.curriculum.done` went 0 → 1; the
`assessment` table took its first row ever; and the cap-recovery selector
matched (checked read-only, nothing was compacted). The tick was then undone,
because it was a test and not the candidate's claim about their own learning.

### One model per job

The model menu governed the tutor and nothing else while reading as though it
governed the app. Three of the five `run_cli` call sites were pinned in the
source: assess, review and judge each ran `claude-haiku-4-5` at effort `low`
whatever the menu said, and discovery ran sonnet. That was never a quality
decision anyone made — those three paths had never once completed on any machine
until the max-turns fix in ADR 0005, so the pins were inherited from code nobody
had watched run.

Five roles now, one per call site, so "the candidate picks the model end to end"
is checkable by grep rather than asserted. Resolution is three layers, each
fail-closed: this request, then the saved choice for that role and provider, then
the shipped default. `ROLE_DEFAULTS` reproduces the old pins exactly, so adopting
it changed no grade and no bill until a control was moved.

Choices are stored per provider. One flat model field would carry a Claude id
into a Codex run the moment the provider changed.

### One preference shared with Resume Studio

`~/.config/claude-apps/model-prefs.json`, three string keys. Only the TUTOR role
takes it: "which model do I want these tools to use" is a statement about the one
that talks to you, and quietly moving the grader because a resume was tailored on
Opus would be a bill nobody asked for.

(2026-09-28) For a chat turn this layer was dead until the page learned to stay
silent. The page always sent its bar's model, seeded as `claude-opus-5`, so the
request layer won every time. The bar now sends a model only after the candidate
moves it on that track (`tutorChosen`, a per-track pref); an untouched bar shows
what the role resolves to. `AnUntouchedBarLetsTheTutorRoleDecide` pins it.

The reader is duplicated in both apps rather than shared, because Prepwright is a
flat standard-library-only tree that installs nothing and cannot be imported from
the other side. The format is kept small enough that two copies cannot drift in
an interesting way.

The two apps spell exactly one model differently: Resume Studio pins the dated
`claude-haiku-4-5-20251001` where this one uses `claude-haiku-4-5`. Each
normalises on the way in and writes the id the other reads. Without that alias
the preference is discarded every time the sibling writes it, which is the
difference between shared and shared-looking.

## What running it found that reading it had not

**`_pdf_streams` was a denial of service on a hand-picked file.**
`PDF_MAX_STREAMS` caps successes, not attempts, so a file of `stream\n` repeated
made ~150,000 attempts per MiB, each scanning for a terminator that is not there.
Measured: 1 MiB took **60.6 s** and yielded nothing, against a 67 MiB file cap,
on the extraction path the owner's own file chooser drives, with no time bound
anywhere on it. After the rewrite the same input takes 0.001 s and the full
64 MiB takes 0.055 s.

The first revert written for that fix PASSED, which meant the fix was not yet
understood. It changed the search bound while keeping the early return, and the
early return is the fix. The four variants were then measured one at a time.

**Resume Studio's own test suite wrote a real preference into the real
`~/.config`** and moved Prepwright's default model on a machine where nobody had
chosen anything, because the suite exercises the route that commits a job and
that is where the mirror fires. The path is redirectable now, the suite redirects
it, and a test asserts the redirect rather than trusting it. The file was also
being written `0644`; it is opened `0600` explicitly.

## Consequences

The suite went from 439 to 477 tests. Every fix was proved by reverting it and
confirming its own test fails: 12 reverts across four commits, each file restored
byte-identical afterwards. One revert caught a test passing for the wrong reason
(three defence layers, one combined test) and one caught a revert that was not a
revert (the PDF scan).

`prep` is on PATH for the first time. It was written to be symlinked and never
had been.

The brief's definition-of-done item 1 required non-zero `assessment` rows. It was
reported unmet at the start of this session because the table was structurally
unreachable. It is met.

## Still open

- The file chooser has never been clicked. It is a native macOS dialog driven by
  `osascript`; browser automation cannot reach it and a hung one parks the bridge
  for 240 s. That click is the owner's.
- Task C's four module extractions: `provider.py`, `teach.py`, `assess.py` and
  `serve.py` are still 7-line stubs.
- The 17 probes on `t-454d410f0522`, parked by owner decision.
- `state.assessList` still does not persist. The measured objection stands: 18
  rows with 200-character reasons reach 4,753 bytes against a 4,096-byte cap, and
  `validate_ops` rejects the whole delta. Writing grades to the `assessment`
  table gives the panel a durable source that is not page state, which is the
  better fix and is now available.
