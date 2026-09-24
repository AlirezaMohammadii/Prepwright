"""Job description ingest: the paste box and the folder import.

Two sources, one canonical record. The paste box is first-class: the candidate
may study something no job posting mentions, and a page that will not part with
its body is answered by asking for a paste rather than by failing.

Everything here reads. The only writes are into the track this module creates:
the external resume folder is read exactly once, at track creation, and hashed,
so a later edit on that side cannot retroactively change what the track was built
from. DESIGN-state-corpus.md:407 states that rule and this module keeps it.

Network access lives in `research.py`, which is the one module permitted it. A
second copy of the SSRF guard is how one of the two drifts.
"""

import hashlib
import html as _html
import json
import os
import re
import time

from . import config as C
from . import corpus as CO
from . import research as R
from . import state as S
from . import track as T


class IntakeRefused(ValueError):
    """The input cannot become a track. The message says what to do instead."""


# A posting is capped by the store at INTAKE_MAX_BYTES. Truncation is explicit
# and marked: a silently shortened posting reads as a complete requirement list
# that happens to stop early, which is the confident-and-wrong failure the whole
# design exists to prevent.
TRUNCATED = "\n\n[TRUNCATED: the posting was longer than this track stores]"


# ---- pulling a posting out of a page ---------------------------------------
# Ordered most to least structured. Each returns None rather than guessing, so a
# page whose shape has changed falls through to the next reader and finally to
# "paste it instead" rather than to a plausible wrong answer.

_LD_JSON = re.compile(
    r"(?is)<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>")
_META = (r"(?is)<meta[^>]+(?:property|name)=[\"']%s[\"'][^>]+content=[\"']([^\"']*)")
_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
# LinkedIn's job body. Kept first because it is the page this was built against,
# and it is checked by name so a class rename is a miss rather than a wrong body.
_LINKEDIN_BODY = re.compile(
    r"(?is)<div[^>]*class=[\"'][^\"']*show-more-less-html__markup"
    r"[^\"']*[\"'][^>]*>(.*?)</div>")
_GENERIC_BODY = re.compile(
    r"(?is)<(?:div|section|article)[^>]*(?:class|id)=[\"'][^\"']*"
    r"(?:job[-_]?description|jobDescription|description__text|posting-body)"
    r"[^\"']*[\"'][^>]*>(.*?)</(?:div|section|article)>")

# "Role at Employer — Location | Board". The separator set is wide because job
# boards disagree about which dash they use, and the trailing board name is
# dropped rather than trusted.
_TITLE_SHAPE = re.compile(
    r"^\s*(?P<role>.+?)\s+at\s+(?P<employer>.+?)"
    r"(?:\s*[—–‒-]\s*(?P<location>[^|]+?))?"
    r"(?:\s*[|•]\s*[^|•]+)?\s*$")


def unescape(text, rounds=3):
    """HTML-unescape until stable, bounded.

    LinkedIn serves `&amp;amp;` in og:title: escaped twice, so one pass leaves
    `&amp;` sitting in an employer name that then never matches anything.
    Bounded, because unescaping to a fixed point on hostile input is a loop.
    """
    s = str(text or "")
    for _ in range(rounds):
        out = _html.unescape(s)
        if out == s:
            break
        s = out
    return s


def _meta(markup, name):
    m = re.search(_META % re.escape(name), markup)
    return unescape(m.group(1)).strip() if m else ""


def _from_ld_json(markup):
    """schema.org JobPosting, if the page publishes one. The best source there is.

    LinkedIn does not always serve it (it did not on the fetch this was written
    against), so this is tried and allowed to miss rather than relied on.
    """
    for block in _LD_JSON.findall(markup):
        try:
            doc = json.loads(block.strip())
        except ValueError:
            continue
        for item in (doc if isinstance(doc, list) else [doc]):
            if not isinstance(item, dict):
                continue
            if item.get("@type") != "JobPosting":
                continue
            org = item.get("hiringOrganization")
            employer = org.get("name") if isinstance(org, dict) else org
            loc = item.get("jobLocation")
            if isinstance(loc, list):
                loc = loc[0] if loc else None
            city = region = ""
            if isinstance(loc, dict):
                addr = loc.get("address") or {}
                if isinstance(addr, dict):
                    city = addr.get("addressLocality") or ""
                    region = addr.get("addressRegion") or ""
            return {
                "role_title": str(item.get("title") or "").strip() or None,
                "employer": str(employer or "").strip() or None,
                "location": ", ".join(p for p in (city, region) if p) or None,
                "body": str(item.get("description") or "") or None,
                "how": "schema.org JobPosting",
            }
    return None


