# Prepwright. Session 11 brief. Final session. Execute this.

Written 2026-09-09 at the end of session 10. It replaces the session-5 brief that sat
in this file until now; that one described a repo that no longer exists (88 tests,
1,791-line bridge, four unbuilt front-half modules). Everything below was measured on
the tree at `f4ef87d` the day this was written, and every number names its command.

**This is the last session.** The goal is not more features. The goal is: every known
inconsistency found and closed, the four planned modules extracted, the store and page
agreeing with each other, the documents telling the truth, and the owner able to run
`prep` and study the Wingtip track end to end without an engineer in the room.

## 0. Activate. In this order, nothing before it.

```
cd ~/Desktop/Prepwright        # NEVER from inside ~/Desktop/Deep Fake Detection Project/
/engage                        # Ruthless Candor + prompt-to-start. Apply, do not restate.
```

Starting anywhere under the VeriCall tree loads 320 KB of unrelated `.claude/rules/`
into every turn and re-contaminates a product that must carry none of it. Prepwright
has no `.claude/` directory of its own, deliberately.

Read, in this order, once:

1. `HANDOFF.md` (255 lines). The state document. Written by the session that shipped
   the last change, every claim naming its command. This brief does not repeat it.
2. `docs/adr/0006-one-model-per-job-and-the-writer-that-was-missing.md`, then the
   head of `0005-*` (it says which of its statements 0006 superseded).
3. This file to the end.

Do not read `DESIGN-state-corpus.md` end to end (839 lines, lettered sections). Read
only the section a task names. Do not read `README.md` as truth; see §2.

Then run these and paste the output before touching anything:

```
/usr/sbin/lsof -ti tcp:8010 | xargs kill 2>/dev/null
python3 -m unittest discover -s tests 2>&1 | tail -3
/usr/bin/python3 -m unittest discover -s tests 2>&1 | tail -3
/usr/bin/python3 -I -S -m unittest discover -s tests 2>&1 | tail -3
/usr/bin/shasum -a 256 --strict -c MANIFEST.sha256 | grep -c ': OK'
/usr/bin/python3 -I tools/orphan_scan.py bridge.py prepwright/*.py tests/*.py tools/*.py
git status --porcelain; git branch --show-current; git log --oneline -3
```

Expected at the time of writing: `Ran 477 tests ... OK` three times, `20`, `no orphaned
names in 35 file(s)`, a clean tree on branch `round0-hunt-and-per-track-evidence` at
`f4ef87d`. Any difference is the first finding of the session. Run each interpreter
**literally** as written: zsh does not word-split a quoted loop variable and a loop over
"three interpreters" silently runs nothing and prints a clean pass.

## 1. Ground truth, 2026-09-09

| Fact | Backed by |
|---|---|
| 477 tests, three interpreters, no network, model or dialog call, under 15 s | the three commands above |
| Manifest pins 20 files: `bridge.py`, `index.html`, `prepwright/*.py` | `tools/make_manifest.sh` globs them; count stays 20 when a stub is filled in |
| `bridge.py` 3,215 lines, 50 top-level defs; `index.html` 4,441 lines | `wc -l` |
| Front half built: `intake.py` 528, `diagnose.py` 601, `research.py` 731, `curriculum.py` 601, `ingest.py` 1,432 | `wc -l prepwright/*.py` |
| Four 7-line stubs remain: `provider.py`, `teach.py`, `assess.py`, `serve.py` | same |
| `prep` on PATH from any directory; `--no-browser` exists | `ls -la ~/bin/prep`; `cd / && prep --help` |
| Five model roles (tutor, assess, review, judge, discover), each with model and effort, settings at `~/.prepwright/settings.json`; the tutor role shared with Resume Studio via `~/.config/claude-apps/model-prefs.json` (`CLAUDE_APPS_PREFS` redirects it) | `tests/test_model_roles.py`, 42 tests |
| Step lifecycle written by reconciliation (`TrackHandle.sync_step_lifecycle`), grades reach the `assessment` table, `flow.curriculum.done` is real, the 3 MiB transcript cap is recoverable | `tests/test_step_lifecycle.py`, 22 tests; HANDOFF §5a |
| Terminal stage `prepared` exists; "finished" = the written plan | `bridge.flow_state`, `STAGES` has 7 entries |
| A malformed PDF cannot hang the ingest (60.6 s → 0.001 s) | `tests/test_ingest.py::AMalformedPdfCannotHangTheIngest` |
| Live store: 3 track directories, 12 library rows. Current track `t-93c97fdd6d77` (Wingtip, imported). `t-454d410f0522` (Proseware, active, 17 probes parked by owner). `t-6c3a05d5f79f` ("Prepwright track", active, empty, scratch). Nine rows archived or trashed with no directory | `ls ~/.prepwright/tracks`; `sqlite3 ~/.prepwright/library.db "select track_id,title,lifecycle from track"` |
| `settings.json` is `{"roles": {}}`; the shared prefs file is absent; both are the clean post-test state | `cat` them |
| resume-studio: branch `prepwright-jobdescription`, 23 commits ahead of origin, clean tree, 527 tests | `git log --oneline @{u}..HEAD \| wc -l` there |

