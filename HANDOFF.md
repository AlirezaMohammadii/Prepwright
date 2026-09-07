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
   page mints against it, the bridge validates against it, and `curriculum`
   now mints against it too. Renaming one side locks the other out.
4. **Do not edit a file while a hunt is reading it.** Line numbers go stale.
5. **Do not report completion for partial work.** If a test fails, paste it. If
   you skipped something, name it.
6. **Do not regenerate `MANIFEST.sha256` without reading what changed.** It is
   the integrity pin. `shasum -a 256 --strict -c MANIFEST.sha256 | grep -v ': OK$'`
   before you regenerate: it names exactly the files you touched, and anything
   else in that list is something you did not mean to change.

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
corpus in front of it says. It is enforced by construction, and as of this
session the page shows the enforcement.

## 3. Settled decisions. Do not re-open them.

| Question | Answer |
|---|---|
| Deadline | None scheduled. Build in dependency order. |
| Persistence | Done. `/api/state` runs on the store. |
| Evidence | Per track, through one `TrackHandle`. ADR 0004. |
| Integrity | `MANIFEST.sha256` over 19 runtime files. |
| Resume Studio | Done both ways. It writes `<base>_JobDescription.md` at DONE. `intake` reads that folder once and hashes it. |
| Research sources | The candidate supplies URLs. The model never searches. |
| Where the page lands | `#start`, which renders whichever stage the track is in. Not a constant. |

Six ADRs in `docs/adr/`. **Read 0003 then 0004 first**: 0003 is the delta
protocol, 0004 is per-track evidence and the manifest.

## 4. Current state, and how much of it is verified

**Verified by running it.** The command backing each claim is named.

| Claim | Command |
|---|---|
| 209 tests pass, Homebrew 3.14 | `python3 -m unittest discover -s tests` |
| 209 tests pass, system 3.9.6 | `/usr/bin/python3 -m unittest discover -s tests` |
| 209 tests pass, launcher mode | `/usr/bin/python3 -I -S -m unittest discover -s tests` |
| No module reads a name it never binds | `/usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py tests/*.py tools/*.py` |
| The manifest verifies | `/usr/bin/shasum -a 256 --strict -c MANIFEST.sha256` |
| A tampered module refuses to start | append a line to `prepwright/keep.py`, run `./prep-launcher.sh`: names the file, exits 1 |
| The app runs clean from the launcher | `./prep-launcher.sh`: page loads, `/favicon.ico` 204, `/api/flow` 200, no errors |
| The whole pipeline runs end to end | a browser walkthrough from an empty store: posting pasted, 20 gaps proposed and decided, 2 real sources fetched and stored with a third refused and its reason shown, plan built, teaching turn grounded |
| A teaching turn is grounded and cites correctly | `grounded: true`, `cites` three sections, `citations.invented: []`, addressed by a curriculum-minted step key |
| The tutor refuses rather than inventing | given a corpus deliberately irrelevant to the question it named what it had and said it did not cover the topic |
| All three grounding states render | screenshotted in light and dark |
| The pipeline routes hold their boundary | `tests/test_pipeline_routes.py`, 24 tests against a live bridge over HTTP |

**Assumed, not verified.** The Codex provider path has never run (no `codex`
binary here). The iPhone (`prep iphone`) path has never run. `prep` is still not
on `PATH`, so run `./prep-launcher.sh` from the repo.

## 5. What was built this session

`intake.py`, `diagnose.py`, `research.py` and `curriculum.py` were eight-line
stubs. They are the front half of the product and they are now built, tested,
and driven from the page.

- **`research.py`** owns network access, and `intake` imports it rather than
  carrying a second SSRF guard. The address check is POSITIVE: an address passes
  when it is globally routable and not multicast. A negative list reads as if it
  covers the space and does not, because 100.64.0.0/10 answers False to
  is_private, is_reserved, is_loopback AND is_link_local on 3.9.6 and 3.14 alike.
  Every hop is re-checked, the socket is pinned to the checked address while TLS
  still validates the certificate against the name, and credentials in a URL are
  refused rather than redacted.
- **`intake.py`** takes a URL or a paste, reads the resume pipeline's application
  folder exactly once and hashes it, and writes `intake/source.txt` because
  `archive_track` tars `intake/*`.
- **`diagnose.py`** probes the posting and the candidate's own resume claims,
  with a reserved share for the resume side, and proposes gaps that nothing
  downstream can act on until the candidate decides them.
- **`curriculum.py`** orders by dependency, chooses each step's sections rather
  than pinning everything, and defers a gap no source covers instead of writing
  a step with an empty slice.
- **`bridge.py`** gained `GET /api/flow`, `GET /api/tracks` and six POST routes.
- **`index.html`** gained the guided path and the grounding footer.

## 6. The work, in dependency order

A task is done when its acceptance command runs and passes.

### Task A. The teaching views still render the placeholder curriculum. **NEXT.**

This is the biggest remaining gap and the reason the product is not yet whole.

