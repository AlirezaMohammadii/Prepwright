"""The request boundary: routes, the handler, and the track this process serves.

Everything the page can reach. `prepwright/security.py` decides whether a
request may proceed; this module decides what it means and answers it.

Also here, because it is per-process routing state rather than store state:
which track this bridge is currently serving. `current_track_id` resolves,
adopts or creates exactly one, under a lock so two requests arriving in the
same instant on a machine with no track cannot each create one and leave the
loser's writes in a track nothing will ever open again.

That block could not go in `prepwright/track.py`, which owns the lifecycle
transitions: `keep.py` already imports `track.py`, and `open_state_track` needs
`keep.open_track_or_recover`, so track importing keep would be a cycle. It
could not stay in bridge.py either, because `prepwright/assess.py` needed it
and nothing in this package imports bridge. The third option was the one taken:
`_persist_assessment` takes its opener as an argument, which left this as the
only consumer outside `main()` and put it here without a fifth module.

Two edits for a new POST route, not one: the whitelist in `do_POST` AND an
explicit block before the `/api/chat` fall-through, or its requests are
answered by the tutor.

Extracted from bridge.py in ADR 0007. The alias names came across with the
bodies unchanged. Import direction is one-way: nothing here is imported by
anything lower in the package, and nothing in this package imports bridge.
"""

import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler

from prepwright import assess as PASSESS
from prepwright import config as PC
from prepwright import corpus as PCORPUS
from prepwright import curriculum as PCURR
from prepwright import diagnose as PDIAG
from prepwright import ingest as PINGEST
from prepwright import intake as PINTAKE
from prepwright import keep as PK
from prepwright import pagestate as PS
from prepwright import provider as PPROV
from prepwright import research as PRESEARCH
from prepwright import security as PSEC
from prepwright import state as PSTATE
from prepwright import teach as PTEACH
from prepwright import track as PTRACK

# ---- constants that live in config.py --------------------------------------
APP_ID = PC.APP_ID
MAX_REQUEST_BYTES = PC.MAX_REQUEST_BYTES
MAX_STATE_BYTES = PC.MAX_STATE_BYTES
REQUEST_TIMEOUT = PC.REQUEST_TIMEOUT
SCRIPT_DIR = PC.SCRIPT_DIR
LEGACY_STATE_DIR = PC.LEGACY_STATE_DIR
LEGACY_STATE_FILE = PC.LEGACY_STATE_FILE

# ---- the surfaces this module routes to, aliased ----------------------------
# Short module-level names for what the handler reads directly, the way this
# file already aliases prepwright/security.py below, and for the same stated
# reason: renaming several hundred call sites for a file move is churn rather
# than a refactor.
EFFORT_LEVELS = PPROV.EFFORT_LEVELS
MODEL_DISABLED = PPROV.MODEL_DISABLED
PROVIDER_DEFAULTS = PPROV.PROVIDER_DEFAULTS
PROVIDER_LABELS = PPROV.PROVIDER_LABELS
PROVIDER_LOGIN_HINT = PPROV.PROVIDER_LOGIN_HINT
PROVIDER_MODELS = PPROV.PROVIDER_MODELS
_effort_flag = PPROV._effort_flag
_provider_bin = PPROV._provider_bin
_provider_ready = PPROV._provider_ready
_role_choice = PPROV._role_choice
_settings_view = PPROV._settings_view
_validate_provider_model = PPROV._validate_provider_model
_write_settings = PPROV._write_settings
claude_bin = PPROV.claude_bin
codex_bin = PPROV.codex_bin
run_cli = PPROV.run_cli

MAX_ASSESS_STEPS = PASSESS.MAX_ASSESS_STEPS
_persist_assessment = PASSESS._persist_assessment
assess_via_cli = PASSESS.assess_via_cli
review_via_cli = PASSESS.review_via_cli

chat_via_cli = PTEACH.chat_via_cli
evidence_pack = PTEACH.evidence_pack
flow_state = PTEACH.flow_state

# Redaction lives in prepwright.corpus, because the research path that fetches a
# page and the teaching path that reads one back both need the same rules, and
# two copies of a credential pattern drift.
_redact = PCORPUS.redact


SESSION_COOKIE = "prepwright_session"


SESSION_TOKEN = secrets.token_urlsafe(32)


MODEL_GATE = threading.BoundedSemaphore(1)


# Set by the test harness. A chooser is a modal window on a real screen,
# so a test that opened one would wait on a human, exactly as a test that
# called a model would spend the candidate's money.
DIALOG_DISABLED = os.environ.get("PREPWRIGHT_NO_DIALOG") == "1"


CURRENT_TRACK_FILE = os.path.join(PC.HOME, "CURRENT_TRACK")


DEFAULT_TRACK_TITLE = "Prepwright track"


# Resolution and creation both run under this, so two requests arriving in the
# same instant on a machine with no track yet cannot each create one and leave
# the loser's writes in a track nothing will ever open again.
_track_gate = threading.Lock()


def _read_current_track():
    """The track id this bridge is serving, or None. Never trusts the file."""
    try:
        with open(CURRENT_TRACK_FILE, "r", encoding="utf-8") as fh:
            candidate = fh.read().strip()
    except OSError:
        return None
    if not re.match(PC.TRACK_ID_RE, candidate):
        return None
    if not os.path.isdir(os.path.join(PC.TRACKS_ROOT, candidate)):
        return None
    return candidate


def _write_current_track(track_id):
    PSTATE.atomic_write(CURRENT_TRACK_FILE, track_id + "\n")


def current_track_id():
    """Resolve, or adopt, or create. Exactly one track is served at a time.

    A track switcher is not built yet, so the pointer file is the whole
    selection mechanism. When it is missing the newest active track is adopted
    rather than a second one created, because creating one beside real work is
    how a candidate opens the page and finds an empty session.
    """
    with _track_gate:
        found = _read_current_track()
        if found:
            return found
        PSTATE.ensure_home()
        lib = PSTATE.open_library()
        try:
            row = lib.execute(
                "SELECT track_id FROM track WHERE lifecycle='active'"
                " ORDER BY touched_utc DESC, created_utc DESC LIMIT 1").fetchone()
            track_id = row["track_id"] if row else None
        finally:
            lib.close()
        if track_id is None:
            track_id = PTRACK.create_track(DEFAULT_TRACK_TITLE)
        _write_current_track(track_id)
        return track_id


MAX_RESEARCH_URLS = 12


# Twelve hosts at the per-fetch ceiling is nine minutes. Handler.timeout bounds
# idle sockets and not a running handler, so nothing server-side would stop it:
# what this protects is the candidate, who otherwise watches a spinner with no
# way to tell a slow fetch from a hung one.
RESEARCH_BUDGET_SECONDS = 100


# Discovery is the slowest thing this bridge does: a search, then a fetch, per
# candidate, per gap. These three bound it. `DISCOVER_MAX_DOCS` is the one that
# matters for the plan rather than the wait: MAX_DOCS_PER_TRACK is 48, twenty
# gaps at four sources each would be eighty, and a corpus that fills up on the
# first eight gaps leaves the rest of the plan unteachable. Fewer, better sources
# per gap is not a compromise here, it is the only shape that fits.
DISCOVER_MAX_DOCS = 24


DISCOVER_BUDGET_SECONDS = 900


DISCOVER_PER_GAP = 3


