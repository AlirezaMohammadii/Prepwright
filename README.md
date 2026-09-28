# Prepwright

A local interview-preparation tutor for one person on one machine.

You give it a job description. It works out what you already know and what you do
not, agrees a gap list with you, researches those gaps into a private corpus of
vetted sources, cuts a curriculum down to the small set of ideas that carries most
of the value, and then teaches that curriculum back to you conversationally, citing
the source of every claim it makes.

One job is one **track**. A track is divided into **stages**, and a stage into
**steps**. The curriculum is cut to the time available: an interview might be five
days away or five weeks away, and a track is sized to fit.

It runs as a browser page plus a Python bridge on `127.0.0.1:8010`. Python standard
library only. Nothing to `pip install`.

---

## Status, in one paragraph

Read this before anything else. The whole flow above is written and runs. Intake,
the diagnostic, gap approval, research, curriculum generation, teaching, grading,
the end-of-session review and the recap bank all exist, and the page calls each of
them: `grep -n 'api/' index.html` names every route it uses, and every one is
handled in `prepwright/serve.py`. Rehearsal (ADR 0008) and the day-before brief
followed on 2026-09-24. Progress lives in per-track SQLite under
`~/.prepwright`, delta-only, with caps enforced inside the transaction that causes
them; the second store this file used to describe, `progress/state.json`, is gone,
imported once and renamed. 551 tests pass on three interpreters, including under
the launcher's `/usr/bin/python3 -I -S` (2026-09-24).

What is *not* proved is a smaller and more specific list, and it is in **Known
limitations** below. Two things there are worth knowing before you rely on this:
the native file-picker dialog has never been opened, because no test may open one,
and the Codex provider path has never run: Prepwright looks for `codex` on PATH and
in `/opt/homebrew/bin`, and there is none in either place on this machine.

---

## The intended flow

Eight parts, in order. All eight are built, and each one below says what it does
today.

**1. Intake.** *(built)* A link, the description pasted into a box, or a finished
application from Resume Studio. Resume Studio's Prep button opens
`http://localhost:8010/?application=<folder>`, and the page shows which role that
folder would open and creates nothing until you confirm. The folder's real path
must sit under the applications root (`config.APPLICATIONS_ROOT`, moved with
`PREPWRIGHT_APPLICATIONS`), and a folder-only intake also needs its
`*_JobDescription.md`: the INTERIM folders an unfinished run leaves behind have
none and are refused by name. The employer and role come from that file's
provenance block (`position`, `company`, written since 2026-09-24). An older folder
is read against its posting, the longest name suffix the posting carries being the
employer, because the last-underscore split opened
`Research_Fellow_University_Of_Example` as employer "Example". A second Prep on
the same folder reopens its track. The external folder is read exactly once, copied
into the track, and hashed. After that it is provenance and is never reopened, so a
later edit on the other side cannot retroactively change what a track was built
from.

**2. Diagnostic.** *(built)* A conversation, not a quiz. The probes come from the
posting's requirements, from the fit report's weak rows, from the claims your
application makes, and from its red-team objection. The judge grades your answers
into solid, shaky or empty. With no model available it still produces a list and
says it graded by length only.

**3. Gap list, which you approve.** *(built)* The diagnostic produces a proposed
list of gaps. You edit it and approve it. Nothing is researched or taught until you
have said yes, because a curriculum built on a misread of your background wastes the
days you do not have.

**4. Research.** *(built)* Sources come three ways: links you paste, a file you
choose, or "Find sources for my gaps". There the model nominates URLs through
search, and each one is fetched through the same guard as a pasted link. It is kept
only if it answers and covers the gap, so a page the model named is never trusted
unread. Each stored document carries its origin URL, hashes of the fetched bytes, a
vetting tier, and a verbatim quoted fragment of the source span every section was
distilled from. Raw pages are not retained.

**5. Curriculum.** *(built)* Each approved gap becomes a step, tiered core, depth or
reference by its level and by whether the posting states it, and pinned to the
exact corpus sections it will be taught from. A gap the posting comes back to, or the
fit report's red team expects a panel to raise, moves up one tier
(`curriculum.emphasis`); it never changes which gaps are planned, set aside or cut. A
gap no stored source covers is set aside and named, not dropped.

