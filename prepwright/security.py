"""The request boundary: origin, host, cookie, header, remote identity.

The only module allowed to decide that a request may proceed. Every check is
positive: a request is refused unless it matches something named here.

The origin, host, cookie and remote-identity checks live here, with the two
payload sanitisers and the static-route allowlist. `PORT` moved into
`config.py` first, which was the stated blocker: the allowlists below are
computed from it at import time and a module inside the package cannot import
that back out of the script that starts it.

`bridge.py` keeps short module-level aliases for every public name here,
because its handler reads them directly and renaming two hundred call sites for
a file move is churn rather than a refactor.
"""

import grp
import os
import pwd
import secrets
import shutil
import stat

from . import config as C

PORT = C.PORT

# What one chat request may carry. Bounded here rather than at the route,
# because the sanitisers below are what enforce them and a limit stated away
# from its check drifts from it.
MAX_MESSAGE_CHARS = 12_000
MAX_MESSAGE_COUNT = 64
MAX_MESSAGE_TOTAL_CHARS = 64_000
MAX_ASSESS_STEPS = 18
ASSESS_EXCERPT = 420


def _other_login_accounts():
    """Real local login accounts other than root and this user.

    A system account that cannot log in is not "another local account". macOS
    ships dozens of them and several sit in `admin`.
    """
    try:
        me = os.getuid()
        return {u.pw_name for u in pwd.getpwall()
                if u.pw_uid >= 500 and u.pw_uid != me
                and u.pw_shell not in ("/usr/bin/false", "/sbin/nologin",
                                       "/usr/bin/nologin", "/dev/null", "")}
    except (OSError, KeyError):
        return None                  # unreadable: fail closed at the caller


def _group_has_another_writer(gid):
    """True when a group could let a DIFFERENT person write. Fails closed.

    Group-writable is not itself the threat. The threat this module names is a
    binary "another local account could replace", and on an ordinary Homebrew
    install /opt/homebrew/bin is drwxrwxr-x owned by the installing user with
    group `admin`, whose only members are root, that same user, and disabled
    system accounts. Refusing it disables poppler on every such Mac while
    removing no risk, and `chmod g-w` there breaks `brew` itself.
    """
    others = _other_login_accounts()
    if others is None:
        return True
    try:
        group = grp.getgrgid(gid)
    except (KeyError, OSError):
        return True
    return bool(set(group.gr_mem) & others)


def _owner_only(path):
    """True when `path` exists and no other local account can write to it."""
    try:
        info = os.stat(path)
    except OSError:
        return False
    if info.st_uid not in (0, os.getuid()):
        return False
    if info.st_mode & stat.S_IWOTH:          # world-writable is never allowed
        return False
    if info.st_mode & stat.S_IWGRP:
        return not _group_has_another_writer(info.st_gid)
    return True


def _directories_ok(candidate):
    """Every directory traversed to reach `candidate`, symlinks resolved step by
    step so the directory holding each link is checked as well as the target's."""
    seen, node = set(), os.path.abspath(candidate)
    while node and node not in seen:
        seen.add(node)
        parent = os.path.dirname(node)
        while parent and parent != os.path.dirname(parent):
            if not _owner_only(parent):
                return False
            parent = os.path.dirname(parent)
        if not os.path.islink(node):
            return True
        target = os.readlink(node)
        node = target if os.path.isabs(target) else os.path.normpath(
            os.path.join(os.path.dirname(node), target))
    return True


def trusted_executable(name, fallbacks=()):
    """Resolve a CLI and reject files another local account could replace.

    The realpath must be a regular file owned by root or by this user, not
    group- or world-writable, and every directory on the way to it must satisfy
    the same two conditions. A writable parent is enough to swap the binary, so
    checking the file alone would be theatre.
    """
    candidates = [shutil.which(name)] + [os.path.expanduser(p) for p in fallbacks]
    for candidate in candidates:
        if not candidate:
            continue
        real = os.path.realpath(candidate)
        # Every directory on the way to the binary, INCLUDING the ones holding
        # the symlinks that lead to it. Walking only the realpath's ancestors
        # missed the ordinary Homebrew layout: /opt/homebrew/bin/pdftotext is a
        # symlink into ../Cellar, /opt/homebrew/bin is drwxrwxr-x, and it is not
        # on the resolved path's ancestor chain. Anyone in the admin group could
        # repoint that symlink at another already-trusted binary and have it run
        # with this program's arguments.
        if not _directories_ok(candidate):
            continue
        try:
            info = os.stat(real)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        if info.st_uid not in (0, os.getuid()):
            continue
        if info.st_mode & stat.S_IWOTH:
            continue
        if info.st_mode & stat.S_IWGRP and _group_has_another_writer(info.st_gid):
            continue
        parent, parents_ok = os.path.dirname(real), True
        while parent and parent != os.path.dirname(parent):
            try:
                parent_info = os.stat(parent)
            except OSError:
                parents_ok = False
                break
            if not _owner_only(parent):
                parents_ok = False
                break
            parent = os.path.dirname(parent)
        if not parents_ok:
            continue
        return real
    return None


