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
   `TUTOR_TS_*` are two-sided contracts. So is `pagestate.STEP_KEY_RE`: the
   curriculum mints against it, the bridge validates against it, and the page now
   carries what the curriculum minted rather than minting a second one.
4. **Do not edit a file while a hunt is reading it.** Line numbers go stale.
5. **Do not report completion for partial work.** If a test fails, paste it. If
   you skipped something, name it.
6. **Do not regenerate `MANIFEST.sha256` without reading what changed.** It is
   the integrity pin. `shasum -a 256 --strict -c MANIFEST.sha256 | grep -v ': OK$'`
   before you regenerate: it names exactly the files you touched, and anything
   else in that list is something you did not mean to change.
7. **Do not let a test call a model.** `PREPWRIGHT_NO_MODEL=1` is set by the test
   harness and makes every provider call fail closed with a named reason. It
   exists because wiring the diagnostic judge silently turned an 8-second suite
   into a 60-second billable one. After adding any route that calls a model,
   check the suite's wall clock.
8. **Do not run the hunt after the commit.** Round 1 confirmed 14 defects and 8
   of them had been written that same day.

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
corpus in front of it says. It is enforced by construction, the page shows the
enforcement, and as of this session the corpus is filled by the app itself
without weakening any of that.

## 3. Settled decisions. Do not re-open them.

| Question | Answer |
|---|---|
| Deadline | None scheduled. Build in dependency order. |
| Persistence | Done. `/api/state` runs on the store. |
| Evidence | Per track, through one `TrackHandle`. ADR 0004. |
| Integrity | `MANIFEST.sha256` over 20 runtime files. |
| Agentic orchestration | **No.** `/langgraph-architect` ran 2026-09-08. The dossier is committed at `langgraph-design/dossier.json`, machine-validated, verdict `plain_code` with zero triggering needs. Deterministic code owns all control flow, and a model call is always one bounded, schema-shaped request. A graph runtime would break stdlib-only and no-API-key. |
| Research sources | **Changed this session, on the owner's instruction.** The app finds them. The fetch decides. Model memory only nominates a URL. `research.discover` fetches every nomination through the same SSRF guard a pasted link goes through, checks the page shares vocabulary with the gap, and records every discard with its reason. The candidate can still paste URLs, and can distrust anything the app found. |
| The goal the design serves | **Corrected by the owner on 2026-09-08. Read this before planning anything.** The 20/80 cut applies to the MATERIAL INSIDE a source, not to WHICH gaps get studied. The owner's words: "I might need to learn all of those identified gaps and the app must be accommodating of that." So the app must be able to carry every approved gap to a taught, assessed step, and the economy comes from teaching each gap from the smallest sufficient evidence, never from dropping gaps. Declining a gap stays the candidate's choice and is never the app's optimisation. Any cap, tier, or budget that silently drops an approved gap is a defect, not a feature. |
| Supplied resources | **New this session.** The candidate points at a file and says what to learn from it. `prepwright/ingest.py` reads PDF, Word, Excel, CSV, HTML, Markdown and text, proves the extraction is prose, and keeps only the part that answers the goal. No model is called and no socket is opened: a 300-page book and an empty file cost the same number of tokens to ingest, which is zero. |
| Where the page lands | `#start`, which renders whichever stage the track is in. |

Six ADRs in `docs/adr/`. **Read 0003 then 0004 first**: 0003 is the delta
protocol, 0004 is per-track evidence and the manifest.

## 4. Current state, and how much of it is verified

**Verified by running it, this session.** The command backing each claim is named.

