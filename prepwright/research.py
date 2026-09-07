"""Source fetching, source vetting, and distillation into the corpus.

The ONLY module in this package permitted network access. It runs rarely and
deliberately. Everything downstream reads what it stored, never the network,
which is what keeps a teaching turn cheap and grounded.

`intake.py` fetches a job posting through `fetch()` here rather than carrying its
own copy of the guard below. Two copies of an SSRF check is how one of them
drifts, and it also keeps the sentence above literally true.

The model never finds the sources. The candidate pastes URLs they already trust,
this module fetches them, the provider CLI distils what came back, and
`corpus.ingest_text` writes it with provenance a citation can be checked against
years later. Inverting that puts the choice of evidence back in the model's
hands, which is the grounding claim inverted.
"""

import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse

from . import config as C
from . import corpus as CO


class FetchRefused(RuntimeError):
    """The URL was refused before a byte was read. Never a network failure."""


class FetchFailed(RuntimeError):
    """The network attempt was made and did not produce a usable document."""


# ---- limits ----------------------------------------------------------------
# A fetch is a rare, deliberate act, so these are tight on purpose. A source the
# candidate cares about that does not fit is a source to excerpt by hand, not a
# reason to raise a cap.
MAX_REDIRECTS = 5
MAX_BYTES = 2 * 1024 * 1024
CONNECT_TIMEOUT = 10.0
TOTAL_TIMEOUT = 45.0
USER_AGENT = "prepwright/1.0 (local single-user study tool)"

ALLOWED_SCHEMES = ("https",)
ALLOWED_CONTENT = ("text/html", "text/plain", "text/markdown", "application/xhtml+xml",
                   "application/json", "text/x-markdown")