**6. Teaching.** *(built)* One step at a time,
Socratic, a few sentences and one question. Every factual claim comes from the corpus
sections supplied with the turn, and the tutor names the source. When something is
missing it says so in one sentence rather than filling the gap from memory. The
prompt assembly, history trimming, corpus retrieval, provider invocation, and the
no-errands rule are written. The corpus is what research stored for the step. The
tutor does not see your application, so defending your own record is rehearsal's
job, not teaching's (ADR 0008).

**8. Rehearsal.** *(built, ADR 0008)* After the teaching, the panel's likeliest
questions become steps of their own: the red-team objection first, then what the
posting asks for and the application could not show, the resume's own claims, and
behavioural prompts, eight at most, with application logistics and work rights left
out. Your resume's lines (never its header or contact block) and your fit report are
stored as corpus titled "Your application", vetting primary, and pinned to each
question, so the grader builds a strong answer from your own record and cites it.
An answer is graded against a stated rubric: grounded, specific, structured, and
about 250 words spoken, which is counted rather than judged. You get what would
cost you in the room, the strong answer, and the panel's next question. A
rehearsal step is delivered only by a grade of 6 of 8 or better with no criterion
at 0, never by a tick, so `prepared` now means you answered well. "Day-before
brief" writes one page from the store: the concepts that carry the interview, your
five strongest stories mapped to requirements, the three likeliest objections with
the answer you rehearsed, and cited questions to ask them.

**7. Assessment and review.** *(built)* A grading
pass reads the transcripts of steps you have not ticked off and scores how much of
each step's own task you delivered. Off-topic conversation scores zero on purpose. A
separate end-of-session review reads one step's transcript back and drafts what was
covered, what you explained in your own words, what slipped, the single most useful
next thing, and two or three recap questions. Both run on the cheaper model of the
selected provider, because reading a transcript back is mechanical work rather than
teaching. Both produce drafts you can edit or delete, and neither ticks anything off
for you.

---

## Running it

```
cd ~/Desktop/Prepwright
/usr/bin/python3 -I -S bridge.py
# then open http://localhost:8010/
```

`-I -S` starts Python isolated, so user site packages and startup customisations
cannot shadow a standard-library import.

`PREPWRIGHT_PORT` overrides the port. The launcher validates it as a number in
range before using it.

### The `prep` launcher

`prep-launcher.sh` wraps the above: it checks the port, clears credential and proxy
variables out of the environment, starts the bridge, waits for `/api/health` to
identify itself as Prepwright, and only then opens the browser. It refuses to open a
port that answers with something other than Prepwright.

```
prep                  start it and open the browser (loopback only)
prep iphone           also serve it to your own iPhone over Tailscale
prep iphone off       tear that down again
prep --help
```

Two things about the launcher are unfinished today:

- It is **not installed** on this machine. There is no `prep` on `PATH`. To install
  it, symlink `prep-launcher.sh` into a directory on your `PATH`.
- Its two SHA-256 integrity pins (`BRIDGE_SHA256`, `INDEX_SHA256`) are deliberately
  **empty** while the code is still being written, because a stale pin would refuse
  to run correct code. While a pin is empty the check is skipped and the launcher
  prints a warning saying so. Re-pin before treating the launcher as an integrity
  control:

  ```
  /usr/bin/shasum -a 256 bridge.py index.html
  ```

### iPhone access

`prep iphone` serves the page to your phone without opening a port to the LAN or the
internet. The bridge still binds `127.0.0.1` and nothing else. Remote access is a
`tailscale serve` reverse proxy on this same Mac: the phone speaks WireGuard to your
own tailnet, Tailscale terminates TLS here and proxies to loopback.

- **No credential moves.** The provider login stays in the macOS Keychain and is read
  by the CLI on this machine. The phone holds only the session cookie, which is
  `HttpOnly`, `Secure`, and useless off your tailnet.