# Exact origins the served page will present. Prefix matching is unsafe here:
# "http://localhost".startswith checks would also accept http://localhost.evil.com.
LOCAL_ORIGINS = frozenset({
    "http://localhost:%d" % PORT,
    "http://127.0.0.1:%d" % PORT,
})
LOCAL_HOSTS = frozenset({
    "localhost:%d" % PORT,
    "127.0.0.1:%d" % PORT,
})
ALLOWED_HOSTS = LOCAL_HOSTS
# There is deliberately no ALLOWED_ORIGINS constant. Every origin decision goes
# through _origins_for(), which derives the answer per request, and a module
# constant beside it would read like the live allowlist while feeding nothing.

# ---- iPhone / remote access over the candidate's own tailnet ----------------
# The bridge NEVER binds anything but 127.0.0.1. Remote access exists only as a
# `tailscale serve` reverse proxy on this same machine: the phone talks
# WireGuard to tailscaled, tailscaled terminates TLS and proxies to loopback.
# No port is opened to the LAN or the internet, and no credential moves — the
# Claude login stays in the macOS Keychain; the phone only ever holds the
# session cookie, which is useless off this tailnet.
#
# Both values come from the launcher, which reads them from the running
# tailscaled (`tailscale status --json`) at startup. Either one missing means
# remote mode is OFF and behavior is byte-identical to the loopback-only bridge.
#
#   TUTOR_TS_HOST   this machine's MagicDNS name, e.g. "mac.tail1234.ts.net"
#   TUTOR_TS_LOGIN  the candidate's tailnet login, e.g. "user@example.com"
#
# Identity check: `tailscale serve` injects Tailscale-User-Login with the
# authenticated requester's login and STRIPS any inbound copy of that header
# before proxying (documented anti-spoof behavior). Funnel (public internet)
# requests carry no identity header at all, so even an accidentally enabled
# funnel fails closed here. A malicious *local* process could forge the header
# by connecting to loopback directly — but a local process can already read
# state.json off disk, so that adds nothing; the header is trusted only to
# distinguish tailnet requesters from each other and from the public internet.
TS_HOST = (os.environ.get("TUTOR_TS_HOST") or "").strip().lower().rstrip(".")
TS_LOGIN = (os.environ.get("TUTOR_TS_LOGIN") or "").strip()
REMOTE_ENABLED = bool(TS_HOST and TS_LOGIN)
# https default port: browsers send the bare host; be exact about the one
# variant with an explicit port rather than parsing.
REMOTE_HOSTS = frozenset({TS_HOST, "%s:443" % TS_HOST}) if REMOTE_ENABLED else frozenset()
REMOTE_ORIGINS = frozenset({"https://%s" % TS_HOST}) if REMOTE_ENABLED else frozenset()
if REMOTE_ENABLED:
    ALLOWED_HOSTS = LOCAL_HOSTS | REMOTE_HOSTS

# Header-injected proxy headers, not the Host header, decide who must pass the
# identity pin.
#
# Why not the Host header: `tailscale serve` routes by TLS SNI and forwards the
# client's Host header verbatim, so a tailnet peer picks whatever Host it likes.
# A Host-based _is_remote_request() would let that peer answer False, skip the
# pin, and collect the session cookie from GET /. Letting the REQUESTER decide
# whether the lock applies is the defect this avoids.
#
# The decision rests on what the proxy injects instead. `tailscale serve`
# INJECTS Tailscale-User-Login on every proxied tailnet request and STRIPS any
# inbound copy before proxying (documented anti-spoof behavior), and marks
# public Funnel requests with Tailscale-Funnel-Request. A tailnet peer can
# neither forge nor suppress those. Their presence marks a request as proxied,
# so every proxied request reaches the pin no matter which Host it claims. A
# direct loopback request carries neither header and is checked against the
# loopback host allowlist. That is also what keeps this Mac's own browser, and
# every MagicDNS alias this node answers to, able to reach the page.
#
# Residual risk: a malicious LOCAL process could connect straight to loopback
# with no headers and be treated as local — but such a process can already read
# state.json off disk, so this adds nothing.
PROXY_MARK_HEADERS = ("Tailscale-User-Login", "Tailscale-Funnel-Request")


