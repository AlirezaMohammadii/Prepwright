# Prepwright. Session brief 5. Execute this.

## 0. Before anything else

**Start this session with `~/Desktop/Prepwright` as the working directory.** Never from
inside `~/Desktop/Deep Fake Detection Project/`. That sibling carries 320 KB of
`.claude/rules/` about deepfake detection, manuscripts and grant proposals. None of it
applies here and all of it loads into every turn if you start there.

Run `/engage`. It loads Ruthless Candor (uncomfortable truth in line one, confidence tags
`[Certain]`/`[Likely]`/`[Guessing]`, no praise openers, spartan prose, a sensitive-data
watch) and prompt-to-start (phase order, the metric gate, the evidence-first loop). Apply
them. Do not restate them. Operate in absolute mode: no hedging where evidence exists, no
softening, no filler.

Then read, in this order:

1. `HANDOFF.md` in full. 374 lines, accurate, written by the session that shipped the last
   change. **This brief does not repeat it.** It corrects and extends it. Do not ask me
   anything HANDOFF.md answers.
2. `docs/adr/0003-page-marks-and-the-delta-protocol.md` then
   `docs/adr/0004-per-track-evidence-and-a-runtime-manifest.md`.
3. `prepwright/corpus.py` and `prepwright/pagestate.py` before writing against the store.

**Do not read `DESIGN-state-corpus.md` end to end.** It is 63 KB with lettered sections
(A layout, B schema, C track lifecycle, D caps, E corpus format, F concurrency, G
isolation). Read only the section your current task names.

A bridge may be listening on 8010. Check `/usr/sbin/lsof -ti tcp:8010` and kill it first.

## 1. Where the code is

Branch `round0-hunt-and-per-track-evidence`, two commits ahead of `master`. Everything
below builds on it. Fast-forward `master` when you are confident, not before.

**Working and tested:** the store (append-only SQLite per track, delta writes, survives
SIGKILL, two writers produce a union), the teaching engine, per-track grounding, the
citation validator, recovery, `MANIFEST.sha256` integrity over all 19 runtime files.
88 tests pass under `python3` (3.14), `/usr/bin/python3` (3.9.6) and `/usr/bin/python3 -I -S`.

**Stubs, 8 lines each, the entire front half of the product:**
`intake.py`, `diagnose.py`, `research.py`, `curriculum.py`. Building these is this
session's job.

**Built but not extracted:** `bridge.py` is 1791 lines and still holds security, provider,
teach, assess and serve. It works. Extraction is deferred, see §8.

## 2. The job this must actually work for

**AI Security & Governance Analyst, Wingtip Australia & New Zealand, Brisbane QLD.**
`https://www.linkedin.com/jobs/view/4456850604/`

Verified fetchable: HTTP 200, 283 KB, **no auth wall**, employer and title readable from
`<title>`, job body inside `show-more-less-html__markup`. LinkedIn's HTML shape changes
without notice, so parse defensively and fall back to the paste box rather than failing.

This is a real application the owner is making. The app is not done until it produces a
usable study plan for this posting.

## 3. The order, and why it is this order

Each step's output is the next step's input. Do not reorder.

### Task 0. resume-studio: push, then branch, then connect

`~/Desktop/Thesis/Job Applications/resume-studio/`, remote
`https://github.com/AlirezaMohammadii/resume-tailor.git`, branch `audit-fixes`, which is
level with origin. It holds **3,107 uncommitted lines across 6 files**, including a new
1,739-line `tests/test_bridge.py`.

**The owner has decided: commit and push those 6 files as-is to `audit-fixes` first.** Do
not rewrite, tidy, or "improve" them. Run its own test suite first and report the result.
If the suite fails, stop and tell the owner before pushing.

Then branch from `audit-fixes` and add the connection to Prepwright as a separate commit:
when `_accept_done` validates a result, also write the posting into the job's own output
folder as `<base>_JobDescription.md`, carrying the posting text, the source URL, the
capture date and a sha256 of the text. Write it **before** the scratch is purged.
`start_generation` builds the dict at `bridge.py:2847`; `_file_application` currently keeps
only the `.tex`, `.pdf` and `_FitReport.md`. **If the write fails, log and continue.** A
resume run must never fail because of this.

