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
`/usr/sbin/lsof -ti tcp:8010` and kill it before starting your own.

## 1. Do not

1. **Do not treat grep as proof of absence.** An early session grepped four
   times, declared `index.html` clean, and was wrong by 21 items.
2. **Do not add a dependency.** Standard library only. `sqlite3` is stdlib and
   is the persistence layer.
3. **Do not rename `tutor`, `student`, `candidate`, `step`, `session`,
   `assessment`, `recap`, `transcript`, `track`, `stage`, `corpus`, `citation`,**
   or ordinary security English about forged HTTP headers. `X-Tutor-Bridge` and
   `TUTOR_TS_*` are two-sided contracts. Renaming one side locks the other out.
4. **Do not edit a file while a hunt is reading it.** Line numbers go stale. If
   you must, the consolidator handles it: this session edited `keep.py` mid-run
   and the consolidator correctly reported "ALREADY FIXED IN THE LIVE TREE" with
   the timestamp, rather than confirming a stale finding. Do not rely on that.
5. **Do not report completion for partial work.** If a test fails, paste it. If
   you skipped something, name it.
6. **Do not regenerate `MANIFEST.sha256` without reading what changed.** It is
   the integrity pin. Regenerating it launders an edit you did not intend.

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
corpus in front of it says. **As of this session that is enforced by
construction**, not by convention. See §4.

## 3. Settled decisions. Do not re-open them.

| Question | Answer |
|---|---|
| Deadline | None scheduled. Build in dependency order. |
| Persistence | Done and wired. `/api/state` runs on the store. |
| Evidence | Per track, through one `TrackHandle`. ADR 0004. Done. |
| Integrity | `MANIFEST.sha256` over the runtime set. Done. |
| Resume Studio | Additive write on their side, import on ours. Not started. §8. |
| Research | The candidate supplies URLs, Prepwright fetches. §9. |

Five ADRs in `docs/adr/`. **Read 0003 then 0004 first**: 0003 is the delta
protocol, 0004 is per-track evidence and the manifest.

## 4. Current state, and how much of it is verified

**Verified by running it.** The command backing each claim is named.

| Claim | Command |
|---|---|
| 88 tests pass, Homebrew 3.14 | `python3 -m unittest discover -s tests` |
| 88 tests pass, system 3.9.6 | `/usr/bin/python3 -m unittest discover -s tests` |
| 88 tests pass, launcher mode | `/usr/bin/python3 -I -S -m unittest discover -s tests` |
| No module reads a name it never binds | `/usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py tests/*.py tools/*.py` |
| The manifest verifies | `/usr/bin/shasum -a 256 --strict -c MANIFEST.sha256` |
| A tampered module refuses to start | append a line to `prepwright/keep.py`, run `./prep-launcher.sh`: names the file, exits 1 |
| An unsigned module refuses to start | `echo x=1 > prepwright/rogue.py`, run it: "20 runtime files on disk but 19 pinned" |
| The app runs clean | `./prep-launcher.sh`: page loads, `/favicon.ico` 204, no TLS garbage in the log |
| A real teaching turn is grounded | POST `/api/chat` with the session cookie: `grounded: true`, cites `D01§s01..s03`, reply cited `D01§s02`, **invented: []** |
| A credential in a source never reaches a prompt | `tests/test_corpus_pack.py::Redaction` |
| One track's pack carries no other track's bytes | `test_corpus_pack.py::Pack::test_e` |
| A citation the pack did not supply is invented | `test_corpus_pack.py::Citations::test_b` |
| Recovery survives a corrupt newest backup | `test_hunt_round0.py::RecoveryNeverDestroysTheOnlyCopy` |

**Every security guard was proved by removing it** and confirming its own test
fails: redaction, symlink refusal, the citation check, and all four recovery
properties. Keep doing this. A security test that passes on the first try has
not been shown to be capable of failing.

**Assumed, not verified.** The Codex provider path has never run (no `codex`
binary here). The iPhone (`prep iphone`) path has never run. `prep` is still not
on `PATH`; run `./prep-launcher.sh` from the repo. The page has not been driven
in a browser this session; the chat and state paths were exercised over HTTP
with `curl`, and the previous session's Playwright run still stands for the UI.

## 5. The round-0 hunt: what it found and what it cost

It ran, and it worked. Four lenses, two files and one question each, then one
consolidator verifying every finding against live text. **33 raw findings, 14
confirmed, 19 rejected. 934K tokens, 5/5 agents, zero failures.** The previous
attempt used seven lenses with 4 KB prompts telling each to read six files, and
all seven died on the session limit returning nothing.

