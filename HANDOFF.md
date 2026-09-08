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
| Integrity | `MANIFEST.sha256` over 19 runtime files. |
| Agentic orchestration | **No.** `/langgraph-architect` ran 2026-09-08. The dossier is committed at `langgraph-design/dossier.json`, machine-validated, verdict `plain_code` with zero triggering needs. Deterministic code owns all control flow, and a model call is always one bounded, schema-shaped request. A graph runtime would break stdlib-only and no-API-key. |
| Research sources | **Changed this session, on the owner's instruction.** The app finds them. The fetch decides. Model memory only nominates a URL. `research.discover` fetches every nomination through the same SSRF guard a pasted link goes through, checks the page shares vocabulary with the gap, and records every discard with its reason. The candidate can still paste URLs, and can distrust anything the app found. |
| The goal the design serves | **Corrected by the owner on 2026-09-08. Read this before planning anything.** The 20/80 cut applies to the MATERIAL INSIDE a source, not to WHICH gaps get studied. The owner's words: "I might need to learn all of those identified gaps and the app must be accommodating of that." So the app must be able to carry every approved gap to a taught, assessed step, and the economy comes from teaching each gap from the smallest sufficient evidence, never from dropping gaps. Declining a gap stays the candidate's choice and is never the app's optimisation. Any cap, tier, or budget that silently drops an approved gap is a defect, not a feature. |
| Supplied resources | **New this session.** The candidate points at a file and says what to learn from it. `prepwright/ingest.py` reads PDF, Word, Excel, CSV, HTML, Markdown and text, proves the extraction is prose, and keeps only the part that answers the goal. No model is called and no socket is opened: a 300-page book and an empty file cost the same number of tokens to ingest, which is zero. |
| Where the page lands | `#start`, which renders whichever stage the track is in. |

Six ADRs in `docs/adr/`. **Read 0003 then 0004 first**: 0003 is the delta
protocol, 0004 is per-track evidence and the manifest.

## 4. Current state, and how much of it is verified

**Verified by running it.** The command backing each claim is named.

| Claim | Command |
|---|---|
| 235 tests pass, Homebrew 3.14 | `python3 -m unittest discover -s tests` |
| 235 tests pass, system 3.9.6 | `/usr/bin/python3 -m unittest discover -s tests` |
| 235 tests pass, launcher mode | `/usr/bin/python3 -I -S -m unittest discover -s tests` |
| The suite makes no network or CLI calls | the same command, wall clock under 10 s |
| No module reads a name it never binds | `/usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py tests/*.py tools/*.py` |
| The manifest verifies | `/usr/bin/shasum -a 256 --strict -c MANIFEST.sha256` |
| The page's JavaScript parses | extract every `<script>` body, `node --check` |
| The app runs clean from the launcher | `./prep-launcher.sh`: page loads, `/favicon.ico` 204, `/api/flow` 200, zero console errors |
| Every one of the 11 views renders on a real adopted curriculum | click each `[data-view]` in turn, then `playwright-cli console` |

**Verified this session, on a supplied 1.9 MB PDF (NIST AI 100-1).**

| Claim | Evidence |
|---|---|
| A PDF becomes teachable corpus | 1.9 MB PDF, `pdftotext`, 122,041 characters, 172 sections, 0.1 s, **zero model tokens**. The text is 15.9x smaller than the container it came out of, and the pack cap means only ~3 KB of it reaches a model per turn however large the resource is. |
| The cut is the product | `focused` keeps 20 of 172 sections on a real goal, `balanced` 81, `broad` 109. `focused` is the default because a twelfth of a document is the fraction one candidate needs before one interview. |
| Garbage is refused, not stored | `gate()` refuses six named failure modes and every refusal quotes the measurement that failed. Removing `max_longest_run` makes its own test fail. |
| A resource that does not answer is said so | Goal "agent orchestration tool calling planning memory LLM evaluation" against the AI RMF: 0 of 172 sections kept, and the warning names the ideas the book does not contain. |
| One track's corpus cannot re-enter another as a supplied file | `resolve()` refuses any path under `config.HOME`. Removing the check makes its own test fail. |
| A teaching turn is grounded in the supplied PDF | Scratch track `t-3216946fb811`, step `3:topic:S03`, `grounded=True`, cites `['D04§s04','D04§s06','D05§s01','D04§s10','D01§s04','D01§s09']`, `citations.invented=[]`, pack `d73f29bb4fc386bd`. |
| The tutor still refuses to remember a version | Asked which edition it was teaching: "every excerpt I have is headed 'nist.pdf, date not stated' ... I won't supply one from memory - a remembered version number is exactly the thing you'd repeat confidently in the room and be wrong about." |
| The whole panel works in a browser | Path + goal + depth, Check it first, Add it to this track. Report renders, ledger updates, **zero console errors**. |