| Claim | Command |
|---|---|
| 357 tests pass, Homebrew 3.14 | `python3 -m unittest discover -s tests` |
| 357 tests pass, system 3.9.6 | `/usr/bin/python3 -m unittest discover -s tests` |
| 357 tests pass, launcher mode | `/usr/bin/python3 -I -S -m unittest discover -s tests` |
| The suite makes no network, model or dialog call | the same command, wall clock under 13 s |
| No module reads a name it never binds | `/usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py tests/*.py tools/*.py` |
| The manifest verifies over 20 pinned files | `/usr/bin/shasum -a 256 --strict -c MANIFEST.sha256` |
| The page's JavaScript parses | extract the `<script>` body, `node --check` |
| The app runs clean from the launcher | `./prep-launcher.sh`, then `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8010/` |
| Every one of the 11 views renders, zero console errors | click each `[data-view]`, then `playwright-cli console` |
| The Sources file panel drives its own ingest | walked in a browser: two panels mounted, a decoy path in the hidden one, the visible one read `handbook.md` and kept 4 of 7 sections |
| A commit advances the stage without a reload | the stage card went to "Build your plan, 1 document(s) stored, 3 gap(s) approved" straight after the ingest |
| A file run's ledger rows name their section | `section | Contents | shares no vocabulary with the goal`, and the summary says "3 section(s) left out of supplied files" |
| A decided gap can be undone from the page | the approved gap renders "Actually, I can explain it" |
| Poppler is still trusted after the ownership fix | `ingest.pdftotext_bin()` returns the Cellar path |
| 16 of 16 real PDFs on this machine now pass the gate | was 10 of 16, measured with `pdftotext -layout` against `ingest.gate` |

**Not verified. Say so rather than assuming.**

- **The file chooser dialog has never been opened.** The plumbing is proven
  (`osascript -e 'POSIX path of (path to home folder)'` returns a path, and
  `/usr/bin/osascript` is root-owned and passes `trusted_executable`), and the
  route refuses cleanly under `PREPWRIGHT_NO_DIALOG=1`, which the test harness
  sets. But no one has clicked "Choose a file…" and picked something. **That is
  one click and it is yours.**
- **19 of the hunt's 49 raw findings were never verified.** The workflow capped
  refutation at 30. The 30 that ran produced 25 survivors and 23 confirmed
  findings, all now fixed. The other 19 are in the journal at
  `subagents/workflows/wf_4fa46a86-880/journal.jsonl` and have been neither
  confirmed nor dismissed.
- **The back half of the product has never run, on any track, ever.** Measured
  2026-09-09 across all three live track databases:
  `assessment` 0 rows, `card` 0 rows, and `mark` holds no row of kind `session`,
  `qa`, `assess` or `card`. Command:
  `sqlite3 ~/.Prepwright/tracks/<t>/track.db "select kind,count(*) from mark group by kind"`.
  Teaching has produced 5 turns on one track and 2 on another. Nothing has ever
  been graded, no session has ever been logged, no recap card has ever existed.
- **`/api/assess` and `/api/review` have zero test coverage.** The other 13
  routes are exercised. These two are not, by name, anywhere under `tests/`.
  Command: `grep -rho "/api/[a-z_]*" tests/*.py | sort | uniq -c`, compared
  against `grep -o '"/api/[a-z_]*"' bridge.py | sort -u`.
- **The stage machine has no state after `learn`.** `flow_state` returns
  intake, diagnose, approve, research, curriculum, learn, and then stays on
  `learn` forever. A finished plan and a plan on its first step are the same
  stage. Whether a track should be able to end is an open design question.
- The Codex provider path has never run (no `codex` binary here). The iPhone
  (`prep iphone`) path has never run. `prep` is still not on `PATH`, so run
  `./prep-launcher.sh` from the repo.

## 5. What this session did

Two things, and the second is most of it: the capacity test the owner's ruling
required, and the adversarial hunt over the code that had never been hunted.

### 5a. Capacity: every approved gap survives, or is named

`tests/test_capacity.py` is new. Its acceptance property is one line and
everything else supports it:

    the approved gaps == the gaps that got a step + the gaps named as deferred

Set equality, both directions, no gap in both halves, every deferral carrying a
reason and a label. It runs at 25 approved gaps against a multi-document
supplied resource, at 45 gaps against the store's own `MAX_STEPS`, with no
corpus at all, and against a resource large enough to hit the per-track document
cap. It found one real defect immediately, which is item 4 below.

It also found a fixture trap worth keeping: `MIN_TERMS` is 2, but one hit in a
HEADING sets `labelled` and one word is then enough (`curriculum.py:144`, a
deliberate rule with a stated reason). The first version of the fixture headed
every section "<subject> in practice", so "practice" made every section a
labelled match for every gap and a gap about kombucha was pinned to twenty-five
sections about agents. The fixture was wrong, not the scorer. But one generic
heading word defeats `MIN_TERMS` on any corpus, and nothing records that.

### 5b. The hunt: 6 lenses, 49 raw findings, 23 confirmed, 23 fixed

