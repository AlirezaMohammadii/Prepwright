"""The distilled document store, its index, and citation lookup.

Implements DESIGN-state-corpus.md sections E and G. Reads are contained by realpath
under exactly one track's subtree and refuse symlinks outright, which is what
makes cross-track isolation a mechanism rather than a promise.

Status: owns redaction, loose-document parsing, the one-time directory seed, and
the prompt-ready evidence pack. `bridge.corpus_evidence` and its private helpers
are superseded by `pack_text()`, which reads a track handle rather than a shared
directory.

The shared `corpus/` directory this module seeds FROM is a development fixture,
not a runtime store. A track's real corpus lives inside that track, and nothing
here reads a second track's bytes: every read goes through the handle it was
given, and `TrackHandle.read_section` raises `IsolationError` on a token the
handle does not own.
"""

import hashlib
import os
import re

from . import config as C

# ---- redaction -------------------------------------------------------------
# Credential shapes that must never reach a provider prompt, whether they came
# from a fetched page, a pasted posting, or a hand-written seed document. This is
# the one copy; research and the teaching path both call redact().
SECRET_LINE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY|sk-ant-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{20,}|"
    r"gho_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[bap]-[A-Za-z0-9-]{10,}|"
    r"eyJ[A-Za-z0-9_-]{20,}\.eyJ|"
    r"(?:password|passwd|api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)"
    r"\s*[=:]\s*[\"'][^\"']{8,})", re.I)
PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|SECRET)[A-Z0-9 ]*-----", re.I)
PEM_END = re.compile(r"-----END [A-Z0-9 ]*(?:PRIVATE KEY|SECRET)[A-Z0-9 ]*-----", re.I)

REDACTED_LINE = "[REDACTED: line withheld - matches a credential pattern]"
REDACTED_BLOCK = "[REDACTED: credential block withheld]"


def redact(text):
    """Redact credential-shaped lines and complete multi-line PEM blocks.

    An unterminated PEM block swallows the rest of the document on purpose. The
    alternative is emitting key material because the END marker was cut off by a
    byte cap, and losing the tail of one document is the cheaper failure.
    """
    out, inside_pem = [], False
    for line in str(text or "").split("\n"):
        if PEM_BEGIN.search(line):
            inside_pem = True
            out.append(REDACTED_BLOCK)
            continue
        if inside_pem:
            if PEM_END.search(line):
                inside_pem = False
            continue
        out.append(REDACTED_LINE if SECRET_LINE.search(line) else line)
    return "\n".join(out)