**Verified in a browser, on the real Wingtip track (`t-93c97fdd6d77`), an earlier session.**

| Claim | Evidence |
|---|---|
| The app finds its own sources | 9 documents, publishers `oaic.gov.au`, `nist.gov`, `airc.nist.gov`, `isc2.org` and two secondary hosts. 12 nominations rejected with reasons. A second run on another track stored 6 more and rejected 2, driven from the page button. |
| A nomination cannot inflate its own rank | `vetting_for` refuses `primary` to any non-institutional host, so `evil.gov.attacker.com` claiming primary resolves to `secondary`. |
| A date is read off the page, never remembered | OAIC docs carry `2022-07-25` and `2026-05-13`. The NIST pages carry none, and the ledger says "no date on the page". A copyright year is not read as a publication date. |
| Teaching views render the real curriculum | Stage 1 · The core, Topic g08 · Core, real title, NIST as citation. |
| A turn is grounded against a curriculum-written step | step `8:topic:S08`, `grounded=True`, `cites=['D03§s02','D07§s06','D07§s10']`, `packSha16=72d60102f70b6172`, stored in `turn.client_meta`. |
| The tutor refuses rather than inventing | "the excerpts I have do not contain a risk-tiering scheme or the RMF's core functions, so I can't teach you a severity-based ladder from this corpus". |
| The tutor will not supply a version from memory | asked which edition of the AI RMF: quoted "Released on January 26, 2023" from the excerpt, then "It does not give a version or edition number, so I won't supply one", and warned against saying "current" or "latest" in the room. |
| The probes are asked, and answering one drops it | 20 probes, 2 answered, "Graded by Claude claude-haiku-4-5 · 19 to learn". The solid answer produced no gap row at all, and the shaky one carries a real reason. |
| Quarantine removes a source from future packs | `D03§s02` present in `build_pack('8:topic:S08')` before, absent after, present again when restored. The row and its provenance never moved. |
| Switching tracks does not contaminate either one | turns and marks counted in both tracks either side of a real switch from the page: unchanged. |

**Assumed, not verified.** The Codex provider path has never run (no `codex`
binary here), so `discover` has only run against `claude`. The iPhone
(`prep iphone`) path has never run. `prep` is still not on `PATH`, so run
`./prep-launcher.sh` from the repo.

## 5. What was built this session

- **`prepwright/ingest.py` is new**, 900 lines, the owner-supplied resource path.
  Format readers for PDF (poppler when present and ownership-checked, a modest
  stdlib inflate-and-scan reader otherwise), Word (real heading styles, which is
  a better outline than any heuristic recovers from flat text), Excel, CSV/TSV,
  HTML and text. Then `gate()`, then `outline()`/`strip_running()`, then
  `select()`, then `group()`. **It imports no provider and opens no socket, and
  a test asserts that by reading its own source.**
- **`prepwright/security.py` stopped being a stub.** `trusted_executable` moved
  out of `bridge.py` into it, because the PDF extractor needs the same ownership
  check the provider CLIs need and two copies of that check is one too many.
  This is a real slice of Task C: it has no dependency on `PORT`, which is what
  blocks the rest.
- **`bridge.py`** gained `_route_file` (`preview_file` and `ingest_file` as
  ACTIONS on `/api/research`, per trap 20) and `_trim_report`, which is
  `_discovery_report`'s trimming loop lifted out so a supplied file and a
  discovery run cannot diverge on the bug trap 24 records.
- **`index.html`** gained `fileBox()`, `fileReportHtml()`, `fileAction()` in the
  Sources view, and made the probe conversation reachable from the gap-approval
  stage.
