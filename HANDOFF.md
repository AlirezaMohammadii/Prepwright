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

**A third round was launched over the delta change and every agent died on the account
session limit.** It burned 1.39M subagent tokens across 249 tool calls and returned
nothing. Read §5 before you launch another one: the prompts were too large.

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
   two-sided contracts between the page and the bridge. Renaming one side locks the
   other out.
4. **Do not edit a file while a hunt is reading it.** Line numbers in the report go
   stale and you lose the verification you paid for. Either wait, or write atomically
   and re-verify afterwards. This was violated once in the session that wrote this file:
   `README.md` was edited while a lens was reading it.
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
| Persistence before features | Done, and now wired. `/api/state` runs on the store. |
| Resume Studio | Additive write on their side, import on ours. Not started. See §8. |
| Research | The candidate supplies URLs, Prepwright fetches. The model never finds a source. See §9. |

Four ADRs sit in `docs/adr/`. `DESIGN-state-corpus.md` is the persistence specification
and the code follows it. Where the design was wrong the code fixed it and the ADR
records the amendment, which is the pattern to keep. **ADR 0003 is the one to read
first**: it covers the delta protocol, the three schema amendments it needed, and the
two things it deliberately did not do.

## 4. Current state, and how much of it is verified

**Verified by running it.** The command whose output backs each claim is named.

| Claim | Command |
|---|---|
| 63 tests pass, ordinary interpreter | `python3 -m unittest discover -s tests` |
| 63 tests pass on the system 3.9.6 | `/usr/bin/python3 -m unittest discover -s tests` |
| 63 tests pass under the launcher's isolated mode | `/usr/bin/python3 -I -S -m unittest discover -s tests` |
| `/api/state` reads and writes `track.db`, over real HTTP | `tests/test_page_state.py::BridgeStateApi`, 8 tests against a real bridge subprocess |
| A legacy `progress/state.json` imports once and is renamed | same class, `test_a_...imported_once_and_renamed` |
| A retried delta writes no second row and does not move the revision | same class, `test_c_...does_not_duplicate` |
| A replace from a stale view is refused with the real document | same class, `test_d_...` |
| An update from a stale view applies and returns the merge | same class, `test_e_...` |
| A process killed inside a delta leaves the store readable and writable | `KilledMidDelta`, which SIGKILLs a real subprocess mid-transaction |
| Two processes writing at once produce a union, not a clobber | `TwoWritersRacingDeltas`, two real subprocesses |
| A second track sees none of the first track's turns | `TrackIsolation` |
| Every page field and all four chat roles round-trip | `WholeDocumentRoundTrip` |
| The page saves, reloads, and gets its work back | driven in Chromium with `playwright-cli`: ticked a topic, banked a question, pushed all four chat roles, reloaded, zero console errors except the favicon 404 |
| Offline work is folded in and not subtracted | bridge killed, work typed into the tab, a second writer added rows to the same track, bridge restarted: all three sides present in `track.db` |
| A save too large for one request is sliced and lands intact | 200 turns of 7 KB across two steps: 2 slices, 200 rows, 200 distinct `client_turn_id`, no duplicates |
| The security tests fail without their guards | the step-key anchor and the mark-kind check were each removed in turn; each broke exactly its own test |

**Assumed, not verified.** The Codex provider path has never run, because no `codex`
binary is installed on this machine. The iPhone (`prep iphone`) path has never run. The
launcher has never been installed on `PATH`. `prep-launcher.sh` has not been run against
the new bridge.

**Defects found and fixed this session.** Five were pre-existing, four were mine.

*A recovery path that never fired for the damage it existed for.* `quick_check()` let
`sqlite3.DatabaseError` out. A scribble deep enough to break the page the PRAGMA itself
reads makes the PRAGMA raise rather than report, so the raw error walked past
`open_track`'s `CorruptStore` and past `keep.open_track_or_recover`'s handler, and no
restore ever ran. Two existing tests had been passing on where the scribble happened to
land relative to the b-tree. Growing the schema moved it and they both broke.