def _addresses_for(host, port):
    """Every address this host resolves to, or refuse.

    The test is POSITIVE: an address is allowed when it is globally routable and
    is not multicast. A negative list of "private, loopback, link-local,
    reserved" reads as if it covers the space and does not: on CPython 3.9 and
    3.14 alike, 100.64.0.1 (RFC 6598 carrier-grade NAT, which reaches a home
    router's WAN side on many networks) answers False to is_private, is_reserved,
    is_loopback AND is_link_local, so a negative list waves it through. It also
    answers False to is_global, so the positive test refuses it. Verified against
    both interpreters before this was written, not assumed.

    EVERY address is checked, not the first. A host that resolves to one public
    and one private address must be refused: which one gets connected to is the
    resolver's choice, not ours.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM,
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise FetchRefused("%s does not resolve (%s)" % (host, exc.args[-1]))
    seen = []
    for family, _type, _proto, _canon, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise FetchRefused("%s resolved to something that is not an address" % host)
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            # ::ffff:10.0.0.1 is a private address wearing an IPv6 spelling.
            # Judge the address it actually names.
            ip = mapped
        if not ip.is_global or ip.is_multicast:
            raise FetchRefused(
                "%s resolves to %s, which is not a public address; Prepwright does"
                " not fetch from private, loopback, link-local, carrier-NAT or"
                " metadata space" % (host, ip))
        seen.append((family, ip))
    if not seen:
        raise FetchRefused("%s resolved to no address at all" % host)
    return seen


def _split(url):
    """Scheme, host, port and path for a URL this module is willing to open."""
    parts = urllib.parse.urlsplit(str(url or "").strip())
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise FetchRefused(
            "only https is fetched; %r asks for %r"
            % (url, parts.scheme or "no scheme"))
    if not parts.hostname:
        raise FetchRefused("%r names no host" % (url,))
    if parts.username or parts.password:
        # Credentials in a URL end up in a stored provenance record and in a log
        # line. Refuse rather than redact: the candidate can paste the URL again
        # without them.
        raise FetchRefused("the URL carries credentials; paste it without them")
    try:
        port = parts.port or 443
    except ValueError:
        raise FetchRefused("%r names a port that is not a number" % (url,))
    path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
    return parts.hostname, int(port), path


def _one_hop(url, deadline):
    """One request, connected to an address that was checked a moment ago.

    The connection is opened to the RESOLVED ADDRESS with the Host header and the
    TLS server name set to the original hostname. Handing a hostname to
    http.client would let it resolve a second time, and the answer to that second
    lookup is not the answer this function checked: a DNS record with a one
    second TTL can return a public address to the check and a private one to the
    connect. Pinning the address closes that window; the certificate is still
    validated against the name, so pinning costs no authentication.
    """
    host, port, path = _split(url)
    _family, ip = _addresses_for(host, port)[0]
    left = max(1.0, deadline - time.time())
    ctx = ssl.create_default_context()
    # Constructed with the NAME, so http.client sets server_hostname from it and
    # the certificate is validated against the name the candidate pasted. The
    # socket underneath is then forced to the address that was just checked:
    # HTTPConnection.connect() calls self._create_connection(...), and replacing
    # that is the documented seam for exactly this. Handing the IP to the
    # constructor instead would send the IP as SNI and fail every ordinary
    # certificate, which is how a "pinned" fetcher quietly becomes an unverified
    # one when somebody later disables the check to make it work.
    conn = http.client.HTTPSConnection(
        host, port, timeout=min(CONNECT_TIMEOUT, left), context=ctx)
    conn._create_connection = (
        lambda _address, timeout=None, source_address=None:
        socket.create_connection((str(ip), port),
                                 timeout or min(CONNECT_TIMEOUT, left)))
    try:
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", host if port == 443 else "%s:%d" % (host, port))
        conn.putheader("User-Agent", USER_AGENT)
        conn.putheader("Accept", "text/html,text/plain,text/markdown;q=0.9,*/*;q=0.1")
        conn.putheader("Accept-Encoding", "identity")
        conn.putheader("Connection", "close")
        conn.endheaders()
        resp = conn.getresponse()
        status = resp.status
        location = resp.getheader("Location") or ""
        ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
        # read() is bounded by one more byte than the cap, so an oversized body
        # is DETECTED rather than silently truncated into a document that reads
        # as complete and ends mid-sentence.
        body = resp.read(MAX_BYTES + 1) if status < 300 or status >= 400 else b""
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise FetchFailed("could not read %s (%s)" % (host, exc))
    finally:
        try:
            conn.close()
        except OSError:
            pass
    return status, location, ctype, body


def fetch(url, max_redirects=MAX_REDIRECTS):
    """Fetch one https URL, re-checking the host at every hop.

    Returns a dict carrying the bytes and everything a later reader needs to
    falsify a citation made from them: the URL asked for, the URL finally read,
    the sha256 of the exact bytes, their length, the declared content type and
    the UTC time of the fetch.

    A permitted host that redirects into private space is the whole reason the
    check runs per hop rather than once: 302 to http://169.254.169.254/ is one
    line of attacker HTML, and a check that ran only on the URL the candidate
    pasted would have already passed.
    """
    deadline = time.time() + TOTAL_TIMEOUT
    asked, current = str(url or "").strip(), str(url or "").strip()
    for hop in range(max_redirects + 1):
        if time.time() > deadline:
            raise FetchFailed("fetching %s took longer than %.0f seconds"
                              % (asked, TOTAL_TIMEOUT))
        status, location, ctype, body = _one_hop(current, deadline)
        if status in (301, 302, 303, 307, 308):
            if not location:
                raise FetchFailed("%s redirected without saying where" % current)
            nxt = urllib.parse.urljoin(current, location)
            if nxt == current:
                raise FetchFailed("%s redirects to itself" % current)
            current = nxt          # _split and _addresses_for run again next hop
            continue
        if status != 200:
            raise FetchFailed("%s answered %d" % (current, status))
        if len(body) > MAX_BYTES:
            raise FetchFailed(
                "%s is larger than %d bytes; excerpt the part you want and paste"
                " it instead" % (current, MAX_BYTES))
        if ctype and ctype not in ALLOWED_CONTENT:
            raise FetchRefused(
                "%s served %s; only text is read" % (current, ctype))
        return {
            "asked_url": asked,
            "final_url": current,
            "status": status,
            "content_type": ctype,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "redirects": hop,
            "body": body,
        }
    raise FetchFailed("%s redirected more than %d times" % (asked, max_redirects))


def text_of(fetched):
    """The decoded, redacted text of a fetch. HTML is reduced to readable prose.

    Redaction runs here as well as in `corpus.ingest_text`, because a caller may
    show this text or hand it to a distiller before anything is stored, and the
    one place a credential must never reach is a provider prompt.
    """
    body = fetched.get("body") or b""
    raw = body.decode("utf-8", "replace")
    if "html" in (fetched.get("content_type") or "") or raw.lstrip()[:1] == "<":
        raw = html_to_text(raw)
    return CO.redact(raw)


# ---- HTML to prose ---------------------------------------------------------
import re  # noqa: E402  (kept beside the only functions that use it)

_SCRIPT = re.compile(
    r"(?is)<(script|style|template|noscript|nav|footer|header|aside|form|svg)\b.*?</\1\s*>")
_COMMENT = re.compile(r"(?s)<!--.*?-->")
_BREAK = re.compile(r"(?i)<br\s*/?>")
_TITLE_TAG = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
# Headings become markdown headings, because that is what the store parses.
_H1 = re.compile(r"(?is)<h1\b[^>]*>(.*?)</h1\s*>")
_HN = re.compile(r"(?is)<h([2-6])\b[^>]*>(.*?)</h\1\s*>")
_BLOCK_END = re.compile(r"(?i)</(p|li|ul|ol|div|section|article|tr|blockquote|dd|dt)\s*>")
_LI = re.compile(r"(?i)<li\b[^>]*>")
_TAG = re.compile(r"(?s)<[^>]+>")
_WS = re.compile("[ \t\u00a0]+")
_BLANKS = re.compile(r"\n\s*\n\s*\n+")


def _flatten(fragment):
    import html as _html
    return " ".join(_html.unescape(_TAG.sub(" ", fragment)).split())


def html_to_text(markup):
    """Readable markdown from markup, with headings kept AS headings.

    Headings are the whole point. `corpus.parse_loose` splits on `## ` and drops
    everything before the first one, because the store addresses every byte it
    serves by (doc_id, sec_id) and text that cannot be cited cannot be taught
    from. The first version of this function stripped every tag including the
    headings, so a fetched page arrived as one unbroken block of prose, produced
    zero citable sections, and was refused with "nothing citable: the page has no
    headings". Every real page failed that way: the research pipeline could
    fetch and could not store a single thing.

    Deliberately not a parser. It has to survive whatever a site serves,
    including markup that never closes a tag, and the failure mode of a strict
    parser here is losing a source the candidate can read in their own browser.
    Navigation, headers and footers are dropped by tag name first, so a menu
    does not become a section.
    """
    import html as _html
    s = str(markup or "")
    found = _TITLE_TAG.search(s)
    title = _flatten(found.group(1)) if found else ""
    s = _SCRIPT.sub(" ", s)
    s = _COMMENT.sub(" ", s)
    s = _H1.sub(lambda m: "\n\n# %s\n\n" % _flatten(m.group(1)), s)
    s = _HN.sub(lambda m: "\n\n## %s\n\n" % _flatten(m.group(2)), s)
    s = _BREAK.sub("\n", s)
    s = _LI.sub("\n- ", s)
    s = _BLOCK_END.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = _html.unescape(s)
    s = _WS.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    s = _BLANKS.sub("\n\n", s).strip()
    # A page with no h1 still needs a title, or every such document is stored as
    # "(untitled)" and nothing downstream can tell two of them apart.
    if title and not s.startswith("# "):
        s = "# %s\n\n%s" % (title[:200], s)
    return s