Acceptance: run resume-studio for the Wingtip job. `applications/<date>__AI_Security…/`
contains the four files, the run succeeded, and the existing tests still pass.

### Task 1. Run resume-studio for the Wingtip role

The owner has decided the diagnostic reads a **tailored** application folder, not the base
`.tex`. So generate it: the `_FitReport.md` already contains a requirement matrix, a
steelman and a red-team pass, and that is the seed the diagnostic needs. The base resume at
`~/Desktop/Thesis/Job Applications/Alireza_Mohammadi_Resume.tex` is the fallback only.

### Task 2. `intake.py`. Two ways in, one track out

**Both input paths are required.** The owner will sometimes have a URL and sometimes only
text pasted into a box in the app.

- **URL path.** Fetch with `urllib`. The SSRF rules in `HANDOFF.md` §9 are not optional and
  apply here: https only, refuse private and reserved address space, **re-check after every
  redirect**, cap redirects, bytes and wall-clock, record sha256 and the final URL.
- **Paste path.** Take the raw text. Same caps, same provenance record, `source_kind`
  `pasted`.
- Either way, extract employer, role title and the requirement text, and create the track
  through `prepwright/track.create_track`.
- Read the resume-studio application folder **exactly once**, at track creation, hash it,
  and never reopen it, so a later edit on that side cannot retroactively change what the
  track was built from.
- Run everything through `corpus.redact()` before it can reach a prompt or the store.
  Routing it through `corpus.ingest_text` gets that for free.

Acceptance: `intake_from_url(<the Wingtip link>)` and `intake_from_text(<pasted body>)` both
produce a track whose employer and role are correct, with provenance recorded.

### Task 3. `diagnose.py`. Probe, do not quiz

Conversational probing against two sources: the posting's requirements, and the owner's own
resume claims from the FitReport.

- A requirement they explain unprompted is **not** a gap.
- A resume claim they cannot defend **is** a gap, even when the posting never mentions it.
  This is the part that makes the tool worth using, and it is the part that is easy to skip.
- Produce a gap list. **Show it. Change nothing until it is approved.** Approval is a
  screen, not an assumption.

Acceptance: a gap list for the Wingtip role that names at least one gap the posting does not
mention, sourced from a resume claim.

### Task 4. `research.py`. The owner supplies the sources

The model never finds sources. That is the entire grounding claim. The owner pastes URLs
they already trust, `research.py` fetches, the provider CLI distils, `corpus.ingest_text`
writes with real provenance.

Same SSRF rules as Task 2. Store the sha256, the final URL after redirects and the fetch
date, so a citation can be falsified years later.

Acceptance, and it is a real one: `index.html` topics T2 and T4 cite
`database-indexing.doc.md`, which does not exist, so the tutor correctly refuses to teach
them today. **Produce that document through this pipeline. Do not hand-write it.**

### Task 5. `curriculum.py`. Order by dependency

Approved gaps plus corpus into ordered steps.

- Order by **dependency**, not importance. A concept whose prerequisite is unlearned is
  unteachable.
- Cut to the smallest set carrying most of the value.
- Tag every step `core`, `depth` or `reference`.
- Every step pins its exact corpus sections through `TrackHandle.pin_slice`.
  **`corpus.pin_all` is a placeholder** that pins every section of a document to a step.
  The curriculum is what should choose. Replace that call.

Acceptance: a plan for the Wingtip role where every step's `step_slice` names sections chosen
for it, and no step is pinned material it does not teach from.

### Task 6. The page. See §4, it is a full brief.

## 4. Design brief: the landing page and the flow

The owner's words: the visual is confusing. They are right, and here is why. `index.html`
renders **ten flat sibling views** (`overview`, `stages`, `curriculum`, `continue`,
`practice`, `questions`, `qa`, `sessions`, `recap`, `story`) with no entry point, no
onboarding and no first-run state. A new user lands inside a ten-view application already
populated with a synthetic backend-engineer track they never asked for. There is no moment
where the app explains itself.

**Build a welcome page and a guided flow. Apple-software inspired, and mean it:**