37 agents, 6.37M tokens, 2,213 s, zero failures. Six lenses (PDF and Office
readers, selection, the file route, the page contract, concurrency, error
paths), then one skeptic per finding defaulting to REFUTED, then a consolidator
that verified all 25 survivors against live text and **reproduced 21 of them by
executing the real functions**. It rejected nothing outright and instead struck
sub-claims inside otherwise sound findings, which is a better outcome than round
1's quarter-to-half rejection rate and suggests the refutation pass ahead of it
did the coarse filtering.

Every one of the 23 is fixed, and every fix is proven by reverting it and
confirming its own test fails. Ranked by what it did to the candidate:

| # | What it did | Where |
|---|---|---|
| 1 | A two-column Title Case sheet lost 100% of its content, and both reports said the opposite of what happened | `ingest.looks_like_heading` |
| 2 | A spreadsheet row with an omitted cell stored with its columns shifted left: "Access review \| Open" says the OWNER is "Open" | `ingest.read_xlsx` |
| 3 | Excel sheet names attached to the wrong worksheets from ten sheets up, because names came from workbook order and parts from a LEXICOGRAPHIC sort | `ingest.read_xlsx` |
| 4 | The resource TITLE, not its content, decided what was stored: front matter outranked every chapter | `ingest._as_index` |
| 5 | An over-long paragraph's chunks were emitted before the paragraphs already buffered, so a chapter's opening definition was demoted to "(cont. 4)" | `ingest._split_body` |
| 6 | `-layout` padding was measured as the document's own spacing: **6 of 16 real PDFs on this machine were refused**, every one at 0.13 without it | `ingest.quality` |
| 7 | One non-UTF-8 byte re-read the whole file as cp1252 and rewrote every accented letter, and the gate is structurally unable to see it | `ingest._decode` |
| 8 | A goal of only short words ("AI and ML") silently disabled the cut and reported full coverage | `ingest.select` |
| 9 | A partial ingest rendered as a complete one, because `failed` was never read | `index.html`, `bridge._file_report` |
| 10 | **The stdlib PDF reader crashed on every ordinary PDF** and the request got no reply at all. `re.sub` parses its replacement template whether or not the pattern matches, and the last one ended in a bare backslash. The documented fallback for machines without poppler had never once run | `ingest._PDF_ESCAPES` |
| 11 | **The Sources file box was dead.** `fileBox()` is mounted twice and `$` is an unscoped `querySelector`, so every read returned the hidden copy in `#view-start` | `index.html` |
| 12 | `longest_run` is a maximum, so one long URL refused a whole clean document | `ingest.gate` |
| 13 | Truncation past 2,000,000 characters was unmarked, so the coverage warning named present material as missing | `ingest.normalise` |
| 14 | The commit reply carried no `flow`, so the stage and the document count never updated and "Build the plan" stayed unreachable | `bridge._route_file` |
| 15 | `_trim_report` shrank only the discard list, so an over-cap base body was sliced mid-JSON and a run that dropped hundreds of sections displayed as one that dropped none | `bridge._trim_report` |
| 16 | `resolve()` compared the HOME prefix case-sensitively. APFS is case-insensitive and `realpath` does not canonicalise case, so **one capital letter crossed the isolation boundary**. HANDOFF §4 had listed that refusal as verified, and it was verified on the exact spelling only | `ingest.resolve` |
| 17 | `trusted_executable` walked the resolved path's ancestors, so the directory holding a SYMLINK to the binary was never checked. Verified live: `/opt/homebrew/bin` is `drwxrwxr-x` and is not on the Cellar path's chain | `security.py` |
| 18 | `zlib.decompress` with no `max_length`, measured 1029:1, and `pdftotext` stdout read to EOF into memory | `ingest._pdf_via_stdlib`, `_pdf_via_poppler` |
| 19 | The ledger rendered file-run discards with discovery's keys, and counted the candidate's own dropped sections as "nominations rejected" | `index.html` |
| 20 | `stream(.*?)endstream` rescanned to EOF for every unterminated stream: quadratic, on the one path with no time bound | `ingest._pdf_streams` |
| 21 | `_csv.Error` is neither OSError nor ValueError, so a malformed CSV dropped the connection with no reply | `ingest.read_table` |
| 22 | `group()` had no document cap, so one resource could claim 40 of the track's 48 slots | `ingest.group` |
| 23 | "shares no vocabulary with the goal" was false for any section the scorer skipped on the term count | `ingest.select` |