- **Only you get in.** `tailscale serve` stamps each proxied request with the
  authenticated tailnet login and strips any inbound forgery of that header. The
  bridge pins every proxied request to your exact login before it serves even the
  page, because the page is what hands out the session cookie. Another tailnet user
  gets a 403. A public `tailscale funnel` request, which carries no identity at all,
  is refused on an affirmative signal rather than by inferring from a missing header.
- **Off by default.** Remote mode reads two environment variables that only
  `prep iphone` sets, after reading your MagicDNS name and login from the running
  Tailscale daemon. A plain `prep` behaves identically to the loopback-only bridge.
  The launcher clears both variables at the top so an inherited value cannot widen
  the host allowlist by accident.

The preflight refuses to publish the port if Funnel is on, if something else holds
`:443`, if tailnet HTTPS certificates are not enabled, or if the port is held by a
service that did not identify itself as Prepwright. If it creates the proxy mapping
and then fails, it retracts the mapping rather than leaving your tailnet pointed at a
dead or foreign port.

---

## What the bridge can and cannot reach

File reading belongs to the bridge, never to the model. That is the whole point of a
grounded tutor, so the boundary is drawn before anything is read, and it is this.

**The reachable set is one track's own corpus, and nothing else.** Prepwright reads
only inside the track it is teaching. Every corpus path is resolved with `realpath`
and tested for containment before it is opened, and a symlink inside the corpus is
refused rather than followed. A path typed into the chat box is retrieval input,
never permission.

**Credential-shaped lines are redacted before anything reaches a prompt.** Private
key blocks, provider API keys, AWS keys, GitHub tokens, Slack tokens, JWTs, and
`password:`/`api_key:`-style assignments are replaced with a `[REDACTED]` marker.
Multi-line PEM blocks are dropped whole rather than line by line.

**The model gets no tools.** One invocation per reply, no tool schemas, no web
access, no ability to read a file or edit anything.

- Claude runs with `--safe-mode` (no project instructions, skills, plugins, hooks or
  MCP), `--tools ""`, `--disable-slash-commands`, `--strict-mcp-config`,
  `--max-turns 1`, and no session persistence.
- Codex runs `-a never -s read-only exec --ephemeral --ignore-user-config
  --ignore-rules --strict-config`, with web search off, agents off, an empty inherited
  shell environment, and a long explicit list of features disabled.
- Both run with the working directory set to a fresh empty temporary directory at
  mode 0700, so there is nothing around them to read even if the sandbox failed.
- All prompt text travels on stdin. None of it appears in process arguments, where
  any local process could read it out of the process table.

**No API key is stored or accepted.** Chat uses the existing login already held by
the selected CLI. There is no key in this folder, in the page, in its state file, or
in a request body. The bridge builds a minimal environment for the CLI rather than
passing your shell's, and derives the account name from the passwd database for the
running uid rather than from a caller-supplied `USER`.

**Live tutoring is not zero-egress, and nothing here pretends otherwise.** The system
prompt, the trimmed conversation tail, the current step's metadata, and the corpus
excerpts selected for that turn are sent to Anthropic when Claude is selected, or to
OpenAI when Codex is selected. No honest cloud-backed tutor can promise a reply and
also promise that no prompt leaves the machine. The guarantee is narrower and real:
unrelated files, other tracks, credentials and private documents are never included.

**Local HTTP surface.**

- Binds `127.0.0.1` only.
- Serves exactly two static routes, `/` and `/index.html`. The progress directory and
  the corpus are never web-served, and `HEAD` never falls through to the default
  static handler, which would leak metadata about private paths.
- Every sensitive route requires all of: an `HttpOnly` `SameSite=Strict` session
  cookie minted fresh at launch, a custom request header, an exact matching
  `Origin` and `Referer` when present, and either an exact localhost `Host` on
  the direct path or the pinned tailnet login on the proxied one. A proxied
  request that clears the identity pin is not asked for a `Host` as well,
  because the pin is the stronger of the two and no choice of `Host` can skip
  it.
- One model call at a time. A second concurrent request gets a 429 rather than
  queueing behind a call that might take three minutes.
- Failures return a short reference id to the browser. The reason goes to the
  terminal on your own machine, redacted like any other text.
