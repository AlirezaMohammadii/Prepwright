# Prepwright. Session brief.

## 0. Activate first

Run `/engage`. It loads two standing contracts: Ruthless Candor (the uncomfortable
truth in line one, confidence tags `[Certain]`/`[Likely]`/`[Guessing]`, no praise
openers, spartan prose, a sensitive-data watch) and prompt-to-start (phase order, the
metric-integrity gate, the evidence-first change loop). Apply them, do not restate them.

Work in `~/Desktop/Prepwright`.

Use the `Workflow` tool for anything substantive. The pattern that keeps paying is one
agent edits one file, then three or four adversarial lenses read it back through
separate angles, then one consolidator verifies every finding against the live text and
drops the over-corrections. Two rounds of that have run over this tree. Between them
they caught a state guard that had been protecting nothing, an undefined CSS token, an
unreachable cap that contradicted its own arithmetic, and a package layout that could
not be imported at all. Never skip the consolidator: roughly a third of what the lenses
report is over-correction, and applying it would make the tree worse.

Two mechanical rules that come from real damage: never put two agents on the same file,
and do line-precise surgery yourself rather than delegating it.

## 1. Do not

1. **Do not treat grep as proof of absence.** An early session grepped four times,
   declared `index.html` clean, and was wrong by 21 items.
2. **Do not add a dependency.** Standard library only. `sqlite3` is stdlib and is the
   persistence layer.
3. **Do not rename `tutor`, `student`, `candidate`, `step`, `session`, `assessment`,
   `recap`, `transcript`, `track`, `stage`, `corpus`, `citation`,** or ordinary security
   English about forged or spoofed HTTP headers. `X-Tutor-Bridge` and `TUTOR_TS_*` are
   two-sided contracts between the page and the bridge; renaming one side locks the
   other out.
4. **Do not edit a file while a hunt is reading it.** Line numbers in the report go
   stale and you lose the verification you paid for. Either wait, or write atomically
   and re-verify afterwards.
5. **Do not report completion for partial work.** If a test fails, paste the output. If
   you skipped something, name it.

## 2. What Prepwright is

A local, single-user interview-preparation tutor. One person, one machine, no server.

    intake ──► diagnostic ──► gap list ──► the candidate approves ──► research ──► corpus
                                                                                    │
    assessment ◄── teaching ◄── curriculum ◄─────────────────────────────────────────┘

One job is a **track**. A track has **stages**, a stage has **steps**. Nothing assumes a
fixed programme length: an interview may be five days away or five weeks away.

A browser page plus a Python bridge on `127.0.0.1:8010`. Command `prep`. Standard
library only, no pip, no install, run in place under `/usr/bin/python3 -I -S`. Chat runs
through the owner's already logged-in `claude` or `codex` CLI, so **no API key exists
anywhere in this project**.

The grounding claim is the whole product: the tutor may assert only what the corpus in
front of it says, and says "that is not in the corpus" otherwise. Everything in the
persistence layer exists to keep that true.

## 3. Settled decisions. Do not re-open them.

| Question | Answer |
|---|---|
| Deadline | None scheduled. Build in dependency order. |
| Persistence before features | Done. `state.py`, `track.py`, `keep.py` are implemented and tested. |
| Resume Studio | Additive write on their side, import on ours. Not started. See §7. |
| Research | The candidate supplies URLs, Prepwright fetches. The model never finds a source. See §8. |

Three ADRs' worth of reasoning sits in `docs/adr/`. `DESIGN-state-corpus.md` is the
persistence specification and the code follows it. Where the design was wrong the code
fixed it and the ADR records the amendment, which is the pattern to keep.

## 4. Current state, and how much of it is verified

**Verified by running it.** The command whose output backs each claim is named.