def _trim_report(body, discarded):
    """Serialise a ledger entry, dropping whole discards until it fits.

    `research_run.queries` is charged against MARK_MAX_BYTES. Trimming the
    SERIALISED form to that many bytes produces text that is not JSON, and the
    only reader parses it, so the whole discard list disappears silently. This
    drops whole entries instead and records how many it dropped, because a
    ledger that quietly shows nothing is worse than one that says it is partial.

    One copy, two callers: a discovery run and a supplied file both write this
    column, and the second must not reintroduce the bug the first one fixed.
    """
    base = dict(body)
    kept = list(discarded)
    dropped = shed = 0
    while True:
        out = dict(base, discarded=kept)
        if dropped:
            out["discarded_not_recorded"] = dropped
        if shed:
            out["stored_not_recorded"] = shed
        text = json.dumps(out)
        if len(text.encode("utf-8")) <= PC.MARK_MAX_BYTES:
            return text
        if kept:
            # Halve, do not walk: a run can reject dozens and one-at-a-time
            # would re-serialise the whole list every time.
            cut = max(1, len(kept) // 2)
            kept = kept[:-cut]
            dropped += cut
            continue
        # The BASE body can exceed the cap on its own, which the original
        # `or not kept` exit returned unbounded: a supplied file embeds up to 24
        # `stored` entries, each a title of up to 200 characters plus
        # " (part i of N)", and 15 of those already serialise past 4 KiB. The
        # store then applied a character slice to finished JSON, the only reader
        # failed to parse it, and the card read "Nothing was rejected on this
        # run" for an ingest that dropped hundreds of sections. That is the
        # exact bug this function was written to fix.
        stored = base.get("stored")
        if isinstance(stored, list) and stored:
            cut = max(1, len(stored) // 2)
            base["stored"] = stored[:-cut]
            shed += cut
            continue
        # Nothing structured left to shed. Counts only: small, valid JSON, and
        # it still says what happened.
        return json.dumps({
            "kind": str(base.get("kind") or "")[:40],
            "file": str(base.get("file") or "")[:200],
            "error": str(base.get("error") or "")[:400] or None,
            "discarded": [], "discarded_not_recorded": dropped,
            "stored_not_recorded": shed,
            "note": "the full report did not fit the per-mark cap",
        })


def _discovery_report(provider, model, out):
    """The ledger entry for one discovery run."""
    return _trim_report({"kind": "discover", "provider": provider,
                         "model": model, "stored": out["stored"]},
                        out["discarded"])


def _file_report(report, error=None):
    """The ledger entry for one supplied file, stored or refused.

    A refused file still gets a row. "I pointed at that PDF and nothing
    happened" is the complaint this prevents: the ledger says the file was read,
    what the extractor was, and why it was refused.
    """
    if error:
        return _trim_report({"kind": "file", "file": report, "error": error}, [])
    return _trim_report({
        "kind": "file", "file": report["name"], "title": report["title"],
        "how": report["how"], "sections": report["sections"],
        "kept": report["kept"], "stored": report["stored"],
        "coverage": (report.get("coverage") or {}).get("ratio"),
        # What did NOT get stored. `ingest_file` breaks on the first cap refusal
        # and grouping sheds sections past one resource's share of the track;
        # both were invisible to the ledger and to the page.
        "failed": report.get("failed") or [],
        "overflow": int(report.get("overflow") or 0),
    }, report.get("dropped") or [])


def _switch_track(track_id):
    """Point this bridge at a track. Under the same lock that resolves one."""
    with _track_gate:
        _write_current_track(track_id)
    return track_id


def open_state_track(take_lease):
    """One handle for one request, repaired in the open path if it is corrupt.

    Per request rather than one held open for the process: sqlite3 refuses a
    connection used from a thread other than the one that made it, and this is a
    threading server. The cost is one connection open per save, against
    relaxing check_same_thread for the whole package to suit one caller.

    open_track_or_recover, not open_track: the moment a corrupt track.db is
    found is the moment the candidate is waiting on it, and this is the call
    keep.py built that path for.
    """
    track_id = current_track_id()
    handle = PK.open_track_or_recover(track_id, client_label="bridge")
    if not take_lease and handle.holds_lease:
        # A read must not hold the track: it would make an archive or a delete
        # refuse for as long as the page is merely open.
        try:
            PSTATE.release_lease(handle._library(), track_id)
        except Exception:
            pass
        handle.holds_lease = False
    return handle


def state_snapshot(handle):
    """Everything the page needs to render and to compute its next delta."""
    return {
        "ok": True,
        "trackId": handle.track_id,
        "revision": handle.revision(),
        "state": PS.materialise(handle),
        "fields": {f: list(v) for f, v in PS.FIELDS.items()},
    }


def _legacy_document():
    """The old whole-document state, from the file or the newest good snapshot.

    The snapshots are read too. `read_state()` used to fall back to them, so
    skipping them here would make the migration lose exactly the history that
    the old recovery path existed to keep.
    """
    try:
        with open(LEGACY_STATE_FILE, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        if isinstance(obj, dict):
            return obj, LEGACY_STATE_FILE
    except (OSError, ValueError):
        pass
    backups = os.path.join(LEGACY_STATE_DIR, "backups")
    try:
        names = sorted(n for n in os.listdir(backups)
                       if n.startswith("state-") and n.endswith(".json"))
    except OSError:
        return None, None
    for name in reversed(names):
        path = os.path.join(backups, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            if isinstance(obj, dict):
                return obj, path
        except (OSError, ValueError):
            continue
    return None, None


def import_legacy_state():
    """Read progress/state.json once, write it as ops, then rename it.

    Renamed, never deleted: if this import is wrong in a way nobody notices for
    a week, the bytes are still there. Called once from main(), before the
    server accepts a request, so no write can interleave with it.
    """
    document, source = _legacy_document()
    if document is None:
        return None
    handle = open_state_track(take_lease=True)
    try:
        ops = PS.validate_ops(PS.ops_from_document(document, "legacy"))
        report = PS.apply_ops(handle, ops)
        report["track"] = handle.track_id
        report["source"] = source
    finally:
        handle.close()
    if os.path.isfile(LEGACY_STATE_FILE):
        moved = "%s.imported-%s" % (LEGACY_STATE_FILE,
                                    time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
        os.rename(LEGACY_STATE_FILE, moved)
        report["renamed_to"] = moved
    return report


# The request boundary lives in `prepwright/security.py`. These aliases stay
# because the handler below reads the short names directly, and renaming every
# call site for a file move is churn rather than a refactor.
LOCAL_ORIGINS = PSEC.LOCAL_ORIGINS


LOCAL_HOSTS = PSEC.LOCAL_HOSTS


ALLOWED_HOSTS = PSEC.ALLOWED_HOSTS


TS_HOST = PSEC.TS_HOST


TS_LOGIN = PSEC.TS_LOGIN


REMOTE_ENABLED = PSEC.REMOTE_ENABLED


REMOTE_HOSTS = PSEC.REMOTE_HOSTS


REMOTE_ORIGINS = PSEC.REMOTE_ORIGINS


_is_remote_request = PSEC._is_remote_request


_origins_for = PSEC._origins_for


_allowed_host = PSEC._allowed_host


_remote_identity_ok = PSEC._remote_identity_ok


_safe_messages = PSEC._safe_messages


_safe_assess_items = PSEC._safe_assess_items


_static_route_allowed = PSEC._static_route_allowed


class Handler(SimpleHTTPRequestHandler):
    server_version = "TutorBridge"
    sys_version = ""
    # One thread per connection with no read deadline lets any local process
    # open sockets, dribble headers and pin threads forever. A model call does
    # not touch the socket while it runs, so this bounds idle connections only.
    timeout = 60

    # keep the console quiet and never echo request bodies (which contain chat text)
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # A browser opening http://127.0.0.1:8010 often probes https:// first, so a
    # TLS ClientHello arrives on a plaintext port. parse_request cannot read it
    # and logs the raw bytes, which fills the launcher log with binary garbage
    # and buries the lines that matter. The probe is normal and the 400 reply is
    # correct; only the log line is noise. Nothing else is suppressed.
    _PROBE_NOISE = ("Bad request version", "Bad HTTP/0.9 request type",
                    "Bad request syntax")

    def log_error(self, fmt, *args):
        try:
            rendered = fmt % args
        except Exception:                                    # noqa: BLE001
            rendered = str(fmt)
        if any(marker in rendered for marker in self._PROBE_NOISE):
            return
        self.log_message("%s", rendered)

    def log_request(self, code="-", size="-"):
        # A TLS ClientHello on a plaintext port is not a request line, and the
        # base class echoes the raw bytes here, not through log_error. Printing
        # them dumps binary into the launcher log on every page load and buries
        # the lines that matter. A request line with control bytes in it cannot
        # be a real HTTP request, so it is counted and dropped rather than
        # echoed. Everything with a printable request line still logs.
        line = self.requestline or ""
        if any(ch < " " or ch == "\x7f" for ch in line):
            self._tls_probes = getattr(self, "_tls_probes", 0) + 1
            return
        SimpleHTTPRequestHandler.log_request(self, code, size)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'; object-src 'none'",
        )
        super().end_headers()

    def _json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if origin and origin in _origins_for(self.headers):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        # A HEAD response carries headers and nothing else. do_HEAD runs the
        # host check first, and a rejection there returns through this method.
        if self.command != "HEAD":
            self.wfile.write(body)

    def _reject_bad_host(self):
        # A request that came through the tailnet proxy must be the candidate,
        # before ANYTHING is served — including the static page, because the
        # page hands out the session cookie. This is what keeps an accidental
        # `tailscale funnel` (public, no identity header) and any other tailnet
        # user from ever seeing the app at all.
        #
        # The pin is checked FIRST and independently of Host, so no choice of
        # Host header can skip it. Once a request has proved it is the candidate
        # arriving over WireGuard + TLS, the Host it asked for adds nothing: the
        # allowlist exists to stop DNS rebinding against a browser, and rebinding
        # cannot produce a header `tailscale serve` refuses to forward. Skipping
        # it here is what lets any MagicDNS alias of this node work.
        if _is_remote_request(self.headers):
            if not _remote_identity_ok(self.headers):
                self._json(403, {"error": "Tutor remote access is limited to the candidate."})
                return True
            return False
        if not _allowed_host(self.headers.get("Host", "")):
            self._json(421, {"error": "Local Tutor host rejected."})
            return True
        return False

    def _cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, sep, value = part.strip().partition("=")
            if sep and name == SESSION_COOKIE:
                return value
        return ""

    def _authorized(self):
        if self.headers.get("X-Tutor-Bridge") != "1":
            return False
        token = self._cookie_token()
        if not token or not secrets.compare_digest(token, SESSION_TOKEN):
            return False
        expected = _origins_for(self.headers)
        origin = self.headers.get("Origin", "")
        if origin and origin not in expected:
            return False
        referer = self.headers.get("Referer", "")
        if referer and not any(referer == o + "/" or referer.startswith(o + "/")
                               for o in expected):
            return False
        fetch_site = self.headers.get("Sec-Fetch-Site", "")
        if fetch_site and fetch_site not in ("same-origin", "none"):
            return False
        return True

    def _serve_index(self):
        try:
            with open(os.path.join(SCRIPT_DIR, "index.html"), "rb") as stream:
                body = stream.read()
        except OSError:
            return self._json(500, {"error": "Tutor page is unavailable."})
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, must-revalidate")
        # Same token both ways; only the attribute differs. The tailnet path is
        # TLS end to end (serve terminates it), so the cookie is marked Secure
        # there. The plain loopback path keeps the plain cookie — marking it
        # Secure would break Safari on http://localhost.
        secure = "; Secure" if _is_remote_request(self.headers) else ""
        self.send_header(
            "Set-Cookie", "%s=%s; HttpOnly; SameSite=Strict; Path=/%s" %
            (SESSION_COOKIE, SESSION_TOKEN, secure))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self, limit=MAX_REQUEST_BYTES):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked bodies are not accepted.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Bad Content-Length.") from exc
        if length <= 0:
            raise ValueError("Empty request body.")
        if length > limit:
            raise OverflowError("Request body is too large.")
        if "application/json" not in (self.headers.get("Content-Type") or "").lower():
            raise ValueError("Content-Type must be application/json.")
        try:
            obj = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Request body is not valid JSON.") from exc
        if not isinstance(obj, dict):
            raise ValueError("Request body must be an object.")
        return obj

    def _provider(self, payload, require_model=True):
        provider = str(payload.get("provider") or "claude").strip().lower()
        model = str(payload.get("model") or
                    PROVIDER_DEFAULTS.get(provider, "")).strip()
        if require_model:
            provider, model = _validate_provider_model(provider, model)
        elif provider not in PROVIDER_MODELS:
            raise ValueError("Unknown tutor provider.")
        # Name the provider and the fix. "Provider is unavailable" points the
        # candidate at the wrong place: "install codex" and "sign in to claude"
        # are different problems with different one-line remedies.
        if MODEL_DISABLED:
            # Checked here as well as in run_cli so a route reports the real
            # reason instead of "the model did not answer", which would send
            # someone looking for a network fault that is not there.
            raise RuntimeError(
                "Model calls are disabled in this process by"
                " PREPWRIGHT_NO_MODEL, so no provider can answer.")
        if not _provider_bin(provider):
            raise RuntimeError(
                "The %s CLI is not installed on this machine, so its models "
                "cannot answer. Pick another provider in the model menu."
                % PROVIDER_LABELS[provider])
        if not _provider_ready(provider):
            raise RuntimeError(
                "The %s CLI is installed but not logged in. Run `%s` in a "
                "terminal, sign in, then send this again."
                % (PROVIDER_LABELS[provider], PROVIDER_LOGIN_HINT[provider]))
        return provider, model

    def _failure(self, status, label, exc):
        # The browser gets a reference id and nothing else. The terminal is the
        # candidate's own machine, so it gets the reason. Logging only the
        # exception class turns "the CLI cannot see your login" into an opaque
        # reference id, which is how a one-line environment bug hides for weeks.
        ref = secrets.token_hex(4)
        detail = " ".join(_redact(str(exc)).split())[:400]
        sys.stderr.write("tutor: %s failed [%s] %s: %s\n" %
                         (label, ref, type(exc).__name__, detail or "(no detail)"))
        return self._json(status, {
            "error": "%s failed (reference %s). Check the Tutor terminal." %
                     (label, ref),
        })

    def do_GET(self):
        route = self.path.split("?")[0]
        if self._reject_bad_host():
            return
        if route == "/api/health":
            claude_installed, codex_installed = bool(claude_bin()), bool(codex_bin())
            claude_ready = _provider_ready("claude") if claude_installed else False
            codex_ready = _provider_ready("codex") if codex_installed else False
            return self._json(200, {
                "ok": True,
                "app": APP_ID,
                # Whether THIS caller's session cookie is the one this process
                # minted. The route stays outside the session gate on purpose:
                # the launcher reads it over loopback to tell a remote-capable
                # bridge from a local-only one, and gating it would break that.
                # But answering 200 to a page whose cookie died with the last
                # process left the live pill green while every gated route
                # returned 403, so the app looked healthy and did nothing.
                # Reporting it exposes nothing the caller does not already know.
                "session": self._authorized(),
                # Whether the tailnet (iPhone) proxy path is enabled in THIS
                # process. The launcher reads it over loopback to tell a
                # remote-capable bridge from a local-only one; remote mode is
                # decided once at import and cannot be changed in place, so a
                # running bridge that reports false has to be restarted before
                # a tailnet handler is pointed at it. No identity is exposed.
                "remote": REMOTE_ENABLED,
                "claude": claude_ready,
                "providers": {
                    "claude": {"available": claude_ready,
                               "installed": claude_installed,
                               "models": list(PROVIDER_MODELS["claude"])},
                    "codex": {"available": codex_ready,
                              "installed": codex_installed,
                              "models": list(PROVIDER_MODELS["codex"])},
                },
                "efforts": list(EFFORT_LEVELS),
            })
        if route == "/api/settings":
            # Behind the session gate although it exposes no secret: a write
            # lives on the same path, and a reader that is not this page has no
            # business learning which models this machine is configured to bill.
            if not self._authorized():
                return self._json(403, {"error": "Tutor session authorization required."})
            return self._json(200, _settings_view())
        if route == "/api/state":
            if not self._authorized():
                return self._json(403, {"error": "Tutor session authorization required."})
            try:
                handle = open_state_track(take_lease=False)
            except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
                return self._failure(500, "Progress load", exc)
            try:
                snapshot = state_snapshot(handle)
            except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
                return self._failure(500, "Progress load", exc)
            finally:
                handle.close()
            return self._json(200, snapshot)
        if route == "/api/flow":
            if not self._authorized():
                return self._json(403, {"error": "Tutor session authorization required."})
            try:
                handle = open_state_track(take_lease=False)
            except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
                return self._failure(500, "Flow", exc)
            try:
                return self._json(200, flow_state(handle))
            except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
                return self._failure(500, "Flow", exc)
            finally:
                handle.close()
        if route == "/api/ledger":
            if not self._authorized():
                return self._json(403, {"error": "Tutor session authorization required."})
            try:
                handle = open_state_track(take_lease=False)
            except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
                return self._failure(500, "Ledger", exc)
            try:
                return self._route_ledger(handle, {})
            except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
                return self._failure(500, "Ledger", exc)
            finally:
                handle.close()
        if route == "/api/tracks":
            if not self._authorized():
                return self._json(403, {"error": "Tutor session authorization required."})
            current = current_track_id()
            lib = PSTATE.open_library()
            try:
                rows = lib.execute(
                    "SELECT track_id, title, employer, role_title, created_utc,"
                    "       touched_utc FROM track WHERE lifecycle='active'"
                    " ORDER BY touched_utc DESC, created_utc DESC").fetchall()
            finally:
                lib.close()
            return self._json(200, {
                "current": current,
                "tracks": [{k: r[k] for k in r.keys()} for r in rows]})
        if route == "/favicon.ico":
            # 204, not a file. The page ships no icon asset, and an unanswered
            # favicon is a console error on every load that makes "zero console
            # errors" a claim with a caveat attached. No body, so nothing is
            # served that the CSP would have to allow.
            self.send_response(204)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if _static_route_allowed(route):
            return self._serve_index()
        return self._json(404, {"error": "Not found."})

    def do_OPTIONS(self):
        if self._reject_bad_host():
            return
        if self.path.startswith("/api/"):
            self.send_response(204)
            origin = self.headers.get("Origin", "")
            if origin and origin in _origins_for(self.headers):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Tutor-Bridge")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_HEAD(self):
        # Never fall back to SimpleHTTPRequestHandler.send_head(), which would
        # reveal metadata for bridge.py or private progress paths.
        if self._reject_bad_host():
            return
        self.send_response(405)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _save_state(self):
        """Apply one delta. The page sends what changed, never the document.

        Three outcomes, and the difference between the last two is the whole
        point of the change. An update against a stale base is not a conflict:
        every op is an append, so it applies, and the reply carries the merged
        document for the page to fold in. A *replace* against a stale base is
        refused, because replacing the world from a view that is behind it is
        the one write that can subtract.
        """
        try:
            payload = self._read_json_body(MAX_STATE_BYTES)
        except OverflowError:
            return self._json(413, {"error": "That change is too large to save."})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        if not isinstance(payload.get("ops"), list):
            return self._json(400, {"error": "No changes to save."})
        try:
            ops = PS.validate_ops(payload["ops"])
        except PS.OpRejected as exc:
            return self._json(400, {"error": str(exc)})
        base = payload.get("revision")
        replacing = payload.get("intent") == "replace"

        try:
            handle = open_state_track(take_lease=True)
        except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
            return self._failure(500, "Progress save", exc)
        try:
            wanted = payload.get("trackId")
            if wanted is not None and wanted != handle.track_id:
                # The page is holding a different track than this bridge serves.
                # Hand back what is really open rather than writing one track's
                # work into another's file.
                out = state_snapshot(handle)
                out["ok"] = False
                out["regression"] = True
                out["error"] = ("This page is holding a different track."
                                " Merged with the one that is open.")
                return self._json(409, out)

            stale = base is not None and base != handle.revision()
            if stale and replacing:
                out = state_snapshot(handle)
                out["ok"] = False
                out["regression"] = True
                out["error"] = ("Refused a replace from a view that is behind"
                                " what is saved. Merged instead.")
                return self._json(409, out)

            report = PS.apply_ops(handle, ops)
            # Every fact the step table derives from arrives through this one
            # funnel: a topic tick, a grade, a session review. Reconciling here
            # is what finally makes step.status real, and with it the transcript
            # cap recoverable, the assessment table reachable and
            # flow.curriculum.done a number rather than a permanent 0.
            #
            # A reconcile failure must not fail the save. The candidate's words
            # are already committed by this point, and refusing the response
            # would make the page retry a delta that has already applied.
            try:
                report["lifecycle"] = handle.sync_step_lifecycle()
            except (PSTATE.StoreError, sqlite3.Error) as exc:
                sys.stderr.write("tutor: step lifecycle not reconciled: %s\n" % exc)
            out = {"ok": True, "trackId": handle.track_id,
                   "revision": handle.revision(), "applied": report,
                   "stale": bool(stale)}
            if stale:
                # Everything this page has not seen, so it can fold it in and
                # carry on instead of being stranded on a stale document.
                out["state"] = PS.materialise(handle)
                out["fields"] = {f: list(v) for f, v in PS.FIELDS.items()}
            return self._json(200, out)
        except PS.OpRejected as exc:
            return self._json(400, {"error": str(exc)})
        except PSTATE.CapExceeded as exc:
            return self._json(507, {"error": str(exc)})
        except PSTATE.TrackMoved as exc:
            return self._json(409, {"error": str(exc), "revision": None})
        except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
            return self._failure(500, "Progress save", exc)
        finally:
            handle.close()

    # ---- the pipeline routes ------------------------------------------
    def _pipeline(self, route, payload):
        """intake, diagnose, gap, research, curriculum, track.

        One method because they share every rule: validate the client's fields
        here and never trust them, open one handle for the request and close it
        in a finally, refuse by name with a 400 the candidate can act on, and
        turn a store fault into a 500 with a reference id rather than a stack
        trace in a browser.

        None of these calls a model. The judgement inside `diagnose` and the
        prerequisite edges inside `curriculum` both have deterministic
        fallbacks, so the whole pipeline runs with the provider unavailable and
        says which parts were graded without one.
        """
        try:
            if route == "/api/intake":
                return self._route_intake(payload)
            if route == "/api/track":
                return self._route_track(payload)
        except (PINTAKE.IntakeRefused, PRESEARCH.FetchRefused, ValueError) as exc:
            return self._json(400, {"error": str(exc)})
        except PRESEARCH.FetchFailed as exc:
            return self._json(502, {"error": str(exc)})
        except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
            return self._failure(500, "Intake", exc)

        try:
            handle = open_state_track(take_lease=True)
        except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
            return self._failure(500, "Track open", exc)
        try:
            if route == "/api/diagnose":
                return self._route_diagnose(handle, payload)
            if route == "/api/gap":
                return self._route_gap(handle, payload)
            if route == "/api/research":
                return self._route_research(handle, payload)
            if route == "/api/curriculum":
                return self._route_curriculum(handle, payload)
            return self._json(404, {"error": "Not found."})
        except (PDIAG.DiagnoseRefused, PCURR.CurriculumRefused,
                PRESEARCH.FetchRefused, PINGEST.IngestRefused, ValueError) as exc:
            return self._json(400, {"error": str(exc)})
        except PRESEARCH.FetchFailed as exc:
            return self._json(502, {"error": str(exc)})
        except PSTATE.CapExceeded as exc:
            return self._json(507, {"error": str(exc)})
        except PSTATE.TrackMoved as exc:
            return self._json(409, {"error": str(exc)})
        except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
            return self._failure(500, route.split("/")[-1].title(), exc)
        except RuntimeError as exc:
            # LAST, and the position is load-bearing. `StoreError` subclasses
            # RuntimeError, so this clause placed any earlier swallows
            # TrackMoved, CapExceeded, IsolationError and CorruptStore and
            # answers 400 to all of them -- which is how a stale-revision
            # conflict stopped being a 409 the page can act on. Everything the
            # store raises is handled above; what is left here is `_provider`
            # refusing because a CLI is missing, logged out or disabled, and
            # before this existed that escaped the handler with no reply written
            # at all, leaving the browser on an open socket with nothing on
            # screen and nothing in the log.
            return self._json(400, {"error": str(exc)})
        finally:
            handle.close()

    def _route_intake(self, payload):
        """Build a track from a URL or from pasted text. Both are first-class.

        The URL path fetches through `research`, which is the only module with
        network access, so this route inherits its whole SSRF guard rather than
        carrying a second copy. A page that answers but yields no posting body
        comes back as a 422 carrying whatever identity was readable, so the page
        can open the paste box with the employer and role already filled in
        instead of showing a failure the candidate cannot act on.
        """
        url = str(payload.get("url") or "").strip()[:2000]
        text = str(payload.get("text") or "")[:PC.INTAKE_MAX_BYTES * 2]
        folder = payload.get("applicationFolder")
        folder = str(folder)[:1000] if isinstance(folder, str) and folder.strip() else None
        if not url and not text.strip():
            raise ValueError("Give me a job link or paste the description.")

        if text.strip():
            posting = PINTAKE.posting_from_text(
                text,
                employer=str(payload.get("employer") or "")[:200],
                role_title=str(payload.get("roleTitle") or "")[:200],
                location=str(payload.get("location") or "")[:200],
                url=url,
                source_kind=("freeform" if payload.get("freeform") else "pasted"))
        else:
            posting = PINTAKE.posting_from_url(url)
            if not posting["confident"]:
                return self._json(422, {
                    "error": posting["note"],
                    "needsPaste": True,
                    "employer": posting.get("employer"),
                    "roleTitle": posting.get("role_title"),
                    "location": posting.get("location"),
                    "finalUrl": posting.get("final_url"),
                })

        app = PINTAKE.read_application_folder(folder) if folder else None
        track_id, report = PINTAKE.create_from_posting(posting, application=app)
        _switch_track(track_id)
        handle = open_state_track(take_lease=False)
        try:
            report["flow"] = flow_state(handle)
        finally:
            handle.close()
        return self._json(200, report)

    def _route_track(self, payload):
        """Switch which track this bridge serves."""
        track_id = str(payload.get("trackId") or "").strip()
        if not re.match(PC.TRACK_ID_RE, track_id):
            raise ValueError("That is not a track id.")
        lib = PSTATE.open_library()
        try:
            row = lib.execute(
                "SELECT lifecycle FROM track WHERE track_id=?",
                (track_id,)).fetchone()
        finally:
            lib.close()
        if row is None:
            raise ValueError("There is no track %s." % track_id)
        if row["lifecycle"] != "active":
            raise ValueError("Track %s is %s, so it cannot be opened."
                             % (track_id, row["lifecycle"]))
        _switch_track(track_id)
        handle = open_state_track(take_lease=False)
        try:
            return self._json(200, {"ok": True, "flow": flow_state(handle)})
        finally:
            handle.close()

    def _route_diagnose(self, handle, payload):
        """Build the probe plan, or turn answers into a proposed gap list.

        Two actions rather than two routes, because they are one conversation:
        `plan` is the questions, `propose` is what the answers imply. Proposing
        twice is refused rather than appending a second list, since gap ids are
        a primary key and the second call would fail halfway through with rows
        from the first still in place.
        """
        action = str(payload.get("action") or "plan")
        intake = handle.intake()
        if intake is None:
            raise ValueError("There is no posting on this track yet.")
        requirements = PDIAG.requirements_from_posting(intake["body"])
        fit = self._fit_report_for(handle)
        limit = payload.get("limit")
        limit = int(limit) if isinstance(limit, int) and 1 <= limit <= 60 else 24
        plan, cut = PDIAG.probe_plan(requirements, fit, limit=limit)

        if action == "plan":
            return self._json(200, {
                "probes": plan, "cut": cut,
                "requirements": len(requirements),
                "fitReport": bool(fit and fit.get("rows")),
            })
        if action != "propose":
            raise ValueError("Unknown diagnose action.")
        if PDIAG.gap_list(handle):
            raise ValueError(
                "This track already has a gap list. Decide the gaps you have"
                " before proposing more.")

        raw = payload.get("answers")
        answers = {}
        if isinstance(raw, dict):
            for key, value in list(raw.items())[:200]:
                if isinstance(key, str) and isinstance(value, str):
                    answers[key[:16]] = value[:4000]
        # Grading, in three tiers of preference.
        #
        # A caller may pass verdicts it graded itself, which is what the tests
        # use. Otherwise the judge runs here, in the route, exactly as `assess`
        # does: `diagnose` stays free of the provider so it can be tested without
        # one, and the one place that knows how to reach a CLI keeps knowing it.
        # If the judge cannot run -- no CLI, not logged in, a refusal -- the
        # length-only fallback still produces a plan, and the reply SAYS which
        # of the three graded it. A pessimistic grade quietly presented as a real
        # one is how a candidate ends up studying twenty things they already
        # know.
        verdicts = payload.get("verdicts")
        graded_by = "verdicts supplied by the caller"
        if not isinstance(verdicts, list):
            verdicts, graded_by = self._judge(plan, answers, payload)
        rows = PDIAG.proposals_from(plan, verdicts)
        PDIAG.propose(handle, rows)
        return self._json(200, {
            "proposed": len(rows), "gradedBy": graded_by,
            "gaps": PDIAG.gap_list(handle),
            "summary": PDIAG.summary(handle),
            "flow": flow_state(handle),
        })

    def _judge(self, plan, answers, payload):
        """Grade the probes with a model, or say plainly why it could not.

        Returns (verdicts, how_it_was_graded). Never raises: a diagnostic that
        fails because a CLI is logged out should still produce a gap list, just a
        pessimistic one that admits it.

        Two details this had to get right, both of which would fail silently.
        `run_cli` wants the schema as a JSON STRING while JUDGE_SCHEMA is a dict,
        so it is dumped here. And `_pipeline`, which owns this route, does not
        catch RuntimeError, which is exactly what `_provider` raises when the CLI
        is missing or logged out -- so it is caught here rather than becoming a
        500 on the one route that has a working fallback.
        """
        if not plan:
            return [], "there were no probes to grade"
        try:
            provider, _model = self._provider(payload, require_model=False)
        except (ValueError, RuntimeError) as exc:
            return (PDIAG.verdicts_without_a_model(plan, answers),
                    "length only (%s), so nothing was graded better than shaky"
                    % str(exc)[:120])
        model, effort = _role_choice(provider, "judge")
        if not MODEL_GATE.acquire(blocking=False):
            return (PDIAG.verdicts_without_a_model(plan, answers),
                    "length only (a model call was already running), so nothing"
                    " was graded better than shaky")
        try:
            data = run_cli(provider, model, PDIAG.JUDGE_SYSTEM,
                           PDIAG.judge_prompt(plan, answers), effort=effort,
                           schema=json.dumps(PDIAG.JUDGE_SCHEMA))
            parsed = json.loads(data.get("result") or "{}")
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
            sys.stderr.write("diagnostic judge failed: %s\n" % str(exc)[:160])
            return (PDIAG.verdicts_without_a_model(plan, answers),
                    "length only (the judge did not answer), so nothing was"
                    " graded better than shaky")
        finally:
            try:
                MODEL_GATE.release()
            except ValueError:
                pass
        got = parsed.get("verdicts")
        if not isinstance(got, list) or not got:
            return (PDIAG.verdicts_without_a_model(plan, answers),
                    "length only (the judge returned nothing usable), so nothing"
                    " was graded better than shaky")
        return got, "%s %s" % (PROVIDER_LABELS[provider], model)

    def _fit_report_for(self, handle):
        """The imported fit report, read from this track's own intake directory.

        Never from the resume folder. That folder was read once at track
        creation and hashed; reopening it here would let an edit on the other
        side change a diagnostic that has already been run.
        """
        intake_dir = os.path.join(handle.dir, "intake")
        try:
            names = sorted(n for n in os.listdir(intake_dir)
                           if n.endswith("_FitReport.md"))
        except OSError:
            return None
        for name in names:
            path = os.path.join(intake_dir, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    return PDIAG.claims_from_fit_report(fh.read(512 * 1024))
            except OSError:
                continue
        return None

    def _route_gap(self, handle, payload):
        """One decision on one gap. The candidate is the only thing that opens
        the gate, so this is the only route that can approve anything."""
        gap_id = str(payload.get("gapId") or "").strip()[:40]
        status = str(payload.get("status") or "").strip()
        if not gap_id:
            raise ValueError("Which gap?")
        if status not in ("approved", "declined"):
            raise ValueError("A gap is either approved or declined.")
        rev = payload.get("rev")
        rev = int(rev) if isinstance(rev, int) else None
        if status == "approved":
            PDIAG.approve(handle, gap_id, expected_rev=rev)
        else:
            PDIAG.decline(handle, gap_id, expected_rev=rev)
        return self._json(200, {
            "ok": True, "gaps": PDIAG.gap_list(handle),
            "summary": PDIAG.summary(handle), "flow": flow_state(handle)})

    def _route_research(self, handle, payload):
        """Add sources to this track. Three actions, one gate.

        `discover` lets the app go looking; `quarantine` distrusts something it
        found; the default fetches URLs the candidate pasted. All three end at
        the same place: bytes enter the corpus only after `research.fetch` has
        read them through the SSRF guard, so a nominated URL and a pasted URL get
        exactly the same treatment. Nothing here trusts a model's assertion about
        a page, only the page.

        Actions live here rather than on new POST paths on purpose. `/api/chat`
        is the residual branch at the end of do_POST, so every new path is a
        chance to have the tutor answer a pipeline request; an action on a route
        that is already allowlisted and already dispatched cannot make that
        mistake.

        One URL failing does not fail the batch. Each result carries its own
        outcome so the page can show which sources landed and which did not.
        """
        action = str(payload.get("action") or "fetch").strip().lower()
        if action == "discover":
            return self._route_discover(handle, payload)
        if action == "quarantine":
            return self._route_quarantine(handle, payload)
        if action == "pick_file":
            return self._route_pick(handle, payload)
        if action in ("preview_file", "ingest_file"):
            return self._route_file(handle, payload, commit=(action == "ingest_file"))
        if action not in ("fetch", ""):
            raise ValueError("Unknown research action %r." % action)
        raw = payload.get("urls")
        if not isinstance(raw, list) or not raw:
            raise ValueError("Paste at least one https link to a source you trust.")
        urls, seen = [], set()
        for item in raw[:MAX_RESEARCH_URLS]:
            url = str(item or "").strip()[:2000]
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
        if not urls:
            raise ValueError("None of those were usable links.")
        vetting = str(payload.get("vetting") or "secondary")
        if vetting not in ("primary", "secondary", "vendor", "community"):
            vetting = "secondary"
        trust = payload.get("trust")
        trust = int(trust) if isinstance(trust, int) and 1 <= trust <= 5 else 3

        # Bounded across the batch, not only per URL, so a slow set returns the
        # sources that did land instead of holding the page indefinitely. What
        # the deadline stops is reported, never dropped in silence.
        deadline = time.time() + RESEARCH_BUDGET_SECONDS
        results = []
        for url in urls:
            if time.time() > deadline:
                results.append({"url": url, "ok": False,
                                "why": "the batch ran out of time before this one;"
                                       " add it on its own"})
                continue
            try:
                got = PRESEARCH.fetch(url)
                text = PRESEARCH.text_of(got)
                doc_id = PCORPUS.ingest_text(
                    handle, text, origin_url=got["asked_url"],
                    vetting=vetting, trust=trust, final_url=got["final_url"])
                if doc_id is None:
                    results.append({"url": url, "ok": False,
                                    "why": "nothing citable: the page has no headings"})
                else:
                    results.append({"url": url, "ok": True, "docId": doc_id,
                                    "finalUrl": got["final_url"],
                                    "bytes": got["bytes"],
                                    "sha256": got["sha256"],
                                    "fetchedUtc": got["fetched_utc"]})
            except (PRESEARCH.FetchRefused, PRESEARCH.FetchFailed) as exc:
                results.append({"url": url, "ok": False, "why": str(exc)})
            except PSTATE.CapExceeded as exc:
                results.append({"url": url, "ok": False, "why": str(exc)})
        return self._json(200, {
            "results": results,
            "stored": sum(1 for r in results if r["ok"]),
            "flow": flow_state(handle)})

    def _route_discover(self, handle, payload):
        """Go and find sources for the approved gaps, then prove each one.

        The one route in this bridge whose model call may search. What comes back
        is a list of URLs and nothing else: `research.discover` fetches each one
        through the same guard a pasted link goes through, checks that the page
        actually shares vocabulary with the gap it was sought for, and only then
        is anything written. A nomination that 404s, redirects into private
        space, serves a login wall or answers about the wrong subject is
        discarded with its reason recorded on the run.

        The run row is the ledger. `doc.run_id` points every stored document back
        at the pass that looked for it, and the discards live in the run's
        `queries` column, so the question "what did it throw away, and why" has
        an answer on disk rather than in a log line that scrolled past.
        """
        gaps = [g for g in PDIAG.gap_list(handle)
                if g["status"] in PCURR.WORKABLE]
        if not gaps:
            raise ValueError(
                "Nothing is approved yet, so there is nothing to research."
                " Decide the gap list first.")

        provider, _model = self._provider(payload, require_model=False)
        model, effort = _role_choice(provider, "discover")
        only = payload.get("gapIds")
        if isinstance(only, list) and only:
            wanted = {str(x) for x in only[:60]}
            gaps = [g for g in gaps if g["gap_id"] in wanted]
            if not gaps:
                raise ValueError("None of those gap ids are approved on this track.")

        # Skip the gaps this track can already teach. `plan` defers a gap whose
        # corpus turns up nothing relevant, and that deferral list IS the
        # research list -- the curriculum is the thing that knows what is
        # missing. Scoring here with the same functions means a second run tops
        # up what the first could not find instead of buying a second copy of
        # what it did, and running discovery twice is idempotent rather than
        # wasteful.
        covered = set()
        if not payload.get("all"):
            index = PCURR.corpus_index(handle)
            if index:
                df = PCURR.document_frequency(index)
                for g in gaps:
                    query = "%s %s" % (g.get("label") or "", g.get("why") or "")
                    if PCURR.relevant(PCURR.score_sections(index, query, df=df)):
                        covered.add(g["gap_id"])
        skipped = [g["gap_id"] for g in gaps if g["gap_id"] in covered]
        gaps = [g for g in gaps if g["gap_id"] not in covered]
        if not gaps:
            return self._json(200, {
                "runId": None, "stored": 0, "gaps": [], "discarded": [],
                "asked": 0, "skipped": skipped,
                "note": "Every approved gap already has a source in this track's"
                        " corpus. Nothing needed fetching.",
                "flow": flow_state(handle)})

        already = handle.conn.execute(
            "SELECT COUNT(*) c FROM doc WHERE status='ready'").fetchone()["c"]
        room = max(0, min(DISCOVER_MAX_DOCS, PC.MAX_DOCS_PER_TRACK - already))
        if room <= 0:
            raise ValueError(
                "This track already holds %d documents, which is its limit."
                " Quarantine one before adding more." % already)

        meta = {}
        lib = PSTATE.open_library()
        try:
            row = lib.execute("SELECT employer, role_title FROM track WHERE track_id=?",
                              (handle.track_id,)).fetchone()
            meta = {k: row[k] for k in row.keys()} if row else {}
        finally:
            lib.close()

        run_id = "r-" + secrets.token_hex(6)
        handle.start_research_run(run_id, json.dumps(
            {"kind": "discover", "provider": provider, "model": model,
             "gaps": [g["gap_id"] for g in gaps], "room": room}))

        calls = {"n": 0}

        def nominate(system, prompt, schema):
            calls["n"] += 1
            data = run_cli(provider, model, system, prompt, effort=effort,
                           schema=schema, search=True)
            return data.get("result") or "{}"

        def ingest(found, gap):
            return PCORPUS.ingest_text(
                handle, found["text"], origin_url=found["url"],
                title=None, vetting=found["vetting"], trust=found["trust"],
                final_url=found["final_url"], publisher=found["publisher"],
                published_on=found["published_on"], run_id=run_id)

        # One at a time across the whole bridge, the same rule every other model
        # call follows. Non-blocking: a candidate who clicks twice gets a plain
        # 429 rather than two searches racing to fill the same corpus.
        if not MODEL_GATE.acquire(blocking=False):
            handle.finish_research_run(run_id, "aborted")
            return self._json(429, {"error": "A model call is already running."})
        try:
            out = PRESEARCH.discover(
                gaps, nominate, ingest,
                role=meta.get("role_title") or "", employer=meta.get("employer") or "",
                per_gap=DISCOVER_PER_GAP, budget_seconds=DISCOVER_BUDGET_SECONDS,
                max_docs=room)
        except RuntimeError as exc:
            handle.finish_research_run(run_id, "failed")
            return self._json(400, {"error": str(exc)})
        except Exception as exc:
            handle.finish_research_run(run_id, "failed")
            return self._failure(502, "Discovery", exc)
        finally:
            try:
                MODEL_GATE.release()
            except ValueError:
                pass

        stored_bytes = sum(int(d.get("bytes") or 0)
                           for rec in out["gaps"] for d in rec["stored"])
        # Bounded by COUNT, then serialised, and never the other way round.
        # Slicing the finished JSON to MARK_MAX_BYTES cut it mid-string inside a
        # URL, and `discardsOf` in the page throws on that and returns [] -- so
        # a run with many rejections displayed as a run with none, which is the
        # exact opposite of what the ledger is for. The report is trimmed until
        # it fits, and says how many it dropped.
        report = _discovery_report(provider, model, out)
        handle.finish_research_run(
            run_id, "ok" if out["stored"] else "failed", queries=report,
            tool_calls=calls["n"], bytes_fetched=stored_bytes)

        return self._json(200, {
            "runId": run_id,
            "stored": out["stored"],
            "gaps": out["gaps"],
            "discarded": out["discarded"],
            "asked": calls["n"],
            "skipped": skipped,
            "flow": flow_state(handle)})

    # A supplied resource is bounded the same way a pasted URL is. The path is
    # the candidate's own file on their own machine, so the limit that matters is
    # not authorisation but the goal: a goal long enough to be a document is a
    # goal nobody wrote on purpose.
    GOAL_MAX_CHARS = 600
    PATH_MAX_CHARS = 1024

    # One dialog at a time, whoever asks. Two tabs both asking would stack two
    # modal choosers on the candidate's screen and leave two handler threads
    # parked on them.
    PICK_GATE = threading.BoundedSemaphore(1)
    PICK_TIMEOUT_SECONDS = 240

    def _route_pick(self, _handle, _payload):
        """Open the operating system's own file chooser and return what it picked.

        The browser cannot do this. `<input type="file">` hands JavaScript the
        basename and never the path, by design in every browser, so a picker on
        the page could not fill the box this panel reads. The alternative was
        uploading the bytes, which would put a 64 MB body on a route that writes
        to the filesystem and give this app a CSRF-writable upload surface it
        does not otherwise have. The bridge is a local process on the
        candidate's own machine, so it can just ask the operating system, and
        the file still never crosses the HTTP boundary: what comes back is a
        path, which then goes through exactly the same `resolve()` and `gate()`
        a typed path does.
        """
        if DIALOG_DISABLED:
            # The same discipline PREPWRIGHT_NO_MODEL enforces for provider
            # calls, for the same reason. A test that opened a real chooser
            # would park the suite on a modal window until a human clicked it.
            raise ValueError(
                "File chooser dialogs are disabled in this process by"
                " PREPWRIGHT_NO_DIALOG. Paste the path instead.")
        binary = PSEC.trusted_executable("osascript", ("/usr/bin/osascript",))
        if not binary:
            raise ValueError(
                "This machine has no file chooser this can open safely. "
                "Paste the path instead: in Finder, select the file and press "
                "Option-Command-C.")
        if not self.PICK_GATE.acquire(blocking=False):
            raise ValueError("A file chooser is already open. Use that one.")
        try:
            done = subprocess.run(
                [binary, "-e", 'POSIX path of (choose file with prompt'
                 ' "Choose a file to study from")'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.PICK_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            return self._json(200, {"ok": False, "cancelled": True,
                                    "reason": "The chooser was left open too"
                                              " long, so it was closed."})
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("The file chooser could not run: %s" % (exc,))
        finally:
            self.PICK_GATE.release()
        if done.returncode != 0:
            # Cancelling is not an error. osascript exits non-zero for it, and
            # reporting that as a failure would put a red box on the screen
            # every time the candidate changed their mind.
            return self._json(200, {"ok": False, "cancelled": True,
                                    "reason": "No file was chosen."})
        path = (done.stdout or b"").decode("utf-8", "replace").strip()
        if not path:
            return self._json(200, {"ok": False, "cancelled": True,
                                    "reason": "No file was chosen."})
        return self._json(200, {"ok": True, "path": path[:self.PATH_MAX_CHARS]})

    def _route_file(self, handle, payload, commit):
        """Read a file the candidate supplied. Preview, or preview and store.

        Two actions rather than one because the cut is the interesting part and
        the candidate should see it before it spends a track's document budget:
        `preview_file` reads, measures and ranks without writing a byte, and
        `ingest_file` does the same work and then commits. Both cost zero
        provider tokens; `prepwright.ingest` opens no socket and calls no model,
        which is asserted by its own test rather than left as a claim.

        The run is recorded in `research_run` exactly as a discovery run is, so a
        supplied PDF and a found page appear in one ledger with one provenance
        story, and the sections the cut dropped are recorded with their reasons
        next to the sources discovery rejected with theirs.
        """
        path = str(payload.get("path") or "").strip()[:self.PATH_MAX_CHARS]
        if not path:
            raise ValueError("Name the file you want to study from.")
        goal = str(payload.get("goal") or "").strip()[:self.GOAL_MAX_CHARS]
        depth = str(payload.get("depth") or "focused").strip().lower()
        vetting = str(payload.get("vetting") or "community")
        if vetting not in ("primary", "secondary", "vendor", "community"):
            vetting = "community"
        try:
            trust = max(1, min(5, int(payload.get("trust") or 3)))
        except (TypeError, ValueError):
            trust = 3
        if not commit:
            report = PINGEST.preview(path, goal, vetting=vetting, trust=trust,
                                     depth=depth)
            return self._json(200, {"ok": bool(report.get("ok")),
                                    "committed": False, "report": report})
        run_id = "f-" + secrets.token_hex(6)
        handle.start_research_run(run_id, "supplied file: %s | goal: %s"
                                  % (os.path.basename(path), goal or "(none)"))
        try:
            report = PINGEST.ingest_file(handle, path, goal, vetting=vetting,
                                         trust=trust, run_id=run_id, depth=depth)
        except Exception as exc:                              # noqa: BLE001
            # The run row is closed on every path. A run left 'running' is
            # indistinguishable in the ledger from one still in flight, and a
            # refused ingest is a fact worth keeping rather than a gap.
            handle.finish_research_run(
                run_id, "failed",
                queries=_file_report(os.path.basename(path), str(exc)[:400]))
            raise
        handle.finish_research_run(
            run_id, "ok", queries=_file_report(report),
            bytes_fetched=int(report.get("bytes") or 0))
        # `flow` like every sibling mutating route (`_route_discover` and
        # `_route_quarantine` both return it). Without it the page's
        # `flow = d.flow || flow` kept the stale object, so after a successful
        # ingest the stage stayed "research" and the corpus count stayed 0:
        # "Build the plan" was unreachable until some later action or a reload.
        return self._json(200, {"ok": True, "committed": True, "report": report,
                                "flow": flow_state(handle)})

    def _route_ledger(self, handle, _payload):
        """Every source in this track, with where it came from and what it cost.

        The audit surface that replaces asking permission for each URL. Nothing
        is fetched here; this reads what discovery already proved.
        """
        docs = [dict(r) for r in handle.conn.execute(
            "SELECT d.doc_id, d.title, d.origin_url, d.final_url, d.publisher,"
            "       d.published_on, d.vetting, d.trust, d.status, d.n_sections,"
            "       d.origin_bytes, d.fetched_utc, d.run_id, d.cite_count"
            "  FROM doc d ORDER BY d.doc_no").fetchall()]
        serving = {}
        for r in handle.conn.execute(
                "SELECT DISTINCT sl.doc_id AS doc_id, s.gap_id AS gap_id,"
                "       s.step_id AS step_id, s.title AS step_title"
                "  FROM step_slice sl JOIN step s ON s.step_id = sl.step_id"
                " ORDER BY s.ord").fetchall():
            serving.setdefault(r["doc_id"], []).append(
                {"gapId": r["gap_id"], "stepKey": r["step_id"],
                 "stepTitle": r["step_title"]})
        runs = handle.research_runs()
        for doc in docs:
            doc["serves"] = serving.get(doc["doc_id"], [])
        return self._json(200, {"documents": docs, "runs": runs,
                                "flow": flow_state(handle)})

    def _route_quarantine(self, handle, payload):
        """Distrust one document. Its sections stop reaching any future pack.

        Not a delete. The row stays, its provenance stays, and the reason it was
        distrusted stays with it, because a source removed without trace is a
        source nobody can argue with later.
        """
        doc_id = str(payload.get("docId") or "").strip()[:16]
        state = str(payload.get("status") or "quarantined").strip()
        if state not in ("quarantined", "ready"):
            raise ValueError("A document is either ready or quarantined.")
        row = handle.conn.execute(
            "SELECT status FROM doc WHERE doc_id=?", (doc_id,)).fetchone()
        if row is None:
            raise ValueError("No document %r in this track." % doc_id)
        handle.set_doc_status(doc_id, state)
        return self._json(200, {"docId": doc_id, "status": state,
                                "flow": flow_state(handle)})

    def _route_curriculum(self, handle, payload):
        """Approved gaps plus this track's corpus into a written plan."""
        if PCURR.steps_of(handle):
            raise ValueError(
                "This track already has a curriculum. Building a second one over"
                " it would renumber steps the transcript already points at.")
        gaps = PDIAG.approved(handle)
        if not gaps:
            raise ValueError(
                "No gap has been approved yet, so there is nothing to plan.")
        # A partial corpus defers the gaps it does not cover, which is normal
        # and is reported. An EMPTY corpus is a precondition, not a deferral:
        # answering 200 with a plan of zero steps reads as success and leaves
        # the candidate looking at an empty curriculum with nothing saying why.
        if not handle.conn.execute(
                "SELECT COUNT(*) c FROM doc WHERE status='ready'").fetchone()["c"]:
            raise ValueError(
                "This track has no corpus yet, so every step would have nothing"
                " to teach from. Add the sources you trust first.")
        edges = payload.get("edges")
        edges = edges if isinstance(edges, list) else ()
        built = PCURR.build(handle, gaps, edges=edges)
        built["flow"] = flow_state(handle)
        return self._json(200, built)

    def do_POST(self):
        route = self.path.split("?")[0]
        if self._reject_bad_host():
            return
        # /api/chat has no `if route ==` test of its own: it is the residual
        # branch at the end of this method. A new route must be added HERE and
        # given an explicit block BEFORE that fall-through, or its requests are
        # answered by the tutor.
        if route not in ("/api/chat", "/api/state", "/api/assess", "/api/review",
                         "/api/intake", "/api/diagnose", "/api/gap",
                         "/api/research", "/api/curriculum", "/api/track",
                         "/api/settings"):
            return self._json(404, {"error": "Not found."})

        if not self._authorized():
            return self._json(403, {"error": "Tutor session authorization required."})

        if route == "/api/state":
            return self._save_state()

        try:
            payload = self._read_json_body()
        except OverflowError:
            return self._json(413, {"error": "Request body is too large."})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})

        if route == "/api/settings":
            # No model call and no track handle: this writes one small file and
            # answers with what was actually kept, so a value the whitelist
            # dropped is visible in the page rather than silently discarded.
            try:
                _write_settings(payload)
            except OSError as exc:
                return self._failure(500, "Saving model settings", exc)
            return self._json(200, _settings_view())

        if route in ("/api/intake", "/api/diagnose", "/api/gap",
                     "/api/research", "/api/curriculum", "/api/track"):
            return self._pipeline(route, payload)

        if route == "/api/assess":
            try:
                # Shape first, provider second. The payload check is local and
                # free; provider selection consults the machine and, when no
                # model is reachable, raises a reason that has nothing to do
                # with the request. Asking in that order made a malformed step
                # list report "model calls are disabled".
                items = _safe_assess_items(payload.get("steps"))
                provider, _model = self._provider(payload, require_model=False)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                return self._json(400, {"error": str(exc)})
            if not MODEL_GATE.acquire(blocking=False):
                return self._json(429, {"error": "Tutor is already answering another request."})
            try:
                graded, usage, failed = assess_via_cli(provider, items)
            except Exception as exc:  # noqa: BLE001
                return self._failure(502, "Assessment", exc)
            finally:
                MODEL_GATE.release()
            if not graded:
                return self._json(502, {"error": "The grader returned nothing — try again."})
            model = _role_choice(provider, "assess")[0]
            stored = _persist_assessment(graded, provider, model,
                                         open_state_track)
            return self._json(200, {
                "steps": graded, "usage": usage, "stored": stored,
                "provider": provider, "model": model,
                "capped": max(0, len(payload.get("steps") or []) - MAX_ASSESS_STEPS)
                          + failed,
            })

        if route == "/api/review":
            try:
                # Shape first, provider second, for the reason given on
                # /api/assess above.
                messages = _safe_messages(payload.get("messages"))
                step = payload.get("step") if isinstance(payload.get("step"), dict) else {}
                step_key = str(payload.get("stepKey") or "")[:80]
                if not PS.STEP_KEY_RE.match(step_key):
                    raise ValueError("Unknown curriculum step.")
                if str(step.get("key") or "") != step_key:
                    raise ValueError("Curriculum step does not match its key.")
                provider, _model = self._provider(payload, require_model=False)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            except RuntimeError as exc:
                return self._json(400, {"error": str(exc)})
            if not MODEL_GATE.acquire(blocking=False):
                return self._json(429, {"error": "Tutor is already answering another request."})
            try:
                review, usage = review_via_cli(provider, step, messages)
            except Exception as exc:  # noqa: BLE001
                return self._failure(502, "Session review", exc)
            finally:
                MODEL_GATE.release()
            review["provider"] = provider
            review["usage"] = usage
            return self._json(200, review)

        try:
            provider, model = self._provider(payload)
            raw_messages = payload.get("messages")
            messages = _safe_messages(raw_messages)
            # Turns dropped before trim_history even saw them, so the page can
            # say so rather than showing a window that silently starts later.
            pre_dropped = (len(raw_messages) - len(messages)
                           if isinstance(raw_messages, list) else 0)
            step = payload.get("step") if isinstance(payload.get("step"), dict) else {}
            step_key = str(payload.get("stepKey") or "")[:80]
            if not PS.STEP_KEY_RE.match(step_key):
                raise ValueError("Unknown curriculum step.")
            if str(step.get("key") or "") != step_key:
                raise ValueError("Curriculum step does not match its key.")
            # No `citation` here any more. What a turn may cite is decided by
            # what the curriculum pinned to the step, read from step_slice, not
            # by a hint the client sends. Reading a client's citation preference
            # would put the choice of evidence back in the caller's hands, which
            # is the grounding claim inverted.
            # Resolved through the role rather than taken raw, so a candidate
            # who set a tutor model in the settings panel and then reloaded
            # gets it even though the page sent no model on this request.
            # _provider above has already refused anything off the whitelist.
            model, effort = _role_choice(
                provider, "tutor",
                model=str(payload.get("model") or "")[:64],
                effort=str(payload.get("effort") or "")[:12])
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except RuntimeError as exc:
            return self._json(400, {"error": str(exc)})
        if not MODEL_GATE.acquire(blocking=False):
            return self._json(429, {"error": "Tutor is already answering another request."})
        try:
            # The handle's lifetime belongs to this route, not to the teaching
            # function: each request opens and closes its own, and the pack must
            # be built before the provider call so the reply can be checked
            # against exactly what was sent.
            handle = open_state_track(take_lease=False)
            try:
                pack = evidence_pack(handle, step_key)
            finally:
                handle.close()
            text, resolved, usage, dropped, citations = chat_via_cli(
                provider, model, step, messages, pack, effort)
            return self._json(200, {
                "text": text or "(the model returned no text)",
                # What the reply was grounded in, and any token it named that the
                # pack did not contain. The page shows the second as a warning:
                # a confident, well-cited, wrong lesson is the failure this whole
                # design exists to prevent, and silence about it is complicity.
                "grounded": bool(pack.get("grounded")),
                "cites": pack.get("cites") or [],
                "citations": citations,
                "packSha16": pack.get("pack_sha16") or "",
                "provider": provider,
                "model": model,
                "resolved": resolved,
                "usage": usage,
                "dropped": dropped + pre_dropped,
                # Echo what was actually applied, not what was asked for. An
                # unrecognised level falls back, and the page must show the level
                # that ran rather than the one the candidate typed.
                "effort": (_effort_flag(effort) or ["", "default"])[1],
            })
        except subprocess.TimeoutExpired:
            return self._json(504, {"error": "The tutor took longer than %ss — try again." % REQUEST_TIMEOUT})
        except Exception as exc:  # noqa: BLE001
            return self._failure(502, "Tutor request", exc)
        finally:
            MODEL_GATE.release()