- Response headers set a restrictive CSP, `nosniff`, `DENY` framing, `no-referrer`,
  and a `Permissions-Policy` denying camera, microphone and geolocation.

---

## Providers, models and reasoning effort

The provider and model ids are a billing and data-routing boundary, not a UI hint. A
client cannot ask the bridge to invoke an arbitrary backend or model name; anything
outside the table below is refused.

| Provider | Teaching models | Grading and review model |
|---|---|---|
| Claude | Fable 5, Opus 5, Sonnet 5, Haiku 4.5 | Haiku 4.5 |
| Codex | GPT-5.6 Sol, Terra, Luna | Luna |

`/api/health` reports, per provider, whether the CLI is installed and whether it is
logged in. Those are different problems with different one-line fixes, so they are
reported separately rather than collapsed into "unavailable".

Reasoning effort accepts `low`, `medium`, `high`, `xhigh`, `max`. An unrecognised
value fails closed to the default rather than being passed through, because at least
one provider CLI accepts an unknown effort value, warns on stderr nobody reads, and
exits zero. Each reply reports the effort actually applied, not the one requested.

**Which tutor runs.** The chat bar names a model only once you move it on that track.
Until then a turn sends no model, and the bridge resolves the tutor role: the Tutor row
under Models, else the preference Resume Studio shares
(`~/.config/claude-apps/model-prefs.json`), else the shipped default. The untouched bar
shows that resolved pair. Before 2026-09-28 the page sent its seeded `claude-opus-5` on
every turn, so neither the Tutor row nor the shared file ever reached a chat turn.

**Cost is measured after the fact, never predicted.** The bridge reads per-reply
token counts, and Claude's own dollar total, out of each CLI's
response envelope and returns them with the reply, and the page totals them in the
progress panel, so the numbers accumulate as you work. Nothing here predicts them in
advance. Chatting counts against the selected provider's own CLI usage.

---

## How progress is stored

One SQLite database per track, under `~/.prepwright`, written through
`prepwright/state.py`. The page sends changes, never a document.

### The save path, end to end

You tick a topic or the tutor answers. The page compares what it holds against the
document the bridge last acknowledged and sends only the difference, as ops: one op
per appended chat turn, one op per changed mark. `prepwright/pagestate.py` validates
every op, then appends rows. `GET /api/state` rebuilds the page's document out of
those rows.

- A chat turn is a `turn` row. `turn` refuses DELETE outright and refuses UPDATE
  outside one receipted compaction case, both enforced by SQLite triggers rather
  than by application code.
- Everything else the page records is a `mark` row: `(kind, key, value)`, append
  only. The current value of a key is the newest row for it, and the older rows are
  its undo history. Un-ticking a topic writes a new row; it does not erase one.
- **A stale tab cannot express a loss.** There is no op that removes anything, so
  the old whole-document write, its revision hash and the shrink-detector that had
  to sit behind that hash are all gone. That is the whole reason for the change.
- Both row types carry a client-minted id in a UNIQUE column, and a mark whose
  value already matches is skipped without writing. A save retried over a dropped
  connection is a no-op, not a duplicate.
- The revision is derived from the two append-only sequence numbers. A write that
  changed nothing does not move it.
- A save against a stale revision still applies, because every op is an append, and
  the reply carries the merged document for the page to fold in. A bulk write
  marked `replace` against a stale revision is refused with a 409 and the real
  document, because that is the one write that could subtract.
- Caps are charged inside the same transaction as the row they pay for: 3 MiB of
  transcript, 256 KiB of marks, 8 KiB per turn, 4 KiB per mark. The request body cap
  for one delta is 4 MB, and the page slices a larger change into several writes.
- Each request opens its own handle and closes it. `sqlite3` refuses a connection
  used from a thread other than the one that made it, and this is a threading
  server. A handle that wrote takes a backup on close, under a published interval.
- A `track.db` that fails its integrity check on open is repaired in the open path,
  from the newest backup, before the request is answered. It is quarantined rather
  than deleted, and never moved aside when there is nothing to restore from.
