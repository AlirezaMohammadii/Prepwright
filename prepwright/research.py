"""Source fetching, source vetting, and distillation into the corpus.

The ONLY module in this package permitted network access. It runs rarely and
deliberately. Everything downstream reads what it stored, never the network,
which is what keeps a teaching turn cheap and grounded.

`intake.py` fetches a job posting through `fetch()` here rather than carrying its
own copy of the guard below. Two copies of an SSRF check is how one of them
drifts, and it also keeps the sentence above literally true.

Sources arrive two ways and both end at the same gate. The candidate can paste
URLs they already trust. The app can also go looking, which is what `discover`
below does. Neither one lets a model put bytes into the corpus by asserting
them: a nomination is only ever a URL, this module FETCHES that URL through the
guard above, checks that what came back actually covers the gap it was sought
for, and only then does `corpus.ingest_text` write it with provenance a citation
can be falsified against years later.

Model memory nominates. Fetched reality decides. A URL that 404s, redirects out
of the allowed space, serves the wrong content type, or answers with a page that
shares almost no vocabulary with the gap is discarded, and the discard is
recorded on the run with its reason rather than dropped in silence. That is the
difference between a research pipeline and a model repeating what it half
remembers.

Nothing here imports the bridge. The provider call is injected as `nominate` and
the store write as `ingest`, so this module keeps its one real privilege --
being the only place in the package that opens a socket -- and gains no second
one.
"""

import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import time
import urllib.parse

from . import config as C
from . import corpus as CO
from . import curriculum as CU


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
    return CO.redact(keep_lead(raw))


LEAD_HEADING = "Opening"


def keep_lead(text):
    """Prose above the first `## ` becomes a section of its own, "Opening".

    `corpus.parse_loose` drops it, which is right for a hand-written document
    and wrong for a fetched page. An arXiv abstract, a Wikipedia lead and a
    blog's opening definition all sit under the page's h1 and above its first
    h2, so the page was stored as its chrome and the one paragraph that taught
    was the one thrown away. A page with no `## ` at all is left alone and is
    still refused as having nothing citable, and a lead that is only an h1
    line adds nothing: a heading on its own is not text to teach from.
    """
    lines = str(text or "").split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), None)
    if first is None:
        return text
    top = 1 if lines[0].startswith("# ") else 0
    if not any(ln.strip() and not ln.startswith("# ") for ln in lines[top:first]):
        return text
    return "\n".join(lines[:top] + ["", "## " + LEAD_HEADING, ""] + lines[top:])


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
    s = _TITLE_TAG.sub(" ", s)
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
    if title:
        s = "# %s\n\n%s" % (title[:200], s)
    return s


# ============================================================================
# Source discovery. The app looks; the fetch decides.
# ============================================================================
# Bounds are constants, not judgement, because every loop below is driven by a
# model's answer and a loop a model can lengthen is a loop that does not
# terminate. Two rounds per gap, four candidates a round, and a wall clock over
# the whole pass.
DISCOVER_ROUNDS = 2
DISCOVER_PER_GAP = 4
DISCOVER_MIN_BODY = 400          # chars of readable prose; less is a stub page

# Coverage. A fetched page earns its place by sharing vocabulary with the gap it
# was fetched for. Both tests have to pass: a long page brushing the topic once
# clears a ratio test on a short gap, and a short page repeating one word clears
# a count test, so neither alone is a filter.
COVERAGE_MIN_TERMS = 3
COVERAGE_MIN_RATIO = 0.12

# Reliability. The nominator PROPOSES a label; the host decides what it is
# allowed to be. Without this a model can promote any blog to "primary" by
# saying so, and the whole ledger becomes decoration.
INSTITUTIONAL_SUFFIXES = (".gov", ".gov.au", ".gov.uk", ".edu", ".edu.au",
                          ".ac.uk", ".int", ".mil")