**Never verified, and say so rather than assume:** the native file chooser (see §5E);
the Codex provider path (no `codex` binary here); `prep iphone`; a second close-session
on the same step; the `prepared` stage on a real track (needs 20 real ticks, which is the
owner studying, not you); `keep._compact_done_steps` (see §5F).

## 2. Documents that are wrong today. Fix them; do not trust them.

- **`README.md` is stale in three sections and lies in present tense.** "Status, in
  one paragraph" says the front half is not written. "Not built yet" (line 356) lists
  intake, diagnostic, gap approval, research, curriculum and "the bridge is not on the
  persistence layer" as missing. "Known limitations" says integrity pins are empty.
  "Files" says most of `prepwright/` is docstring stubs. All false. Rewrite in §5G.
- **`langgraph-design/dossier.json`** is a tracked design-evaluation artefact pinned to
  head `ae675e8` (2026-09-08). Nothing runtime reads it (`grep -rn dossier --include=*.py
  --include=*.html --include=*.sh .` to confirm). Remove it in the docs commit and say so.
- **`HANDOFF.md` §4 counts 11 views.** `grep -o 'id="view-[a-z]*"' index.html | sort -u`
  finds 12: `start overview stages curriculum continue practice questions qa sessions
  recap story sources`. Walk all 12.

## 3. Owner rulings. Do not re-ask any of these.

Ruled 2026-09-09 for this session, in plain words:

1. **Extract all four modules.** `provider.py`, `teach.py`, `assess.py`, `serve.py`,
   for real, this session. Not "delete the stubs", not "provider only". §5B.
2. **The owner will click the file chooser when you ask.** Start the bridge, open the
   panel, name the button, wait for their report. §5E.
3. **Push resume-studio after its suite passes.** Green: push
   `prepwright-jobdescription` to `origin`. Red: push nothing and report. §5H.
4. **Archive `t-6c3a05d5f79f`** through the normal lifecycle. Not trash, not delete. §5D.

Ruled earlier and recorded in ADR 0006; still binding:

- SM-2 tables stay, dormant and annotated. Not dropped.
- "Finished" means the written plan: the `step` table is the denominator.
- `MIN_TERMS` scales to the goal: `min(MIN_TERMS, len(terms(goal)))`.
- Persistence through `TrackHandle`, one SQLite database per track, append-only.
- No agentic orchestration inside the app. The app is a page and a bridge.
- Research fetches candidate-supplied URLs. The model never finds sources.
- No interview deadline. Build in dependency order.
- The 17 probes on `t-454d410f0522` stay parked.

## 4. Non-negotiables. Verbatim from the owner. Any one of these broken fails the session.

1. **Standard library only. No pip, no dependency, ever.** Python 3.9 compatible; the
   launcher runs `/usr/bin/python3 -I -S`.
2. **No API key anywhere.** Chat runs through the owner's logged-in `claude` CLI.
3. **Grounding by construction. Do not weaken it to make a walk pass.**
   `build_pack` → `cites` → `check_citations` → `citations.invented` → refusal.