- **One decision per screen.** Never present two primary actions.
- **Progressive disclosure.** The ten views stay, but they are earned. A first-run user
  sees a linear path, not a dashboard.
- **Generous whitespace, large type, few colours.** The existing palette and design tokens
  are good. Use them. Do not introduce a second design language.
- **Motion with purpose.** Transitions that show where content came from. No decoration.
- **Plain language.** "Add the job you are preparing for", not "Initialise track intake".
- **Honest empty states.** When there is no corpus, say so and say what to do next. The
  refusal is the product working, so it should look deliberate, not broken.

**The flow to build, in this order:**

    Welcome  ──►  Add the job        ──►  Confirm what we read
                  (paste a link OR
                   paste the text)
                                          │
    Learn    ◄──  Your plan          ◄──  Add sources  ◄──  Approve the gaps  ◄──  Short conversation
    (step by step,                        (paste URLs                              (the diagnostic)
     one step at a time)                   you trust)

Each stage shows: where you are, what this stage is for, one action, and what happens next.
The owner should never have to guess what to do.

**During learning**, one step at a time. Show what the tutor is grounded in and let them
open it. Show progress against the plan, not against a clock.

**Wire the fields the bridge already returns.** `/api/chat` returns `grounded`, `cites`,
`citations` and `packSha16`. The page reads none of them. `citations.invented` lists tokens
the tutor named that the pack did not contain, which is precisely the confident-and-wrong
failure this whole design exists to prevent. Surfacing it is small and is the highest-value
change on the page.

Reuse the existing views for the parts that already work. Do not redesign what is not
confusing.

## 5. Already decided. Do not ask again.

| Question | Answer |
|---|---|
| resume-studio uncommitted work | Commit and push as-is to `audit-fixes` first, then branch for the Prepwright connection |
| Resume source for the diagnostic | Run resume-studio for the Wingtip job, import that folder |
| The job | Wingtip AI Security & Governance Analyst, Brisbane. Link in §2 |
| Intake input | Both a URL and a paste box. Both required |
| Deadline | None scheduled. Build in dependency order |
| Research sources | The candidate supplies URLs. The model never finds them |
| Persistence, grounding, integrity | Done. ADRs 0002, 0003, 0004 |

## 6. Stop and ask the owner these, at the moment they block

Give context, then wait. Do not guess, and do not batch them at the start.

1. **When you reach Task 4.** Ask for the URLs, naming which gaps they are for. The gap list
   from Task 3 must exist first, so the question is specific. If they have none to hand, ask
   whether vendor or standards documentation is acceptable for that gap and name the exact
   pages you would fetch.
2. **If the resume-studio suite fails in Task 0.** Do not push. Report what failed.
3. **If the Wingtip posting cannot be parsed** from the URL. Say so plainly and ask them to
   paste the text instead. Do not silently produce a track from a half-read page.
4. **Anything that changes what the product is** rather than how it is built. What Prepwright
   teaches, and from what sources, is the owner's call. Ordering, caps, schema, security
   posture and vocabulary are yours.

## 7. The adversarial hunt: the recipe that works

Round 0 ran last session and worked: 33 raw findings, 14 confirmed, 19 rejected, 934K
tokens, 5/5 agents, zero failures. The attempt before it used seven lenses with 4 KB prompts
telling each to read six files, and all seven died on the session limit returning nothing.

**Keep this shape exactly:**

- One lens gets **two files and one question**. Not six files and a checklist.
- **600 words maximum** per lens prompt.
- **Four lenses**, then **one consolidator** that verifies every finding against live text.
- **The consolidator is not optional.** It rejected 19 findings, including real analyses
  whose consequence did not follow, and one that was the published design rather than a
  defect.
- Run it **early**, while the session budget is fresh.
- `Workflow` for the hunt, which is read-only and parallel. **Never for edits.** Line-precise
  surgery is yours: exact-text `replace` on delimited blocks with the match count asserted
  before the write.
- If the journal shows `"type":"failed"`, do not re-run the workflow. Fall back to four
  sequential `Agent` calls with the same prompts.