STANDARDS_HOSTS = (
    "iso.org", "iec.ch", "nist.gov", "csrc.nist.gov", "oaic.gov.au",
    "w3.org", "ietf.org", "rfc-editor.org", "ieee.org", "itu.int",
    "europa.eu", "enisa.europa.eu", "cisa.gov", "ncsc.gov.uk",
    "cyber.gov.au", "oecd.org", "un.org", "bis.org", "eur-lex.europa.eu",
)
TRUST_CEILING = {"primary": 5, "secondary": 4, "vendor": 4, "community": 2}

NOMINATE_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "publisher": {"type": "string"},
                    "vetting": {"type": "string",
                                "enum": ["primary", "secondary", "vendor",
                                         "community"]},
                    "trust": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["url", "title", "publisher", "vetting", "trust",
                             "why"],
            },
        },
    },
    "required": ["candidates"],
})

NOMINATE_SYSTEM = (
    "You find primary sources. You are given one thing a candidate has to learn "
    "for one job interview, and you return the https URLs of pages that teach it "
    "as it is practised now, in 2026.\n\n"
    "Search before you answer. Do not answer from memory: a URL you remember is "
    "a URL that may have moved, and every URL you return is fetched and read by "
    "the program that called you, so a wrong one is caught and counted against "
    "this run rather than quietly taught to someone.\n\n"
    "What counts as a good source, best first:\n"
    "  primary    the standard, the regulation, the regulator's own guidance, "
    "the specification, or the technology's own documentation. NIST, ISO, the "
    "OAIC, a government privacy regulator, an RFC, a vendor's own docs for that "
    "vendor's own product.\n"
    "  secondary  a university course page, a published handbook or textbook "
    "chapter that is readable on the open web, a reference work, or an "
    "engineering publication with a named author and a date.\n"
    "  community  everything else. Use it only when nothing better exists.\n\n"
    "Prefer a page that TEACHES over a page that announces. A landing page, a "
    "press release, a pricing page, a login wall or a PDF viewer shell teaches "
    "nothing: the fetch will read the text and discard it, so return the page "
    "with the prose on it.\n\n"
    "Return HTML, never a PDF. The fetch reads text and refuses application/pdf, "
    "so a link ending .pdf is a wasted nomination however authoritative the "
    "document behind it is. Most standards bodies publish an HTML edition beside "
    "the PDF -- the reading room, the online annex, the knowledge base, the "
    "resource page. Find that one. If a source exists only as a PDF, skip it and "
    "return the best HTML source instead.\n\n"
    "The page must carry HEADINGS. The reader splits a document into citable "
    "sections on its headings and discards a page that is one unbroken block of "
    "prose, so a well-structured article beats a longer flat one.\n\n"
    "Return fewer candidates rather than padding. Two good URLs beat six guesses. "
    "If you cannot find a real page for this, return an empty list and say so in "
    "no candidates at all rather than inventing one."
)


def nomination_prompt(gap, role="", employer="", k=DISCOVER_PER_GAP, avoid=()):
    """One gap, stated plainly, plus what has already been tried and rejected."""
    label = str(gap.get("label") or "")[:300]
    why = str(gap.get("why") or "")[:400]
    lines = []
    if role or employer:
        lines.append("The interview is for %s%s."
                     % (role or "a role",
                        (" at %s" % employer) if employer else ""))
    lines.append("What has to be learned: %s" % label)
    if why:
        lines.append("Why it is on the list: %s" % why)
    if avoid:
        lines.append(
            "Already tried and rejected, do not return these again:\n%s"
            % "\n".join("  - %s" % u for u in list(avoid)[:20]))
    lines.append(
        "Return at most %d https URLs that teach this. Search for them." % int(k))
    return "\n\n".join(lines)


def host_of(url):
    """The host of a URL, lowercased, or "" when there is not one."""
    try:
        return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def registrable(host):
    """The host with a leading www. removed. Not a public-suffix parser.

    Deliberately shallow: it feeds a display field and a suffix test, and a real
    public-suffix list is a dependency this package will not take.
    """
    host = str(host or "").lower()
    return host[4:] if host.startswith("www.") else host