**The ownership check needed a judgment call, and it is recorded here.** Fixing
17 strictly disabled poppler on this machine, because `/opt/homebrew/bin` is
group-writable and `chmod g-w` there breaks `brew`. The rule is now the threat
as stated rather than a proxy for it. World-writable always fails.
Group-writable fails only when the group has a member who is a real login
account other than root and this user. It fails closed when group membership
cannot be read. On this machine `admin` holds root, `ali` and `_mbsetupuser`
(uid 248, no login), and no other real account exists.

### 5c. The seven defects HANDOFF §10 left open: all closed

| # | Was | Now |
|---|---|---|
| 1 | `set_doc_status(id,'ready')` flipped the row while `_quarantine_doc` had MOVED the file, so the document counted as ready and served zero sections forever | The file is restored from `quarantine/`, then re-verified. A document whose bytes are still wrong is refused by name and stays quarantined |
| 2 | `relevant()` enforced `PACK_MAX_SECTIONS` and nothing enforced `PACK_MAX_BYTES`, and ten sections of `SECTION_MAX_CHARS` fit the byte cap in ASCII and nothing else | `relevant()` takes a byte budget. Note the relationship that made this subtle: `DOC_MAX_BYTES` sits 288 bytes above `PACK_MAX_BYTES`, so ONE document can never overrun a pack and the overrun only appears once a step draws on several |
| 3 | `build_pack` hashed the raw text and redacted afterwards, and left `sections[].body` raw | Redaction happens first, the hash is taken over what is actually sent, and the bodies are redacted too. **Scope stated honestly: `fit_sections` redacts on the way in, so this was latent, not exploitable.** It was correct by accident, resting on an invariant two modules away with nothing asserting the link. The test writes through `write_doc` directly to test the property rather than the coincidence |
| 4 | `max_steps` had no ceiling against `MAX_STEPS`, so `build(max_steps=50)` committed 40 steps and raised, and the gaps past the cap had neither a step nor a deferral | The ceiling is applied in `plan()`, where the cut is reported and the reason names the cap |
| 5 | `/api/health` bypassed the session gate, so a page open across a restart showed a green pill and a dead app | It stays outside the gate, because the launcher reads it over loopback, and now reports `session`. The page distinguishes "bridge down" from "my cookie died" and says so |
| 6 | Declining every gap satisfied `decided` and advanced to research, where every action refused | The stage stays on approve while nothing is approved, the screen says why, and a decided gap now carries "Actually, study this" |
| 7 | `track.phase` was written once and never updated: nine tracks, all saying `intake`, one of them with 20 taught steps | Dropped, with a guarded migration on library open. `flow_state` was always the real answer |

### 5d. What else landed

- **The file chooser is opened by the BRIDGE, not the page.** A browser file
  input hands JavaScript the basename and never the path, in every browser and
  by design, so a picker on the page could not fill the box the panel reads.
  Uploading the bytes instead would put a 64 MB body on a route that writes to
  the filesystem. The bridge is a local process, so it asks the operating system
  through `osascript` and returns a path, which goes through the same `resolve()`
  and `gate()` a typed path does. Single-flight, 240 s timeout, cancel is not an
  error, and `PREPWRIGHT_NO_DIALOG=1` makes it refuse in tests.
- **`doc.source_sha256` carries the container's own hash** alongside
  `origin_sha256`, which is the hash of the extracted markdown. Without it,
  re-verifying a supplied document needs the same extractor version, so a
  poppler upgrade would make every supplied document look tampered with.
- **Task C is partly done.** `PORT` and `HOST` moved into `config.py`, which was
  the stated blocker, and the whole request boundary moved into `security.py`:
  the origin and host allowlists, the tailnet identity check, the two payload
  sanitisers and the static-route allowlist, 184 lines. `bridge.py` keeps short
  aliases, because renaming 200 call sites for a file move is churn.
  **`bridge.py` is 2,694 lines, down only 56 from 2,750**, because this session
  also added the pick route and a good deal of comment. `provider.py`, `teach.py`,
  `assess.py` and `serve.py` are still unmoved.