**Run a hunt over what you build this session**, once intake, diagnostic and research exist.
Lens candidates: the SSRF boundary in `research.py` and `intake.py`; provenance and the
read-once hash; the gap-approval path where nothing may change before approval; contract
drift between the new page flow and the bridge.

**Lenses never run on the current tree:** dead guard, impossible number, silent no-op.

## 8. Deferred on purpose. Do not start these.

- **Extracting `bridge.py`** into `security`, `provider`, `teach`, `assess`, `serve`. It is
  1791 lines and works. The security half of that task is already closed by
  `MANIFEST.sha256`. The blocker is that `security`'s constants are computed at import from
  `PORT`, so `PORT` moves to `config.py` first. Not this session unless everything above
  lands.
- `TRACK_ID_RE` and `DOC_NAME_RE` use `$` instead of `\Z`. Cheap, do it if you are in
  `config.py` anyway.
- `TRACK_DB_CAP` is defined and enforced by nothing.
- The Codex provider path and `prep iphone` have never run.

## 9. Token discipline

- Start in `~/Desktop/Prepwright`. This is the single largest saving available.
- Read `HANDOFF.md` once, fully. It does not change under you.
- `DESIGN-state-corpus.md` by named section only.
- `index.html` is 3064 lines, `bridge.py` is 1791. Use `grep -n` to find the block, then
  `sed -n 'X,Yp'` to read it. Do not open either whole unless restructuring it.
- Do not spawn an agent to answer what one `grep` answers.
- Verify by running, not by reading twice. The app starts in about four seconds, the suite
  runs in under three.

## 10. Non-negotiables

1. **Standard library only.** No pip, no dependency. `sqlite3` is the persistence layer.
2. **No API key anywhere.** Chat runs through the owner's logged-in `claude` CLI.
3. **Do not rename** `tutor`, `student`, `candidate`, `step`, `session`, `assessment`,
   `recap`, `transcript`, `track`, `stage`, `corpus`, `citation`. `X-Tutor-Bridge` and
   `TUTOR_TS_*` are two-sided contracts; renaming one side locks the other out.
4. **Run `tools/orphan_scan.py` after every deletion.** `py_compile` accepts a module that
   reads an undefined name. The scanner caught a live call to a deleted function last
   session that compiled clean.
5. **Regenerate `MANIFEST.sha256`** with `./tools/make_manifest.sh` after any runtime file
   changes, and commit it with the change. A stale manifest refuses correct code.
6. **Prove every security guard by removing it** and confirming its own test fails. A
   security test that passes first try has not been shown to be capable of failing. Seven
   guards were proved this way last session.
7. **`HANDOFF.md` §10 holds 16 traps that each cost real time.** Read them. Two that will
   bite you this session: `unittest discover -s tests -p 'test_x.py'` is the only form that
   works because `tests/` has no `__init__.py`, and `JSON.stringify(x).length` is UTF-16
   code units, not bytes.

## 11. Definition of done

- resume-studio's 6 files committed and pushed to `audit-fixes`, its own suite passing, and
  the `_JobDescription.md` change on a separate branch with the owner's approval.
- A real Prepwright track built from the Wingtip posting, by URL and provable by paste.
- A gap list produced, shown, and approved before anything downstream ran.
- `database-indexing.doc.md` produced by `research.py` from URLs the owner supplied, with
  provenance recorded, and topics T2 and T4 teaching from it.
- A curriculum whose steps pin sections chosen for them, not everything.
- A welcome page and a guided flow that a first-time user can follow without being told.
- `citations.invented` visible on the page.
- Full suite passing under all three interpreters, with the output pasted.
- `./prep-launcher.sh` run, page loaded, one real teaching turn round-tripped and survived a
  reload.
- Tree clean, manifest current, commit message carrying no content from any other project.
- `HANDOFF.md` rewritten for the session after this one.

## 12. How to report

Lead with what is **not** done or not verified. Then what is.

"Works", "fixed" and "verified" are `[Certain]` only when you can name the command whose
output backs the word. Otherwise tag `[Likely]` or `[Guessing]` and say which.

If a test fails, paste the output. If you skipped something, name it and say why. If you
reach only Task 2, say exactly that. Partial work reported honestly is useful. Partial work
reported as complete costs the next session a day.