`const DATA` at `index.html:790` is a hard-coded track for an invented "Backend
Engineer at Example Company". Every teaching view is built on it: `renderContinue`,
`renderStages`, `renderCurriculum`, `renderPractice`, and the topic ids `T1`–`T4`.
The guided path builds a REAL curriculum in the store, with real steps, real
slices and step keys the chat route accepts, and then hands the reader over to a
screen that knows nothing about it.

What to do: serve the real curriculum to the page. `GET /api/flow` already
reports the counts; it needs the steps themselves, and `renderContinue` needs to
read them instead of `DATA.topics`. `curriculum.steps_of` and `slices_of` are the
readers. The step key is already the contract both sides agree on, so a turn
started from a real step will reach the right pack with no further change.

**Acceptance:** walk the guided path to `learn`, click "Start the first step",
and hold a teaching turn against a step the curriculum wrote, with its own
pinned sections in the footer.

### Task B. The diagnostic conversation is not conversational yet.

`POST /api/diagnose {action:"plan"}` returns the probes, and nothing asks them.
The page offers only "Skip it and list everything as unlearned", so every gap
arrives at level `none` and every step is `core`: the plan the walkthrough
produced was 20 steps and eight hours, which is the honest consequence of never
asking.

`diagnose.JUDGE_SCHEMA` and `judge_prompt` exist and are unused. The route accepts
a `verdicts` array, so the model call can live in the route exactly as `assess`
does. Until then `verdicts_without_a_model` grades on length and never awards
`solid`, which is deliberately pessimistic.

**Acceptance:** answer the probes in the page, and see gaps you explained well
drop off the list rather than becoming steps.

### Task C. Extract `bridge.py`. PARTLY DONE.

**Done:** the corpus seam, and the security half via `MANIFEST.sha256`.
**Not done:** `bridge.py` is now 2,290 lines. The seams:

| Module | What moves |
|---|---|
| `security.py` | `LOCAL_ORIGINS`, `TS_*`, `_is_remote_request`, `_origins_for`, `_allowed_host`, `_remote_identity_ok`, `_safe_messages`, `_safe_assess_items` |
| `provider.py` | `_trusted_executable`, `claude_bin`, `codex_bin`, `_cli_env`, `_provider_ready`, `run_cli`, `_parse_codex_jsonl`, `trim_history`, `_usage`, `_resolved_model` |
| `teach.py` | `NO_ERRANDS`, `TUTOR_SYSTEM_BASE`, `_step_instructions`, `chat_via_cli` |
| `assess.py` | `assess_via_cli`, `review_via_cli` and their schemas |
| `serve.py` | `Handler`, `main` |

The blocker is that `security.py`'s constants are computed at import from `PORT`.
Move `PORT` into `config.py` first. **Run `tools/orphan_scan.py` after every
deletion**; `py_compile` accepts a module that reads an undefined name.

### Task D. Small, known, cheap.

- `TRACK_ID_RE` and `DOC_NAME_RE` in `config.py` are anchored with `$` rather
  than `\Z`. Same shape as a bug this project already fixed once in
  `pagestate.py`. Not currently exploitable; cheap to fix.
- `TRACK_DB_CAP` is defined in `config.py` and enforced by nothing.
- The Pareto cut never fires: `plan(max_steps=None)` defaults to `MAX_STEPS`
  (40), so a 20-gap list is never cut. The cut it needs is a time budget, and
  the honest input for one is Task B.
- A track switcher exists in the API (`GET /api/tracks`, `POST /api/track`) and
  has no UI. `current_track_id()` is still the whole selection mechanism.

## 7. What the store guarantees

Read `prepwright/state.py`, `pagestate.py` and `corpus.py` before writing
against them.

**The page sends changes, never the document.** One op per appended turn, one per
changed mark, diffed against what the bridge last acknowledged. A stale tab
cannot express a loss because no op removes anything.

**A transcript is a `turn`. Everything else is a `mark`.** `FIELDS` in
`pagestate.py` is the whole mapping and the bridge serves it to the page inside
the GET reply. Any key on a chat entry that is not `text`, `seq` or `at` rides
through `client_meta` untouched, which is how the grounding fields persist with
no schema change.

**Nothing shrinks.** `turn`, `mark`, `assessment`, `card_review`, `gap_history`
and `track_history` raise ABORT on DELETE and on any UPDATE outside one
receipted case.

**Caps are counted by the write that causes them**, inside the same
`BEGIN IMMEDIATE`, **in encoded bytes**.

**Foreign keys are ON.** `PRAGMAS_EVERY_OPEN` carries `PRAGMA foreign_keys = ON`,
so `step.gap_id`, `step_slice.step_id` and the composite `(doc_id, sec_id)` are
enforced. They are not decoration, and they fail as `sqlite3.IntegrityError`,
which is **not** a `StoreError` subclass: a caller wrapping in `except StoreError`
misses every one of them. Both `curriculum.check_gaps` and `corpus.pin_all` exist
because of exactly this.

**One track's bytes cannot reach another track's prompt.** `open_track()` is the
only function that connects to a track database, `build_pack()` takes a handle,
and `step_slice` carries a composite foreign key that cannot cross database files.