**Keep this shape.** Two files and one question per lens, under 600 words, four
lenses, and never skip the consolidator: it rejected 19, including several that
were real analyses whose consequence did not follow, and one that was the
published design rather than a defect.

Nine confirmed defects were fixed this session, each with a regression test that
fails without the fix. The commit message lists them. The two worth knowing
about because they shape how you read this code:

- `keep.py` parsed a UTC stamp with `time.mktime(...) - time.timezone`, an hour
  early through every summer. `track.py:114`'s docstring says exactly why that
  is wrong, and four other sites follow it. **The project documented the bug and
  then committed it once.** When a docstring states a rule, grep for violations.
- `apply_ops` has no enclosing transaction, so a batch that fails at op N leaves
  ops 0..N-1 committed while `validate_ops` promises to "refuse the whole
  write". That promise only covers what `validate_ops` actually checks. Anything
  you add that can fail inside `apply_ops` must be checked before it.

**Still open from the hunt.** Nothing confirmed remains unfixed. Two things the
consolidator surfaced that are worth acting on:

- Rung 4 deleting a stale track's backups was **rejected as a defect** because
  `DESIGN-state-corpus.md:411` specifies it. But §7 below used to claim "rungs 0
  to 5 lose nothing", which is then false. The prose has been corrected here;
  check the design says what you want it to say.
- `TRACK_ID_RE` and `DOC_NAME_RE` in `config.py` are anchored with `$` rather
  than `\Z`. The lens flagged it, the consolidator rejected it as not currently
  exploitable. It is the same shape as a bug this project already fixed once in
  `pagestate.py`. Cheap to fix, worth doing.

**Lenses not yet run on the current tree:** dead guard, impossible number,
silent no-op, contract drift between `index.html` and the new pack fields
(`grounded`, `cites`, `citations`, `packSha16`), which the page does not read yet.

## 6. The work, in dependency order

A task is done when its acceptance command runs and passes.

### Task A. Wire `bridge.py` to the persistence layer. DONE.

### Task B. Extract `bridge.py`. PARTLY DONE.

**Done:** the corpus seam. `prepwright/corpus.py` owns redaction, loose-document
parsing, the one-time seed, the pack and the citation check.
`bridge.corpus_evidence` and the shared-directory scanner are gone.
**Done:** the security half. `MANIFEST.sha256` pins all nineteen runtime files,
the launcher refuses to start on a mismatch, and it counts on-disk files against
the pinned count because `shasum -c` cannot see an unlisted module in the import
path. `tools/make_manifest.sh` regenerates it.

**Not done:** `bridge.py` is still 1855 lines. The remaining seams, with line
ranges as of this commit:

| Module | What moves |
|---|---|
| `security.py` | the request boundary: `LOCAL_ORIGINS`, `TS_*`, `_is_remote_request`, `_origins_for`, `_allowed_host`, `_remote_identity_ok`, `_safe_messages`, `_safe_assess_items` |
| `provider.py` | `_trusted_executable`, `claude_bin`, `codex_bin`, `_cli_env`, `_provider_ready`, `run_cli`, `_parse_codex_jsonl`, `trim_history`, `_usage`, `_resolved_model` |
| `teach.py` | `NO_ERRANDS`, `TUTOR_SYSTEM_BASE`, `_step_instructions`, `chat_via_cli` |
| `assess.py` | `assess_via_cli`, `review_via_cli` and their schemas |
| `serve.py` | `Handler`, `main` |

The blocker is that `security.py`'s constants are computed at import from
`PORT`. Move `PORT` into `config.py` first, or make them a function of it.

**Run `tools/orphan_scan.py` after every deletion.** `py_compile` accepts a
module that reads an undefined name. The scanner caught a live call to the
deleted `corpus_evidence` during this change, which compiled clean.

### Task C. Intake, and the Resume Studio bridge. See §8.

**Ask the owner first**, at the moment you start: do they want to paste a real
posting so the first real track can be built, or should you build against a
synthetic one? A synthetic posting has no gap between what it asks for and what
they can defend, so it cannot exercise the diagnostic.

### Task D. Diagnostic and gap approval.

`diagnose.py`. Probe conversationally against the posting's requirements and the
candidate's own resume claims. A requirement they explain unprompted is not a
gap. A resume claim they cannot defend **is** a gap even when the posting never
mentions it. Produce a gap list, show it, change nothing until it is approved.

