# ADR 0001 — A flat package, run in place, with no install step

Date: 2026-09-07
Status: Accepted

## Context

Prepwright was seeded by copying a working tutor app: one `bridge.py`, one
`index.html`, and a shell launcher. The copy was cut down by removing a
code-review subsystem and a source-repository retrieval layer, neither of which a
tutor that teaches from a corpus has any use for.

Three constraints shape the layout.

The launcher is meant to pin SHA-256 hashes of every runtime file and refuse to
start if one changed without the approved launcher being reinstalled. It declares
two pins today, for `bridge.py` and `index.html`, and both are still empty, which
it says out loud at every start. Whatever fills them only works over a fixed,
enumerable file set.

The bridge runs under `/usr/bin/python3 -I -S`. Isolated, no user site packages, no
startup customisation, no pip. Starting the app is `prep`, not create a virtualenv
and install a package.

The domain has sixteen real concerns: configuration, intake, diagnosis, research,
corpus, curriculum, teaching, assessment, prompt assembly, page state, state,
tracks, housekeeping, serving, security, provider invocation. One file cannot
hold them. The count said fifteen and the list held fourteen, which is the sort
of drift a manifest over the package is meant to make visible.

## Decision

A flat `prepwright/` package next to the entry point. One module per concern. No
subpackages, no `src/` layout, no `pyproject.toml`, no install.

The PyPA src-layout is the correct default for an installable package because it
forces tests to run against the installed artifact. Prepwright is never installed,
so that benefit does not exist here and the cost is real: `-I -S` plus a path
manipulation to import from a nested tree, for nothing.

Nesting was rejected for the same reason. `prepwright/serving/handler.py` reads
better in a diagram and pins worse in a launcher. See the first consequence
below: the path manipulation this paragraph hoped to avoid turned out to be
unavoidable either way, which narrows the argument without overturning it.

## Consequences

`bridge.py` has to put its own directory on `sys.path` by hand. This was the one
thing the decision above assumed it would avoid, and the assumption was wrong:
`python3 -I` is documented to place neither the script's directory nor user
site-packages on the path, so under the launcher's `-I -S` a flat package beside
the entry point is no more importable than a nested one. Verified rather than
assumed. A probe run as `python3 -I -S probe.py` reported `sys.path[0]` as the
standard library zip and raised `ModuleNotFoundError` on the local package.

The line appends rather than inserts, which was also verified: with the app
directory first on the path, a file named `json.py` beside the entry point
shadows the standard library. Appending leaves the standard library winning
every name collision, and `prepwright` is not a standard library name, so it
still resolves. Dropping `-I` was rejected because isolation is the property
that stops a user site-packages install from shadowing a standard library
import, and `PYTHONPATH` was rejected because `-I` implies `-E` and ignores it.

The decision itself stands. Nesting would need the same one line plus a deeper
tree, so the flat layout still costs less; it just does not cost nothing.

Integrity pinning becomes a manifest over `prepwright/*.py` plus `bridge.py` and
`index.html`, rather than a growing list of constants. That manifest does not exist
yet and the pins are currently empty, which the launcher reports out loud at every
start rather than treating as trusted.

There is no dependency resolution and no lockfile, because there are no
dependencies. The standard library is the whole surface.

A module that needs a third-party package cannot have one. If that ever becomes the
right answer, this decision is what has to change first.

## Alternatives rejected

Keeping one large `bridge.py`. It already resists change at its current size, and the
features still to be built are larger than the ones already there.

An installable package with `pip install -e .`. It breaks the no-install start and
the hash pinning, and buys packaging Prepwright will never use.
