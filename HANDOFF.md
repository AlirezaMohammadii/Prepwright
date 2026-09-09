# Prepwright — handoff for session 11

Written 2026-09-09 at the end of session 10. Every claim below names the command
that backs it. Where a number appears, it was measured, not estimated.

## 0. Activate first, in this order

```
cd ~/Desktop/Prepwright          # NEVER from inside ~/Desktop/Deep Fake Detection Project/
/engage                          # loads the candour contract and the engineering brief
```

Read this file, then `docs/adr/0006-*` and `0005-*` only. 0006 supersedes three
of 0005's statements and says which at 0005's head.

Then:

```
lsof -ti :8010 | xargs -r kill
prep --no-browser                # `prep` is on PATH now; --no-browser is new
python3 -m unittest discover -s tests
/usr/bin/shasum -a 256 --strict -c MANIFEST.sha256
/usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py tests/*.py tools/*.py
git log --oneline -8
```

Paste the output before touching anything.

## 1. Do not

- **No pip, no dependency, ever.** Standard library only, Python 3.9 compatible.
- **No API key anywhere.** Chat runs through the owner's logged-in CLI.
- **Do not weaken grounding to make a walk pass.** `build_pack` → `cites` →
  `check_citations` → `citations.invented` → refusal.
- **Do not rename:** `tutor, student, candidate, step, session, assessment,
  recap, transcript, track, stage, corpus, citation`, `X-Tutor-Bridge`,
  `TUTOR_TS_*`.
- **The 20/80 cut applies to the material inside a source, not to which gaps get
  studied.** Any cap that silently drops an approved gap is a defect;
  `tests/test_capacity.py` guards it.
- **Ask before deleting any track.** Nine existed, four were scratch and were
  archived.
- **This repository has no remote.** Never invent a push target.
- **Do not treat grep as proof of absence.**
- **Kill and restart the bridge after any server-side edit.**

## 2. What Prepwright is

A local, single-user interview-preparation tutor. One job posting per track. It
teaches only from sources the candidate supplied or approved, cites them, and
refuses rather than inventing. Storage is an append-only SQLite database per
track under `~/.prepwright`. The page never sends a document, only a delta of
ops (ADR 0003).

## 3. Settled. Do not re-open.

Everything in §1, plus these three, ruled by the owner on 2026-09-09 and
recorded in ADR 0006:

1. **SM-2 stays, dormant and annotated.** The `card` and `card_review` tables
   are a complete scheduler nothing can write. Not dropped: the user-visible lie
   is already fixed, the tables cost nothing at rest, and dropping them
   invalidates a documented architecture layer for no visible change.
2. **"Finished" means the written plan.** The denominator is the `step` table.
   Marks with no step row to join to are ignored, which is what disposes of the
   four `T1`-`T4` orphans on the Wingtip track.
3. **`MIN_TERMS` scales to the goal:** `min(MIN_TERMS, len(terms(goal)))`.

Also settled earlier: no agentic orchestration inside the app; persistence
through `TrackHandle`, one DB per track.

## 4. Current state, and how much of it is verified

**Verified by running it, 2026-09-09. The command backing each claim is named.**

