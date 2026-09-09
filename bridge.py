#!/usr/bin/env python3
"""
Local bridge for Prepwright, an interview-preparation tutor.

What it does
------------
1. Serves only the tutor page's index.html (never progress, corpus, or state files).
2. Exposes POST /api/chat, which answers via a logged-in Claude or Codex CLI.
   Both providers run headlessly without tools. This bridge assembles the prompt
   itself from the active track's stored corpus, so a teaching turn can cite the
   source of a claim and can say "not in the corpus" instead of inventing one.
3. Exposes POST /api/assess, a one-call read of several step transcripts that
   grades how far the candidate actually got.
4. Exposes POST /api/review, a one-call read of one step's transcript that
   drafts the end-of-session review and its recap questions.
5. Exposes GET and POST /api/state. GET returns the page document rebuilt
   from the active track's database; POST takes a delta of appended turns
   and marks, never a whole document.
6. Exposes GET /api/health so the page can show a live/offline pill.

Security
--------
- NO API key is accepted by this server. Each CLI uses its existing local login.
- The spawned tutor has no model-invoked tools and runs from an empty
  temporary directory. Codex additionally runs read-only and ephemeral, with
  user config, rules, plugins, web, shell and multi-agent features disabled.
  File reading happens only in this process and is restricted to the active
  track's own corpus.
- The deliberately constructed prompt is sent to the selected cloud provider.
  No honest cloud-backed tutor can promise zero egress; the guarantee here is
  that unrelated files, other tracks, credentials and private artifacts are
  never included or made reachable to the model.
- The server binds to 127.0.0.1 only (localhost). It is not reachable from
  the network.
- Sensitive APIs require an HttpOnly per-launch session cookie, a custom header,
  and (when present) an exact Origin. A direct request must also carry an exact
  localhost Host; a request arriving through the tailnet proxy is pinned to the
  candidate's tailnet login instead, whatever Host it claims.

Run
---
    prep                                            # the launcher, or:
    cd ~/Desktop/Prepwright && python3 bridge.py    # then open http://localhost:8010/

Nothing here needs pip. Stop with Ctrl+C.

This file
---------
Since ADR 0007 it is the composition root and defines one function. The seven
behaviours above are implemented in the package beside it: routes and the
request boundary in prepwright/serve.py, the teaching turn and the stage ladder
in teach.py, grading and review in assess.py, the CLIs and the model roles in
provider.py, and every path and cap in config.py. Imports run one way; nothing
in the package imports this file.
"""
import os
import sys
from functools import partial
from http.server import ThreadingHTTPServer

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# `python3 -I` is documented to put NEITHER the script's directory NOR user
# site-packages on sys.path, so under the launcher's `-I -S` the flat
# prepwright/ package sitting beside this file is not importable at all.
# Verified on this machine rather than assumed: a probe under -I -S reported
# sys.path[0] as the stdlib zip and `from prepwright import ...` raised
# ModuleNotFoundError. This line is what the extraction in ADR 0001 rests on.
#
# APPEND, never insert. With this directory first, a file named json.py beside
# this one shadows the standard library, which was also verified. Appending
# leaves the stdlib winning on any name collision, while `prepwright` is not a
# stdlib name and still resolves.
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

# The composition root, and nothing else. Since ADR 0007 this file defines one
# function. Every constant it used to hold is in prepwright/config.py, every
# route is in prepwright/serve.py, and the four modules below own the rest.
#
# It is deliberately the only file in the tree that may import all of them, and
# deliberately not importable BY any of them: a module that reached back up here
# could not be loaded without also starting a server.
from prepwright import config as PC          # noqa: E402
from prepwright import provider as PPROV     # noqa: E402
from prepwright import security as PSEC      # noqa: E402
from prepwright import serve as PSERVE       # noqa: E402


def main():
    # Before the first request can arrive, so no save can interleave with the
    # import and no page can be served a document the import is still writing.
    track_id = PSERVE.current_track_id()
    imported = None
    try:
        imported = PSERVE.import_legacy_state()
    except Exception as exc:  # noqa: BLE001
        # A failed import must not stop the bridge: the old file is still on
        # disk, unrenamed, and the next start tries again.
        sys.stderr.write("prepwright: could not import %s (%s)\n"
                         % (PC.LEGACY_STATE_FILE, exc))
    handler = partial(PSERVE.Handler, directory=SCRIPT_DIR)
    httpd = ThreadingHTTPServer((PC.HOST, PC.PORT), handler)
    print("Prepwright bridge")
    print("  open      : http://localhost:%d/" % PC.PORT)
    if PSEC.REMOTE_ENABLED:
        # Loopback above is this Mac's URL and works in remote mode too; the
        # tailnet URL is the phone's, and this node cannot reach it itself.
        print("  iPhone    : https://%s/  (pinned to %s)"
              % (PSEC.TS_HOST, PSEC.TS_LOGIN))
    print("  claude    : %s" % (
        "ready" if PPROV._provider_ready("claude") else
        ("installed; login required" if PPROV.claude_bin() else "not found")))
    print("  codex     : %s" % (
        "ready" if PPROV._provider_ready("codex") else
        ("installed; login required" if PPROV.codex_bin() else "not found")))
    print("  track     : %s" % track_id)
    print("  progress  : %s (0600 files in 0700 directories; never web-served)"
          % os.path.join(PC.TRACKS_ROOT, track_id, "track.db"))
    if imported:
        print("  imported  : %d turns and %d marks from %s"
              % (imported.get("turns", 0), imported.get("marks", 0),
                 imported.get("source", "?")))
        if imported.get("renamed_to"):
            print("              the old file is kept at %s"
                  % imported["renamed_to"])
    print("  model I/O : selected tutoring context only; no model tools or file access")
    print("  stop      : Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        httpd.server_close()


if __name__ == "__main__":
    main()