| Claim | Command |
|---|---|
| 40 tests pass, ordinary interpreter | `python3 -m unittest discover -s tests` |
| 40 tests pass under the launcher's isolated mode | `python3 -I -S -m unittest discover -s tests` |
| The bridge starts and serves the page | `python3 -I -S bridge.py`, then `curl /api/health` |
| Every live endpoint answers a real request | `curl` against `/api/health`, `/api/state` GET and POST, `/api/chat`, `/api/assess`, `/api/review` |
| Teaching is grounded, and refuses when it is not | a `/api/chat` turn citing a document the corpus does not hold answered "not in the corpus" rather than inventing one |
| The page renders and talks to the bridge | driven in Chromium with `playwright-cli`: every view, a real chat turn, zero console errors except a favicon 404 |
| Storage survives a kill, a race, and corruption | `tests/test_persistence.py`, which kills a real process mid-transaction, races two real processes, and scribbles over a real database file |
| The store holds at library scale | a soak of 12 tracks, 576 turns and 36 documents, archiving and restoring half of them, then housekeeping: no cross-track leak, no counter drift |
| No test can reach the real storage root | the suite asserts its storage root is inside its own temporary directory before it restores the environment |

**Assumed, not verified.** The Codex provider path has never run, because no `codex`
binary is installed on this machine. The iPhone (`prep iphone`) path has never run. The
launcher has never been installed on `PATH`. `prepwright/` is implemented but nothing
imports it yet: `bridge.py` still holds its own copy of state, corpus retrieval and
provider invocation, and the JSON `progress/state.json` scheme is still what actually
runs. Those two stores now coexist, which is the single biggest thing to fix next.

**Real defects found and fixed this session.** Roughly fifty, over four rounds of
adversarial hunting. Grouped by the class of mistake, because the classes are what to
hunt for next time rather than the individual lines.

*A guard that guarded nothing.* The bridge's anti-clobber check counted a `homework`
key the page has never written, so it was always zero of zero. `assert_live` existed and
no write path called it, so bumping a track's generation did nothing to an open handle.
`heartbeat()` had no caller, so every lease self-expired two minutes after it was taken.
`backup_track` had no caller, so recovery from a corrupt database always found nothing
to restore. Look for a safety mechanism whose only mention in the tree is its own
definition.

*A number that could not be what it said.* `MAX_SECTIONS_PER_DOC` was 12 against a cap
that fits 10. A byte budget was counted in characters in three places. An age was out by
twice the local UTC offset, so a track went stale a day early in Sydney and a day late
in London. Recompute every stated figure: the design document had six such errors inside
the one section headed "stated so they can be checked".

*A path that destroys what it exists to protect.* Recovery moved the live database into
quarantine before checking that a backup existed, so a corrupt track with no backup lost
its only copy. Archiving deleted the quarantine directory that two other paths promise
never to delete from. The trash purge could delete inside the undo window, because a
stray file counted toward the cap. Ask what each recovery path does when the thing it
recovers from is absent.

*A feature that silently did not work.* Grading never moved a card's due date, so every
card was due forever. The registry's card projection was never written, so an archived
track's flashcard file came out empty. Housekeeping's own reads stamped `opened_utc`, so
no track could ever reach the age its own ladder is keyed on.

*A test that passed for the wrong reason.* A symlink test the containment check caught
before the symlink check could. A budget assertion carrying a hundred times the slack it
needed. A clock test that asserted the flag and not one guarded rung. And the suite
itself wrote a registry into the candidate's real `~/.prepwright`.

*Prose that contradicted the code beside it.* The page told the candidate their chat
never touches browser storage while writing the whole transcript there whenever the
bridge was down. A README heading said cost was unmeasured directly above a paragraph
describing where the measurements come from.

## 5. The work, in dependency order

A task is done when its acceptance command runs and passes, not when the edit is made.

### Task A. Land the first commit.

`git init` has not run. Before committing, read `git status` and confirm `.gitignore` is
holding `corpus/`, `progress/`, `var/` and `tracks/` out. One commit: what Prepwright is,
what it was cut down from in the same mechanism-level terms ADR 0001 uses, and what is
not built yet. Nothing about the origins of the copied code beyond that goes into a
file or a commit message.

### Task B. Wire `bridge.py` to the persistence layer, then extract it.

Two stores now describe the same thing. `bridge.py` writes `progress/state.json`;
`prepwright/state.py` writes `~/.prepwright/tracks/<id>/track.db`. The JSON scheme is
what the design rejects, and the longer both exist the more code is written against the
wrong one.