4. **Do not rename:** `tutor, student, candidate, step, session, assessment, recap,
   transcript, track, stage, corpus, citation`, `X-Tutor-Bridge`, `TUTOR_TS_*`. Two-sided
   contracts; renaming one side locks the other out.
5. **Never let a test call a model or open a dialog.** `PREPWRIGHT_NO_MODEL=1` and
   `PREPWRIGHT_NO_DIALOG=1` are the harness guards. Real model calls are allowed only in
   your own hands during the end-to-end walk (§5I).
6. **Ask before deleting any track.** Archive is not delete. §3.4 is the only track
   action pre-approved.
7. **This repository has no remote. Never invent a push target.** Commit locally.
8. **Do not treat grep as proof of absence.**
9. **Kill and restart the bridge after any server-side edit.**
10. **Multi-agent orchestration is authorised, read-only, never for edits, and always
    before a commit rather than after.** §5J.
11. **The 20/80 cut applies to the material inside a source, not to which gaps get
    studied.** A cap that silently drops an approved gap is a defect;
    `tests/test_capacity.py` guards it.
12. **No VeriCall content, name, vocabulary or coordinate may enter this tree or its
    history.** Commit messages carry nothing from any other project.

## 5. The work, in dependency order. Acceptance stated per item.

Run A first. B is the bulk. Everything after B is independent of each other except K.

### A. Baseline browser walk (30 minutes, before any edit)

```
prep --no-browser
playwright-cli open http://127.0.0.1:8010
```

Walk all 12 views (§2), then `playwright-cli console`. Record the count of console
errors (expected 0). This is the reference the post-extraction walk is compared with.
Traps: the topic checkboxes are 0×0 inputs; click
`label.checkline:has(input[data-topic="gNN"]) .checkbox`. After any `goto`, address
elements by role or selector, never by an old `eNN` ref. Never suppress stderr on a
`playwright-cli select`; a silent no-op reads as success.

### B. Extract the four modules (owner ruling 1)

**What moves, from `bridge.py`, with today's line numbers:**

| Module | Takes | Lines today |
|---|---|---|
| `prepwright/provider.py` | `PROVIDER_MODELS` (116), `CLI_BASE` (596), `CLI_SEARCH` (631), `EFFORT_LEVELS` (651), `ROLES` (681), `ROLE_DEFAULTS` (698) and its asserts, `SHARED_PREFS_PATH` and aliases (735), `_read_shared_prefs`, `_read_settings` (866), `_write_settings` (896), `_role_choice` (954), `run_cli` (1318) | ~1,000 |
| `prepwright/assess.py` | `_persist_assessment` (1576) and the grading and review prompt assembly around it | small |
| `prepwright/teach.py` | `build_pack`, `cites`, `check_citations`, the teaching prompt assembly, `flow_state` (352) if it has no HTTP dependency | medium |
| `prepwright/serve.py` | `class Handler` (1853 to end): routing, `do_GET`/`do_POST`, the POST whitelist, the `/api/chat` fall-through, `_route_pick`, `X-Tutor-Bridge` | ~1,350 |

`bridge.py` becomes the composition root: imports, `main()`, argument handling, the
server start. The already-extracted `prepwright/security.py` (340 lines) is the model
to copy: it reads `PORT` from `config.py`, so the old "PORT is computed at import"
blocker is gone. Confirm with `grep -n "PORT" prepwright/config.py prepwright/security.py bridge.py`.

**Method. One module per commit, in this order: provider, assess, teach, serve.**

1. `grep -n` the block boundaries. Read them with `sed -n 'X,Yp'`. Move by exact-text
   replace on delimited blocks with the match count asserted before the write.
2. Import direction is one-way. `prepwright/*` never imports `bridge`. If a moved
   function reaches back for a bridge-level name, that name moves too or is passed in.
3. **The `-I -S` trap.** The launcher runs `/usr/bin/python3 -I -S`, which drops the
   script's own directory from `sys.path`. `bridge.py` appends it back. Keep appending,
   never inserting at 0, or a neighbouring `json.py` shadows the stdlib.