- **`tests/test_ingest.py`** is new: 32 tests. **`tests/test_pipeline_routes.py`**
  gained 7 more over real HTTP.

### The token argument, since it is the reason to build this at all

Ingestion is where a naive design burns tokens: hand a 300-page PDF to a model
and ask it to summarise. This does none of that.

| Stage | Cost |
|---|---|
| Read the file | 0 tokens. `pdftotext`, `zipfile`, `csv`, `zlib`. |
| Find its structure | 0 tokens. Heading heuristics, or real Word styles. |
| Decide what answers the goal | 0 tokens. `curriculum.score_sections`, the same scorer the pack uses. |
| Teach from it | `PACK_MAX_BYTES` = 12,000 bytes, about 3k tokens, **per turn, whatever the resource's size**. |

Markdown is not cheaper than plain text for the same words. It is very slightly
more, because `#` and `*` are characters too. The 15.9x saving measured above is
extraction, not format: it is the PDF container, its fonts and its binary
streams not being sent. What markdown buys is structure, and structure is what
lets the cut happen locally instead of in a model.

## 5b. What the previous session built

- **`research.py`** gained the whole discovery half. The module docstring used to
  say "The model never finds the sources". The owner changed that promise, so the
  docstring was rewritten rather than left contradicting the code under it.
  `NOMINATE_SYSTEM` steers to HTML with headings and away from PDFs.
  `vetting_for` / `trust_for` cap what a nomination may claim. `published_on_from`
  reads only clearly labelled dates. `verify` fetches and gates. `discover` bounds
  every loop and defaults to one source per gap.
- **`bridge.py`** gained `CLI_SEARCH` (the only call allowed to search, and it is
  not a teaching call), `MODEL_DISABLED`, `_step_list`, `_discovery_report`,
  `_judge`, `_route_discover`, `_route_ledger`, `_route_quarantine`, and a
  currency rule in `TUTOR_SYSTEM_BASE`. Discovery and quarantine are ACTIONS on
  `/api/research` rather than new POST paths, because `/api/chat` is the residual
  branch at the end of `do_POST`. The ledger is a GET, which ends in a 404.
- **`state.py`** gained `start_research_run` / `finish_research_run` /
  `research_runs`, `set_doc_status`, `publisher` and `published_on` on
  `write_doc`, and `_pack_block`, which puts the publisher and the date INTO the
  block the tutor reads.
- **`curriculum.py`** gained `slices_by_step` and `document_frequency`,
  `plan()` stopped using the diagnostic's `why` as the learning objective, and
  `_document_frequency` now counts the same fields `score_sections` scores.
- **`index.html`** gained `adoptCurriculum`, `STEP_INDEX`, the probe
  conversation, the source ledger, the track switcher and the discover button.
- **`config.py`**: `TRACK_ID_RE` and `DOC_NAME_RE` re-anchored `$` → `\Z`.
- **`tests/test_discovery.py`** is new: 20 tests, no network, covering the rank
  ceiling, the date rule, the coverage floor and every loop bound under a hostile
  provider.

## 6. The work, in dependency order

### Task P. PDF sources are refused. **DONE this session. Kept for the reasoning.**

The strongest finding of the session, with evidence. Four of the ten discards on
the second discovery run were NIST PDFs: `NIST.AI.100-1` (the AI RMF itself),
`NIST.AI.600-1` (the generative AI profile) and `NIST.AI.100-2e2025` (the
adversarial ML taxonomy). Those are *the* primary sources for this role and the
pipeline cannot read one. `ALLOWED_CONTENT` in `research.py` excludes
`application/pdf`, and `corpus.parse_loose` splits on `## ` headings an extracted
PDF would not have.

**Built.** The caution was right and is now a mechanism rather than a reason to
refuse: `ingest.gate()` measures seven properties of an extraction and refuses
anything that does not read as prose, quoting the measurement that failed.
`pdftotext` is preferred when present and ownership-checked; the stdlib reader is
the floor, and when the floor produces something unconvincing the refusal names
the fix rather than storing it. A refusal is honest. Garbage is not, and now
neither can happen silently.

### Task E. The probes are now reachable, and are waiting for the owner. **NEXT.**

