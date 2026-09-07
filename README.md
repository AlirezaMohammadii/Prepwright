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

Read this before anything else. Roughly half of the product described above is not
written yet. What exists today is the back half: a hardened local HTTP server that
serves one page, stores progress on disk, retrieves cited sections from the local
corpus, relays a bounded teaching prompt to a logged-in `claude` or `codex` CLI,
grades step transcripts, and drafts an end-of-session review. Beside it, and not yet
wired to it, sits the persistence layer of `DESIGN-state-corpus.md`: per-track SQLite
with delta-only writes, caps enforced inside the transaction that causes them, and
cross-track isolation as a path and foreign-key mechanism rather than a promise. What
does not exist is the front half of the flow: intake, the diagnostic, the gap list,
research and curriculum generation. The **Not built yet** section below is the honest
inventory, and it is the section worth reading first.

---

## The intended flow

Seven parts, in order. The build status of each is marked, and only the parts marked
built are described in the present tense.

**1. Intake.** *(not built)* You paste a job description into a box, or point
Prepwright at a per-job folder written by a separate resume-tailoring app. The
external folder is read exactly once, copied into the track, and hashed. After that
it is provenance and is never reopened, so a later edit on the other side cannot
retroactively change what a track was built from.

**2. Diagnostic.** *(not built)* A conversation, not a quiz. The tutor probes what
you actually understand against what the posting demands, and records where you are
solid, where you are shaky, and where you are empty.

**3. Gap list, which you approve.** *(not built)* The diagnostic produces a proposed
list of gaps. You edit it and approve it. Nothing is researched or taught until you
have said yes, because a curriculum built on a misread of your background wastes the
days you do not have.

**4. Research.** *(not built)* Each approved gap is researched into distilled corpus
documents, each carrying its origin URL, hashes of the fetched bytes, a vetting tier,
and a verbatim quoted fragment of the source span every section was distilled from.
Raw pages are not retained. Those quoted fragments are what let a citation be
falsified years later, at 560 bytes per document instead of 400 KB.

**5. Curriculum.** *(not built)* Steps are generated across the stages, cut to the
subset of concepts carrying most of the value, and each step is pinned to the exact
corpus sections it will be taught from.

**6. Teaching.** *(built, against a hand-placed corpus)* One step at a time,
Socratic, a few sentences and one question. Every factual claim comes from the corpus
sections supplied with the turn, and the tutor names the source. When something is
missing it says so in one sentence rather than filling the gap from memory. The
prompt assembly, history trimming, corpus retrieval, provider invocation, and the
no-errands rule are written. What fills the corpus is not, so today the tutor teaches
from whatever documents are placed in `corpus/` by hand.

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

**Cost is measured after the fact, never predicted.** The bridge reads per-reply
token counts, and Claude's own dollar total, out of each CLI's
response envelope and returns them with the reply, and the page totals them in the
progress panel, so the numbers accumulate as you work. Nothing here predicts them in
advance. Chatting counts against the selected provider's own CLI usage.

---

## How progress is stored

Two different things are true here and it matters which one you are reading about.

### What runs today

A single JSON file, `progress/state.json`, written by the bridge after every change.

- The progress directory is `0700` and the files inside it are `0600`. Neither is
  reachable through the static server.
- Writes are atomic: write to a temp file carrying the thread id, `fsync`, then
  `os.replace`. A crash mid-write leaves the previous good file intact instead of a
  truncated one.
- Before each overwrite, the previous file is snapshotted into `progress/backups/`,
  at most one snapshot per five minutes, newest 200 kept.
- If the state file is ever unreadable it is moved aside as `corrupt-*.json` and the
  newest snapshot that still parses is served in its place.
- The richest state ever written is also kept in `progress/high-water.json`, which is
  never pruned, so a rotating backup set cannot become the floor under a bad write.
- Each save carries an opaque revision. A stale tab gets a 409 conflict instead of
  overwriting newer work.
- A second guard sits behind the revision check, because revision agreement only
  proves the client read the file and proves nothing about what the client is
  carrying. A write that would make any step's transcript shrink or disappear, or
  that would un-tick more than one item at once, is refused with the on-disk copy
  handed back so a stale page can adopt reality. Only an explicit user-initiated
  import may write a state carrying less work than the file already holds.
- The request body cap for a state save is 16 MB.

### What is built beside it, and not yet wired

`prepwright/state.py`, `track.py` and `keep.py` implement the design in
`DESIGN-state-corpus.md`, with `config.py` holding every path and every cap. They pass
31 tests that kill a real process mid-transaction, race two real processes, scribble
over a real database file, refuse a delete while a session is open, and prove that one
track's corpus cannot reach another track's prompt. Nothing calls them yet: the bridge
still runs the JSON scheme above, and moving it across is the next piece of work.

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

## Not built yet

Plainly, so nothing above reads as a promise.

- **The page shows a placeholder track.** `index.html` renders stages, steps, tier
  chips, corpus citations, the question bank and the "Your story" view, and it talks to
  every live endpoint. The track it renders is a small synthetic example written into
  the page so it can run before a generator exists. Intake, the gap-approval screen,
  a research panel and a track switcher are not on it yet.
- **Intake.** No job-description box, no folder import, no `intake/` directory, no
  source hashing.
- **The diagnostic.** Nothing probes what you know.
- **The gap list and its approval step.** Nothing produces gaps, and nothing asks you
  to approve them.
- **Research and source vetting.** No fetching, no distillation, no vetting tiers, no
  origin hashes, no quoted source fragments. The `corpus/` directory currently holds
  one hand-written example document. It carries the `## ` sections the retriever
  selects from, but not the JSON header line or the trailing source-fragment block
  the design doc specifies.
- **Curriculum generation.** Nothing turns an approved gap list into stages and steps.
- **The bridge is not on the persistence layer yet.** Per-track SQLite, the library
  registry, append-only triggers, leases, the archive format, the eviction ladder and
  caps enforced at the write are implemented and tested in `prepwright/`, but
  `bridge.py` still reads and writes the single JSON state file described above. Two
  stores now describe the same thing, which is the first thing to fix.
- **Track management at runtime.** The store handles many tracks; the page and the
  bridge still assume one. There is no track switcher and no create-track route.

---

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
  loopback. That grants nothing new: a local process can already read the state file
  off disk. The header only separates tailnet requesters from each other and from the
  public internet.
- **Renaming the machine in Tailscale requires restarting `prep iphone`**, because
  remote mode reads the MagicDNS name once at process start.
- **Two devices editing at once resolve by the same revision rule as two browser
  tabs.** Last writer with a current revision wins; a stale one is refused. This is
  single-user software used from more than one screen, not multi-user software.
- **Integrity pins are empty**, so the launcher currently verifies nothing about the
  files it starts. See the launcher section above.

---

## Files

```
bridge.py                 the local HTTP bridge: routing, auth, state, provider calls
prep-launcher.sh          the `prep` command: preflight, environment scrubbing, iPhone mode
index.html                the local page: stages, chat, progress, question bank
prepwright/               one module per concern; config, state, track and keep are
                          written and tested, the rest are docstring stubs
tests/                    unittest, run with `python3 -m unittest discover -s tests`
docs/adr/                 the decisions that are settled, and why
DESIGN-state-corpus.md    the accepted persistence and corpus design; implemented in
                          prepwright/, not yet wired to bridge.py
HANDOFF.md                what the next session needs to know
corpus/                   distilled documents the tutor teaches from; git-ignored, so a
                          fresh clone starts with none and the tutor says so
progress/                 created at first run: state.json, backups/, high-water.json
.gitignore                keeps study data, secrets and runtime state out of version control
```