def institutional(host):
    """Is this host a standards body, a regulator or an academic institution?

    A positive list plus a suffix test. Both are checked on the FINAL host, after
    redirects, because "nist.gov" that 302s to a content farm is a content farm.
    """
    host = registrable(host)
    if not host:
        return False
    if host in STANDARDS_HOSTS:
        return True
    if any(host == s or host.endswith("." + s) for s in STANDARDS_HOSTS):
        return True
    return any(host.endswith(sfx) for sfx in INSTITUTIONAL_SUFFIXES)


def vetting_for(host, proposed):
    """The label this source is ALLOWED to carry, given where it actually lives.

    The nominator proposes; the host disposes. `primary` survives only on a
    standards body, a regulator or an academic host, because primary means "the
    document that defines the thing", and an article about a standard is not the
    standard. Everything else is capped at `secondary`, and a nominator that
    admitted `community` keeps it: nobody understates a source by accident.
    """
    proposed = str(proposed or "").lower()
    if proposed not in ("primary", "secondary", "vendor", "community"):
        proposed = "community"
    if proposed == "community":
        return "community"
    if institutional(host):
        return "primary" if proposed == "primary" else proposed
    # Not institutional: primary is not available, whatever was claimed.
    return "vendor" if proposed == "vendor" else "secondary"


def trust_for(vetting, proposed):
    """Trust, clamped to 1-5 and then capped by what the vetting label allows."""
    try:
        n = int(proposed)
    except (TypeError, ValueError):
        n = 3
    n = max(1, min(5, n))
    return min(n, TRUST_CEILING.get(vetting, 2))


# A date the PAGE states, not a date anybody remembers. Only clearly labelled
# forms are read: a bare four-digit year anywhere on a page is as likely to be a
# copyright footer or a citation as a publication date, and a wrong date is worse
# than none because the tutor will repeat it.
_MONTHS = ("january february march april may june july august september"
           " october november december").split()
_DATE_ISO = re.compile(r"(?i)\b(?:published|updated|last\s+(?:updated|revised|"
                       r"modified)|revised|effective|issued|date)\b[^0-9]{0,24}"
                       r"(20[0-9]{2})-(0[1-9]|1[0-2])(?:-(0[1-9]|[12][0-9]|3[01]))?")
_DATE_WORDS = re.compile(
    r"(?i)\b(?:published|updated|last\s+(?:updated|revised|modified)|revised|"
    r"effective|issued)\b[^A-Za-z0-9]{0,24}"
    r"(?:([0-9]{1,2})\s+)?(" + "|".join(_MONTHS) + r")[a-z]*\.?,?\s+(20[0-9]{2})")


def published_on_from(text):
    """An ISO-ish publication date this page states about itself, or None.

    Returns "YYYY-MM" or "YYYY-MM-DD". None is a normal answer and is the honest
    one for a page that never says: the tutor is told to say the sources do not
    state a date rather than supply one from memory.
    """
    head = str(text or "")[:8000]
    m = _DATE_ISO.search(head)
    if m:
        return ("%s-%s-%s" % (m.group(1), m.group(2), m.group(3))
                if m.group(3) else "%s-%s" % (m.group(1), m.group(2)))
    m = _DATE_WORDS.search(head)
    if m:
        month = _MONTHS.index(m.group(2).lower()) + 1
        return ("%s-%02d-%02d" % (m.group(3), month, int(m.group(1)))
                if m.group(1) else "%s-%02d" % (m.group(3), month))
    return None


def coverage(text, want_terms):
    """How much of a gap's vocabulary this document actually carries.

    Returns (hits, ratio, sample). Uses the curriculum's own tokenizer so a page
    is judged by the same vocabulary that will later decide which of its sections
    a step pins. Two scorers disagreeing about what a word is would let a page in
    that the planner then finds nothing in.
    """
    want = set(want_terms or ())
    if not want:
        return 0, 0.0, []
    have = set(CU.terms(text))
    hit = sorted(want & have)
    return len(hit), len(hit) / float(len(want)), hit[:12]


def gap_terms(gap):
    return set(CU.terms("%s %s" % (gap.get("label") or "", gap.get("why") or "")))