**Track `t-454d410f0522`, "Agentic AI Consultant at Proseware"**, is at the gap
stage with **17 gaps proposed and none decided**. Every one carries "No answer
given", because the diagnostic was driven through `/api/diagnose {action:
propose}` directly rather than through the conversation.

The blocker the last handoff described is gone. It said the remedy was to start
a second track from the same posting, and the reason was a real defect: the
probe conversation rendered only in the diagnostic stage, so a track that
reached gap approval with the questions unanswered had no way back to them.
`approveCard()` now offers the conversation, and `probePlan` short-circuits to
`probeCard()` from there. Verified in a browser: the button loads 17 questions,
P01 asking "Build the agent system the posting names
- explain how you would do this, and name the closest thing you have actually
done."

**Only the owner can answer them.** Inventing answers corrupts the plan at its
root, which is why this is a handoff and not a task.

### Task E1. The old Wingtip track is still 8 hours.



The real Wingtip track was diagnosed before the judge existed, so all 20 of its gaps
carry `why = "graded without a model: length only"`, every gap graded `none`, and
`tier_for` produced 9 core and 11 depth with no real cut. 500 minutes.

The fix is not code. Run intake on a fresh track from the same posting, answer
the probes, and the judge drops what the owner can already explain. The mechanism
is proven (§4) but only the owner can supply the answers, and inventing them
would corrupt the plan at its root. A smaller code task sits behind it:
`plan(max_steps=None)` still defaults to `MAX_STEPS` (40), so the cut never fires
on 20 gaps. The honest input for a time-budget cut is a graded gap list, which
now exists.

### Task C. Extract `bridge.py`. PARTLY DONE.

2,657 lines, and it grew again this session. Seams unchanged:

| Module | What moves |
|---|---|
| `security.py` | `LOCAL_ORIGINS`, `TS_*`, `_is_remote_request`, `_origins_for`, `_allowed_host`, `_remote_identity_ok`, `_safe_messages`, `_safe_assess_items` |
| `provider.py` | `_trusted_executable`, `claude_bin`, `codex_bin`, `_cli_env`, `_provider_ready`, `run_cli`, `CLI_BASE`, `CLI_SEARCH`, `MODEL_DISABLED`, `_parse_codex_jsonl`, `trim_history`, `_usage`, `_resolved_model` |
| `teach.py` | `NO_ERRANDS`, `TUTOR_SYSTEM_BASE`, `_step_instructions`, `chat_via_cli` |
| `assess.py` | `assess_via_cli`, `review_via_cli` and their schemas |
| `serve.py` | `Handler`, `main` |

Blocker unchanged: `security.py`'s constants are computed at import from `PORT`,
so move `PORT` into `config.py` first. **Run `tools/orphan_scan.py` after every
deletion**. It caught exactly this class of error this session when
`_route_discover` used an unimported `uuid`.

### Task D. Small, known, cheap. PARTLY DONE.

- ~~`TRACK_ID_RE` / `DOC_NAME_RE` anchored with `$`~~ is fixed, with a test that
  fails when the `\Z` is reverted.
- ~~A track switcher exists in the API and has no UI~~ is built, and its first
  version corrupted the incoming track. See §10.
- `TRACK_DB_CAP` is defined in `config.py` and enforced by nothing.
- **Four tracks now exist**, two of them scratch tracks this session created to
  test the probe flow (`t-d1145d35a47a`, `t-2f89726a8ce3`). They were left alone
  rather than deleted, because deleting is the owner's call. The switcher makes
  them harmless. Archiving them is a one-line decision.

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

**Lenses not yet run on the current tree:** concurrency (two tabs, two requests,
the `MODEL_GATE` and the lease), and error-path coverage (what the page does with
every non-200 the new routes can return).
## 10. What the hunt found

Round 1 ran this session over the new code: four lenses, 20 raw findings, **14
confirmed and 6 rejected** by the consolidator. 4.1M tokens, 24 agents, zero
failures. Eight of the fourteen were introduced the same day they were found,
which is the argument for running the hunt before the commit rather than after.

**Fixed this session, with a test that fails when the fix is reverted:**

| Was | Now |
|---|---|
| `_document_frequency` counted heading + concept + body while `score_sections` weighted `doc_title` at 2.0, so a term living only in document titles never entered df, hit the `df.get(term, 1)` sentinel meant for "in exactly one section", and drew the corpus-maximum rarity. On one multi-section standard every section cleared `RELATIVE_FLOOR` together, filled `PACK_MAX_SECTIONS`, and **evicted the sections that actually matched**. | df counts the same four fields that are scored. `tests/test_curriculum.py::RarityIsCountedOverTheFieldsThatAreScored`. |
| The track switcher called `reconcileWithDisk()` after switching, which merged the OUTGOING track's in-memory document into the incoming one and pushed track A's turns and marks into track B's append-only store. The bridge's cross-track guard could not fire because the page had already adopted B's id. | Flush to A, switch, then `location.reload()`. Verified by counting turns and marks in both tracks either side of a real switch: unchanged. |
| `_provider`'s RuntimeError escaped `_pipeline` with no reply written, and the browser held an open socket. | Caught. **The clause is LAST on purpose**: `StoreError` subclasses `RuntimeError`, and placing it earlier turned every 409/507 into a 400. A test caught that on the first attempt. |
| The run report was `json.dumps(...)[:MARK_MAX_BYTES]`, cutting mid-string, so `discardsOf` threw and a run with many rejections displayed as a run with none. | `_discovery_report` drops whole entries until it fits and records how many it dropped. |
| `_clean_candidates` marked every nomination `seen` before the per-gap cap discarded most of them unfetched, blacklisting good sources for the rest of the run. | Marked seen only after the cut. `tests/test_discovery.py::AnUntriedUrlIsNotABlacklistedUrl`. |
| The discover button sent no provider, so a Codex-only machine got an unhandled Claude refusal. | Sends `activeProvider()` / `activeModel()`. |
| The Sources ledger cached and was never invalidated after a pasted fetch or a plan build. | `ledger=null` on both. |
| Switching to a track with no curriculum left the previous track's steps on screen and would have sent its step keys to `/api/chat`. | Fixed by the same reload. |

**Confirmed, NOT fixed. These are the next session's list, in severity order.**

1. **`set_doc_status(doc_id, 'ready')` un-quarantines the row but not the file.**
   A document quarantined by `rescan_doc` after its bytes changed counts as
   ready again and serves zero sections forever. `state.py`. The restore path
   has to re-verify the file, or refuse.
2. **`relevant()` enforces `PACK_MAX_SECTIONS` but nothing enforces
   `PACK_MAX_BYTES` at pin time**, and the arithmetic that makes ten sections fit
   is ASCII-only. Any non-ASCII source (curly quotes are enough) silently loses
   the last pinned sections at `build_pack`. `curriculum.py:212`.
3. **`corpus.build_pack` redacts after `pack_sha16` was computed** over the
   unredacted text, and leaves `sections[].body` unredacted. The hash a citation
   is checked against is not the hash of the bytes that were sent.
   `corpus.py:291`.
4. **`max_steps` has a floor but no ceiling** while the store enforces
   `MAX_STEPS=40` mid-write with no transaction across the batch. `build(...,
   max_steps=50)` on 50 covered gaps commits 40 steps and their slices, then
   raises, and `built['written']` never returns. `curriculum.py:371`.
5. **`/api/health` bypasses the session gate**, so the live pill stays green
   while every gated route returns 403. Restarting the bridge under an open page
   produces a green pill and a dead app. `bridge.py:1676`.
6. **`track.phase` is written once at creation and never read or updated.** Every
   track in the library says `intake` forever, including ones that have taught
   turns. Nothing depends on it, because `flow_state` computes the real stage
   from the data, which is why this has never broken anything. It is worse than
   an unused column: it is an authoritative-looking one that is always wrong, and
   the CHECK constraint on it makes it look maintained. Either drive it from
   `flow_state` or drop it.
7. **Declining every gap is a terminal state** with no way out from the page.
   The stage advances to research, discovery refuses ("nothing is approved"), and
   pasting sources does not help because the curriculum has nothing to plan.
   `bridge.py:300`.

**Rejected, and worth knowing they were looked at.** A malformed nomination
reply being reported as "nothing new was nominated" (that is the published
design). `step_key` sharing stage 99 past ordinal 99 (unreachable: `MAX_STEPS`
is 40). `order_gaps` counting rows rather than distinct ids. `finish_research_run`
not checking rowcount. `build_pack`'s isolation assertion being a tautology (it
is, but the property is enforced by the composite foreign key). `fit_sections`
redacting bodies but not headings.

## 11. Working discipline

Evidence first. Every change carries a hypothesis, the smallest safe patch, a
test against real data, a keep-or-revert decision, and one line recording it.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the
command whose output backs the word.

**Walk it in a browser before you believe it.** This session's two worst defects
were invisible in the source and obvious in thirty seconds of clicking: a
`TypeError` in `stepMeta` from an id that did not exist, and a judge that never
ran because the bridge process was stale. A third, the cross-track write, was
invisible in the browser too and needed the hunt.

## 11b. What this session found and fixed, in its own code

Five defects, all written the same day, all caught before the commit. Four by a
test, one only by clicking.

| Was | Now |
|---|---|
| `strip_running` counted every repeated short line and deleted the body of its own test fixture along with the page header. | Only lines that would otherwise be READ AS HEADINGS are removed, which is the actual harm. A checklist repeating "not applicable" keeps it. |
| `_APPENDIX` matched "Part 2 comprises the Core of the Framework. It describes four specific funct" and produced a citation heading cut off mid-word. | A line over 45 characters carrying a sentence boundary is prose, whatever it starts with. |
| `ingest_file`'s report omitted `ok`, so a successful ingest rendered as "Not stored. That file could not be read." while three documents sat in the corpus. **Invisible in the source and obvious in ten seconds of clicking.** | `ok` is returned, and a test compares the key sets of both reports. |
| `_route_file` called `_discovery_report` with one argument instead of three. | `_trim_report` extracted; `_discovery_report` and `_file_report` both call it, so trap 24's fix has one home. |
| Selection ranked long sections over precise ones, and one dominant match dropped everything else under the relative floor. | `_by_density` divides by the square root of the body's term count. Coverage of the goal is now reported and warned about separately from the cut. |

## 12. Definition of done for the next session

- **The adversarial hunt has not been run on this session's code.** 900 new lines
  in `ingest.py`, a new route, and a new panel, and the hunt is the gate that
  caught 14 defects last round with 8 of them same-day. It was not run because
  the owner did not ask for multi-agent orchestration this session and the tool
  requires that in the owner's own words. **Ask, then run it**: four lenses over
  `ingest.py` + `_route_file`, plus the two lenses still unrun from round 1
  (concurrency, error-path coverage).
- **Capacity for a full-length plan.** Following the owner's correction in §3,
  the assumption is now that every proposed gap may be approved. Prove the whole
  chain carries 17 to 25 approved gaps without silently losing one: `MAX_STEPS`
  is 40, `MAX_DOCS_PER_TRACK` is 48 and one supplied resource may claim 24 of
  them, `CORPUS_BYTES_CAP` is 640 KiB, and `curriculum.build` writes steps and
  slices with no transaction across the batch. Write the test that approves 25
  gaps, ingests a large resource, builds, and asserts every approved gap has a
  step and every step has evidence. A gap that no source covers must be NAMED
  and carried, never dropped.
- Task E: the owner answers the 17 probes on `t-454d410f0522`. The point is
  grading, not shortening: a graded gap list is what lets each gap be taught
  from the right depth. A plan that stays long is a correct outcome.
- Uploading rather than typing a path. The file never crosses the HTTP boundary
  today, which is the safer design and the reason no body cap had to move, but
  it costs the owner a Finder shortcut. If it is built, it belongs behind the
  same `resolve()` and the same `gate()`.
- `origin_sha256` on a supplied file is the hash of the markdown the extractor
  built, not of the PDF. The file's own hash is in the report and the ledger, so
  re-verification is possible but needs the same extractor. Decide whether that
  is good enough or whether `write_doc` should carry both.
- The `max_steps` / time-budget cut fires on a graded gap list.
- The six unfixed hunt findings in §10 closed, or each one refused in writing.
- Task C: `PORT` moved to `config.py`, then `security.py` and `provider.py` split
  out, with `orphan_scan` after every deletion.
- The two remaining hunt lenses in §9 run on the current tree.
- This file rewritten for the session after that one.