4. **The monkeypatch trap.** `tests/test_model_roles.py` patches `bridge._read_settings`
   48 times and `tests/test_back_half.py` 25 times. Python looks a global up in the
   module where the *caller* is defined. Once `_role_choice` lives in `provider.py`,
   patching `bridge._read_settings` no longer reaches it and those tests pass while
   testing nothing. Patch at the new home. Then prove it: revert one patched guard and
   confirm its test fails. Leave thin re-exports in `bridge.py` only where the page or
   the launcher reads them, and say which in a comment.
5. After each move: `tools/orphan_scan.py`, the three-interpreter suite, `shasum -c`
   to read what changed, `./tools/make_manifest.sh`, kill and restart the bridge, walk
   the views that route touched. Then commit with the manifest.
6. After all four: the hunt (§5J), then the full walk of 12 views with 0 console errors,
   then one real teaching turn (§5I) so the seams are exercised by a model reply, not
   only by tests.

**Acceptance:** `wc -l` shows no stub under 50 lines; `bridge.py` is under 700 lines;
477+ tests green on three interpreters; manifest 20 files clean; orphan scan clean;
`prep --no-browser` starts with the re-pinned manifest; 12 views, 0 console errors; a
grade still reports the model that produced it (`POST /api/assess` → `"model"`).

**Stop rule:** if a move breaks a security test and the fix is not obvious in 20
minutes, revert that move, commit the previous state, and report the exact failure.
A half-moved handler is worse than an unmoved one.

### C. `state.assessList` rebuilt from the `assessment` table

The measured objection: 18 rows with 200-character reasons reach 4,753 bytes against
the 4,096-byte delta cap and `validate_ops` rejects the whole delta. Grades are now in
the store, so the panel can hydrate from a `GET` (an `/api/assessments` route or a
field on `/api/flow`) instead of from page state. Keep `assessList` as a client cache
only, or drop it from `PS.FIELDS` and say so. New POST routes need two edits: the
whitelist in `do_POST` and an explicit block before the `/api/chat` fall-through.
**Acceptance:** reload shows every grade with its model and reason; a track with 30
grades saves without a cap error; one test on a temp home proves it.

### D. Library hygiene (owner ruling 4)

Archive `t-6c3a05d5f79f` through the normal lifecycle. Then compare the page's track
list against disk and the library: 12 rows, 3 directories, the nine dead rows must be
labelled as what they are, not shown as live. Change display, not data. Delete nothing.
**Acceptance:** the page lists Wingtip and Proseware as active; the archived set matches
`select track_id from track where lifecycle<>'active'`.

### E. The file chooser, walked once, with the owner (owner ruling 2)

Page button: `[data-file="pick"]` "Choose a file…" (`index.html:4196`), posting
`{"action":"pick_file"}` to `/api/research` (`index.html:4260`). Bridge:
`Handler._route_pick` (`bridge.py:2813`, moves in §5B), guarded by
`DIALOG_DISABLED = PREPWRIGHT_NO_DIALOG=="1"`. It runs `/usr/bin/osascript`, returns a
path, and that path goes through the same `resolve()` and `gate()` a typed path does.
A hung dialog parks the bridge for 240 s.

Procedure, on a throwaway home so nothing touches live tracks:

```
PREPWRIGHT_HOME=$(mktemp -d) PREPWRIGHT_PORT=8011 prep --no-browser
```

Create a scratch track there, open the sources panel, then **stop and tell the owner
in plain words:** which window, which button, which file to pick (any small `.md`),
and that Cancel is also a valid outcome to test. Wait for their report. Then verify:
the path landed in the box, the import created a document, `citations` can name it.
Test Cancel too: osascript exits non-zero on cancel and the bridge must treat that as
"nothing chosen", not an error. Record both outcomes in `HANDOFF.md` with the exact
commands. Never set `PREPWRIGHT_NO_DIALOG` for this run.

### F. Exercise `keep._compact_done_steps`

`prepwright/keep.py:647` selects `status='done' AND review IS NOT NULL AND review<>''`.
That predicate was dead before the lifecycle writer existed and has not been run since.
On a temp home: build a track, tick a step, write a review, run the keep pass, prove a
row was compacted, prove nothing on a step without a review was. Add the test to
`tests/test_step_lifecycle.py`. **Acceptance:** the test fails when the predicate is
reverted to the old dead form.