# ---- loose documents -------------------------------------------------------
# A "loose" document is ordinary markdown: a `# title` and `## heading` sections.
# It is what a human writes by hand and what the distiller emits before the store
# re-renders it into the section format of DESIGN-state-corpus.md section E.
_TITLE_RE = re.compile(r"^#\s+(.+)$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def parse_loose(text):
    """Split ordinary markdown into (title, [(heading, body), ...]).

    Sections split on `## `. Prose before the first `## ` belongs to no section
    and is dropped: the store addresses every byte it serves by (doc_id, sec_id),
    so text that cannot be cited cannot be taught from, and keeping it would only
    spend the document's cap on bytes no citation can ever name.
    """
    title, sections, head, buf = "", [], "", []
    for line in str(text or "").split("\n"):
        if not title:
            m = _TITLE_RE.match(line)
            if m:
                title = m.group(1).strip()[:200]
                continue
        if line.startswith("## "):
            if head:
                sections.append((head, "\n".join(buf).strip()))
            head, buf = line[3:].strip()[:120], []
        else:
            buf.append(line)
    if head:
        sections.append((head, "\n".join(buf).strip()))
    return title, [(h, b) for h, b in sections if b]


def slug_for(name):
    """A file-name-safe slug, non-empty, bounded."""
    s = _SLUG_STRIP.sub("-", str(name or "").lower()).strip("-")
    return (s or "document")[:48]


def fit_sections(pairs):
    """Shape parsed pairs into write_doc section dicts, inside the caps.

    Truncation is explicit and marked. A silently shortened section reads as a
    complete thought that ends early, which is the confident, well-cited, wrong
    lesson the grounding claim exists to prevent.
    """
    marker = "\n\n[TRUNCATED: section exceeded the per-section cap]"
    out = []
    for i, (heading, body) in enumerate(pairs[:C.MAX_SECTIONS_PER_DOC], start=1):
        body = redact(body)
        # write_doc refuses a body line starting with the section delimiter: the
        # offset writer never parses, so a re-scan would lose every offset after
        # it. Indent the line rather than refuse the whole document.
        body = "\n".join(
            (" " + ln) if ln.startswith("§") else ln for ln in body.split("\n"))
        if len(body) > C.SECTION_MAX_CHARS:
            body = body[:C.SECTION_MAX_CHARS - len(marker)].rstrip() + marker
        heading = " ".join(str(heading or "(untitled)").split())[:120] or "(untitled)"
        out.append({"sec_id": "s%02d" % i, "heading": heading, "body": body})
    return out


def ingest_text(handle, text, origin_url, slug=None, title=None,
                vetting="community", trust=2, final_url=None):
    """Write one loose markdown document into this track's corpus.

    Returns the new doc_id, or None when the text carries no citable section.
    Provenance is recorded from the bytes actually ingested, so a citation can be
    falsified later against the source it names.

    `vetting` defaults to the schema's weakest label, because the caller that does
    not set one is ingesting something nobody vetted. A research fetch that knows
    its source passes its own. The schema allows exactly primary, secondary,
    vendor and community, and trust runs 1 to 5.
    """
    raw = str(text or "").encode("utf-8")
    parsed_title, pairs = parse_loose(text)
    sections = fit_sections(pairs)
    if not sections:
        return None
    title = title or parsed_title or "(untitled)"
    extract = "\n\n".join(s["body"] for s in sections).encode("utf-8")
    # write_doc returns (doc_id, file_name); callers here want the citable id.
    doc_id, _file_name = handle.write_doc(
        slug=slug or slug_for(title),
        title=title,
        sections=sections,
        origin_url=origin_url,
        origin_bytes=len(raw),
        origin_sha256=hashlib.sha256(raw).hexdigest(),
        extract_sha256=hashlib.sha256(extract).hexdigest(),
        vetting=vetting,
        trust=int(trust),
        final_url=final_url or origin_url,
    )
    return doc_id


def seed_from_directory(handle, directory, pin_to_step=None):
    """One-time ingest of a development corpus directory into one track.

    Idempotent by title: a track that already holds a document of the same title
    is left alone, so a restart does not duplicate. Returns the list of doc_ids
    written this call, which is empty on the second run.

    This exists so a track created before the research pipeline has something
    real to teach from. It is not the runtime path. Research writes documents
    through `ingest_text` with real provenance, and this seeds with the local
    file path as its origin so the difference stays visible in the store.
    """
    try:
        names = sorted(
            n for n in os.listdir(directory)
            if n.endswith(".md") and not n.startswith("."))
    except OSError:
        return []

    have = {
        (r["title"] or "").strip().lower()
        for r in handle.conn.execute("SELECT title FROM doc").fetchall()
    }
    written = []
    for name in names:
        path = os.path.join(directory, name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                text = fh.read(C.DOC_MAX_BYTES).decode("utf-8", "replace")
        except OSError:
            continue
        title = parse_loose(text)[0] or name
        if title.strip().lower() in have:
            continue
        doc_id = ingest_text(handle, text, origin_url="file://" + os.path.abspath(path),
                             slug=slug_for(os.path.splitext(name)[0]), title=title)
        if doc_id:
            written.append(doc_id)
            have.add(title.strip().lower())

    if pin_to_step and written:
        pin_all(handle, pin_to_step, written)
    return written


def pin_all(handle, step_id, doc_ids=None):
    """Pin every section of the named documents to one step, in order.

    A step teaches only from what is pinned to it. `curriculum.plan` chooses
    slices deliberately; this remains the seed path's default, where there is no
    plan yet and pinning everything is the honest answer: it is visible in
    `step_slice`, and `build_pack` still enforces the pack caps on top.

    The step is ensured first because `step_slice.step_id` carries a foreign key
    to `step` and foreign keys are on. Without it, pinning to a step the track
    has not created yet raised IntegrityError from inside the seed, which
    `bridge.evidence_pack` catches so a seed failure cannot end a lesson. The
    documents were written before the raise, so `n_docs` was no longer zero and
    the seed never ran again: the first teaching turn on a fresh track was
    ungrounded, and so was every turn after it. The swallow was right and the
    silence underneath it was the defect.
    """
    handle.ensure_step(step_id, step_id)
    if doc_ids is None:
        doc_ids = [r["doc_id"] for r in handle.conn.execute(
            "SELECT doc_id FROM doc WHERE status='ready' ORDER BY doc_no").fetchall()]
    ord_ = 0
    for doc_id in doc_ids:
        rows = handle.conn.execute(
            "SELECT sec_id FROM section WHERE doc_id=? ORDER BY sec_id",
            (doc_id,)).fetchall()
        for r in rows:
            handle.pin_slice(step_id, doc_id, r["sec_id"], ord_)
            ord_ += 1
    return ord_


# ---- the evidence pack -----------------------------------------------------
EMPTY_CORPUS = (
    "(The corpus for this track is empty. No source has been researched and stored"
    " yet, so there is nothing to teach from. Say so plainly in one sentence rather"
    " than answering from memory.)")
NO_SLICE = (
    "(This step has no corpus pinned to it, so nothing is grounded. Do not fill the"
    " gap from memory. Name what is missing in one sentence.)")
RETRIEVAL_FAILED = (
    "(Corpus retrieval failed for this turn, so nothing is grounded. Say you cannot"
    " see the material rather than answering from memory.)")


def build_pack(handle, step_id):
    """The bounded evidence pack for one turn, from this track alone.

    Returns the dict `TrackHandle.build_pack` returns, plus a `text` field that is
    always prompt-ready: on an empty or unpinned corpus it carries the honest
    refusal instead of an empty string, so the caller cannot accidentally send a
    prompt with a blank evidence block and get an ungrounded answer.

    Never raises. A retrieval fault degrades to an honest "nothing retrieved"
    rather than a 502 on the teaching path.
    """
    try:
        pack = handle.build_pack(step_id)
    except Exception:                                        # noqa: BLE001
        return {"track_id": getattr(handle, "track_id", None), "step_id": step_id,
                "sections": [], "text": RETRIEVAL_FAILED, "pack_sha16": "",
                "cites": [], "grounded": False}
    if not pack.get("sections"):
        try:
            n_docs = handle.conn.execute(
                "SELECT COUNT(*) c FROM doc WHERE status='ready'").fetchone()["c"]
        except Exception:                                    # noqa: BLE001
            n_docs = 0
        pack["text"] = EMPTY_CORPUS if not n_docs else NO_SLICE
        pack["grounded"] = False
        return pack
    pack["text"] = redact(pack["text"])
    pack["grounded"] = True
    return pack


def pack_text(handle, step_id):
    """Prompt-ready evidence text for one step. The teaching path's entry point."""
    return build_pack(handle, step_id)["text"]


# ---- the citation check ----------------------------------------------------
CITE_RE = re.compile(r"\bD\d{2}§s\d{2}\b")


def check_citations(reply, pack):
    """Every citation token the reply names, split into supplied and invented.

    Checked against the pack that was actually sent, never against the track: a
    token this track owns but did not supply for this turn is still a claim the
    tutor could not have read, and treating it as valid is how a well-cited wrong
    lesson gets through.
    """
    supplied = set(pack.get("cites") or ())
    named = set(CITE_RE.findall(str(reply or "")))
    return {"named": sorted(named),
            "valid": sorted(named & supplied),
            "invented": sorted(named - supplied)}