- **`TRACK_DB_CAP` is enforced by arithmetic, on purpose.** It is the sum of four
  caps that ARE enforced inside the write transaction that causes them, plus
  headroom. A test asserts the sum still fits, which is the real failure mode:
  raising one component silently breaks the budget. `recount` now records a
  library event when the real file exceeds it, since SQLite's own overhead is
  not content and is not a reason to refuse a write.
- **`open_track_or_recover` now recovers from a connect-time failure.** It caught
  `CorruptStore` only, so a file damaged badly enough that `connect` could not
  run its PRAGMAs got "database disk image is malformed" and no recovery, while
  a milder corruption of the same file recovered cleanly. Found because trap 6
  bit the existing test: a 4 KiB scribble at the midpoint is not reliable
  corruption, and adding one column to the `doc` schema shifted the page layout
  enough that it corrupted nothing.
- **Four scratch tracks archived** through `track.archive_track`, plus three this
  session created for the browser walk. Three live tracks remain:
  `t-454d410f0522` (your 17 probes), `t-93c97fdd6d77` (Wingtip), `t-6c3a05d5f79f`.
  `t-0a69f756e38f` and `t-18598f2b65f8` were NOT orphans: they are
  `lifecycle='trashed'` with complete `.pwk` archives in `~/.prepwright/trash/`,
  which is the designed retention state.

## 6. The work, in dependency order

### Task E. The 17 probes. **Still yours.**

They gate a *well-graded* plan. They do **not** gate proving the back half:
`t-93c97fdd6d77` already carries a full corpus and 20 ready steps. See §10a.

Track `t-454d410f0522`, "Agentic AI Consultant at Proseware", 17 gaps, **16 still to
decide, 1 approved**. Every undecided one carries "No answer given" because the
diagnostic was driven through `/api/diagnose {action: propose}` rather than
through the conversation. Only the owner can answer them, and inventing answers
corrupts the plan at its root.

The point is grading, not shortening. A graded gap list is what lets each gap be
taught from the right depth. **A plan that stays long is a correct outcome**, and
after this session the chain is proven to carry it: `tests/test_capacity.py`
shows 25 approved gaps reaching 25 steps, and 45 gaps producing 40 steps and 5
named deferrals with the cap in the reason.

### Task E1. The old Wingtip track is still 8 hours.

Unchanged. `t-93c97fdd6d77` was diagnosed before the judge existed, so all 20
gaps carry `why = "graded without a model: length only"` and `tier_for` produced
9 core and 11 depth with no real cut. The fix is not code: run intake on a fresh
track from the same posting and answer the probes.

### Task C. Extract `bridge.py`. Partly done, see 5d.

Remaining seams, unchanged:

| Module | What moves |
|---|---|
| `provider.py` | `claude_bin`, `codex_bin`, `_cli_env`, `_provider_ready`, `run_cli`, `CLI_BASE`, `CLI_SEARCH`, `MODEL_DISABLED`, `_parse_codex_jsonl`, `trim_history`, `_usage`, `_resolved_model` |
| `teach.py` | `NO_ERRANDS`, `TUTOR_SYSTEM_BASE`, `_step_instructions`, `chat_via_cli` |
| `assess.py` | `assess_via_cli`, `review_via_cli` and their schemas |
| `serve.py` | `Handler`, `main` |

**Run `tools/orphan_scan.py` after every deletion.** It caught eight orphans on
this session's move, including `_static_route_allowed`, which the cut boundary
took with it while `bridge.py` still called it. And run the launcher afterwards:
`py_compile` and the orphan scan both passed a module-scope read that ran
before its own import, and only starting the process found it.

### Task D. Done, see 5d.

## 7. What the store guarantees

Read `prepwright/state.py`, `pagestate.py` and `corpus.py` before writing
against them.

**The page sends changes, never the document.** One op per appended turn, one per
changed mark, diffed against what the bridge last acknowledged. A stale tab
cannot express a loss because no op removes anything. The corollary bit this
session: a page holding track A's document and then pointed at track B will push
A's work into B, and the store cannot take it back out. See §10.

**A transcript is a `turn`. Everything else is a `mark`.** `FIELDS` in
`pagestate.py` is the whole mapping and the bridge serves it to the page inside
the GET reply. Any key on a chat entry that is not `text`, `seq` or `at` rides
through `client_meta` untouched, which is how the grounding fields persist with
no schema change.