def _clean_candidates(raw, seen, limit):
    """Well-formed https candidates, deduped, ranked best-label-first."""
    out = []
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()[:2000]
        if not url or url in seen:
            continue
        host = host_of(url)
        if not host or not url.lower().startswith("https://"):
            continue
        vetting = vetting_for(host, item.get("vetting"))
        out.append({
            "url": url,
            "claimed_title": str(item.get("title") or "")[:200],
            "claimed_publisher": str(item.get("publisher") or "")[:200],
            "vetting": vetting,
            "trust": trust_for(vetting, item.get("trust")),
            "why": str(item.get("why") or "")[:300],
        })
    order = {"primary": 0, "vendor": 1, "secondary": 2, "community": 3}
    out.sort(key=lambda c: (order.get(c["vetting"], 9), -c["trust"]))
    kept = out[:max(1, int(limit))]
    # Marked seen only AFTER the cut, and only for the ones being returned to be
    # fetched. Marking every nomination meant the ones the per-gap cap discarded
    # unfetched were blacklisted for the rest of the run, so round two could not
    # legitimately re-offer a good source that round one had merely not got to.
    # `seen` means "this has been tried", and an untried URL has not been.
    for c in kept:
        seen.add(c["url"])
    return kept


def verify(candidate, want_terms, fetch_fn=None):
    """Fetch one candidate and decide whether it may enter the corpus.

    Every rejection carries the reason it was rejected, because a ledger that
    shows only what landed cannot be audited: the interesting question about an
    automated researcher is what it threw away.
    """
    fetch_fn = fetch_fn or fetch
    url = candidate["url"]
    try:
        got = fetch_fn(url)
    except FetchRefused as exc:
        return {"ok": False, "url": url, "why": "refused: %s" % exc,
                "stage": "fetch"}
    except FetchFailed as exc:
        return {"ok": False, "url": url, "why": "unreachable: %s" % exc,
                "stage": "fetch"}
    text = text_of(got)
    if len(text.strip()) < DISCOVER_MIN_BODY:
        return {"ok": False, "url": url, "stage": "body",
                "why": "the page carried %d characters of readable text, which is"
                       " a stub or a wrapper rather than something to learn from"
                       % len(text.strip())}
    kept = CO.fit_sections(CO.parse_loose(text)[1])
    if not kept:
        return {"ok": False, "url": url, "stage": "sections",
                "why": "nothing citable: the page has no headings"}
    hits, ratio, sample = coverage(
        "\n".join("%s\n%s" % (k["heading"], k["body"]) for k in kept), want_terms)
    if hits < COVERAGE_MIN_TERMS or ratio < COVERAGE_MIN_RATIO:
        return {"ok": False, "url": url, "stage": "coverage",
                "why": "the page answered but does not cover this: it shares %d"
                       " of %d terms with the gap (%.0f%%), below the %d-term,"
                       " %.0f%% floor"
                       % (hits, len(want_terms), 100.0 * ratio,
                          COVERAGE_MIN_TERMS, 100.0 * COVERAGE_MIN_RATIO)}
    final_host = host_of(got["final_url"])
    vetting = vetting_for(final_host, candidate["vetting"])
    return {
        "ok": True, "url": url, "stage": "stored",
        "text": text, "fetched": got,
        "final_url": got["final_url"],
        # Read off the page and off the address it finally answered from. The
        # nominator's own claims about title, publisher and date are dropped
        # here on purpose: they are the half of a nomination nothing checked.
        "publisher": registrable(final_host),
        "published_on": published_on_from(text),
        "vetting": vetting,
        "trust": trust_for(vetting, candidate["trust"]),
        "coverage": {"terms": hits, "ratio": round(ratio, 3), "sample": sample},
        "why": candidate.get("why", ""),
    }


