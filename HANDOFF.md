# Prepwright. Session brief.

## 0. Activate first

**Start in `~/Desktop/Prepwright`.** Not a subdirectory, and never from inside
`~/Desktop/Deep Fake Detection Project/`. That sibling carries 320 KB of
`.claude/rules/` about deepfake detection and manuscripts, none of it applicable
here, and all of it loads into every turn if you start there.

Run `/engage`. It loads Ruthless Candor (uncomfortable truth in line one,
confidence tags, no praise openers, spartan prose, a sensitive-data watch) and
prompt-to-start (phase order, the metric gate, the evidence-first loop). Apply
them, do not restate them.

A bridge may be listening on 8010 from a previous session. Check with
`lsof -ti :8010` and kill it before starting your own.

## 1. Do not

1. **Do not treat grep as proof of absence.** An early session grepped four
   times, declared `index.html` clean, and was wrong by 21 items.
2. **Do not add a dependency.** Standard library only. `sqlite3` is stdlib and
   is the persistence layer.
3. **Do not rename `tutor`, `student`, `candidate`, `step`, `session`,
   `assessment`, `recap`, `transcript`, `track`, `stage`, `corpus`, `citation`,**
   or ordinary security English about forged HTTP headers. `X-Tutor-Bridge` and
   `TUTOR_TS_*` are two-sided contracts. So is `pagestate.STEP_KEY_RE`.
4. **Do not edit a file while a hunt is reading it.** Line numbers go stale.
5. **Do not report completion for partial work.** If a test fails, paste it. If
   you skipped something, name it.
6. **Do not regenerate `MANIFEST.sha256` without reading what changed.**
   `shasum -a 256 --strict -c MANIFEST.sha256 | grep -v ': OK$'` first: it names
   exactly the files you touched, and anything else in that list is something
   you did not mean to change.
7. **Do not let a test call a model or open a dialog.** `PREPWRIGHT_NO_MODEL=1`
   and `PREPWRIGHT_NO_DIALOG=1` are set by the harness.
8. **Do not run the hunt after the commit.**
9. **Do not trust a green suite as coverage.** It was green at 357 tests over a
   product whose last third had never executed. See §5.

## 2. What Prepwright is

A local, single-user interview-preparation tutor. One person, one machine, no
server.

    intake ──► diagnostic ──► gap list ──► the candidate approves ──► research ──► corpus
                                                                                    │
    assessment ◄── teaching ◄── curriculum ◄─────────────────────────────────────────┘

One job is a **track**. A track has **stages**, a stage has **steps**. Nothing
assumes a fixed programme length.

A browser page plus a Python bridge on `127.0.0.1:8010`. Command `prep`. Standard
library only, run in place under `/usr/bin/python3 -I -S`. Chat runs through the
owner's already logged-in `claude` or `codex` CLI, so **no API key exists
anywhere in this project**.

The grounding claim is the whole product: the tutor may assert only what the
corpus in front of it says.

## 3. Settled decisions. Do not re-open them.

| Question | Answer |
|---|---|
| Deadline | None scheduled. Build in dependency order. |
| Persistence | Done. `/api/state` runs on the store. |
| Evidence | Per track, through one `TrackHandle`. ADR 0004. |
| Integrity | `MANIFEST.sha256` over 20 runtime files. |
| Agentic orchestration | **No.** `/langgraph-architect` ran 2026-09-08, verdict `plain_code`. |
| Research sources | The app finds them. The fetch decides. Model memory only nominates a URL. |
| The goal the design serves | The 20/80 cut applies to the MATERIAL INSIDE a source, not to WHICH gaps get studied. Every approved gap must be able to reach a taught, assessed step. Any cap that silently drops an approved gap is a defect. `tests/test_capacity.py` guards it. |
| Supplied resources | The candidate points at a file and says what to learn from it. `prepwright/ingest.py`, no model, no socket. |
| Where the page lands | `#start`, which renders whichever stage the track is in. |
| A schema call gets two turns | Measured, not chosen. ADR 0005. |

Five ADRs in `docs/adr/`. **Read 0003, then 0004, then 0005.** 0003 is the delta
protocol, 0004 is per-track evidence and the manifest, 0005 is this session.

## 4. Current state, and how much of it is verified

**Verified by running it, 2026-09-09. The command backing each claim is named.**