*A row id allocated outside the transaction that assigns it.* `add_step` read
`MAX(ord)` before `BEGIN IMMEDIATE`, so two writers adding a first step both saw the
same maximum and the second died on `step.ord`'s UNIQUE constraint. Found by the racing
test, not by reading.

*A validated string and a stored string that differ.* The step-key regex ended in `$`,
which in Python also matches before a trailing newline, so `"1:topic:T1\n"` validated
and was then stored with the newline. It was in two inline copies in `bridge.py`. There
is now one `PS.STEP_KEY_RE`, anchored with `\Z`, and `OP_ID_RE` and `MARK_KEY_RE` with it.

*A slice the page could build and the bridge would always refuse.* The page sliced by
op count. 400 turns at the 8 KiB turn cap is 3.1 MiB, over the bridge's 2 MiB limit on
one delta, so a long offline session produced a slice refused on every retry and never
saved at all. Slicing is byte-bounded now, and
`test_the_pages_slice_budget_fits_inside_the_bridges_delta_cap` reads the budget out of
`index.html` rather than restating it, so the two cannot drift apart silently.

*A test that asserted nothing.* The HTTP helper called `err.read()` twice. The second
read returns `b""`, so every refusal reply became `{}` and two tests passed on a
`KeyError` that never fired.

*Two numbers that could not be what they said.* "roughly 290 KiB spare" was 320, and
"about 1,300 mark writes" was 1,820 measured. Both were in `config.py` and ADR 0003,
both written by the same session that wrote the cap.

*A byte budget counted in characters.* `_charge("bytes_marks", ...)` summed
`len(raw)` (bytes) with `len(kind)` and `len(key)` (characters). Every term encodes now.

*Two dead keys on the wire.* `savedAt` was returned by both `/api/state` routes and read
by nothing. The one inside `state` stays: `exportData()` writes the whole document to a
file, so it reaches a user.

## 5. Re-run the hunt. It has not run on this change.

This is the highest-priority item and it is not optional. Six lenses and a consolidator
were launched over the delta change. All seven died on the account session limit.

The lenses were, and the four in bold are the ones a self-hunt already covered by hand:
**dead guard** (a safety mechanism whose only mention is its own definition),
**impossible number**, destructive recovery (a path that destroys what it protects when
the thing it recovers from is absent), **silent no-op**, prose versus code, and
**contract drift** between `index.html` and `bridge.py`.

The self-hunt is not a substitute. It was run by the same person who wrote the code,
which is the exact bias the lenses exist to break, and it did not cover
**destructive recovery** or **prose versus code** at all.

What to change when you re-launch it: the prompts were roughly 4 KB each and told each
agent to read six or seven files in full. Give each lens two files and one question.
The script is at
`.claude/projects/.../workflows/scripts/prepwright-delta-hunt-wf_60429c3b-162.js` and
can be resumed with `resumeFromRunId`, but resuming replays cached results and there are
none, so edit it down first.

Two questions the self-hunt raised and did not settle:

- `bridge.py` `import_legacy_state()` and `_legacy_document()` have never been traced
  for the failure cases: rename fails after a successful import, `state.json` is a
  symlink or a directory, two bridges start at once, an `.imported-` file of the same
  name already exists. The happy path and the backup-fallback path are both tested.
- `open_state_track()` releases the lease for reads. `TrackHandle.close()` then takes a
  backup when `writes_since_keep > 0`. Neither interaction has been read adversarially.

## 6. The work, in dependency order

A task is done when its acceptance command runs and passes, not when the edit is made.

### Task A. Wire `bridge.py` to the persistence layer. DONE.

`/api/state` reads and writes `~/.prepwright/tracks/<id>/track.db` through
`prepwright/pagestate.py`. 283 lines of JSON state code are out of `bridge.py`: the
whole-document write, the revision hash, `_regression_reason`, `MAX_UNMARK_PER_WRITE`,
the rotating snapshots and the high-water file. No dual-write, no fallback. See §4 for
what is verified and ADR 0003 for why the mapping is shaped the way it is.

### Task B. Extract `bridge.py`. NOT STARTED. Do this next.

