# ADR 0004 — The evidence pack comes from one track, and the runtime is pinned

Date: 2026-09-07
Status: Accepted. Implemented in `prepwright/corpus.py`, `bridge.py`,
`prep-launcher.sh`, `MANIFEST.sha256` and `tools/`.

## Context

Two things were true at once, and both contradicted claims the project makes
about itself.

The tutor was grounded in a `corpus/` directory shared by every track.
`bridge.corpus_evidence()` scanned it, scored sections by term overlap with the
step and the student's last message, and returned prose. ADR 0002 had already
built the alternative: a corpus inside each track, addressed by
`(doc_id, sec_id)`, with `step_slice` recording exactly which sections a step may
teach from and a composite foreign key that cannot cross a database file. The
store could make the isolation promise. The teaching path could not, because it
never opened a track to answer a teaching question.

The launcher pinned two files and its comment called them "the two files this
launcher starts". That stopped being true when the bridge grew a package. A
`prep` start loads nineteen files. Six of the `prepwright` modules carry the
security envelope, the storage caps and the redaction rules, and `state.py` alone
is 1791 lines. Both pins were empty, so nothing was enforced and nothing was
broken, but filling them as written would have signed the wrapper and left the
contents unsigned.

## Decision

**A teaching turn is grounded through one `TrackHandle` or it is not grounded.**
`prepwright/corpus.py` owns redaction, loose-document parsing, the one-time
directory seed, the evidence pack and the citation check.
`bridge.evidence_pack(handle, step_key)` seeds the development corpus into the
track on first use and returns `TrackHandle.build_pack()`'s result. The route
owns the handle's lifetime and builds the pack before the provider call, so the
reply can be checked against exactly what was sent. `corpus_evidence`, the
directory scanner, the term scorer and the shared-corpus constants are gone.

**A citation is checked against the pack, never against the track.** A token this
track owns but did not supply for this turn is reported as invented. The reply
carries `grounded`, `cites`, `citations` and `packSha16`, so the page can show a
claim the tutor could not have read.

**The client no longer chooses the evidence.** `/api/chat` stopped reading the
`citation` field. What a turn may cite is what the curriculum pinned to the step.
Honouring a client's hint would put the choice of evidence back in the caller's
hands, which is the grounding claim inverted.

**Integrity is a manifest over the runtime set.** `MANIFEST.sha256` pins
`bridge.py`, `index.html` and every `prepwright/*.py`. The launcher verifies it
with `shasum --strict -c` and refuses to start on a mismatch. It also counts the
files on disk against the pinned count, because `shasum -c` cannot see a module
that is present and unlisted, and an unlisted module in the import path is
unsigned code. `tools/make_manifest.sh` regenerates it.

## The seed directory is a fixture, not a store

`corpus/` at the repository root is ingested into a track once, with the local
file path as its origin and `vetting='community'` at trust 2, which is the
schema's weakest label. A researched document will carry its real provenance, so
the two stay distinguishable in `doc`. The seed is idempotent by title, refuses
symlinks, and redacts credential-shaped lines before anything reaches the store,
which is checked by `tests/test_corpus_pack.py` against a corpus file that
carries a deliberate example key.

## What this cost

`tests/test_corpus_containment.py` is deleted. Every test in it addressed
`bridge.corpus_evidence` and the shared directory, and its subject no longer
exists. The properties it protected are covered on the correct subject:
containment and symlink refusal by `TrackHandle.corpus_path` in
`tests/test_persistence.py`, and redaction, empty-corpus honesty and cross-track
isolation by `tests/test_corpus_pack.py`.

`bridge.py` is not yet split into `serve`, `security`, `provider`, `teach`,
`assess` and `prompt`. Only the corpus seam moved. The manifest closes the
security half of that task, and the rest is still open.

## Consequences

An empty corpus and an unpinned step now read differently, and both are
prompt-ready refusals rather than an empty evidence block. A caller cannot
accidentally send a blank pack and get an answer from the model's memory.

Two tracks both call their first document `D01`. That is deliberate: the same
token read through two handles returns two different documents, which is why a
citation validator must be handed the pack rather than the track.

The manifest has to be regenerated with every change to the runtime set, and a
stale manifest refuses correct code. That is the failure mode that kept the old
two-file pin empty and therefore enforcing nothing, so the refusal message names
the offending files and the command that fixes it.