def discover(gaps, nominate, ingest, role="", employer="", per_gap=None,
             rounds=DISCOVER_ROUNDS, budget_seconds=None, max_docs=None,
             max_per_gap=1, fetch_fn=None):
    """Find, fetch, verify and store sources for a list of gaps.

    `nominate(system, prompt, schema)` returns the model's raw JSON text; `ingest`
    (candidate_result, gap) stores an accepted document and returns its doc id or
    None. Both are injected: this module gets no provider and no store of its own.

    Returns one record per gap carrying what landed, what was thrown away and
    why, plus a flat `discarded` list for the ledger. A gap that finds nothing is
    reported as finding nothing; it is not an error, and it is not hidden.

    `max_per_gap` defaults to ONE, and the default is the whole point. A track
    holds 48 documents. Twenty gaps taking three sources each is sixty, so a
    generous early gap eats the budget of a later one and the plan arrives with
    half its steps unteachable. Breadth first: every gap gets its best source
    before any gap gets its second. Raising this is a decision to teach fewer
    things more deeply, which is a real choice, but it is a choice and not a
    default.
    """
    per_gap = DISCOVER_PER_GAP if per_gap is None else max(1, int(per_gap))
    deadline = (time.time() + float(budget_seconds)) if budget_seconds else None
    records, discarded, stored_total = [], [], 0
    seen = set()

    for gap in gaps:
        rec = {"gap_id": gap.get("gap_id"), "label": gap.get("label"),
               "stored": [], "discarded": [], "asked": 0}
        want = gap_terms(gap)
        tried = []
        for _round in range(max(1, int(rounds))):
            if deadline and time.time() > deadline:
                rec["stopped"] = "the discovery budget ran out before this gap"
                break
            if max_docs is not None and stored_total >= max_docs:
                rec["stopped"] = "the corpus is full for this run"
                break
            if len(rec["stored"]) >= max(1, int(max_per_gap)):
                break                       # this gap has what it was allotted
            rec["asked"] += 1
            prompt = nomination_prompt(gap, role, employer, per_gap, avoid=tried)
            try:
                raw = nominate(NOMINATE_SYSTEM, prompt, NOMINATE_SCHEMA)
                parsed = json.loads(raw or "{}")
            except (ValueError, TypeError):
                parsed = {}
            except RuntimeError as exc:
                rec["stopped"] = "the provider could not be asked: %s" % exc
                break
            cands = _clean_candidates(parsed.get("candidates"), seen, per_gap)
            if not cands:
                rec["stopped"] = "nothing new was nominated for this gap"
                break
            for cand in cands:
                if len(rec["stored"]) >= max(1, int(max_per_gap)):
                    break
                if deadline and time.time() > deadline:
                    rec["stopped"] = "the discovery budget ran out mid-gap"
                    break
                if max_docs is not None and stored_total >= max_docs:
                    rec["stopped"] = "the corpus is full for this run"
                    break
                tried.append(cand["url"])
                out = verify(cand, want, fetch_fn=fetch_fn)
                if not out["ok"]:
                    entry = {"gap_id": rec["gap_id"], "url": out["url"],
                             "stage": out["stage"], "why": out["why"]}
                    rec["discarded"].append(entry)
                    discarded.append(entry)
                    continue
                doc_id = None
                try:
                    doc_id = ingest(out, gap)
                except Exception as exc:            # store refusal, cap, anything
                    entry = {"gap_id": rec["gap_id"], "url": out["url"],
                             "stage": "store", "why": str(exc)[:300]}
                    rec["discarded"].append(entry)
                    discarded.append(entry)
                    continue
                if doc_id is None:
                    entry = {"gap_id": rec["gap_id"], "url": out["url"],
                             "stage": "sections",
                             "why": "nothing citable: the page has no headings"}
                    rec["discarded"].append(entry)
                    discarded.append(entry)
                    continue
                stored_total += 1
                rec["stored"].append({  # noqa: the cap is checked after append
                    "docId": doc_id, "url": out["url"],
                    "finalUrl": out["final_url"], "publisher": out["publisher"],
                    "publishedOn": out["published_on"], "vetting": out["vetting"],
                    "trust": out["trust"], "coverage": out["coverage"],
                    "bytes": out["fetched"]["bytes"],
                    "sha256": out["fetched"]["sha256"],
                    "fetchedUtc": out["fetched"]["fetched_utc"],
                })
        records.append(rec)
    return {"gaps": records, "discarded": discarded, "stored": stored_total}