**Recovery verifies before it overwrites.** `keep.py` runs a published,
age-driven ladder of ten rungs. **Rung 4 deletes a stale track's backups**, which
the design specifies at `DESIGN-state-corpus.md:411`. Rung 6 is the first rung
that discards content. Rung 9 stops and asks for a human.

## 8. Traps that have cost real time

1. **A textual patch applied before line-based cuts** shifted every later line
   and corrupted the file. Do surgery in Python with exact-text `replace` on
   delimited blocks and **assert the match count first**.
2. **`py_compile` does not catch an unbound name.** Run `tools/orphan_scan.py`.
3. **A security test can pass for the wrong reason.** Remove the guard and check
   its own test fails. Forty-plus guards were proved that way this session, and
   six of those removals corrected the test rather than confirming it.
4. **A fixture can be more careful than production.** Every corpus test pinned to
   a step `Base.a_track` had created by hand, so nobody noticed that the seed
   path pins to a step that does not exist yet. The bug was permanent and silent.
   When a fixture sets something up, ask whether the real caller does.
5. **Do not add `-t .` to `unittest discover`.** `tests/` has no `__init__.py`.
   Use `discover -s tests -p 'test_x.py'`.
6. **A fixed-offset scribble is not reliable corruption.** Overwrite the whole
   file after the header.
7. **`BACKUPS_ACTIVE` is 1 and `BACKUPS_LEASED` is 2.**
8. **`prep iphone off` tears down whatever holds `:443`.** Guarded now.
9. **`playwright-cli eval` wraps its argument as `() => <expr>`.** A statement
   with a `;` is a SyntaxError, and the click you thought you made never
   happened. Wrap actions in an IIFE: `(function(){...; return 'ok';})()`.
10. **The browser caches `index.html` hard.** A reload, even with a cache-busting
    query, served the old page and cost twenty minutes of debugging a fix that
    was already correct. `playwright-cli close` then `open` is what works.
11. **The system Python is 3.9.6**, SQLite 3.51. `python3` is Homebrew 3.14. Run
    the suite under both. No 3.10+ syntax.
12. **A workflow journal stores an agent's return under `result`, not `value`.**
13. **`unittest` runs every `addCleanup` after `tearDown`.** Register the
    environment restore with `addCleanup` first so it runs last.
14. **On macOS `/var` resolves to `/private/var`.** Realpath both sides.
15. **A `$` anchor in a Python regex also matches before a trailing newline.**
16. **`shasum -c` writes its per-file `FAILED` lines to stdout.**
17. **The `Write` tool will put a real NUL byte in a file** if the content
    contains an interpreted `\x00`.
18. **`JSON.stringify(x).length` is UTF-16 code units, not bytes.** Use `BYTES()`.
19. **The Bash tool resets the working directory between calls.** Every command
    needs its own `cd`.
20. **`/api/chat` has no `if route ==` test.** It is the residual branch at the
    end of `do_POST`. A new POST route needs its path in the allowlist AND an
    explicit block before that fall-through, or the tutor answers its requests.
21. **A public IP is not the same as `is_global`.** 192.0.2.1 (TEST-NET-1) is not
    global, and a test written against it fails for a reason that has nothing to
    do with the guard.

## 9. The adversarial hunt: the recipe that works

Round 0 ran two sessions ago: 33 raw findings, 14 confirmed, 19 rejected, 934K
tokens, 5/5 agents, zero failures. The attempt before it used seven lenses with
4 KB prompts telling each to read six files, and all seven died on the session
limit returning nothing.

**Keep this shape.** One lens gets **two files and one question**, under 600
words. Four lenses, then **one consolidator** that verifies every finding against
live text. The consolidator is not optional: it rejected 19, including real
analyses whose consequence did not follow, and one that was the published design
rather than a defect. Run it early, while the budget is fresh.

`Workflow` for the hunt, which is read-only and parallel. **Never for edits.**

A four-lens read-only map was run this session over `index.html`/`pagestate.py`,
`bridge.py`/`corpus.py`, `state.py`/`track.py` and `DESIGN-state-corpus.md` C+E:
745K tokens, 4/4, zero failures. It was accurate and worth it. Its one wrong
call was reporting `PRAGMA foreign_keys` as unknown because it had been told not
to read `config.py`; the answer was one grep away.

**Lenses not yet run on the current tree:** dead guard, impossible number,
silent no-op, and contract drift between the new page flow and the six pipeline
routes.

## 10. Working discipline

Evidence first. Every change carries a hypothesis, the smallest safe patch, a
test against real data, a keep-or-revert decision, and one line recording it.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the
command whose output backs the word.

**Walk it in a browser before you believe it.** Three defects this session were
invisible in the source and obvious in thirty seconds of clicking: a view that
rendered and was immediately replaced, a fetch pipeline that could not store a
single real page, and a curriculum whose steps no route could open. Reading the
code found none of them.

## 11. Definition of done for the next session

- Task A: the teaching views read the real curriculum, and a step the curriculum
  wrote can be taught from the page.
- Task B: the probes are asked, and answering them shortens the plan.
- The remaining hunt lenses in §9 run on the current tree.
- `TRACK_ID_RE` and `DOC_NAME_RE` re-anchored with `\Z`.
- This file rewritten for the session after that one.