Order that works: give the bridge a track handle and move `/api/state` onto delta writes
first, because that is the endpoint the page hits on every keystroke. Then move
`corpus_evidence()` onto `TrackHandle.build_pack()`, which is a straight swap and gives
the tutor per-section citations. Then move the routes, security envelope, provider
invocation and prompt assembly into `serve.py`, `security.py`, `provider.py`, `teach.py`,
`assess.py`, `corpus.py`, `prompt.py`. One cohesive group at a time, tests after each,
never batch.

Then add `MANIFEST.sha256` over `prepwright/*.py`, `bridge.py` and `index.html` and point
the launcher's pin block at it. The pins are deliberately empty until the file set stops
moving, and the launcher says so out loud at every start rather than treating empty as
trusted. Do not compute them before then, and do not silently treat empty as trusted.

### Task C. Intake, and the Resume Studio bridge. See §7.

### Task D. Diagnostic and gap approval.

`diagnose.py`. Probe conversationally against the posting's requirements and the
candidate's own resume claims. A requirement they explain unprompted is not a gap. A
resume claim they cannot defend **is** a gap even when the posting never mentions it.
Produce a gap list, show it, change nothing until it is approved.

### Task E. Research. See §8.

### Task F. Curriculum.

`curriculum.py`. Approved gaps plus corpus into ordered steps. Order by **dependency**,
not importance: a concept whose prerequisite is unlearned is unteachable. Cut to the
smallest set carrying most of the value. Tag every step core, depth or reference. Every
step pins the exact corpus sections it will be taught from, through `step_slice`.

### Task G. Wire the page to the real flow.

`index.html` renders a seeded synthetic track. Add intake, the gap-approval screen, a
research panel and a track switcher. Reuse the existing views. Do not redesign.

## 6. What the persistence layer already guarantees

Read `prepwright/state.py` before writing against it. The three properties it exists to
make structural rather than careful:

- **Nothing shrinks.** `turn`, `assessment`, `card_review`, `gap_history` and
  `track_history` raise ABORT on DELETE and on any UPDATE outside one receipted case, so
  a stale client cannot express a loss. Tutor prose in a completed step can be compacted,
  but only after a receipt row records its original length and hash, and only when the
  step already carries its review. A student turn cannot be emptied by any path.
- **Caps are counted by the write that causes them.** `bytes_turns`, `bytes_cards` and
  `bytes_corpus` accumulate inside the same `BEGIN IMMEDIATE` as the row. A cap checked
  against a number refreshed at startup is not a cap.
- **One track's bytes cannot reach another track's prompt.** `open_track()` is the only
  function that connects to a track database, `build_pack()` takes a handle rather than a
  track id, and `step_slice` carries a composite foreign key that cannot cross database
  files. Citation tokens are per-track, so both tracks call their first document `D01`.
  That is deliberate: the same token read through two handles returns two different
  documents. A citation validator must therefore check against the exact pack that was
  sent, never against "a valid id in this track".

`keep.py` runs a published, age-driven ladder of ten rungs. Rungs 0 to 5 lose nothing.
Rung 6 is the first lossy step. Rung 9 stops and asks for a human, and there is no rung
10. Every age-driven rung suspends itself when the clock looks wrong.

## 7. The Resume Studio change

`~/Desktop/Thesis/Job Applications/resume-studio/` is a separate git repo the owner uses
regularly, currently on branch `audit-fixes` with uncommitted work in it. **Branch it.
Never commit to its main. Never alter existing behaviour. Get approval before any
commit.**

Today the posting text lives in a scratch `inputs.json` under a session directory that
`_retire_job` deletes. Its `start_generation` builds that dict at `bridge.py:2847`, and
the finished application is filed into `applications/<date>__<Base>/` by
`_file_application`, which keeps only the `.tex`, the `.pdf` and the `_FitReport.md`. The
posting itself does not survive.

The change: when `_accept_done` validates a result, also write the posting into the job's
own output folder as `<base>_JobDescription.md`, carrying the posting text, the source URL
if any, the capture date and a sha256 of the text. Write it before the scratch is purged.
**If the write fails, log and continue.** A resume run must never fail because of this.

    applications/2026-09-04__Role_Company/
      Role_Company_A_Mohammadi.tex      unchanged
      Role_Company_A_Mohammadi.pdf      unchanged
      Role_Company_FitReport.md         unchanged
    + Role_Company_JobDescription.md    new