**Nothing shrinks.** `turn`, `mark`, `assessment`, `card_review`, `gap_history`
and `track_history` raise ABORT on DELETE and on any UPDATE outside one
receipted case. A distrusted document is `quarantined`, never deleted: its rows,
its origin hash and its provenance all stay, and only `build_pack`'s
`status='ready'` filter stops it reaching a prompt.

**Caps are counted by the write that causes them**, inside the same
`BEGIN IMMEDIATE`, **in encoded bytes**.

**Foreign keys are ON.** `PRAGMAS_EVERY_OPEN` carries `PRAGMA foreign_keys = ON`,
so `step.gap_id`, `step_slice.step_id` and the composite `(doc_id, sec_id)` are
enforced. They fail as `sqlite3.IntegrityError`, which is **not** a `StoreError`
subclass: a caller wrapping in `except StoreError` misses every one of them.

**`StoreError` IS a `RuntimeError` subclass.** The opposite direction bites just
as hard: a broad `except RuntimeError` placed above the store's own handlers
turns every 409 and 507 into a 400. Put it last.

**One track's bytes cannot reach another track's prompt.** `open_track()` is the
only function that connects to a track database, `build_pack()` takes a handle,
and `step_slice` carries a composite foreign key that cannot cross database files.

**A document knows why it was looked for.** `doc.run_id` points at the
`research_run` that fetched it, and that run's `queries` column carries the
discard list. Both existed in the schema from the start and nothing wrote them
until this session.

## 8. Traps that have cost real time

1. **A textual patch applied before line-based cuts** shifted every later line
   and corrupted the file. Do surgery in Python with exact-text `replace` on
   delimited blocks and **assert the match count first**.
2. **`py_compile` does not catch an unbound name.** Run `tools/orphan_scan.py`.
3. **A security test can pass for the wrong reason.** Remove the guard and check
   its own test fails. Done four times this session. Every time it either
   confirmed the guard or corrected the test.
4. **A fixture can be more careful than production.** When a fixture sets
   something up, ask whether the real caller does.
5. **Do not add `-t .` to `unittest discover`.** `tests/` has no `__init__.py`.
6. **A fixed-offset scribble is not reliable corruption.** Overwrite the whole
   file after the header.
7. **`BACKUPS_ACTIVE` is 1 and `BACKUPS_LEASED` is 2.**
8. **`prep iphone off` tears down whatever holds `:443`.** Guarded now.
9. **`playwright-cli eval` wraps its argument as `() => <expr>`.** Wrap actions
   in an IIFE: `(function(){...; return 'ok';})()`.
10. **The browser caches `index.html` hard.** `playwright-cli close` then `open`.
    A reload is not enough.
11. **The system Python is 3.9.6**, SQLite 3.51. `python3` is Homebrew 3.14. Run
    the suite under both. No 3.10+ syntax.
12. **A workflow journal stores an agent's return under `result`, not `value`.**
13. **`unittest` runs every `addCleanup` after `tearDown`.**
14. **On macOS `/var` resolves to `/private/var`.** Realpath both sides.
15. **A `$` anchor in a Python regex also matches before a trailing newline.**
16. **`shasum -c` writes its per-file `FAILED` lines to stdout.**
17. **The `Write` tool will put a real NUL byte in a file** if the content
    contains an interpreted `\x00`.
18. **`JSON.stringify(x).length` is UTF-16 code units, not bytes.** Use `BYTES()`.
19. **The Bash tool resets the working directory between calls.**
20. **`/api/chat` has no `if route ==` test.** It is the residual branch at the
    end of `do_POST`. Prefer an ACTION on an existing allowlisted route over a
    new POST path; that is why `discover` and `quarantine` live on
    `/api/research`.
21. **A public IP is not the same as `is_global`.** 192.0.2.1 is not global.
22. **A running bridge does not reload edited code.** Forty minutes went into a
    "failing" judge that was working: the process had been started before the
    route was written, and the page reported the old string faithfully. Kill and
    restart after every server-side edit, and check the reply for a string only
    the new code can produce.