### G. Make the documents true

Rewrite `README.md` "Status", "Not built yet", "Known limitations" and "Files" so every
present-tense sentence is one you could back with a command. Remove
`langgraph-design/dossier.json` (§2). Add ADR 0007 recording the extraction: what moved
where, the import direction rule, the monkeypatch rule, and the re-exports kept. Update
`DESIGN-state-corpus.md` only where a section now names a wrong file. One commit.

### H. resume-studio push (owner ruling 3)

```
cd "$HOME/Desktop/Thesis/Job Applications/resume-studio"
CLAUDE_APPS_PREFS=$(mktemp) python3 -m unittest discover -s tests 2>&1 | tail -3
```

527 tests expected. Green: `git push origin prepwright-jobdescription` and paste the
result. Red: push nothing, paste the failure, continue with Prepwright. Do not edit
that repo unless a Prepwright change requires it; if it does, the suite runs again
before the push. The suite must never write to the real `~/.config`; the redirect
above is the guard and `test_the_suite_never_writes_to_the_real_home` asserts it.

### I. End-to-end use walk on the real Wingtip track

Real model calls, your hands, live home, port 8010, after §5B. Open `t-93c97fdd6d77`.
One teaching turn on an open step: confirm `grounded`, `cites` non-empty,
`citations.invented` empty, and the reply cites a document the page can open. Grade it:
`assessment` gains a row naming the model. Close the session: a review lands on the
step row. Close a second session on the **same** step (never verified). Reload: nothing
lost. `GET /api/flow`: `curriculum.done` and the rail agree with the store. Then undo
nothing: these are the owner's real study marks. Paste the SQL that proves each line.

### J. The adversarial hunt, read-only, before the extraction commits are final

Authorised for this and only this shape. The recipe that has worked twice:

- Four lenses, each **two files and one question**, prompt under 600 words.
- One consolidator that verifies every finding against live text and rejects what does
  not follow. It has rejected a third of raw findings each time; it is not optional.
- `Workflow` for the hunt. If its journal shows `"type":"failed"`, do not re-run it;
  fall back to four sequential `Agent` calls with the same prompts.
- Never for edits. Surgery is yours.

Lenses for this session: (1) `provider.py` + `tests/test_model_roles.py`: does any test
still patch a name where nothing looks it up? (2) `serve.py` + `index.html`: does every
route the page calls still exist, with the same whitelist and the same fall-through
order? (3) `teach.py` + `prepwright/corpus.py`: is the grounding chain unbroken across
the seam, with no place a pack can be built without `check_citations` seeing it?
(4) `bridge.py` + `prep-launcher.sh` + `MANIFEST.sha256`: does the launcher still load
exactly the files the manifest pins, under `-I -S`?

Fix what the consolidator confirms. Prove each fix by revert. Then commit.

### K. Close

Three interpreters green, manifest clean, orphan scan clean, 12 views walked with 0
console errors, `prep` started from `~/Music`. Fast-forward `master` to the branch
head (`git checkout master && git merge --ff-only round0-hunt-and-per-track-evidence`);
local repo, no remote, low risk, and the owner should not have to know a branch name.
Rewrite `HANDOFF.md` as the owner-facing state of the product: what it does, how to
run it, what was never verified, every claim naming its command. Delete this brief or
reduce it to a pointer at `HANDOFF.md`; a stale brief cost this session its first hour.

## 6. Claude Cowork

Available on this machine. Use it as a second, independent pair of eyes, never as a
second editor. Good uses: an unscripted UX walk of the 12 views after §5B by an agent
that has not read the source, reporting what confused it; a plain-language read of the
rewritten README by someone who has never seen the code. Rules: it runs against a
`PREPWRIGHT_HOME` temp home on port 8011, never the live store; it does not write to
this repository; its findings enter your evidence loop like any other report, verified
against live text before they change anything. Whether Cowork can press a native macOS
dialog is unknown; the owner's click in §5E does not depend on it.

## 7. Stop and ask the owner. Only these, at the moment they block, with context.

