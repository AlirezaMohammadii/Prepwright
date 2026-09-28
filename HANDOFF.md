# Prepwright — what it is, how to run it, and what is not proved

Written 2026-09-10 at the end of session 11, updated 2026-09-24. Every claim below names the
command that backs it. Where a number appears, it was measured.

## 1. Run it

```
prep                 # opens http://localhost:8010/ in your browser
prep --no-browser    # same, without opening one
```

`prep` is on PATH and works from any directory (`cd ~/Music && prep --help`).
Resume Studio's Prep button opens `http://localhost:8010/?application=<folder>` for
the finished application on its screen; confirm once and the track opens.

Stop it with Ctrl+C. Nothing here needs pip, and no API key exists anywhere:
the tutor answers through the `claude` CLI you are already logged in to.

The launcher verifies `MANIFEST.sha256` and refuses to start if any of the 21
pinned files differs. After editing `bridge.py`, `index.html` or
`prepwright/*.py`, run `./tools/make_manifest.sh` or it will not start.

## 2. What it is

A local, single-user interview-preparation tutor. One job posting per track. It
teaches only from sources you supplied or approved, cites them, and refuses
rather than inventing. Storage is an append-only SQLite database per track under
`~/.prepwright`. The page never sends a document, only a delta of ops.

The whole flow is built and runs: intake (a link, pasted text, or a finished
application from Resume Studio's Prep button), the diagnostic, gap approval,
research, curriculum, teaching, grading, the end-of-session review, the recap
bank, rehearsal of the panel's questions graded against your own record, the
day-before brief, and a terminal `prepared` stage that needs passing rehearsal
answers (ADR 0008).

## 3. State, and how much of it is verified

**Verified by running it on 2026-09-10.**

| Claim | Command |
|---|---|
| 530 tests pass on three interpreters, no network, model or dialog call, 13 to 18 s (2026-09-28) | `python3 -m unittest discover -s tests`, then the same with `/usr/bin/python3`, then `/usr/bin/python3 -I -S`. Run each **literally**: see trap 1 |
| The manifest verifies over 21 pinned files | `/usr/bin/shasum -a 256 --strict -c MANIFEST.sha256` |
| No module reads a name it never binds | `/usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py tests/*.py tools/*.py` → 38 files |
| All 12 views render, 0 console errors | click each nav button, then `playwright-cli console` |
| `bridge.py` is a 137-line composition root, and the four stubs are gone | `wc -l bridge.py prepwright/*.py` |
| The design doc's file inventory equals the manifest | `diff` of the two lists, sorted, is empty |
| A real teaching turn is grounded | on `8:topic:S08`: `grounded` true, cites `D03§s02 D07§s06 D07§s10`, cited two, `invented` empty, `packSha16` `7e54093720db5dad` |
| Grading writes a row naming the model | `select * from assessment` → seq 2, 0.3, `claude/claude-haiku-4-5` |
| A **second** session close on the same step works | session marks seq 228 and seq 71 both on `8:topic:S08`, and `step.review == TrackHandle._review_text(newest)`, 600 of 600 chars. Never verified before this session |
| The grade panel survives a reload | reload, then `state.assessList` → the 0.3 grade with its reason, hydrated by `GET /api/assessments` |
| Page and store agree after a reload | page 11 turns == store 11, `curriculum.done` 0 == 0 done steps, 20 == 20 total |
| The eviction ladder can reach a completed step | `tests/test_step_lifecycle.py::TheEvictionLadderCanReachACompletedStep`, 5 tests |
| Only the tutor role takes the shared model preference | in one walk the tutor ran on `claude-sonnet-5` and the grader on `claude-haiku-4-5` |
| Rehearsal works on a real application (2026-09-24, ADR 0008) | fresh throwaway home, the UniExample folder by deep link: the plan came out as 1 study step and 8 rehearsal steps R01–R08, the resume and the fit report were stored as "Your application" D03/D04 (vetting primary). R01, the red-team objection, graded 6/8 on claude-sonnet-5 ($0.051), with every sentence of the strong answer cited to D03/D04, and the step went to `done` from the grade alone. On R06 the grader caught an answer that said "no number for FakeAVCeleb" against his resume's 96.8% AUC, which is the false line the blind tutor had coached ($0.031). The brief rendered with 0 console errors |
| Resume Studio's Prep button opens a finished application as its own role (2026-09-24) | `?application=<the real 2026-09-24__Research_Fellow_University_Of_Example folder>` on a throwaway :8011 instance: the card read "Prepare for Research Fellow at University Of Example", one click made one `imported` track with that employer and role, the page landed on the diagnostic, 0 console errors. Tests: `AFinishedApplicationOpensAsItsOwnRole`, `AFinishedApplicationOpensOverHttp` |

**Found on the 2026-09-24 live walk and fixed on 2026-09-28.**

- Discovery kept an arXiv `/abs/` page as seven sections of page chrome
  ("Submission history", "Access Paper:", "BibTeX", "Bookmark") and dropped the
  abstract. It sits under the page's h1, above the first h2, and
  `corpus.parse_loose` drops prose before the first `## `. The coverage floor was
  measured on the whole page, so it passed on text the store then threw away.
  Now `research.keep_lead` keeps a fetched page's opening prose as a section
  called "Opening", `html_to_text` no longer reads the `<title>` as body text,
  and `verify` measures coverage on the sections the store will keep.
  `parse_loose` is unchanged, because it also parses the résumé, whose opening
  lines are the contact block. Tests: `AnAbstractAboveTheFirstHeadingIsKeptNotDropped`,
  `TheCoverageFloorMeasuresWhatTheStoreKeeps`; each of the four edits was
  reverted once, and a test failed each time.

**Found on the 2026-09-24 live walk and not fixed.** Each is a measured fact
from the throwaway walk on the UniExample application.

- `curriculum.tier_for` ranks by gap level and by whether the posting states the
  gap. It has no term for how strongly the posting stresses a gap or how likely a
  panel is to probe it. Three of four approved gaps had no source and were set
  aside. (Fixed the same day: a fit-report requirement had no `jd_span`, so it was
  labelled "your application claims" and tiered as one. It now carries its
  `fit:<n>` row; `AFitReportRequirementIsThePostingsNotAClaim`.)
- The page sends its own tutor model on every turn (default `claude-opus-5`),
  which overrides the shared preference file. `_role_choice` reads the shared
  model only when the request names none.
- A turn on a step the plan lacks makes a step row through `ensure_step`, and
  that row counts in the "finished" denominator. `bcc3b57` removed the cause
  (the page now adopts the plan it builds) and `/api/chat` refuses such a topic.
  A track that already holds one keeps it. Excluding such rows from the plan
  would re-open the settled denominator rule in §5, so it waits for the owner.

**Not verified. Say so rather than assuming.**

- **The native file chooser has never been opened.** Three sessions could not
  attempt it, and a fourth attempt on 2026-09-10 was staged and not taken. Both
  halves were tested separately instead: clicking the button posts
  `{"action":"pick_file"}` to `/api/research` (proved with `fetch` stubbed, so
  no dialog could open), and the route returns `cancelled: true` on a non-zero
  `osascript` exit, which the page handles silently. The dialog between them is
  what nobody has watched. See §6.
- The Codex provider path has never run. Prepwright looks on PATH and in
  `/opt/homebrew/bin` and finds no `codex`. One ships inside the VS Code ChatGPT
  extension (Resume Studio uses it); Prepwright does not look there.
- `prep iphone` has never run.
- The `prepared` stage has not been reached on a real track. It needs every
  study step ticked and every rehearsal step answered at 6 of 8 or better (ADR
  0008), which is you preparing, not an engineer. The route to it is tested
  (`ARehearsalStepIsDeliveredByAGradeNotATick`).

## 4. Do not

- **No pip, no dependency, ever.** Standard library only, Python 3.9 compatible.
- **No API key anywhere.**
- **Do not weaken grounding to make a walk pass.** `build_pack` → `cites` →
  `check_citations` → `citations.invented` → refusal.
- **Do not rename:** `tutor, student, candidate, step, session, assessment,
  recap, transcript, track, stage, corpus, citation`, `X-Tutor-Bridge`,
  `TUTOR_TS_*`.
- **The 20/80 cut applies to the material inside a source, not to which gaps get
  studied.** `tests/test_capacity.py` guards it.
- **Ask before deleting any track.** Archive is not delete.
- **This repository has no remote.** Never invent a push target.
- **Do not treat grep as proof of absence.**
- **Kill and restart the bridge after any server-side edit.**

## 5. Settled. Do not re-open.

- SM-2 stays, dormant and annotated. The `card` and `card_review` tables are a
  complete scheduler nothing can write.
- "Finished" means the written plan: the `step` table is the denominator.
- `MIN_TERMS` scales to the goal: `min(MIN_TERMS, len(terms(goal)))`.
- Persistence through `TrackHandle`, one database per track, append-only.
- No agentic orchestration inside the app. It is a page and a bridge.
- The model may nominate sources (`research.discover`, search on); the fetch
  decides. Every nominated URL is fetched through the same guard as a pasted link
  and stored only if it answers and covers its gap. Nothing becomes corpus unread.
- The 17 probes on `t-454d410f0522` stay parked.

## 6. The one thing left for you

**Click the file chooser once.** A throwaway instance may still be running on
port 8011 against a temp home. If not, start one:

```
PREPWRIGHT_HOME=$(mktemp -d) PREPWRIGHT_PORT=8011 prep --no-browser
```

Open it, go to Sources, click "Choose a file…", pick any small `.md`, then click
it again and press Cancel. Both outcomes matter. Never set
`PREPWRIGHT_NO_DIALOG` for that run. Afterwards, the evidence is:

```
sqlite3 <that home>/tracks/*/track.db "select count(*) from research_run;"
```

A hung dialog parks the bridge for 240 seconds, which is the whole reason no
test may open one.

## 7. Layout

```
bridge.py            the composition root. 137 lines, one function
prepwright/          one module per concern, all written
  config.py          identity, every path and every cap
  state.py           the store: schema, triggers, caps, TrackHandle
  track.py           lifecycle: create, archive, restore, trash, purge
  keep.py            housekeeping: recounts, the eviction ladder, backups
  pagestate.py       the page document and the delta protocol
  security.py        origin, host, cookie, remote identity, sanitisers
  corpus.py          build_pack and check_citations: grounding
  intake diagnose research curriculum ingest
  provider.py        the CLIs, the model registry, five roles, settings
  teach.py           one teaching turn, and the stage ladder
  assess.py          grading, the end-of-session review, recap cards
  rehearse.py        rehearsal steps, the rubric grade, the day-before brief
  serve.py           every route, and which track this process serves
index.html           the page: 12 views, no framework, no build step
docs/adr/            the decisions that are settled, and why
```

Imports run one way. Nothing in `prepwright/` imports `bridge`, and
`tests/test_extraction_seams.py` fails the suite if that changes.

## 8. Traps that have each cost real time

1. **zsh does not word-split.** A loop over "three interpreters" silently runs
   nothing and prints a clean pass. Run each literally.
2. **The launcher refuses to start on a manifest mismatch.** Read what
   `shasum -c` names as changed **before** running `./tools/make_manifest.sh`.
3. **A stale `.pyc` can survive a byte-identical restore.** Measured on
   2026-09-10: after reverting a file and restoring it with `cp`, the next run
   executed the reverted code, and it looked exactly like a Python-version
   difference in a regex. `find . -name __pycache__ -type d -exec rm -rf {} +`
   belongs in the revert loop.
4. **A revert that PASSES means the fix is not understood.** Two this session:
   one because a downstream layer caught it, one because the pyc was stale.
5. **Patch a name at the module whose code READS it.** Python looks a global up
   in the module where the caller is defined. A `setattr` restore names the
   module in a *string*, which a search-and-replace does not match, so an
   assignment and its cleanup can end up naming different modules.
6. **`playwright-cli check` cannot click a 0×0 input.** Use
   `label.checkline:has(input[data-topic="gNN"]) .checkbox`.
7. **A stale playwright ref survives a reload and clicks nothing.** Address
   elements by role or selector after a `goto`.
8. **`2>/dev/null` on a playwright call hides a failed click.**
9. **A new POST route needs two edits:** the whitelist in `do_POST` *and* an
   explicit block before the `/api/chat` fall-through. A GET route needs one.
10. **`_read_json_body` requires a JSON object.** A list body is a 400.
11. **`StoreError` IS a `RuntimeError`. `sqlite3.IntegrityError` is NOT a
    `StoreError`.**
12. **The launcher's output does not reliably land in a file it is redirected
    to.** Read the bridge's own terminal.
13. **`unittest discover -s tests -p 'test_x.py'` is the only single-file form
    that works.** `tests/` has no `__init__.py`.
14. **A test that re-implements a predicate proves nothing.** One this session
    mis-rendered a review with the wrong cap and disagreed with a correct store.
15. **`JSON.stringify(x).length` is UTF-16 code units, not bytes.**
16. **`set_intake` kinds are `pasted|imported|freeform`.** **`TrackHandle`, not
    `Handle`.**

## 9. Working discipline

Evidence first. Hypothesis, smallest safe patch, test against real data, keep or
revert, one line recording it.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the
command whose output backs the word.

Prove every fix by reverting it and confirming its own test fails. Revert the
call site, not only the helper, clear `__pycache__`, and check that the revert
is a revert. Restore byte-identical and confirm with `diff -q`.

Walk it in a browser before you believe it. Read `sed -n` regions, never whole
files: `index.html` is 4,500 lines and `prepwright/serve.py` is 1,767.

Committed locally on `master`. **This repository has no remote.**