### Task E. Research. See §9.

**The acceptance test is real.** `index.html` topics T2 and T4 cite
`database-indexing.doc.md`, which does not exist, so the tutor correctly refuses
to teach them. Producing that document through `research.py` is how you know the
pipeline works. **Do not hand-write it.** Ask the owner for URLs, naming which
topics they are for.

The write side already exists and is exercised: `prepwright/corpus.ingest_text`
takes text plus provenance and calls `TrackHandle.write_doc`. `research.py`
supplies the fetched bytes and the origin metadata, nothing more.

### Task F. Curriculum.

`curriculum.py`. Approved gaps plus corpus into ordered steps. Order by
**dependency**, not importance. Cut to the smallest set carrying most of the
value. Tag every step core, depth or reference. Every step pins its exact corpus
sections through `pin_slice`. **`corpus.pin_all` is a placeholder** that pins
every section of a document to a step; the curriculum is what should choose.

### Task G. Wire the page to the real flow.

`index.html` renders a seeded synthetic track. Add intake, gap approval, a
research panel and a track switcher. Reuse the existing views.

**The page does not yet read the new pack fields.** `/api/chat` returns
`grounded`, `cites`, `citations` and `packSha16`. `citations.invented` is a list
of tokens the tutor named that the pack did not contain, which is precisely the
confident-and-wrong failure the design exists to prevent. Showing it is a small
change and the highest-value one on the page.

The track switcher has two jobs waiting on it. `current_track_id()` is the whole
selection mechanism today. And Import merges rather than replaces, because an
append-only store cannot forget; a true replace is a new track built from the
file.

## 7. What the store guarantees

Read `prepwright/state.py`, `pagestate.py` and `corpus.py` before writing
against them.

**The page sends changes, never the document.** One op per appended turn, one per
changed mark, diffed against what the bridge last acknowledged. A stale tab
cannot express a loss because no op removes anything.

**A transcript is a `turn`. Everything else is a `mark`.** `FIELDS` in
`pagestate.py` is the whole mapping and the bridge serves it to the page inside
the GET reply.

**Nothing shrinks.** `turn`, `mark`, `assessment`, `card_review`, `gap_history`
and `track_history` raise ABORT on DELETE and on any UPDATE outside one
receipted case.

**Caps are counted by the write that causes them**, inside the same
`BEGIN IMMEDIATE`, **in encoded bytes**. Three sites charged characters until
this session; if you add a byte class, encode it.

**One track's bytes cannot reach another track's prompt.** `open_track()` is the
only function that connects to a track database, `build_pack()` takes a handle,
and `step_slice` carries a composite foreign key that cannot cross database
files. Citation tokens are per-track, so both tracks call their first document
`D01`. **That is why a citation validator is handed the pack and not the track.**

**Recovery verifies before it overwrites.** `recover_track` proves a backup reads
on a temp copy, walks backups newest to oldest, and leaves the damaged file
exactly where it is when none verifies. Quarantine is a per-recovery directory
created exclusively, with sidecars named off the database so SQLite still pairs
the `-wal`.

`keep.py` runs a published, age-driven ladder of ten rungs. **Rung 4 deletes a
stale track's backups**, which the design specifies at
`DESIGN-state-corpus.md:411`. Rung 6 is the first rung that discards content.
Rung 9 stops and asks for a human.

**There is no compaction rung for marks.** At `MARKS_BYTES_CAP` a write is
refused with the cap named. A ticked topic charges a measured 144 bytes, so
256 KiB is roughly 1,800 of those.

**`TRACK_DB_CAP` is defined in `config.py` and enforced by nothing.**
Pre-existing. Worth a look while you are in `keep.py`.

## 8. The Resume Studio change

`~/Desktop/Thesis/Job Applications/resume-studio/` is a separate git repo the
owner uses regularly, on branch `audit-fixes` with uncommitted work. **Branch it.
Never commit to its main. Never alter existing behaviour. Get approval before any
commit, and show the diff first.** Confirm its state before branching; if you
cannot reach it, stop and say so rather than improvising.

Today the posting lives in a scratch `inputs.json` that `_retire_job` deletes.
`start_generation` builds it at `bridge.py:2847`; `_file_application` keeps only
the `.tex`, `.pdf` and `_FitReport.md`. The posting does not survive.

The change: when `_accept_done` validates a result, also write the posting into
the job's own output folder as `<base>_JobDescription.md`, carrying the text, the
source URL, the capture date and a sha256. Write it before the scratch is purged.
**If the write fails, log and continue.** A resume run must never fail for this.