1. §5E: the moment the dialog is ready to be clicked. Say which window, which button,
   which file. Wait.
2. §5H: the resume-studio suite is red. Report; do not push.
3. §5B stop rule triggered: a move reverted. Report which and why before continuing.
4. Anything that changes **what the product teaches or from what sources.** Ordering,
   caps, schema, module layout, security posture and vocabulary are yours.

Everything else has been answered in §3 or in `HANDOFF.md`. Do not batch questions at
the start.

## 8. Traps that have each cost real time

1. zsh does not word-split. Run each interpreter literally.
2. The launcher refuses to start on a manifest mismatch. Read `shasum -c` output before
   `make_manifest.sh`, then re-pin, then commit the manifest with the change. Session 10
   shipped a broken tree for one commit by forgetting this on a docs-looking edit.
3. `python3 -I -S` drops the script's directory from `sys.path`; append, never insert.
4. `playwright-cli check` cannot click a 0×0 input. Use the `.checkbox` span.
5. A stale playwright ref survives a reload and clicks nothing.
6. `2>/dev/null` on a `playwright-cli` call hides a failed click.
7. A new POST route needs two edits: the whitelist and the pre-`/api/chat` block.
8. `_read_json_body` requires a JSON object; a list body is a 400 before any route.
9. `StoreError` IS a `RuntimeError`; `sqlite3.IntegrityError` is NOT a `StoreError`.
10. The launcher's output does not land in a file it is redirected to; read the
    bridge's own terminal for `_failure` reference ids.
11. Monkeypatching a name in the wrong module passes silently (§5B.4).
12. A revert that PASSES means the fix is not understood. Measure the variants one at a
    time until you know which line does the work. Restore byte-identical, `diff -q`.
13. `unittest discover -s tests -p 'test_x.py'` is the only single-file form that
    works; `tests/` has no `__init__.py`.
14. `JSON.stringify(x).length` is UTF-16 code units, not bytes; the caps are bytes.
15. `set_intake` kinds are `pasted|imported|freeform`; there is no `text`.
16. `TrackHandle`, not `Handle`.

## 9. Token discipline

- Start in `~/Desktop/Prepwright`. The single largest saving available.
- `HANDOFF.md` once. ADR 0006 once. This file once.
- `bridge.py` and `index.html` by `grep -n` then `sed -n 'X,Yp'`. Never whole.
- Do not spawn an agent to answer what one `grep` answers. Agents are for §5J and §6.
- Verify by running. The app starts in about four seconds; the suite in eleven.
- One line per change record: hypothesis, patch, test, keep or revert.
- Spartan prose in every report. Lead with what is not done.

## 10. Definition of done. All of it, or say which line is missing and why.

1. Four modules extracted, `bridge.py` a composition root, ADR 0007 written.
2. Grades hydrate from the `assessment` table; the 4,096-byte delta cap no longer
   scales with reasons.
3. `t-6c3a05d5f79f` archived; the page's track list matches the library.
4. The file chooser walked once with the owner, pick and cancel both recorded.
5. `keep._compact_done_steps` exercised and proved by revert.
6. `README.md` true in present tense; the dossier removed; `DESIGN-state-corpus.md`
   names no wrong file.
7. resume-studio pushed, or the red suite reported and nothing pushed.
8. One real teaching turn, grade, review and a second close on the same step on the
   Wingtip track, each backed by SQL.
9. The hunt run before the extraction commits were final; confirmed findings fixed
   and proved by revert.
10. Three interpreters green, manifest clean, orphan scan clean, 12 views at 0 console
    errors, `prep` from a foreign directory.
11. `master` fast-forwarded; tree clean; no remote invented.
12. `HANDOFF.md` rewritten for the owner, every claim naming its command; this brief
    deleted or reduced to a pointer.

## 11. How to report

Lead with what is **not** done or not verified. Then what is. "Works", "fixed" and
"verified" are `[Certain]` only when you can name the command whose output backs the
word; otherwise `[Likely]` or `[Guessing]`, and say which. If a test fails, paste the
output. If you skipped something, name it and say why. Partial work reported honestly
is useful; partial work reported as complete costs the owner a day.