| Claim | Command |
|---|---|
| 477 tests pass on three interpreters | `python3 -m unittest discover -s tests`, then the same with `/usr/bin/python3`, then `/usr/bin/python3 -I -S`. Run each **literally**: see trap 1. |
| The suite makes no network, model or dialog call | the same commands, wall clock under 15 s |
| No module reads a name it never binds | `/usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py tests/*.py tools/*.py` → 35 files |
| The manifest verifies over 20 pinned files | `/usr/bin/shasum -a 256 --strict -c MANIFEST.sha256` |
| The page's JavaScript parses | extract the `<script>` bodies, `node --check` |
| Every one of the 11 views renders, 0 console errors | click each nav button, then `playwright-cli console` |
| `prep` starts the app from any directory | `cd / && prep --help`; `cd ~/Music && prep --no-browser` |
| **The grader runs on the model you choose** | picked Opus in the Models panel, then `POST /api/assess` reported `"model":"claude-opus-5"`. That route was hardwired to haiku before. |
| **A grade reaches the store** | `sqlite3 ~/.prepwright/tracks/t-93c97fdd6d77/track.db "select * from assessment"` → one row, `claude/claude-haiku-4-5`, with its reason |
| **The step lifecycle is written** | ticked g08 in a browser; `select status,opened_utc,completed_utc,score from step where gap_id='g08'` → `done`, both timestamps, 0.2 |
| **`flow.curriculum.done` is a real number** | `GET /api/flow` → `done: 1`, where it was a permanent 0 |
| **The cap-recovery selector matches** | `select step_id from step where status='done' and review is not null and review<>'' and compacted=0` → `8:topic:S08` (read-only; nothing was compacted) |
| **The terminal stage renders** | a throwaway track in a temp home on port 8011: rail shows 7 rungs with "Ready" current, card says "1 delivered of 2 in the written plan" |
| **The two apps share one model choice** | wrote from each side, read from the other, including the one model they spell differently; the grader asserted untouched throughout |
| **A malformed PDF cannot hang the ingest** | 1 MiB of `stream\n` took 60.6 s before, 0.001 s after; the full 64 MiB takes 0.055 s |

**Not verified. Say so rather than assuming.**

- **The file chooser dialog has never been opened.** Native macOS, driven by
  `osascript`. Browser automation cannot click it and a hung one parks the
  bridge for 240 s. **That click is the owner's.** It is the only
  definition-of-done item three sessions have been unable to attempt.
- The Codex provider path has never run (no `codex` binary here).
- `prep iphone` has never run.
- The second close-session on the same step was not walked.
- The `prepared` stage has not been reached on a real track, only on a
  throwaway. Reaching it needs 20 real ticks.

## 5. What session 10 did

Six commits on top of `c0c66a7`.

```
7fe22ae  one model choice per job, and prep on PATH
44b9b54  share one model choice with Resume Studio, and cross-link them
0bea692  the step-lifecycle writer, and the three symptoms it closes
df3a2f1  the ladder gets an end
a19f4d7  the last three round-3 findings, one of them a DoS
471f582  record the three owner rulings and what running it found
```

Plus `b17ff53` in `~/Desktop/Thesis/Job Applications/resume-studio`, **committed
locally and not pushed**. That repo has a GitHub remote; pushing is the owner's
call.

### 5a. The headline

`step.status`, `step.score`, `step.review`, `step.opened_utc` and
`step.completed_utc` had **never been written by production code**. One missing
writer, three measured symptoms, all now closed:

1. The 3 MiB transcript cap was **unrecoverable**. `append_turn` catches
   `CapExceeded` and calls `compact_oldest_completed_step`, whose selector needs
   `status='done'`. It could never match, so every save past the cap failed
   forever. 12,438 B is the worst legal single turn, so 252 turns is the floor;
   1,300 to 2,700 is typical. This was data loss on a timer.
2. The `assessment` table was dead. `/api/assess` opened no handle.
3. `flow.curriculum.done` was a permanent 0.

`sync_step_lifecycle` is **reconciliation, not an event hook**. Every fact it
needs was already durable in the `mark` table, so existing tracks rebuild from
marks written long before the method existed.

### 5b. What running it found that reading it had not

- **`_pdf_streams` was a denial of service on a hand-picked file.** 1 MiB of
  `stream\n` took 60.6 s and yielded nothing, against a 67 MiB file cap.
- **Resume Studio's own test suite wrote a real preference into the real
  `~/.config`** and moved Prepwright's default model. The path is redirectable
  now and a test asserts the redirect.
- **A revert that was not a revert.** The first revert of the PDF fix passed,
  because it changed the search bound while keeping the early return, and the
  early return is the fix. The four variants were then measured one at a time.
- **A test passing for the wrong reason.** Reverting the write-side model
  whitelist left its test green, because a downstream layer caught it. It is now
  three tests, one per defence layer.

## 6. What the store guarantees

`turn`, `mark`, `assessment`, `card_review`, `gap_history`, `track_history`
raise ABORT on DELETE. Caps are counted in encoded bytes inside
`BEGIN IMMEDIATE`. `PRAGMA foreign_keys = ON` at every open. A client sends ops,
never a document, and `validate_ops` accepts exactly two op kinds, neither of
which removes anything.