23. **The page's identity and the curriculum's identity are different strings.**
    A step key is `8:topic:S08`; the topic it names is keyed by its gap, `g08`.
    `stepFromKey` resolves through `STEP_INDEX` rather than parsing, because
    parsing produced an id that was not in `DATA.topics` and every render site
    that dereferences it threw. A new kind of step must be added to `STEP_INDEX`
    in `adoptCurriculum` or it will parse and break.
24. **Bounding a JSON report by slicing its serialised bytes produces text that
    is not JSON.** The only reader parses it, so the data disappears silently.
25. **zsh does not word-split an unquoted variable.** `for P in "python3"
    "/usr/bin/python3 -I -S"; do $P -m unittest ...` silently runs nothing for
    the third interpreter and prints an empty section that reads as a pass. Run
    the three commands literally.
26. **Two report shapes that the page branches on must carry the same keys.**
    `preview()` returned `ok` and `ingest_file()` did not, so a successful
    ingest rendered as "Not stored. That file could not be read." with the
    documents sitting in the corpus. `tests/test_ingest.py::
    test_both_reports_answer_the_same_questions` compares the two.
27. **A route helper's arity is not checked until it runs.** `_discovery_report`
    takes `(provider, model, out)`; calling it with one argument passed
    `py_compile`, passed `orphan_scan`, and killed the request at run time. Four
    route tests went red at once and the first traceback was a connection error,
    which reads like a dead server rather than a wrong call.
28. **A relative score floor collapses against a single dominant match**, and a
    scorer that sums term hits over a body is biased toward long sections. Both
    matter for choosing what to ingest out of a whole book and neither matters
    inside a pack, where sections are all near the cap. `ingest._by_density`
    corrects the second HERE rather than in `score_sections`, whose ranking is
    settled behaviour.
29. **`/api/gap` takes `status`, not `action`.** Sending the wrong key returns a
    400 that a script ignoring status codes will not see, and the failure
    surfaces two steps later as "No gap has been approved yet".
    Drop whole entries and record how many.

## 9. The adversarial hunt: the recipe that works

**Keep this shape.** One lens gets **two files and one question**, under 600
words. Four lenses, then **one consolidator** that verifies every finding against
live text and defaults to REJECTED. The consolidator is not optional: across two
rounds it has rejected a quarter to a half of everything reported, including real
analyses whose consequence did not follow and one that was the published design.

`Workflow` for the hunt, which is read-only and parallel. **Never for edits.**

Round 1 ran this session over the new code: dead guard, impossible number, silent
no-op, and contract drift between the page flow and the pipeline routes. 4.1M
tokens, 24 agents, zero failures, 20 raw findings, 14 confirmed.

**Round 2 ran all six lenses**, including the two that were outstanding:
concurrency (two tabs, the `MODEL_GATE`, the lease) and error-path coverage.
It added a refutation pass between the lenses and the consolidator, one skeptic
per finding defaulting to REFUTED, which did the coarse filtering the
consolidator used to do alone. 37 agents, 6.37M tokens, 49 raw findings, 25
survivors, 23 confirmed. **The cap was 30 of 49, so 19 were never verified.**

## 10. What is left

**Nothing from the hunt and nothing from the old §10 is open.** All 23 confirmed
findings and all 7 pre-existing defects are fixed, each with a test that fails
when the fix is reverted. What remains is not defect repair. It is the third of
the product that was built and never executed.

### 10a. The largest hole: the loop has never closed

The front half is proven on real data. `t-93c97fdd6d77` carries 20 approved
gaps, 9 documents, 66 sections and 20 steps, all `ready`, all
`evidence_state='full'`. Everything after that point is untested and unrun. See
the three new bullets in §4.

**Nothing blocks proving it.** That track does not need the 17 probes. Open a
step, teach, close the session, run the assessment, drill the recap, and watch
what breaks. Do that before writing a single test for `/api/assess`: writing the
contract before anyone has seen the route run once is guessing.

### 10b. The recap drill claims something it does not do

The page kicker reads `Recap drill · spaced repetition`. The implementation is
`recapQueue = shuffle([...bank])` plus a `missed >= 2` weak list
(`index.html:3516`). No intervals, no due dates, no ease.

Meanwhile `state.py` carries a complete SM-2 schema (`card.interval_days`,
`ease`, `reps`, `lapses`, `due_utc`, and an append-only `card_review` ledger)
whose only caller in the whole repository is `tests/test_persistence.py`. The
page persists its bank as `mark` rows of kind `card` (`pagestate.FIELDS`)
instead, so the durable scheduler is dead code and the live drill is
mislabelled. Two card systems exist. One of them has to go, or the scheduler has
to be wired up and the label earned.