| Claim | Command |
|---|---|
| 399 tests pass on Homebrew 3.14, system 3.9.6 and launcher mode | `python3 -m unittest discover -s tests`, then `/usr/bin/python3 ...`, then `/usr/bin/python3 -I -S ...` |
| The suite makes no network, model or dialog call | the same commands, wall clock under 14 s |
| No module reads a name it never binds | `/usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py tests/*.py tools/*.py` |
| The manifest verifies over 20 pinned files | `/usr/bin/shasum -a 256 --strict -c MANIFEST.sha256` |
| The page's JavaScript parses | extract the `<script>` body, `node --check` |
| Every one of the 11 views renders, zero console errors | click each nav button, then `playwright-cli console` |
| **A step was taught, graded, closed and drilled, end to end, with real model calls** | see §5a for the four SQL counts that prove it |
| **The grader works** | `sqlite3 ~/.prepwright/tracks/t-93c97fdd6d77/track.db "select key,value from mark where kind='assess'"` returns `8:topic:S08 \| {"mastery":0.3,...}` |
| **The session review works** | same database, `select kind,count(*) from mark where kind in ('session','card','recap')` returns 1, 3, 3 |
| A review's `client_meta` is 1809 bytes against a 4096 cap | `select length(client_meta) from turn where role='system'` |

**Not verified. Say so rather than assuming.**

- **The file chooser dialog has never been opened.** It is a native macOS dialog
  driven by `osascript`, so a browser automation tool cannot click it and a hung
  one would park the bridge for 240 s. **That click is the owner's, and it is
  the only definition-of-done item this session could not attempt.**
- **The second close-session on the same step was not walked.** The behaviour is
  certain from source (`buildMsgs` returns null for role `session`, and `tail()`
  selects `body` only, which is `""` for a review turn), so a review is invisible
  to every later prompt and a second close writes a second review. Not observed
  in a browser.
- The Codex provider path has never run (no `codex` binary here). The iPhone
  (`prep iphone`) path has never run. `prep` is still not on `PATH`, so run
  `./prep-launcher.sh` from the repo.
- The launcher's output did not land in a file it was redirected to. Read the
  bridge's own terminal for `_failure` reference ids, not a redirect.

## 5. What this session did

**The back half ran for the first time, and the reason it never had was one
flag.**

### 5a. The walk

Track `t-93c97fdd6d77` (Wingtip), in a real browser, with real model calls. Before:
`assessment` 0, `card` 0, `card_review` 0, and `mark` holding only `check`,
`practice`, `pref`, `topic`. After:

    sqlite3 ~/.prepwright/tracks/t-93c97fdd6d77/track.db \
      "select kind,count(*) from mark group by kind; select role,count(*) from turn group by role"

    assess 1 · card 3 · recap 3 · session 1   (all four kinds new)
    system 1 · tutor 3 · user 4               (turn 7 is a real grounded reply)

Zero console errors across all 11 views throughout.

### 5b. The blocker: three of four model features had never worked

`CLI_BASE` pins `--max-turns 1`. A `--json-schema` reply needs two: the model
answers, then it emits the structured output. The CLI exits 1 with `subtype`
`error_max_turns`, `result` null, empty stderr. `_cli_reason` read only `result`,
and only when it was a string, so the terminal said `Claude tutor CLI failed
(exit 1)` with nothing after it.

Everything on `CLI_BASE` with a schema was dead: `/api/assess`, `/api/review`,
**and the diagnostic judge**. Discovery survived because `CLI_SEARCH` allows 6.
The evidence is still on disk:

    sqlite3 <track>/track.db "select substr(why,1,40), count(*) from gap group by 1"
    t-454d410f0522 -> "No answer given" 17
    t-93c97fdd6d77 -> "graded without a model: length only" 20

Both are the judge's fallback. It has never once graded a gap.

### 5c. The fix pack, each fix proven by reverting it

| What | Where |
|---|---|
| A schema call gets 2 turns, `CLI_SCHEMA_MAX_TURNS`, non-search path only | `bridge.py` `_build_claude_cmd` |
| A failure with no prose still names itself, from `subtype` | `bridge.py` `_cli_reason` |
| `mastery` bounded 0..1 in the schema and in `_clean_rows`. 45 becomes 0.45, off-scale drops | `bridge.py` `ASSESS_SCHEMA`, `_clean_rows` |
| A parsed-but-empty batch is a failure, not a silent success. Usage charged first | `bridge.py` `assess_via_cli` |
| A top-level JSON array no longer costs both attempts | `bridge.py` `_assess_batch` |
| Payload validated before provider selection, so a bad step key names the step | `bridge.py` both routes |
| Recap kicker and copy describe the lap, not a scheduler that does not exist | `index.html` `renderRecap` |
| Session-log and Q&A Delete removed. The store cannot express a delete | `index.html` |
| The false "assessList is persisted" comment corrected | `index.html` |
| A Word table is one `" \| "` joined row, like every other table reader | `ingest.py` `read_docx` |

