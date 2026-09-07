#!/bin/bash
# prep — launch the Prepwright tutor from any directory.

set -eu
umask 077

case "${1:-}" in
  -h|--help|help)
    cat <<'USAGE'
prep — Prepwright interview-preparation tutor

  prep                  start the tutor, open it in your browser (loopback only)
  prep iphone           also serve it to your iPhone over your own Tailscale tailnet
  prep iphone off       stop serving to the phone; the tutor is loopback only again
  prep --help           this message

iPhone mode keeps the bridge on 127.0.0.1 and reaches the phone through a
`tailscale serve` HTTPS proxy on this same Mac, pinned to your tailnet login.
No port is opened to the internet or the LAN and no credential leaves this
machine. Set PREPWRIGHT_PORT to use a port other than 8010.
USAGE
    exit 0
    ;;
esac

# The app directory is this script's own resolved location. `prep` is normally a
# symlink from a bin directory, so the link is followed to its target first, and
# a copy of this launcher then runs the tree it was copied into.
_self="${BASH_SOURCE[0]:-$0}"
while [ -L "$_self" ]; do
  _link=$(/usr/bin/readlink "$_self")
  case "$_link" in
    /*) _self="$_link" ;;
     *) _self="$(/usr/bin/dirname "$_self")/$_link" ;;
  esac
done
DIR="$(cd "$(/usr/bin/dirname "$_self")" && pwd -P)"
PORT="${PREPWRIGHT_PORT:-8010}"
URL="http://localhost:${PORT}/"
HEALTH="http://127.0.0.1:${PORT}/api/health"
APP_MARKER='"app"[[:space:]]*:[[:space:]]*"prepwright"'
# Remote mode is reported by /api/health over loopback. A remote-mode bridge
# still answers loopback, so its status code says nothing; the health field is
# the only reliable signal.
REMOTE_MARKER='"remote"[[:space:]]*:[[:space:]]*true'

# lsof is /usr/sbin/lsof on macOS, not /usr/bin. The wrong absolute path makes
# every port check a silent no-op that still reports success, so it is probed.
if [ -x /usr/sbin/lsof ]; then
  LSOF_BIN=/usr/sbin/lsof
elif [ -x /usr/bin/lsof ]; then
  LSOF_BIN=/usr/bin/lsof
else
  LSOF_BIN=""
  echo "prep: lsof was not found at /usr/sbin/lsof or /usr/bin/lsof." >&2
  echo "  Port checks that need it are skipped; stop the bridge with Ctrl+C." >&2
fi
PYTHON_BIN="/usr/bin/python3"

# Integrity is pinned by MANIFEST.sha256, not by two hashes in this file.
#
# The old pin named bridge.py and index.html and called them "the two files this
# launcher starts". That stopped being true when the bridge grew a package: a
# `prep` start now loads nineteen files, and six of the prepwright modules carry
# the security envelope, the storage caps and the redaction rules. Pinning two of
# nineteen checked the wrapper and left the contents unsigned.
#
# Regenerate after any change to the runtime set, and commit it with the change:
#   ./tools/make_manifest.sh
MANIFEST="$DIR/MANIFEST.sha256"

case "$PORT" in
  ''|*[!0-9]*)
    echo "prep: PREPWRIGHT_PORT must be a number" >&2
    exit 2
    ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  echo "prep: PREPWRIGHT_PORT must be between 1 and 65535" >&2
  exit 2
fi

# Completeness is checked against the manifest, so this fires for a missing
# prepwright module too. Testing only bridge.py and index.html meant the message
# never printed for the files that actually go missing.
for required in bridge.py index.html prepwright/__init__.py prepwright/config.py \
                prepwright/state.py prepwright/track.py prepwright/keep.py \
                prepwright/pagestate.py prepwright/corpus.py; do
  if [ ! -f "$DIR/$required" ]; then
    echo "prep: the Prepwright implementation is incomplete at:" >&2
    echo "  $DIR (missing $required)" >&2
    exit 1
  fi
done
if [ ! -x "$PYTHON_BIN" ]; then
  echo "prep: trusted system Python was not found at $PYTHON_BIN" >&2
  exit 1
fi

# Verify every pinned file, and refuse to start on any mismatch. shasum -c reads
# the manifest and reports per file; --strict makes a malformed line an error
# rather than a skip, so a truncated manifest cannot silently check nothing.
if [ -f "$MANIFEST" ]; then
  # shasum -c writes its per-file "FAILED" lines to STDOUT and only the summary
  # warning to stderr, so stdout is what has to be captured to name the file.
  if ! ( cd "$DIR" && /usr/bin/shasum -a 256 --strict -c MANIFEST.sha256 \
         >"$DIR/.manifest-check.err" 2>&1 ); then
    echo "prep: integrity check FAILED." >&2
    echo "One or more runtime files differ from MANIFEST.sha256:" >&2
    sed -n 's/^\(.*\): FAILED.*$/  \1/p' "$DIR/.manifest-check.err" >&2 || true
    rm -f "$DIR/.manifest-check.err"
    echo "Refusing to run modified code. If YOU changed these files, run:" >&2
    echo "  ./tools/make_manifest.sh" >&2
    exit 1
  fi
  rm -f "$DIR/.manifest-check.err"
  # A file present on disk but absent from the manifest is unsigned code sitting
  # in the import path. shasum -c cannot see it, so it is counted here.
  manifest_n="$(grep -c '^[0-9a-f]' "$MANIFEST" || echo 0)"
  ondisk_n="$(cd "$DIR" && ls bridge.py index.html prepwright/*.py 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$manifest_n" != "$ondisk_n" ]; then
    echo "prep: $ondisk_n runtime files on disk but $manifest_n pinned." >&2
    echo "Refusing to start with unsigned code in the import path. Run:" >&2
    echo "  ./tools/make_manifest.sh" >&2
    exit 1
  fi
else
  echo "prep: MANIFEST.sha256 is missing, so nothing is integrity-checked." >&2
  echo "  Generate it with ./tools/make_manifest.sh" >&2
fi

# The bridge never consumes raw API-key/proxy/Python startup variables. Remove
# them before even the short readiness helper is spawned.
unset ANTHROPIC_API_KEY OPENAI_API_KEY AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
unset GITHUB_TOKEN GH_TOKEN HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY
unset PYTHONPATH PYTHONHOME BASH_ENV ENV CDPATH
# Remote (iPhone) mode is opt-in per launch. These two enable it inside the
# bridge; an inherited value would silently widen the host allowlist, so they
# are cleared here and set ONLY by the `prep iphone` preflight below.
unset TUTOR_TS_HOST TUTOR_TS_LOGIN

# ---- `prep iphone` — serve the tutor to your own iPhone --------------------
# Design, in one breath: the bridge stays loopback-only; `tailscale serve`
# reverse-proxies HTTPS from this machine's tailnet name to 127.0.0.1:PORT over
# WireGuard; the bridge additionally pins every proxied request to your own
# tailnet login (serve injects Tailscale-User-Login and strips inbound
# forgeries). Nothing listens on the LAN, nothing is public, and no credential
# leaves this Mac — the phone gets pixels and a session cookie.
#
#   prep iphone         preflight, enable, self-test, print the URL (+ QR)
#   prep iphone off     tear the HTTPS proxy down again
find_tailscale() {
  if [ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]; then
    printf '%s' "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
  elif command -v tailscale >/dev/null 2>&1; then
    command -v tailscale
  else
    return 1
  fi
}

if [ "${1:-}" = "iphone" ]; then
  TS_BIN="$(find_tailscale)" || {
    echo "prep iphone: Tailscale is not installed." >&2
    echo "  Mac:    brew install --cask tailscale   (then open it and sign in)" >&2
    echo "  iPhone: App Store > Tailscale, sign in to the SAME account" >&2
    exit 1
  }

  if [ "${2:-}" = "off" ]; then
    # There is ONE :443 handler per machine and another local app may own it.
    # Tearing down a handler that is not ours would silently kill someone else's
    # phone access, so the mapping is checked against OUR port before removal.
    if "$TS_BIN" serve status 2>/dev/null | /usr/bin/grep -Fq "127.0.0.1:${PORT}"; then
      "$TS_BIN" serve --https=443 off >/dev/null 2>&1 || true
    elif "$TS_BIN" serve status 2>/dev/null | /usr/bin/grep -q ":443"; then
      echo "prep iphone: :443 on this machine is served by something else, so it" >&2
      echo "  was left untouched. Prepwright had no handler of its own to remove." >&2
    fi
    # Also stop the bridge. Remote mode is fixed at process start, so a bridge
    # left running after its handler is removed still believes it is remote and
    # makes the next `prep iphone` think everything is already fine. One
    # command, one unambiguous end state.
    if /usr/bin/curl --noproxy '*' --silent --max-time 2 "$HEALTH" 2>/dev/null \
         | /usr/bin/grep -Eq "$APP_MARKER"; then
      if [ -n "$LSOF_BIN" ]; then
        for stop_pid in $("$LSOF_BIN" -ti tcp:"$PORT" 2>/dev/null); do
          kill "$stop_pid" 2>/dev/null || true
        done
      fi
      /bin/sleep 1
      # Report what happened, not what was attempted.
      if [ -n "$LSOF_BIN" ] && "$LSOF_BIN" -ti tcp:"$PORT" >/dev/null 2>&1; then
        echo "prep iphone: proxy removed, but the bridge on port ${PORT} did not stop." >&2
        "$LSOF_BIN" -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/sed -n '1,3p' >&2
        echo "  Stop it in its own terminal with Ctrl+C." >&2
        exit 1
      fi
      if [ -n "$LSOF_BIN" ]; then
        echo "prep iphone: proxy removed and the bridge stopped."
      else
        echo "prep iphone: proxy removed, but lsof is missing so the bridge was" >&2
        echo "  not stopped. Stop it with Ctrl+C in its own terminal." >&2
      fi
    else
      echo "prep iphone: proxy removed."
    fi
    echo "  Start again with:  prep iphone       (phone)"
    echo "                     prep              (this Mac only)"
    exit 0
  fi

  # Everything about the tailnet is read from the running daemon, not assumed.
  TS_FACTS="$("$TS_BIN" status --json 2>/dev/null | /usr/bin/python3 -I -S -c '
import json, sys
try:
    st = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if st.get("BackendState") != "Running":
    sys.exit(2)
self_node = st.get("Self") or {}
dns = (self_node.get("DNSName") or "").rstrip(".")
user = (st.get("User") or {}).get(str(self_node.get("UserID") or ""), {})
login = user.get("LoginName") or ""
if not dns or not login:
    sys.exit(3)
print(dns); print(login)
')" || {
    echo "prep iphone: Tailscale is installed but not running/signed in." >&2
    echo "  Open the Tailscale menu-bar app and sign in, then run this again." >&2
    exit 1
  }
  TS_HOST="$(printf '%s' "$TS_FACTS" | /usr/bin/sed -n 1p)"
  TS_LOGIN="$(printf '%s' "$TS_FACTS" | /usr/bin/sed -n 2p)"

  # Classify whoever holds ${PORT} before deciding anything. The port is fixed on
  # purpose: the phone bookmarks one URL and the tailnet handler points at one
  # backend, so silently drifting to another port would break both.
  PORT_PROBE="$(/usr/bin/curl --noproxy '*' --silent --max-time 2 "$HEALTH" 2>/dev/null || true)"
  if /usr/bin/printf '%s' "$PORT_PROBE" | /usr/bin/grep -Eq "$APP_MARKER" \
     && /usr/bin/printf '%s' "$PORT_PROBE" | /usr/bin/grep -Eq "$REMOTE_MARKER"; then
    # Our own bridge, already in remote mode. A healthy bridge does NOT imply a
    # live tailnet handler: the two are separate objects and either can be gone
    # while the other runs, leaving the phone nothing to reach. Verify the
    # handler, and rebuild it if it is missing, before calling this a no-op.
    if "$TS_BIN" serve status 2>/dev/null \
         | /usr/bin/grep -Fq "127.0.0.1:${PORT}"; then
      echo "prep iphone: already running in remote mode on port ${PORT}."
    else
      # --bg REPLACES an existing :443 mapping rather than refusing one, and this
      # path is reached exactly when something has claimed :443 since startup, so
      # it needs the same refusal the primary path makes before it creates one.
      if "$TS_BIN" serve status 2>/dev/null | /usr/bin/grep -q ":443"; then
        echo "prep iphone: :443 on this machine is served by something else:" >&2
        "$TS_BIN" serve status 2>/dev/null >&2
        echo "  Refusing to replace it." >&2
        exit 1
      fi
      "$TS_BIN" serve --bg --https=443 "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || {
        echo "prep iphone: the bridge is up but the tailnet handler could not be" >&2
        echo "  restored. Run 'prep iphone off', then 'prep iphone'." >&2
        exit 1
      }
      echo "prep iphone: bridge was already up; the tailnet handler was missing"
      echo "  and has been restored on port ${PORT}."
    fi
    echo ""
    echo "  On your iPhone (Tailscale connected):  https://${TS_HOST}/"
    echo "  On this Mac:                           ${URL}"
    echo ""
    echo "  Stop it with:  prep iphone off"
    exit 0
  fi
  if /usr/bin/printf '%s' "$PORT_PROBE" | /usr/bin/grep -Eq "$APP_MARKER"; then
    # Our bridge, but local-only. It reads TUTOR_TS_* once at import and cannot
    # learn the tailnet identity now, so pointing the proxy at it would answer
    # every phone request with 421. Restart it rather than half-enable remote.
    echo "prep iphone: Prepwright is running on port ${PORT} in local-only mode." >&2
    echo "  Stop it (Ctrl+C in its terminal), then run 'prep iphone' again." >&2
    exit 1
  fi

  # `tailscale serve --https` needs tailnet HTTPS certificates. Without them the
  # CLI drops into an interactive consent flow and waits on a link it printed —
  # which, with output redirected, is indistinguishable from a hang. Check first.
  if ! "$TS_BIN" status --json 2>/dev/null | /usr/bin/python3 -I -S -c '
import json, sys
try:
    st = json.load(sys.stdin)
except Exception:
    sys.exit(1)
sys.exit(0 if (st.get("CertDomains") or []) else 1)
'; then
    echo "prep iphone: HTTPS certificates are not enabled for your tailnet." >&2
    echo "  One-time setup, ~30 seconds, no password:" >&2
    echo "    1. open  https://login.tailscale.com/admin/dns" >&2
    echo "    2. under 'HTTPS Certificates', click Enable HTTPS" >&2
    echo "  Then run 'prep iphone' again." >&2
    exit 1
  fi

  # Refuse to share port 443 with anything else, and refuse Funnel outright.
  # Funnel would put the URL on the public internet; the bridge's identity pin
  # would still reject every public request, but the front door stays shut too.
  SERVE_STATUS="$("$TS_BIN" serve status 2>/dev/null || true)"
  if printf '%s' "$SERVE_STATUS" | /usr/bin/grep -q "funnel"; then
    echo "prep iphone: Funnel is enabled on this machine. Turn it off first:" >&2
    echo "  $TS_BIN funnel --https=443 off" >&2
    exit 1
  fi
  if printf '%s' "$SERVE_STATUS" | /usr/bin/grep -q ":443"      && ! printf '%s' "$SERVE_STATUS" | /usr/bin/grep -q "127.0.0.1:${PORT}"; then
    echo "prep iphone: something else is already served on :443 of this machine:" >&2
    printf '%s\n' "$SERVE_STATUS" >&2
    exit 1
  fi

  # Do not publish a port we have not proven is free. A foreign service holding
  # ${PORT} would otherwise be exposed at https://<magicdns>/ to the whole
  # tailnet, with a valid cert and no identity pin, because the pin lives in
  # bridge.py and bridge.py never starts. Check before creating any mapping.
  if /usr/bin/nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then
    echo "prep iphone: port ${PORT} is held by a service that is not Prepwright." >&2
    echo "  Refusing to publish it to your tailnet. Whatever is on it:" >&2
    if [ -n "$LSOF_BIN" ]; then
      "$LSOF_BIN" -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | /usr/bin/sed -n '1,4p' >&2
    else
      echo "  (lsof is unavailable, so the holder cannot be named here)" >&2
    fi
    echo "  Free port ${PORT} and run 'prep iphone' again. The port is fixed so" >&2
    echo "  your phone's bookmark and the tailnet handler always agree." >&2
    exit 1
  fi

  export TUTOR_TS_HOST="$TS_HOST" TUTOR_TS_LOGIN="$TS_LOGIN"

  SERVE_ERR="$("$TS_BIN" serve --bg --https=443 "http://127.0.0.1:${PORT}" 2>&1)" || {
    echo "prep iphone: could not configure the HTTPS proxy:" >&2
    printf '%s\n' "$SERVE_ERR" >&2
    exit 1
  }

  # From here the mapping exists. `serve --bg` outlives this shell and survives
  # reboot by design, so any exit that is not a healthy running bridge must
  # retract it rather than leave the tailnet pointed at a dead or foreign port.
  trap '"$TS_BIN" serve --https=443 off >/dev/null 2>&1 || true' EXIT INT TERM

  REMOTE_URL="https://${TS_HOST}/"
  echo "prep iphone: remote mode ON, pinned to ${TS_LOGIN}"
  echo "  On your iPhone (Tailscale app connected):  ${REMOTE_URL}"
  if command -v qrencode >/dev/null 2>&1; then
    qrencode -t ANSIUTF8 "$REMOTE_URL" || true
  else
    echo "  (optional: 'brew install qrencode' to print a scannable QR here)"
  fi
  echo "  Turn it off later with:  prep iphone off"
  # This Mac keeps using loopback. A node cannot reach its own `tailscale serve`
  # endpoint, so ${REMOTE_URL} would simply time out here; loopback is the only
  # URL that works locally, and remote mode permits it. The identity
  # pin is enforced on proxy-injected headers rather than on the Host header, so
  # allowing loopback cannot be used by a tailnet peer to skip it.
  # fall through: start the bridge exactly as a normal launch would
fi

READY_TRIES=60

# Proof of life, always over loopback. A node cannot reach its own `tailscale
# serve` endpoint (the packets never traverse the tunnel), so probing the tailnet
# URL from this Mac would time out even when the phone path is perfect.
#
# Loopback answers /api/health in both modes, and in remote mode the body also
# carries "remote": true. So one probe proves the bridge is up AND which mode it
# came up in.
health_body() {
  /usr/bin/curl --noproxy '*' --fail --silent --show-error --max-time 3 "$HEALTH" 2>/dev/null
}

bridge_is_up() {
  probe_body="$(health_body || true)"
  /usr/bin/printf '%s' "$probe_body" | /usr/bin/grep -Eq "$APP_MARKER" || return 1
  if [ -n "${TUTOR_TS_HOST:-}" ]; then
    /usr/bin/printf '%s' "$probe_body" | /usr/bin/grep -Eq "$REMOTE_MARKER" || return 1
  fi
  return 0
}

running_body="$(health_body || true)"
if [ -n "$running_body" ]; then
  if /usr/bin/printf '%s' "$running_body" | /usr/bin/grep -Eq "$APP_MARKER"; then
    # A bridge already up in remote mode serves this Mac at ${URL} too, so a
    # plain `prep` can hand it over instead of reporting a phantom conflict.
    if /usr/bin/printf '%s' "$running_body" | /usr/bin/grep -Eq "$REMOTE_MARKER"; then
      echo "prep: already running (iPhone mode is on) — opening ${URL}"
    else
      echo "prep: already running — opening ${URL}"
    fi
    /usr/bin/open "$URL"
    exit 0
  fi
  echo "prep: port ${PORT} is occupied by a different local service; not opening it" >&2
  echo "Set a free port, for example: PREPWRIGHT_PORT=8011 prep" >&2
  exit 1
fi
if /usr/bin/nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1; then
  echo "prep: port ${PORT} is occupied but did not identify as Prepwright" >&2
  echo "Set a free port, for example: PREPWRIGHT_PORT=8011 prep" >&2
  exit 1
fi

# Open only after the expected app identifies itself, never merely because some
# process happened to answer on the port.
(
  # Critical: bash runs an inherited EXIT trap when this subshell exits, which
  # would retract the tailnet mapping the parent is about to serve from. The
  # teardown belongs to the parent only.
  trap - EXIT INT TERM
  i=0
  while [ "$i" -lt "$READY_TRIES" ]; do
    if bridge_is_up; then
      if [ -n "${TUTOR_TS_HOST:-}" ]; then
        echo ""
        echo "prep iphone: READY. Open this on your iPhone (Tailscale connected):"
        echo "    https://${TUTOR_TS_HOST}/"
        echo "  Verified: bridge up in remote mode, tailnet handler live."
        echo "  This Mac cannot open that tailnet URL itself (a node cannot reach"
        echo "  its own serve endpoint) — it uses ${URL} instead, opened now."
      fi
      /usr/bin/open "$URL"
      exit 0
    fi
    i=$((i + 1))
    /bin/sleep 0.25
  done
  echo "prep: bridge did not become ready; check the terminal output" >&2
) &

detected=""
if command -v claude >/dev/null 2>&1; then detected="Claude"; fi
if command -v codex >/dev/null 2>&1; then
  if [ -n "$detected" ]; then detected="$detected + Codex"; else detected="Codex"; fi
fi
if [ -n "$detected" ]; then
  echo "prep: starting on ${URL}  (CLIs detected: ${detected}; bridge verifies login; Ctrl+C to stop)"
else
  echo "prep: starting on ${URL}  (no trusted provider CLI found; Ctrl+C to stop)"
fi

cd "$DIR"
if [ -n "${TUTOR_TS_HOST:-}" ]; then
  # Not exec: exec would replace this shell and discard the teardown trap.
  "$PYTHON_BIN" -I -S bridge.py
  exit $?
fi
exec "$PYTHON_BIN" -I -S bridge.py