def _from_title(markup):
    """`<og:title>` or `<title>`, matched against the "Role at Employer" shape."""
    for raw in (_meta(markup, "og:title"), _meta(markup, "twitter:title"),
                unescape((_TITLE.search(markup) or [None, ""])[1]).strip()
                if _TITLE.search(markup) else ""):
        if not raw:
            continue
        m = _TITLE_SHAPE.match(raw)
        if not m:
            continue
        role = (m.group("role") or "").strip()
        employer = (m.group("employer") or "").strip()
        if not role or not employer:
            continue
        return {"role_title": role[:200], "employer": employer[:200],
                "location": ((m.group("location") or "").strip() or None),
                "body": None, "how": "page title"}
    return None


# The named elements a job board puts its identity in, when the title tag will
# not give it up. LinkedIn served a variant page during a real track creation
# where the title shape was absent: the body still parsed, so the track was
# built on the right requirements, but it was called "Untitled posting" with no
# employer and no role. The content survived and the identity did not, which is
# the wrong half to lose. Matched by class NAME, so a rename is a miss rather
# than a wrong answer.
_TOPCARD = {
    "role_title": (r'(?is)class="[^"]*(?:topcard__title|jobs-unified-top-card__job-title|'
                   r'job-details-jobs-unified-top-card__job-title)[^"]*"[^>]*>(.*?)<',
                   r"(?is)<h1[^>]*>(.*?)</h1>"),
    "employer": (r'(?is)class="[^"]*(?:topcard__org-name-link|topcard__flavor(?!--bullet)|'
                 r'jobs-unified-top-card__company-name)[^"]*"[^>]*>(.*?)<',),
    "location": (r'(?is)class="[^"]*(?:topcard__flavor--bullet|'
                 r'jobs-unified-top-card__bullet)[^"]*"[^>]*>(.*?)<',),
}


def _from_topcard(markup):
    """Role, employer and location from the page's own header elements."""
    found = {}
    for field, patterns in _TOPCARD.items():
        for pattern in patterns:
            hits = re.findall(pattern, markup)
            value = unescape(hits[0]).strip() if hits else ""
            value = " ".join(value.split())
            if value and len(value) <= 200 and "<" not in value:
                found[field] = value
                break
    if not found.get("role_title") and not found.get("employer"):
        return None
    found["body"] = None
    found["how"] = "page header"
    return found


def _body_from(markup):
    """The requirement text, or None. Never the whole page dressed as a posting."""
    for pattern in (_LINKEDIN_BODY, _GENERIC_BODY):
        m = pattern.search(markup)
        if m and len(m.group(1)) > 200:
            return R.html_to_text(m.group(1))
    return None


def parse_posting_page(markup, url=""):
    """Employer, role, location and requirement text from one fetched page.

    `confident` is False when the body could not be located. The caller shows the
    paste box then, because a posting silently replaced by page furniture is a
    track built on the wrong requirements, and every gap derived from it is wrong
    in a way nothing downstream can detect.
    """
    markup = str(markup or "")
    # Most structured first. Each reader fills only what the ones before it left
    # empty, so a page that publishes half its identity in one place and half in
    # another still comes out whole.
    found = {}
    for reader in (_from_ld_json, _from_title, _from_topcard):
        if found.get("employer") and found.get("role_title"):
            break
        got = reader(markup) or {}
        found = dict(got, **{k: v for k, v in found.items() if v})
    body = found.get("body")
    if body and "<" in body:
        body = R.html_to_text(body)
    if not body:
        body = _body_from(markup)
    return {
        "employer": found.get("employer"),
        "role_title": found.get("role_title"),
        "location": found.get("location"),
        "text": CO.redact(body or ""),
        "confident": bool(body),
        "how": found.get("how") or ("page body" if body else "nothing usable"),
        "note": "" if body else (
            "The posting body could not be found on %s. The page may need a login,"
            " or its markup changed. Copy the description from your browser and"
            " paste it instead." % (url or "that page")),
    }


