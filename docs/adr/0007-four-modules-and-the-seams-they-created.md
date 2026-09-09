# ADR 0007 — Four modules, and the seams the split created

Date: 2026-09-10
Status: accepted

## Context

`bridge.py` was 3,215 lines. Four files under `prepwright/` had been reserved for
its contents since ADR 0001 and were still 7-line stubs: `provider.py`,
`teach.py`, `assess.py`, `serve.py`. The owner ruled on 2026-09-09 that all four
be filled, for real, in one session.

The stated blocker was gone. `PORT` had already moved into `config.py` so
`security.py` could compute its allowlists at import, and `security.py` was the
worked example: 340 lines, extracted, with short aliases left behind in
`bridge.py` because renaming several hundred call sites for a file move is churn
rather than a refactor.

## Decision

Four modules, one commit each, in dependency order.

```
config -> state -> track -> keep
                \-> corpus, curriculum, diagnose, research, ingest, intake,
                    pagestate, security
provider -> config, corpus, security, state
teach    -> config, corpus, curriculum, diagnose, provider, state
assess   -> provider, security, state
serve    -> everything above
bridge   -> config, provider, security, serve      (the composition root)
```

`bridge.py` is 137 lines and defines one function. It keeps its own `SCRIPT_DIR`
because that copy is what puts the package on `sys.path`, so it cannot wait for
an import of the package. `config.SCRIPT_DIR` is computed a second time from
`config.py`'s own location and the two are asserted equal rather than assumed.

Every move is a byte-identical block. The `PC`, `PSTATE`, `PCORPUS` and `PSEC`
alias names came across with the bodies, so each commit's diff is checkable with
`diff` rather than by reading.

### Where the current-track block went, and why not elsewhere

`current_track_id` and `open_state_track` were consumed by grading, by the
handler and by `main()`. Three homes were considered.

`track.py` is wrong and would not even import: `keep.py` already imports
`track.py`, and `open_state_track` wraps `keep.open_track_or_recover`, so that
is a cycle. `keep.py` would work but is housekeeping, and DESIGN section D would
then stop describing it. Staying in `bridge.py` is forbidden by the import rule.

The fourth option is the one taken, and it removed the problem rather than
placing it. `_persist_assessment` now takes its opener as an argument, because
grading a transcript and deciding which track the grade lands in are two
decisions and only the second belongs to the server. That left `serve.py` as the
only consumer outside `main()`, which is where per-process routing state belongs
anyway. No fifth module, no import-time wiring seam, and two monkeypatches gone:
the tests hand in a fake opener, which is what they were faking all along.

### Constants stop living in two places

`SCRIPT_DIR`, `SEED_CORPUS_DIR`, `LEGACY_STATE_*`, `REQUEST_TIMEOUT`,
`MAX_REQUEST_BYTES` and `MAX_STATE_BYTES` moved into `config.py`, whose own
docstring already said nothing else in the package may hardcode a path or a cap.
`bridge.py` was a second constants store, which is the defect ADR 0002 removed
for the page document.

## The seam that bit, twice

Python looks a global up in the module where the CALLER is defined. Once
`_role_choice` lives in `provider.py`, patching `bridge._read_settings` changes
nothing it sees: the test goes green while the code reads the real file on disk.

Both halves fired during the work.

1. Two patch targets were left pointing at aliases. Caught because three other
   names raised `AttributeError` first, loudly. Without those three, two tests
   would have passed while testing nothing.
2. The subtler half. A search-and-replace moved the assignment
   `PROV._read_settings = <lambda>` but not
   `addCleanup(setattr, bridge, "_read_settings", real)`, because the name in a
   `setattr` restore is a string literal that the pattern does not match. The
   two halves then named different modules, so the junk lambda was never
   restored. `unittest` orders methods alphabetically, so `layer_three` ran
   before `layer_two` and the leak reached it.

`tests/test_extraction_seams.py` makes three rules mechanical instead of
remembered:

- a patched name must be defined by the module it is patched on, checked by AST
  over every `M.N = ...` and `addCleanup(setattr, M, "N", ...)` in `tests/`,
- nothing in `prepwright/` may import `bridge`,
- `bridge.SCRIPT_DIR` and `config.SCRIPT_DIR` are the same directory.

Proved by reverting the restore target: the guard fails and names the line.

## What the move surfaced that it did not cause

**A census that could only see one file.** `test_there_is_one_role_per_model_callsite`
counted `run_cli(` by matching source text in `bridge.py` at two hard-coded
indentation levels. Two of the five call sites moved and it broke at 3 != 5. It
is now counted over the AST of every file that can hold a call, which also fixes
a hole the string form always had: a call written at any third indentation was
invisible to it. Proved by adding a sixth call site. The test fails and names it.

**A suite that read the machine's cross-app preference.** `RoleBase` redirected
`PC.HOME` but not `SHARED_PREFS_PATH`, so the shipped-defaults tests asserted
against `~/.config/claude-apps/model-prefs.json`. It failed on 2026-09-10
because that file had come to hold `claude-sonnet-5` at `xhigh`, written by
resume-studio, and the tutor default read `xhigh` instead of `""`. Only the tutor
role takes the shared preference, which is why only the two tutor assertions
moved. The write side was checked and is safe: `_write_settings` mirrors only
when the caller passes an explicit `shared` block. ADR 0006 recorded the writing
half of this hazard for the sibling tool. This is the reading half on this side.

**A fifth stub.** `prepwright/prompt.py` said "Status: partly in bridge.py"
after `bridge.py` stopped holding any prompt text. Its concern is genuinely
split between `corpus.py` (`build_pack`, `check_citations`) and `teach.py` (the
prompt assembly), so it is now a pointer at both. It holds no code and nothing
imports it, but "prompt" is what a reader greps for.

## Consequences

`bridge.py` 3,215 -> 137. `provider.py` 895, `serve.py` 1,747, `assess.py` 380,
`teach.py` 325. The manifest still pins 20 files, because no file was added or
removed. 487 tests pass on `python3`, `/usr/bin/python3` and
`/usr/bin/python3 -I -S`. The orphan scan is clean over 36 files. The 12 views
render with zero console errors, and `/api/health`, `/api/settings`, `/api/flow`,
`/api/state` and `/api/tracks` all answer 200 through the moved handler.

The rule that costs nothing to keep and everything to forget: **patch at the
module whose code reads the name.** A test file addresses the owning module now,
not `bridge`, and the guard fails the suite if that slips.