Into `prepwright/{serve,security,provider,teach,assess,corpus,prompt}.py`. `config.py`
and `pagestate.py` already exist. `bridge.py` ends as serve wiring only.

Move `corpus_evidence()` onto `TrackHandle.build_pack()` first. It is a straight swap,
it gives the tutor per-section citations, and it is the last thing in `bridge.py` still
reading a directory instead of a track.

Acceptance: every module imports under `python3 -I`. `MANIFEST.sha256` is generated from
the runtime file list, the launcher's pin block verifies it and refuses to start on a
mismatch, and the pinned list equals `ls prepwright/*.py` plus `bridge.py` and
`index.html`.

**This is now a security item, not only tidiness.** Before Task A, `bridge.py` was
self-contained, so pinning `bridge.py` and `index.html` covered the whole trusted set.
It now imports `prepwright/{config,state,track,keep,pagestate}.py`, none of which the
launcher checks, and the comment at `prep-launcher.sh:61` still calls them "the two
files this launcher starts". Nothing is enforced today because both pins are
deliberately empty, so this is a latent gap and not a live hole. Do not pin now: the
file set is still moving and a stale pin refuses correct code.

### Task C. Intake, and the Resume Studio bridge. See §8.

### Task D. Diagnostic and gap approval.

`diagnose.py`. Probe conversationally against the posting's requirements and the
candidate's own resume claims. A requirement they explain unprompted is not a gap. A
resume claim they cannot defend **is** a gap even when the posting never mentions it.
Produce a gap list, show it, change nothing until it is approved.

### Task E. Research. See §9.

### Task F. Curriculum.

`curriculum.py`. Approved gaps plus corpus into ordered steps. Order by **dependency**,
not importance: a concept whose prerequisite is unlearned is unteachable. Cut to the
smallest set carrying most of the value. Tag every step core, depth or reference. Every
step pins the exact corpus sections it will be taught from, through `step_slice`.

### Task G. Wire the page to the real flow.

`index.html` renders a seeded synthetic track. Add intake, the gap-approval screen, a
research panel and a track switcher. Reuse the existing views. Do not redesign.

The track switcher has two jobs waiting on it. `bridge.py current_track_id()` is the
whole selection mechanism today: a `CURRENT_TRACK` file, falling back to the most
recently touched active track, falling back to creating one. And the Import button now
merges rather than replaces, because an append-only store cannot be made to forget. A
true replace is a new track built from the file, which is a switcher feature.

## 7. What the store guarantees, and how the page now talks to it

Read `prepwright/state.py` and `prepwright/pagestate.py` before writing against them.

**The page sends changes, never the document.** One op per appended chat turn, one op
per changed mark, diffed against the document the bridge last acknowledged. A stale tab
cannot express a loss because no op removes anything. That is what replaced the revision
hash and the shrink-detector, and it is why `pagestate.py` enforces no monotonicity rule:
a value that goes backwards in that diff went backwards because the candidate did it,
and the superseded row is still on disk under its own `seq`.

**A transcript is a `turn`. Everything else is a `mark`.** `FIELDS` in `pagestate.py`
is the whole mapping and the bridge serves it to the page inside the GET reply, so the
page builds its diff from that table rather than from a second copy of the rules. Adding
a field is one edit. Three names are deliberately absent and the file says why.

**Nothing shrinks.** `turn`, `mark`, `assessment`, `card_review`, `gap_history` and
`track_history` raise ABORT on DELETE and on any UPDATE outside one receipted case.

**Caps are counted by the write that causes them**, inside the same `BEGIN IMMEDIATE`.
`bytes_turns`, `bytes_cards`, `bytes_corpus` and now `bytes_marks`.

**One track's bytes cannot reach another track's prompt.** `open_track()` is the only
function that connects to a track database, `build_pack()` takes a handle rather than a
track id, and `step_slice` carries a composite foreign key that cannot cross database
files. Citation tokens are per-track, so both tracks call their first document `D01`.
That is deliberate: the same token read through two handles returns two different
documents. A citation validator must check against the exact pack that was sent.