# ---- the three ways in -----------------------------------------------------
def posting_from_url(url):
    """Fetch one https URL and read a posting out of it.

    Raises FetchRefused / FetchFailed from `research`. A page that answers but
    yields no body is NOT an error: it comes back with confident False, so the
    caller can offer the paste box with the employer and role already filled in.
    """
    got = R.fetch(url)
    markup = (got["body"] or b"").decode("utf-8", "replace")
    posting = parse_posting_page(markup, url=got["final_url"])
    posting.update({
        "source_kind": "pasted",
        "asked_url": got["asked_url"],
        "final_url": got["final_url"],
        "origin_sha256": got["sha256"],
        "origin_bytes": got["bytes"],
        "captured_utc": got["fetched_utc"],
    })
    return posting


def posting_from_text(text, employer=None, role_title=None, location=None,
                      url="", source_kind="pasted"):
    """A posting the candidate pasted. The text is authoritative exactly as given.

    `source_kind` "freeform" is the no-job-behind-it case the design admits at
    DESIGN-state-corpus.md:407: bare material to study with no posting at all.
    """
    if source_kind not in C.SOURCE_KINDS:
        raise IntakeRefused("unknown source kind %r" % (source_kind,))
    body = str(text or "").strip()
    if not body:
        raise IntakeRefused("nothing was pasted")
    raw = body.encode("utf-8")
    return {
        "employer": (employer or "").strip() or None,
        "role_title": (role_title or "").strip() or None,
        "location": (location or "").strip() or None,
        "text": CO.redact(body),
        "confident": True,
        "how": "pasted by the candidate",
        "note": "",
        "source_kind": source_kind,
        "asked_url": url or "",
        "final_url": url or "",
        "origin_sha256": hashlib.sha256(raw).hexdigest(),
        "origin_bytes": len(raw),
        "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ---- the resume pipeline's application folder -------------------------------
# Read once, hashed, never reopened. The names come from resume-studio's
# _file_application: every deliverable is <base><suffix> inside
# applications/<date>__<base>/.
FOLDER_PARTS = {
    "job_description": "_JobDescription.md",
    "fit_report": "_FitReport.md",
    "resume_tex": "_A_Mohammadi.tex",
    "resume_pdf": "_A_Mohammadi.pdf",
    "cover_tex": "_CoverLetter_A_Mohammadi.tex",
}
FOLDER_READ_CAP = 512 * 1024        # per file; a FitReport runs about 11 KB
_EVAL = re.compile(r"_Evaluation_R\d+\.json\Z")


def _read_file(path):
    """Bytes of one ordinary file, or None. Symlinks are refused, not followed."""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        with open(path, "rb") as fh:
            return fh.read(FOLDER_READ_CAP)
    except OSError:
        return None


def read_application_folder(path):
    """Everything one application folder holds, read exactly once and hashed.

    Returns a dict whose `manifest` lists each file read with its size and
    sha256, and whose `sha256` is a hash over that manifest. That single value is
    what the track records, so a later edit on the resume side is detectable
    without this module ever opening the folder again.

    Raises IntakeRefused when the folder is not one, which is a thing to tell the
    candidate rather than a fault to log.
    """
    root = os.path.realpath(str(path or ""))
    if not os.path.isdir(root):
        raise IntakeRefused("%s is not a folder" % (path,))
    base = os.path.basename(root).split("__", 1)[-1]
    if not base:
        raise IntakeRefused("%s does not look like an application folder" % (path,))

    found, manifest = {}, []

    def take(label, name):
        full = os.path.join(root, name)
        data = _read_file(full)
        if data is None:
            return
        found[label] = {"name": name, "path": full,
                        "text": data.decode("utf-8", "replace")
                        if not name.endswith(".pdf") else "",
                        "bytes": len(data)}
        manifest.append({"name": name, "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()})

    for label, suffix in sorted(FOLDER_PARTS.items()):
        take(label, base + suffix)
    try:
        for name in sorted(os.listdir(root)):
            if _EVAL.search(name):
                take("evaluation", name)
    except OSError:
        pass

    if not manifest:
        raise IntakeRefused(
            "%s holds none of the files a finished application leaves behind" % (path,))
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "base": base,
        "path": root,
        "files": found,
        "manifest": manifest,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "read_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def split_application_base(base, posting=""):
    """(role, employer) read out of `<Position>_<Company>`, or (None, None).

    The name marks no boundary, so it is read against the posting, as Resume
    Studio's own _split_application_base does: the longest suffix of at most four
    tokens the posting carries is the employer's name, and with no such suffix
    the last token is the only reading the name supports. The last-token split
    alone gave "Melbourne" for University_Of_Example and "Research" for
    Northwind_Research.
    """
    tokens = [t for t in str(base or "").split("_") if t]
    if len(tokens) < 2:
        return None, None
    take = 1
    text = " ".join(str(posting or "").split()).lower()
    for n in range(2, min(4, len(tokens) - 1) + 1):
        if " ".join(tokens[-n:]).lower() in text:
            take = n
    return " ".join(tokens[:-take]), " ".join(tokens[-take:])


def posting_from_application(application):
    """The posting a finished application was built against, if it kept one.

    resume-studio writes <base>_JobDescription.md at DONE, carrying the text and a
    provenance block. Returns None when the folder predates that change, which is
    a reason to ask for the posting, not a reason to invent one from the resume.
    """
    doc = (application.get("files") or {}).get("job_description")
    if not doc or not doc.get("text"):
        return None
    text = doc["text"]
    meta = {}
    if "```json" in text:
        try:
            meta = json.loads(text.split("```json", 1)[1].split("```", 1)[0])
        except (ValueError, IndexError):
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    body = text.split("## Posting", 1)[-1].lstrip("\n").strip() if "## Posting" in text else ""
    if not body or body.startswith("(No posting text"):
        return None
    # Resume Studio writes `position` and `company` into the provenance block
    # since 2026-09-24. They are read first: the folder name marks no boundary
    # between the two, and splitting it at the last underscore opened
    # 2026-09-24__Research_Fellow_University_Of_Example as employer
    # "Melbourne", role "Research Fellow University Of".
    role = str(meta.get("position") or "").strip()[:200] or None
    employer = str(meta.get("company") or "").strip()[:200] or None
    name = application.get("base") or ""
    if not (role and employer):
        # An older folder: applications/<date>__<Position>_<Company>/.
        role, employer = split_application_base(name, body)
    return dict(
        posting_from_text(body, employer=employer, role_title=role,
                          url=meta.get("source_url") or "",
                          source_kind="imported"),
        captured_utc=meta.get("captured_utc") or time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        how="imported from %s" % doc["name"],
    )


# ---- creating the track ----------------------------------------------------
def _title_for(posting):
    role = posting.get("role_title")
    employer = posting.get("employer")
    if role and employer:
        return ("%s at %s" % (role, employer))[:200]
    return (role or employer or "Untitled posting")[:200]


def create_from_posting(posting, application=None, lib=None):
    """One posting, optionally one application folder, into one new track.

    The order matters. The track is created, then `intake/source.txt` is written
    from the exact text the track was built on, then the intake row records the
    body with the source path and hash. DESIGN-state-corpus.md:407 requires the
    external file to be copied in and hashed, because the archive carries
    `intake/*` and a provenance path with nothing behind it archives to nothing.

    Returns (track_id, report).
    """
    text = str(posting.get("text") or "").strip()
    if not text:
        raise IntakeRefused(
            posting.get("note")
            or "there is no posting text to build a track from")
    raw = text.encode("utf-8")
    truncated = False
    if len(raw) > C.INTAKE_MAX_BYTES:
        keep = C.INTAKE_MAX_BYTES - len(TRUNCATED.encode("utf-8"))
        text = raw[:keep].decode("utf-8", "ignore").rstrip() + TRUNCATED
        raw = text.encode("utf-8")
        truncated = True

    kind = posting.get("source_kind") or "pasted"
    if kind not in C.SOURCE_KINDS:
        raise IntakeRefused("unknown source kind %r" % (kind,))

    source_path = None
    source_sha = posting.get("origin_sha256")
    if application:
        source_path = application["path"]
        source_sha = application["sha256"]
        kind = "imported"

    track_id = T.create_track(
        _title_for(posting), kind="job" if kind != "freeform" else "topic",
        source_kind=kind,
        employer=posting.get("employer"), role_title=posting.get("role_title"),
        source_path=source_path or (posting.get("final_url") or None),
        source_sha256=source_sha, lib=lib)

    handle = S.open_track(track_id, lib=lib, client_label="intake")
    try:
        intake_dir = os.path.join(handle.dir, "intake")
        S.ensure_dir(intake_dir)
        S.atomic_write(os.path.join(intake_dir, "source.txt"), text)
        S.atomic_write(
            os.path.join(intake_dir, "provenance.json"),
            json.dumps({k: posting.get(k) for k in (
                "asked_url", "final_url", "origin_sha256", "origin_bytes",
                "captured_utc", "how", "employer", "role_title", "location",
                "source_kind")}, indent=1, sort_keys=True))
        if application:
            S.atomic_write(
                os.path.join(intake_dir, "application.json"),
                json.dumps({"path": application["path"],
                            "base": application["base"],
                            "read_utc": application["read_utc"],
                            "sha256": application["sha256"],
                            "manifest": application["manifest"]},
                           indent=1, sort_keys=True))
            for label in ("fit_report", "job_description"):
                got = (application.get("files") or {}).get(label)
                if got and got.get("text"):
                    S.atomic_write(os.path.join(intake_dir, got["name"]),
                                   CO.redact(got["text"]))
        handle.set_intake(kind, text, source_path=source_path,
                          source_sha256=source_sha)
    finally:
        handle.close()

    return track_id, {
        "track_id": track_id,
        "title": _title_for(posting),
        "employer": posting.get("employer"),
        "role_title": posting.get("role_title"),
        "location": posting.get("location"),
        "source_kind": kind,
        "how": posting.get("how"),
        "bytes": len(raw),
        "truncated": truncated,
        "application": (application or {}).get("path"),
        "confident": bool(posting.get("confident")),
    }


def intake_from_url(url, application_folder=None, lib=None):
    """Fetch a posting and build a track from it. The URL path."""
    posting = posting_from_url(url)
    if not posting["confident"]:
        raise IntakeRefused(posting["note"])
    app = read_application_folder(application_folder) if application_folder else None
    return create_from_posting(posting, application=app, lib=lib)


def intake_from_text(text, employer=None, role_title=None, location=None,
                     url="", application_folder=None, source_kind="pasted",
                     lib=None):
    """Build a track from pasted text. The path that always works."""
    posting = posting_from_text(text, employer=employer, role_title=role_title,
                                location=location, url=url,
                                source_kind=source_kind)
    app = read_application_folder(application_folder) if application_folder else None
    return create_from_posting(posting, application=app, lib=lib)


# Read here, where check_application_folder reads it, so a test patches it here.
APPLICATIONS_ROOT = C.APPLICATIONS_ROOT
NO_POSTING = ("%s kept no job description, so there is nothing to study against."
              " Paste the posting, and pass this folder alongside it.")


def check_application_folder(path, need_posting=False):
    """The real path of an application folder intake may read, or IntakeRefused.

    The folder can arrive in a URL (Resume Studio's Prep button deep-links
    ?application=<folder>), so it is untrusted input naming a local path. Its real
    path has to sit under APPLICATIONS_ROOT, which neither `..` nor a symlink
    can leave. A folder-only intake also needs the posting the application was
    built against: a folder without a *_JobDescription.md (the INTERIM folders
    Resume Studio leaves behind a run that never finished) is refused with the
    message intake_from_application already gives.
    """
    root = APPLICATIONS_ROOT
    real = os.path.realpath(os.path.expanduser(str(path or "").strip()))
    if not (real.startswith(root + os.sep) and os.path.isdir(real)):
        raise IntakeRefused(
            "%s is not an application folder under %s" % (path, root))
    base = os.path.basename(real).split("__", 1)[-1]
    if need_posting:
        try:
            kept = [n for n in os.listdir(real) if n.endswith("_JobDescription.md")
                    and os.path.isfile(os.path.join(real, n))
                    and not os.path.islink(os.path.join(real, n))]
        except OSError:
            kept = []
        if not kept:
            raise IntakeRefused(NO_POSTING % base)
    return real


def application_preview(folder):
    """What a folder-only intake would open, read before anyone confirms it."""
    real = check_application_folder(folder, need_posting=True)
    posting = posting_from_application(read_application_folder(real))
    if posting is None:
        raise IntakeRefused(NO_POSTING % os.path.basename(real).split("__", 1)[-1])
    return {"folder": real, "employer": posting.get("employer"),
            "roleTitle": posting.get("role_title"), "title": _title_for(posting)}


def intake_from_application(folder, lib=None):
    """Build a track from a finished application folder alone.

    Refuses rather than falling back to the resume when the folder kept no
    posting: a track built from the answers instead of the questions produces a
    gap list that agrees with itself.
    """
    app = read_application_folder(folder)
    posting = posting_from_application(app)
    if posting is None:
        raise IntakeRefused(NO_POSTING % app["base"])
    return create_from_posting(posting, application=app, lib=lib)