Then build `prepwright/intake.py` to read it back. `_FitReport.md` already holds a
requirement matrix, a steelman and a red-team pass. Import it as a **seed** for the
diagnostic, never as a substitute for it. Prepwright reads that folder exactly once, at
track creation, hashes it, and never reopens it, so a later edit on the other side cannot
retroactively change what a track was built from.

## 8. Research: fetching URLs the candidate supplies

The candidate pastes URLs they already trust. `research.py` fetches with `urllib`, and
the provider CLI distils what came back into corpus documents. **The model never finds
the sources.** That is the entire grounding claim.

A local app fetching arbitrary URLs is an SSRF surface. Requirements, not suggestions:

- `https` only. Reject `file`, `ftp`, `data`, `gopher` and everything else.
- Resolve the hostname and **refuse private and reserved address space**: loopback,
  link-local, `169.254.0.0/16`, RFC1918, unique-local, and the cloud metadata address.
  Re-check after **every** redirect, not only the first request. A permitted host can
  redirect into private space.
- Cap redirects, cap response bytes, cap wall-clock, set an explicit timeout.
- Store the fetched bytes' sha256, the final URL after redirects, and the fetch date, so
  a citation can be falsified years later.
- Redact credential-shaped lines before anything can enter a prompt.

`state.py` already has the write side: `TrackHandle.write_doc()` takes the sections and
the origin metadata, renders the document format of `DESIGN-state-corpus.md` section E,
writes it atomically, and records each section's byte offsets. `research.py` supplies the
sections and the provenance, nothing more.

## 9. Traps that have cost real time

1. **A textual patch applied before line-based cuts** shifted every later line by three
   and corrupted the file. Do line surgery bottom-up, textual replaces last, and pin
   both the first and the last line of every range before deleting.
2. **Two cut boundaries were silently wrong**, taking constants the security envelope
   needed. `py_compile` does not catch an unbound name. Run an AST scan for
   `Load`-context names with no binding after any deletion.
3. **A security test passed for the wrong reason.** A redaction assertion succeeded
   because the dangerous section was never selected, not because it was redacted. If a
   security test passes on the first try, force the dangerous input in deliberately and
   check again.
4. **`prep iphone off` tears down whatever holds `:443`.** One handler per machine, and
   another local app may own it. Guarded now. Any new teardown needs the same guard.
5. **`playwright-cli` needs a shell function, not a variable.** `PW="playwright-cli -s=x"`
   then `$PW open` fails under zsh, which does not word-split unquoted expansions.
6. **The system Python is 3.9.6** and its SQLite is 3.51, which does support STRICT
   tables, `RETURNING`, `VACUUM INTO` and contentless FTS5. Do not reach for 3.10+ syntax.
7. **A workflow journal stores an agent's return value under `result`, not `value`.**
   Parsing the wrong key reports zero findings from a run that produced dozens.
8. **`unittest` runs every `addCleanup` after `tearDown`.** Restoring an environment
   variable in `tearDown` lets a later cleanup run against the real environment, which
   is how the suite came to write into the candidate's own storage root. Register the
   restore with `addCleanup` first, so it runs last.
9. **On macOS `/var` resolves to `/private/var`.** A guard comparing a temporary
   directory against a realpath'd one fails on a correct path unless both are resolved.

## 10. Working discipline

Evidence first. Every change carries a hypothesis, the smallest safe patch, a test
against real data, a keep-or-revert decision, and one line recording it. A
well-documented reverted experiment beats an untested change.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the command whose
output backs the word. Otherwise `[Likely]` or `[Guessing]`.

## 11. Definition of done for the next session

- `git init` plus a first commit that a stranger could read without learning anything
  about where this code came from.
- `/api/state` moved onto delta writes, with the JSON scheme deleted rather than left
  beside it.
- `corpus_evidence()` replaced by `TrackHandle.build_pack()`, with the citation validator
  checking against the pack that was sent.
- `README.md` brought back in line with the code, which has moved under it.
- This file rewritten for the session after that one.