- Storage is in `~/.prepwright`, not in this repository, in `0600` files inside
  `0700` directories. Nothing under it is reachable through the static server.

### Coming from the old scheme

`progress/state.json` was the store until this change. On the first start after it,
the bridge reads that file once, or the newest snapshot in `progress/backups/` that
still parses, imports it as ops, and renames it to `state.json.imported-<utc>`. It
is never deleted. The import runs before the server accepts a request, so no save
can interleave with it.

Storage lives in `~/.prepwright`, outside this repository, at mode 0700. Set
`PREPWRIGHT_HOME` to put it elsewhere. Its decisions, in brief:

- **Storage lives outside the repo**, under a `0700` directory in your home folder,
  with a layout generation file read before any database is opened.
- **One SQLite database per track**, plus a small library registry holding no content
  at all: no job text, no gaps, no turns, no document bodies, no card fronts.
  Reading another track's material would require opening a second file, and foreign
  keys cannot cross database files, so a curriculum step is structurally incapable of
  naming a corpus section outside its own track.
- **Every write is a delta.** There is no endpoint that accepts a client's copy of a
  track and makes it the truth. Append-only tables are enforced by triggers, so the
  stale-tab clobber is not detected by a heuristic; it is unrepresentable.
- **Sequence numbers are server-allocated** and transcript order is by sequence,
  never by a clock. A wrong system clock cannot reorder a transcript, and every
  age-driven automatic behaviour suspends itself when the clock looks suspect.
- **Corpus documents are immutable files** with a one-line JSON header, sections
  capped at 900 characters each, and a trailing block of verbatim source fragments. The tutor is handed
  sections by byte offset, never whole documents. A correction produces a new
  document rather than editing an existing one.
- **Citations are validated on the way out.** Every citation token in a reply is
  checked against the exact pack that was sent, not against "a valid id somewhere".
  A reply that fails is regenerated once, then stored flagged rather than presented
  as taught fact.
- **A track lifecycle** of active, stale, archived, trashed, with one rule governing
  every automatic transition: it may change how bytes are represented, never whether
  a decision survives. Deleting a track, a card, a corpus document, a gap, a step, a
  score, a citation or a student turn is never automatic.

Storage caps, with the arithmetic worked out in the design doc rather than guessed:

| Bound | Value |
|---|---|
| Prompt budget per teaching turn | ~29,800 bytes, about 9,000 tokens |
| Corpus documents per track | 48, each at most 12,288 bytes |
| Corpus bytes per track | 640 KiB |
| Transcript bytes per track | 3 MiB |
| Flashcards per track | 300, 320 KiB |
| Track database | 4.5 MiB |
| One active track, everything counted | 13 MiB ceiling, ~6.7 MiB typical |
| Library soft cap | 192 MiB, triggers notices and pressure archiving |
| Library hard cap | 384 MiB, refuses research; teaching, review, restore and delete keep working |

At the transcript cap the design compacts the tutor's prose in completed steps rather
than refusing writes, and never touches a student turn. Freezing the one track you
are studying the night before an interview is the worse failure.

`.gitignore` already excludes study data by directory rather than by extension, so a
new file type inside a track is ignored the day it is invented. Tracks, corpora,
progress, state files, backups and anything a job description was dropped into stay
out of version control.

---

## What is deliberately absent

Not a to-do list. These are decisions, and each one has a reason.

- **No spaced-repetition scheduler.** The `card` and `card_review` tables are a
  complete SM-2 schema that nothing writes, kept dormant and annotated by the
  owner's ruling of 2026-09-09 (ADR 0006). The Recap drill shuffles a bank; it does
  not schedule, and the page no longer claims it does. Dropping the tables would
  invalidate a documented isolation layer for no change any user can see.
- **No agentic orchestration inside the app.** The app is a page and a bridge. The
  model is called once per turn, from an empty directory, with no tools.
- **The model never finds sources.** Research fetches URLs the candidate supplied
  or approved. Nothing crawls.
- **No multi-user anything.** Two devices editing at once both land, because every
  write is an append. That is single-user software used from more than one screen.
- **No pip, ever.** Standard library only, Python 3.9 compatible.

## Known limitations