def _is_remote_request(headers) -> bool:
    """True when this request arrived through the tailscale serve proxy."""
    if not REMOTE_ENABLED:
        return False
    if str(headers.get("Host", "") or "").strip().lower() in REMOTE_HOSTS:
        return True
    return any(str(headers.get(name, "") or "").strip()
               for name in PROXY_MARK_HEADERS)


def _origins_for(headers) -> frozenset:
    """Origins a page served to THIS requester is allowed to present.

    A proxied request has already been pinned to the candidate by the time this
    is consulted, and `tailscale serve` terminates TLS, so the page it received
    was served from https://<the Host it asked for>. Deriving the origin from
    that request keeps the check same-origin while surviving every MagicDNS
    alias this node answers to — which a fixed TS_HOST list does not.
    """
    if not _is_remote_request(headers):
        return LOCAL_ORIGINS
    host = str(headers.get("Host", "") or "").strip().lower()
    if not host:
        return REMOTE_ORIGINS
    if host.endswith(":443"):
        host = host[:-4]
    return REMOTE_ORIGINS | frozenset({"https://%s" % host})


def _allowed_host(host: str) -> bool:
    return str(host or "").strip().lower() in ALLOWED_HOSTS


def _remote_identity_ok(headers) -> bool:
    """The proxied requester is the candidate, exactly.

    Compared with compare_digest out of habit, not necessity — the login is not
    secret, but timing discipline costs one line. An empty header (funnel /
    unauthenticated) can never match a non-empty pinned login.
    """
    # Funnel puts the URL on the public internet and forwards requests with no
    # user identity. Upstream sets Tailscale-Funnel-Request: ?1 on every one of
    # them. Refuse on that affirmative signal rather than inferring from the
    # absence of a login header, and refuse before comparing anything.
    if str(headers.get("Tailscale-Funnel-Request", "") or "").strip():
        return False
    presented = str(headers.get("Tailscale-User-Login", "") or "")
    # Bytes, not str: compare_digest raises TypeError on non-ASCII str input,
    # and a forged non-ASCII header must fail closed as a 403, never a 500.
    return bool(TS_LOGIN) and secrets.compare_digest(
        presented.encode("utf-8", "surrogateescape"), TS_LOGIN.encode("utf-8"))


def _static_route_allowed(route: str) -> bool:
    return route in ("/", "/index.html")


def _safe_messages(raw):
    """Validate a conversation and keep the newest turns that fit the budget.

    Trimmed, not refused. A step worked through thirty exchanges legitimately
    exceeds any turn ceiling, the page has no way to shorten a log it has
    already stored, and trim_history() cuts the window to sixteen turns on the
    very next call anyway. Refusing here would end that step permanently in
    order to protect a bound nothing downstream needs.

    A malformed message is still refused, because that is a client defect
    rather than a long conversation.
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("Conversation is empty.")
    clean = []
    for item in raw:
        if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
            raise ValueError("Conversation roles are invalid.")
        content = item.get("content")
        if not isinstance(content, str) or len(content) > MAX_MESSAGE_CHARS:
            raise ValueError("A conversation message is too large.")
        clean.append({"role": item["role"], "content": content})
    out, total = [], 0
    for item in reversed(clean):                 # newest first, at least one
        if out and (len(out) >= MAX_MESSAGE_COUNT
                    or total + len(item["content"]) > MAX_MESSAGE_TOTAL_CHARS):
            break
        out.append(item)
        total += len(item["content"])
    out.reverse()
    return out


def _safe_assess_items(raw):
    if not isinstance(raw, list) or not raw:
        raise ValueError("Nothing to assess.")
    out = []
    for item in raw[:MAX_ASSESS_STEPS]:
        if not isinstance(item, dict):
            continue
        said = item.get("said") if isinstance(item.get("said"), list) else []
        out.append({
            "key": str(item.get("key") or "")[:80],
            "title": str(item.get("title") or "")[:120],
            "task": str(item.get("task") or "")[:600],
            "turns": max(0, min(500, int(item.get("turns") or 0))),
            "said": [str(x)[:ASSESS_EXCERPT] for x in said[-3:]],
            "tutor": str(item.get("tutor") or "")[:ASSESS_EXCERPT],
        })
    if not out:
        raise ValueError("Nothing to assess.")
    return out