### 10c. Secondary, unchanged

1. **The 19 unverified findings.** The round-2 workflow capped refutation at 30
   of 49. Read them from `journal.jsonl` in the run directory named in §4 and
   put them through a refutation pass. Neither confirmed nor dismissed.
2. **Click "Choose a file…" once.** The chooser has never opened. Everything
   around it is proven. The dialog itself is not.
3. **Answer the 17 probes** (Task E). They gate a well-graded plan, not the
   loop.
4. **Task C's remaining four modules** (`provider.py`, `teach.py`, `assess.py`,
   `serve.py`). Refactor debt, zero functional impact.
5. **One generic heading word defeats MIN_TERMS**, because a hit in a heading
   sets `labelled` and one word is then enough. That is a deliberate rule with a
   stated reason (`curriculum.py:144`) and it was not changed. On a corpus whose
   headings share a common word ("Overview", "Scope", "Policy"), a gap with no
   real coverage can still be pinned to a near-miss. It cost an hour of fixture
   debugging. Decide whether the rule should require the heading hit to be a
   rare term.
6. **The relative floor still collapses against a single dominant match.**
   Trap 28 named it and it is still true: at the `focused` floor, one short
   dense section can drop two genuinely on-topic chapters. A test in
   `test_hunt_round2.py` documents the current behaviour rather than asserting
   it is right.

## 11. Working discipline

Evidence first. Every change carries a hypothesis, the smallest safe patch, a
test against real data, a keep-or-revert decision, and one line recording it.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the
command whose output backs the word.

**Prove every fix by reverting it and confirming its own test fails.** Done for
all 30 fixes this session. It caught six tests that passed for the wrong reason:
a flate bomb with no text operator that returned empty either way. A
`space_ratio` fixture not padded enough to cross the ceiling. An xlsx fixture
whose sheet order matched its part numbering, so positional pairing happened to
be right. A pack-hash fixture whose corpus had nothing to redact. A wide-corpus
fixture that fit inside one document's cap. And a timing test too small to
expose quadratic C-speed scanning.

**Walk it in a browser before you believe it.** The two defects with the widest
blast radius this session were invisible in the source: the Sources file box
reading the hidden panel's empty input, and the stage never advancing after a
successful ingest.

Surgery: exact-text `replace` in Python on delimited blocks, and **assert the
match count before writing**. One replacement in this session's page patch
silently matched zero because the file held a real `·` where the anchor had the
JS escape `·`, and only the assertion caught it.

After any server-side edit, **kill and restart the bridge**. After any change to
`bridge.py`, `index.html` or `prepwright/*.py`: read what
`shasum -c MANIFEST.sha256` names as changed *before* regenerating, then
`./tools/make_manifest.sh`.

Never let a test call a model, and now never let one open a dialog.
`PREPWRIGHT_NO_MODEL=1` and `PREPWRIGHT_NO_DIALOG=1` are both set by the harness.

## 12. Definition of done for the next session

In dependency order. Each line is done only when you can name the command or the
browser action whose output backs it.

1. The back half walked end to end on `t-93c97fdd6d77` in a real browser with a
   real model call: a step opened, taught, closed, assessed, and drilled.
   Evidence: non-zero `assessment` rows and non-zero `mark` rows of kind
   `session` and `card` in that track's database.
2. Whatever that walk breaks, fixed, each fix proven by reverting it and
   confirming its own test fails.
3. `/api/assess` and `/api/review` covered by stub-provider route tests, written
   against the contract the walk revealed, not the one the source implies.
4. The card duplication in §10b decided and the decision implemented: either the
   SM-2 tables are wired to the drill and the label is earned, or they are
   deleted and the kicker is corrected. Do not leave both.
5. The 19 unverified hunt findings triaged: each confirmed and fixed, or
   dismissed in writing with a reason.
6. The file chooser walked once in a browser.
7. 3-interpreter suite green, manifest clean, orphan scan clean, every view
   walked with zero console errors.
8. This file rewritten for the session after that one, every claim naming its
   command.

Committed locally. **This repository has no remote. Never invent a push
target.**