`/tmp/.../prove_reverts.py` reverted each bridge fix and required its own test to
fail: seven of seven, with `bridge.py` restored and hash-checked. The same for
the `read_docx` fix: 5 of 6 tests fail on revert.

**One revert caught a test passing for the wrong reason.** `_clean_rows` had a
direct unit test, so reverting the call site inside `_assess_batch` left it
green. `test_the_batch_path_actually_applies_the_bound` exists because of that.

### 5d. Round 3 of the hunt: the 19 that were never verified

19 skeptics, one per finding, defaulting to REFUTED, against current code, with
`ALREADY_FIXED` as a first-class verdict. Then a consolidator that re-read every
survivor. **14 already fixed by `e2d95f1`, 5 genuinely open, all 5 reproduced by
executing the real functions.** One of the five is fixed (`read_docx`). Four
remain, in §10.

## 6. What the store guarantees

Read `prepwright/state.py`, `pagestate.py` and `corpus.py` before writing
against them.

**The page sends changes, never the document.** One op per appended turn, one per
changed mark, diffed against what the bridge last acknowledged.

**A transcript is a `turn`. Everything else is a `mark`.** `FIELDS` in
`pagestate.py` is the whole mapping.

**Nothing shrinks.** `turn`, `mark`, `assessment`, `card_review`, `gap_history`
and `track_history` raise ABORT on DELETE and on any UPDATE outside one
receipted case. This is why the page's Delete buttons were removed rather than
fixed.

**Caps are counted by the write that causes them**, inside the same
`BEGIN IMMEDIATE`, **in encoded bytes**.

**Foreign keys are ON.** They fail as `sqlite3.IntegrityError`, which is **not** a
`StoreError` subclass. `StoreError` IS a `RuntimeError` subclass, so a broad
`except RuntimeError` above the store's own handlers turns every 409 and 507
into a 400. Put it last.

**One track's bytes cannot reach another track's prompt.**

## 7. Traps that have cost real time

Traps 1 to 29 from the previous brief still hold and are not repeated here in
full. The file at `e2d95f1` has them. The ones added this session:

30. **`--max-turns 1` and `--json-schema` are incompatible.** A schema reply
    costs two turns. `_build_claude_cmd` handles it, so any new schema call must go
    through it rather than building argv by hand.
31. **A green suite can cover only the half of the product that runs.** Compare
    `grep -rho "/api/[a-z_]*" tests/*.py | sort | uniq -c` against
    `grep -o '"/api/[a-z_]*"' bridge.py | sort -u` before trusting coverage.
32. **A unit test on a helper proves nothing about its caller.** Revert the call
    site, not just the helper.
33. **`DROP TABLE` does not fire a `BEFORE DELETE` trigger in SQLite, but a
    foreign key from a child table DOES block it, and only when the child has
    rows.** Measured. A migration tested on an empty database passes and then
    fails for the one user who has data.
34. **`orphan_scan` cannot see a table name inside a SQL string literal.**
    `keep.py:121` counts cards inside a `try` that catches `sqlite3.Error` and
    passes, so dropping the table would pin `n_cards` to 0 silently.
35. **`index.html` has 136 occurrences of "card" across 115 lines**, including
    live JavaScript (`mergeRecap`) and the `.card` panel class. A grep-driven
    deletion destroys working code.
36. **Under `PREPWRIGHT_NO_MODEL` both model routes return 400, not 502.** A
    route test must assert the message, not only the status, to tell "rejected
    the request" from "could not reach a model".

## 8. The adversarial hunt: the recipe that works

One lens gets **two files and one question**, under 600 words. Then **one
consolidator** that verifies every finding against live text and defaults to
REJECTED. `Workflow` for the hunt, which is read-only and parallel. **Never for
edits.**

Round 3 added one thing worth keeping: when findings are older than the code,
give the skeptic **three** verdicts, not two. `ALREADY_FIXED` was the right
answer for 14 of 19, and without it they would have been argued as refutations.

## 9. What is left, in dependency order

### 9a. The missing step-lifecycle writer. One root cause, three symptoms.

Nothing in production writes `step.status`, `step.score`, `step.review`,
`step.opened_utc` or `step.completed_utc`. Verified: the only two `UPDATE step`
statements in production set `evidence_state` and `compacted`, and the three that
touch status or review are all in `tests/test_persistence.py`. Three consequences,
all measured:

1. **The 3 MiB transcript cap is unrecoverable.** `append_turn` catches
   `CapExceeded` and calls `compact_oldest_completed_step`, whose selector needs
   `status='done' AND review IS NOT NULL`. It can never match, so every save past
   the cap fails forever. `compact_turn` carries a second guard on the same dead
   column and `keep._compact_done_steps` uses the same predicate, so there are
   three dead guards, not one. Reachability: 12,438 B is the worst legal single
   turn, so 252 turns is the floor. At typical verbosity it is 1,300 to 2,700.
   The byte cap always binds before the turn-count cap.
2. **The `assessment` table is dead.** Its only INSERT is `add_assessment`, whose
   only callers are two tests. `/api/assess` opens no handle at all.
3. **`flow.curriculum.done` is a permanent 0**, counting `step.status == 'done'`.

The honest terminal-stage fact, if a track is to be able to end, is the newest
`mark` of kind `topic` under a gap id joined to `step.gap_id`, which is the join
`adoptCurriculum` already makes and the one that survives a re-cut. **One owner
decision blocks it:** does "finished" mean the written plan, which the server can
derive, or what the page shows, which includes practice and check items that the
page invents client-side and that have no step row to join to? They disagree on
any track where a practice item was never ticked.

### 9b. The SM-2 removal, costed

The decision to drop `card` and `card_review` was taken on the understanding
that it was small. It is a 37-point surface across ten files. The label half is
done and the tables now carry a comment saying they are unreachable. ADR 0005
records the five open questions. The two that would bite first: `card_review`
must be dropped **before** `card`, and `keep.py:121` would fail silently.

### 9c. Four confirmed findings from round 3, not fixed

Each was reproduced by executing the real function. The proposed fixes are in
the run's `journal.jsonl` under `wf_a639e20b-107`.

1. **`select()` tells the candidate their own resource "shares no vocabulary
   with the goal"** when the scorer skipped it on the term count, and refuses the
   whole file with the same false claim. A one-word goal can never satisfy
   `MIN_TERMS = 2`, so rewording does not help and the advice is unactionable.
   `ingest.py:979-981`, refusal at `1241-1245`.
2. **`goal_coverage` is measured over every parsed section, not the kept ones**,
   so the panel prints 100% coverage beside a "what it will teach from" list that
   does not contain the idea. `ingest.py:1179` and `:1281`.
3. **`plan()` defers a gap the corpus does teach**, same root cause as 1, at
   `curriculum.py:207`. **This one touches `MIN_TERMS`, which the previous brief
   already flagged as a deliberate rule awaiting an owner ruling.** Do not change
   the gate without that ruling. The message can be fixed independently.
4. **`_pdf_streams` is quadratic when no `endstream` is found**, on the
   poppler-free path, with no timeout. `ingest.py:297-301`.

### 9d. Secondary

`state.assessList` is still not persisted, and the reason recorded beside
`pagestate.FIELDS` was wrong and is now corrected with measured numbers: it is
capped at 18 rows, 1,693 bytes typical, but 4,753 bytes worst case, and a mark
that size is refused with the whole delta. Also: Task C's remaining four module
extractions, the 17 probes on `t-454d410f0522`, and the file chooser click.

## 10. Working discipline

Evidence first. Hypothesis, smallest safe patch, test against real data, keep or
revert, one line recording it.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the
command whose output backs the word.

**Prove every fix by reverting it and confirming its own test fails.** Revert the
call site, not only the helper.

**Walk it in a browser before you believe it.** The blocker this session was
invisible in the source and obvious on the first click.

Surgery: exact-text `replace` in Python on delimited blocks, and **assert the
match count before writing**.

After any server-side edit, **kill and restart the bridge**. After any change to
`bridge.py`, `index.html` or `prepwright/*.py`: read what
`shasum -c MANIFEST.sha256` names as changed *before* regenerating, then
`./tools/make_manifest.sh`. The launcher refuses to start on a mismatch, which is
the first thing to check when a correct edit will not run.

## 11. Definition of done for the next session

1. The step-lifecycle writer decided and built, or explicitly deferred in
   writing. It is the root of §9a's three symptoms and is now the largest thing
   in the project.
2. The four confirmed findings in §9c fixed or dismissed, each proven by revert.
3. The SM-2 removal executed against ADR 0005's checklist, in the safe drop
   order, or the tables kept and the ADR annotated with that decision.
4. The terminal stage built, after the owner answers the one question in §9a.
5. The file chooser walked once in a browser. One click, the owner's.
6. 3-interpreter suite green, manifest clean, orphan scan clean, every view
   walked with zero console errors.
7. This file rewritten, every claim naming its command.

Committed locally. **This repository has no remote. Never invent a push
target.**