- **A track is capped at 99 stages.** A step key is
  `<stage>:<topic|practice|check>:<id>`, and the bridge validates the stage field as
  one or two digits. The page mints keys in the same shape, so both sides change
  together or neither does.
- **Session review reads the tail of a transcript, not all of it.** The last 40
  turns, each capped. A very long step gets a review of its recent half.
- **History is trimmed by a turn cap and a byte budget, whichever binds first.**
  The last four turns go over verbatim, older ones are condensed, and the tutor's
  own past replies are cut hardest because they are the bulk of the bytes and the
  least load-bearing part of the context. A conversation longer than the request
  limits is trimmed to its newest turns rather than refused, and the reply says
  how many were left out.
- **Grading is advisory.** It never ticks a step off for you. The bar is still
  whether you can explain the thing back, and only you decide that.
- **The review call fails rather than inventing a flashcard.** If the model returns
  no recap questions the call errors. A fabricated card is worse than no card,
  because you would then drill it as true.
- **A local process can forge the tailnet identity header** by connecting straight to
  loopback. That grants nothing new: a local process running as you can already read
  the track database off disk. The header only separates tailnet requesters from each other and from the
  public internet.
- **Renaming the machine in Tailscale requires restarting `prep iphone`**, because
  remote mode reads the MagicDNS name once at process start.
- **Two devices editing at once both land.** Every write is an append, so neither
  device can overwrite the other's work and there is nothing to resolve. This is
  single-user software used from more than one screen, not multi-user software.
- **The native file chooser has never been opened.** It is a macOS dialog driven by
  `osascript`, so no test can open it: a test that did would wait on a human, and a
  hung one parks the bridge for 240 seconds. The typed-path route into the same
  `resolve()` and `gate()` is tested; the dialog itself is not.
- **The Codex provider path has never run.** Prepwright looks for `codex` on PATH
  and in `/opt/homebrew/bin`, and neither has one. A copy ships inside the VS Code
  ChatGPT extension, which Resume Studio finds and Prepwright does not look for.
  The argv builder, the JSONL parser and the model whitelist are unit tested;
  nothing has watched the process itself answer.
- **`prep iphone` has never run**, so remote mode is implemented and untried.

---

## Files

```
bridge.py                 the composition root. 137 lines, one function: it puts the
                          package on sys.path, wires the four modules and serves
prepwright/               one module per concern, all written and tested
  config.py               identity, every path and every cap, in one place
  state.py                the store: schema, triggers, caps, one handle per track
  track.py                lifecycle: create, archive, restore, trash, purge
  keep.py                 housekeeping: recounts, the eviction ladder, backups
  pagestate.py            the page document and the delta protocol
  security.py             the request boundary: origin, host, cookie, identity
  corpus.py               build_pack and check_citations: grounding, by construction
  intake.py               the job posting, pasted or imported
  diagnose.py             the diagnostic and its judge
  research.py             fetching and distilling candidate-supplied sources
  curriculum.py           an approved gap list becomes stages and steps
  ingest.py               documents into a track's own corpus
  provider.py             the CLIs, the model registry, six roles, settings
  teach.py                one teaching turn, and the stage ladder
  assess.py               grading, the end-of-session review, recap cards
  rehearse.py             the panel's questions, the rubric grade, the brief
  serve.py                every route, and which track this process serves
  prompt.py               a pointer at corpus.py and teach.py; holds no code
prep-launcher.sh          the `prep` command: preflight, manifest check, iPhone mode
index.html                the page: 12 views, no framework, no build step
MANIFEST.sha256           21 pinned files. The launcher refuses to start on any
                          mismatch, so an edited file must be re-pinned
tools/                    make_manifest.sh, orphan_scan.py
tests/                    551 tests: python3 -m unittest discover -s tests
docs/adr/                 the decisions that are settled, and why
DESIGN-state-corpus.md    the accepted persistence and corpus design
HANDOFF.md                the state of the product, every claim naming its command
corpus/                   a one-document development seed, git-ignored. NOT the
                          runtime store: each track carries its own corpus
.gitignore                keeps study data, secrets and runtime state out of git
```