## 7. Traps that have cost real time

1. **zsh does not word-split.** `for C in "python3" "/usr/bin/python3 -I -S"; do $C ...`
   silently runs nothing and prints a clean pass. Run each interpreter
   literally.
2. **The launcher refuses to start on a manifest mismatch.** After editing
   `bridge.py`, `index.html` or `prepwright/*.py`: read what
   `shasum -c MANIFEST.sha256` names as changed **before** running
   `./tools/make_manifest.sh`.
3. **`playwright-cli check` cannot click a 0×0 input.** The topic checkboxes are
   visually replaced by a styled span. Click
   `label.checkline:has(input[data-topic="gNN"]) .checkbox`.
4. **A stale playwright ref survives a reload and clicks nothing.** After
   `goto`, address elements by role or selector, not by an old `eNN`.
5. **Suppressing stderr hides a failed click.** A `playwright-cli select` on an
   element that does not exist yet is a silent no-op.
6. **A new POST route needs two edits.** The whitelist in `do_POST` *and* an
   explicit block before the `/api/chat` fall-through, or its requests are
   answered by the tutor. The source says so at the whitelist.
7. **`_read_json_body` requires a JSON object.** A list body is a 400 before any
   route sees it.
8. `StoreError` IS a `RuntimeError`; `sqlite3.IntegrityError` is NOT a
   `StoreError`.
9. The launcher's output does not land in a file it is redirected to. Read the
   bridge's own terminal for `_failure` reference ids.

## 8. What is left, in dependency order

### 8a. The owner's own action

**Click the file chooser once.** Three sessions have been unable to attempt it.

### 8b. Real work, unblocked

1. **Task C's four module extractions.** `prepwright/provider.py`, `teach.py`,
   `assess.py` and `serve.py` are 7-line stubs; `wc -l` proves it. `bridge.py`
   is now ~3,000 lines and the role registry, the settings API and
   `_persist_assessment` all landed in it this session, so the case is stronger
   than it was.
2. **`state.assessList` persistence.** The measured objection stands: 18 rows
   with 200-character reasons reach 4,753 bytes against a 4,096-byte cap, and
   `validate_ops` rejects the whole delta. The better fix is now available —
   grades are in the `assessment` table, so the panel can be rebuilt from the
   store instead of from page state.
3. **The Recap drill still shuffles.** The label is honest and the tables are
   dormant by decision, but nothing schedules. If real spaced repetition is ever
   wanted, ADR 0006 says what the wiring costs.
4. **The 17 probes on `t-454d410f0522`**, parked by owner decision.

### 8c. Worth a look, not yet evidenced

- `keep._compact_done_steps` carried the same dead `status='done'` predicate as
  the cap recovery. It should now be reachable. It was not exercised this
  session; the cap-recovery path was.
- The library holds 12 rows against 3 track directories. The other nine are
  archived or scratch. Nobody has checked that the list the page shows matches
  what is on disk.

## 9. Working discipline

Evidence first. Hypothesis, smallest safe patch, test against real data, keep or
revert, one line recording it.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the
command whose output backs the word.

**Prove every fix by reverting it and confirming its own test fails.** Revert the
call site, not only the helper. **And check that the revert is a revert**: one
this session passed because it changed the wrong line.

**Walk it in a browser before you believe it.** Two of this session's four
worst findings were invisible in the source.

Surgery: exact-text `replace` in Python on delimited blocks, and **assert the
match count before writing**.

Read `sed -n` regions, never whole files. `bridge.py` is ~3,000 lines and
`index.html` is ~4,400.

## 10. Definition of done for the next session

1. The file chooser walked once. One click, the owner's.
2. Task C's four extractions done, or explicitly deferred in writing with the
   reason.
3. `state.assessList` rebuilt from the `assessment` table, or the decision
   recorded not to.
4. Anything found by a browser walk fixed and proved by revert.
5. 3-interpreter suite green, manifest clean, orphan scan clean, all 11 views
   walked with zero console errors.
6. This file rewritten, every claim naming its command.

Committed locally. **This repository has no remote. Never invent a push
target.**