`keep.py` runs a published, age-driven ladder of ten rungs. Rungs 0 to 5 lose nothing.
Rung 6 is the first lossy step. Rung 9 stops and asks for a human. Every age-driven rung
suspends itself when the clock looks wrong.

**There is no compaction rung for marks.** At `MARKS_BYTES_CAP` a write is refused with
the cap named, rather than reclaiming superseded rows the way rung 6 reclaims tutor
prose. That is the same failure the design rejected for turns, and the reason it is
acceptable is arithmetic rather than principle: a ticked topic charges a measured 144
bytes, so 256 KiB is roughly 1,800 of those. If that stops being true, build a receipted
compaction of superseded non-append marks in the shape `turn_compaction` already has.

**`TRACK_DB_CAP` is defined in `config.py` and enforced by nothing.** Pre-existing, not
introduced by this change, and worth a look while you are in `keep.py`.

## 8. The Resume Studio change

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

## 9. Research: fetching URLs the candidate supplies

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

## 10. Traps that have cost real time

1. **A textual patch applied before line-based cuts** shifted every later line by three
   and corrupted the file. Better than doing it bottom-up: do the surgery in Python with
   exact-text `replace` on delimited blocks and assert the match count first, which
   sidesteps line numbers entirely. Every edit in the delta change was made that way,
   and two of them caught a wrong assumption at the assert instead of in the file.
2. **Two cut boundaries were silently wrong**, taking constants the security envelope
   needed. `py_compile` does not catch an unbound name. Run an AST scan for
   `Load`-context names with no binding after any deletion. This caught one orphaned
   call in the delta change that `py_compile` passed clean.
3. **A security test passed for the wrong reason.** If a security test passes on the
   first try, force the dangerous input in deliberately and check it fails. Two guards
   in `pagestate.py` were proved this way, and a third test was found to be asserting
   nothing at all.
4. **`prep iphone off` tears down whatever holds `:443`.** One handler per machine, and
   another local app may own it. Guarded now. Any new teardown needs the same guard.
5. **`playwright-cli` needs a shell function, not a variable.** `PW="playwright-cli -s=x"`
   then `$PW open` fails under zsh, which does not word-split unquoted expansions.
6. **The system Python is 3.9.6** and its SQLite is 3.51, which does support STRICT
   tables, `RETURNING`, `VACUUM INTO` and contentless FTS5. Do not reach for 3.10+ syntax.
   Note `python3` on this machine is Homebrew 3.14, so run the suite under both.
7. **A workflow journal stores an agent's return value under `result`, not `value`.**
   Parsing the wrong key reports zero findings from a run that produced dozens. A run
   that produced none writes `"type":"failed"` lines instead, which is what happened here.
8. **`unittest` runs every `addCleanup` after `tearDown`.** Register the environment
   restore with `addCleanup` first, so it runs last.
9. **On macOS `/var` resolves to `/private/var`.** Realpath both sides of a containment
   assertion.
10. **A `$` anchor in a Python regex also matches before a trailing newline.** Use `\Z`
    on anything whose validated form must equal its stored form.
11. **The `Write` tool will put a real NUL byte in a file** if the content contains a
    `\x00` escape that gets interpreted. `SyntaxError: source code string cannot contain
    null bytes` is what that looks like.

## 11. Working discipline

Evidence first. Every change carries a hypothesis, the smallest safe patch, a test
against real data, a keep-or-revert decision, and one line recording it. A
well-documented reverted experiment beats an untested change.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the command whose
output backs the word. Otherwise `[Likely]` or `[Guessing]`.

## 12. Definition of done for the next session

- The adversarial hunt in §5 re-run with smaller prompts, and its confirmed findings
  applied. Nothing else in this list matters more.
- `corpus_evidence()` replaced by `TrackHandle.build_pack()`, with the citation
  validator checking against the pack that was sent.
- `bridge.py` extracted into the modules named in Task B, `MANIFEST.sha256` generated,
  and the launcher's pin block pointed at it.
- `prep-launcher.sh` run against the new bridge at least once. It never has been.
- This file rewritten for the session after that one.