Then `prepwright/intake.py` reads it back. `_FitReport.md` already holds a
requirement matrix, a steelman and a red-team pass. Import it as a **seed** for
the diagnostic, never as a substitute. Prepwright reads that folder once, at
track creation, hashes it, and never reopens it.

## 9. Research: fetching URLs the candidate supplies

The candidate pastes URLs they already trust. `research.py` fetches with
`urllib`; the provider CLI distils what came back. **The model never finds the
sources.** That is the entire grounding claim.

A local app fetching arbitrary URLs is an SSRF surface. Requirements:

- `https` only. Reject `file`, `ftp`, `data`, `gopher` and everything else.
- Resolve the hostname and **refuse private and reserved space**: loopback,
  link-local, `169.254.0.0/16`, RFC1918, unique-local, cloud metadata.
  **Re-check after every redirect.** A permitted host can redirect into private
  space.
- Cap redirects, response bytes and wall-clock. Set an explicit timeout.
- Store the sha256, the final URL after redirects, and the fetch date, so a
  citation can be falsified years later.
- **Call `corpus.redact()` before anything can enter a prompt or the store.** It
  is already the one copy, and `ingest_text` calls it, so routing fetched text
  through `ingest_text` gets this for free.

## 10. Traps that have cost real time

1. **A textual patch applied before line-based cuts** shifted every later line
   and corrupted the file. Do surgery in Python with exact-text `replace` on
   delimited blocks and **assert the match count first**. Every edit this session
   was made that way and three asserts caught a wrong assumption before the
   write.
2. **`py_compile` does not catch an unbound name.** Run
   `tools/orphan_scan.py` after any deletion. It caught one this session.
3. **A security test can pass for the wrong reason.** Remove the guard and check
   its own test fails. Seven guards were proved this way this session.
4. **Do not add `-t .` to `unittest discover`.** `tests/` has no `__init__.py`,
   so it fails with `ImportError: Start directory is not importable` on a suite
   that is fine. `python3 -m unittest tests.test_x` fails the same way; use
   `discover -s tests -p 'test_x.py'`.
5. **A fixed-offset scribble is not reliable corruption.** Whether `quick_check`
   notices depends on where it lands relative to the b-tree. Two earlier tests
   passed for the wrong reason and broke when the schema grew. Overwrite the
   whole file after the header.
6. **`BACKUPS_ACTIVE` is 1 and `BACKUPS_LEASED` is 2.** A second backup only
   survives while a lease is held, which is also the only state in which the
   older-backup fallback can engage.
7. **`prep iphone off` tears down whatever holds `:443`.** Guarded now. Any new
   teardown needs the same guard.
8. **`playwright-cli` needs a shell function, not a variable** under zsh.
9. **The system Python is 3.9.6**, SQLite 3.51. `python3` is Homebrew 3.14. Run
   the suite under both. No 3.10+ syntax.
10. **A workflow journal stores an agent's return under `result`, not `value`.**
11. **`unittest` runs every `addCleanup` after `tearDown`.** Register the
    environment restore with `addCleanup` first so it runs last.
12. **On macOS `/var` resolves to `/private/var`.** Realpath both sides.
13. **A `$` anchor in a Python regex also matches before a trailing newline.**
    Use `\Z` where the validated form must equal the stored form.
14. **`shasum -c` writes its per-file `FAILED` lines to stdout**, only the
    summary to stderr. Capture stdout to name the failing file.
15. **The `Write` tool will put a real NUL byte in a file** if the content
    contains an interpreted `\x00`.
16. **`JSON.stringify(x).length` is UTF-16 code units, not bytes.** Use the
    `BYTES()` helper in `index.html`.

## 11. Working discipline

Evidence first. Every change carries a hypothesis, the smallest safe patch, a
test against real data, a keep-or-revert decision, and one line recording it.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the
command whose output backs the word.

## 12. Definition of done for the next session

- The remaining hunt lenses in §5 run on the current tree, findings verified and
  applied.
- `bridge.py` extracted into the modules in §6 Task B, `PORT` moved to
  `config.py` first, `orphan_scan.py` clean after every cut, manifest
  regenerated.
- The page shows `citations.invented` from `/api/chat`.
- `TRACK_ID_RE` and `DOC_NAME_RE` re-anchored with `\Z`.
- Task C started, after asking the owner the §6 question about a real posting.
- This file rewritten for the session after that one.
