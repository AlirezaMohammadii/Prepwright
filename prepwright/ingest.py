"""Owner-supplied resources: read a file, prove it is prose, cut it to the goal.

The candidate points at a PDF, a spreadsheet, a Word file or a page of notes and
says what they want to learn from it. This module turns that into corpus
documents. Everything the research path guarantees about a fetched page holds
here too: the bytes are hashed, the provenance is recorded, credential-shaped
lines are redacted, and a document that cannot be cited is not stored.

**No model is called from this module, at any size.** Extraction, structure
detection and relevance selection are ordinary Python. A 300-page book and an
empty file cost the same number of tokens to ingest: zero. The only text that
ever reaches a provider is `corpus.build_pack`'s pack, capped at
`config.PACK_MAX_BYTES`, so the tokens a resource costs per teaching turn do not
grow with the resource. That is the whole reason ingestion is worth doing
locally rather than by handing a document to a model and asking it to summarise.

Three refusals, in the order they fire:

1. **The format is unreadable.** Named, with the reason.
2. **The text is not prose.** `gate()` measures the extraction and refuses
   anything that reads like decoded font tables. A fragile extractor that emits
   garbage into a store the tutor treats as ground truth is the one failure this
   product cannot have, so an unconvincing extraction is refused rather than
   stored. The refusal names what would fix it.
3. **Nothing in the resource matches the goal.** Better than filling a track's
   48-document budget with the parts of a book the candidate did not ask for.

`select()` is the cut the owner asked for: roughly the fifth of a resource that
carries most of what they need. It scores candidate chunks with the same
`curriculum` machinery that later picks pack sections, so what is stored and what
is taught are ranked by one function, not two that can disagree.
"""

import csv
import hashlib
import io
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib

from . import config as C
from . import corpus as CORPUS
from . import curriculum as CURR
from . import research as RESEARCH
from . import security as SEC


class IngestRefused(RuntimeError):
    """The resource cannot become corpus, and the message says why."""


# ---- bounds ----------------------------------------------------------------
# A resource is read whole into memory once. 64 MiB is far above any handbook a
# person actually studies from and far below anything that threatens this
# process. The extracted-text cap is separate and much smaller: past roughly a
# megabyte of prose the selection step is choosing from more than a track can
# hold anyway, and reading further only slows the refusal down.
FILE_MAX_BYTES = 67_108_864
TEXT_MAX_CHARS = 2_000_000
PDF_TIMEOUT_SECONDS = 120
# One inflated PDF stream, and the whole extraction. `zlib.decompress` with no
# max_length measured 1029:1 on repetitive bytes, and around a thousand such
# streams fit inside FILE_MAX_BYTES, so one conforming file could demand tens of
# gigabytes in a single allocation. `_zip_member` already refuses on
# `info.file_size`; this is the same rule for the path that had none.
PDF_STREAM_MAX = 8_388_608          # 8 MiB inflated, per stream
PDF_OUTPUT_MAX_BYTES = 33_554_432   # 32 MiB of extracted text, per file
SHEET_MAX_ROWS = 2_000
SHEET_MAX_COLS = 40

# Selection: how many documents one resource may claim, and the floor a chunk
# must clear to be worth a document at all. MAX_DOCS_PER_TRACK is 48 and a track
# also holds sources the app discovered, so one resource takes at most half.
RESOURCE_MAX_DOCS = 24
SELECT_FLOOR = 0.18

TEXT_EXT = (".md", ".markdown", ".txt", ".text", ".rst", ".log")
HTML_EXT = (".html", ".htm", ".xhtml")
TABLE_EXT = (".csv", ".tsv")
EXTENSIONS = TEXT_EXT + HTML_EXT + TABLE_EXT + (".pdf", ".xlsx", ".docx")


# Above this share of undecodable bytes a file is not UTF-8 at all and the
# other codecs are worth trying. Below it, it is UTF-8 with damage, and the
# replacement characters are visible to `replacement_ratio` in the gate.
UTF8_SLIP_RATIO = 0.001


def _decode(raw):
    """Decode bytes without ever raising, and without inventing characters.

    UTF-8 first because everything modern is UTF-8, then UTF-16 when the BOM says
    so, then cp1252 which is what Windows-authored text actually is. Latin-1 is
    the last resort and cannot fail; the gate downstream is what catches a wrong
    guess, because a wrong guess reads as non-prose.
    """
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return raw.decode("utf-16")
        except (UnicodeDecodeError, ValueError):
            pass
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # A file that is almost entirely valid UTF-8 IS UTF-8 carrying a few corrupt
    # bytes, and re-reading the whole of it as cp1252 rewrites every multi-byte
    # character: one stray 0x93 turned every curly quote and accented letter in
    # a 100,000-character document into mojibake. The gate cannot catch that,
    # because neither cp1252 nor latin-1 emits U+FFFD and `normalise` deletes
    # the C1 bytes latin-1 produces. So measure the damage before choosing.
    lenient = raw.decode("utf-8", errors="replace")
    if lenient and lenient.count("\ufffd") / len(lenient) <= UTF8_SLIP_RATIO:
        return lenient
    for encoding in ("cp1252",):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


# ---- prose quality ---------------------------------------------------------
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]{1,19}")
_TOKEN = re.compile(r"\S+")
_RUN_OF_SPACES = re.compile(r"[ \t]{2,}")
# Ligatures a PDF font table emits as single code points. Expanding them before
# the gate stops "efficient" being counted as two non-words, which would fail a
# perfectly good extraction.
_LIGATURES = ((u"ﬀ", "ff"), (u"ﬁ", "fi"), (u"ﬂ", "fl"),
              (u"ﬃ", "ffi"), (u"ﬄ", "ffl"), (u"ﬅ", "st"))


TRUNCATION_MARK = ("\n\n## [TRUNCATED: this resource continues past %d "
                   "characters and the rest was not read]\n")


def normalise(text):
    """Expand ligatures, unify line endings, drop control bytes, cap length."""
    text = str(text or "")
    for bad, good in _LIGATURES:
        text = text.replace(bad, good)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ").replace(" ", "\n")
    # Keep tab and newline; drop the rest of C0 and the C1 block, which is where
    # a mis-decoded font table lands.
    text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]", "", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    if len(text) > TEXT_MAX_CHARS:
        # Marked, not silent. An unmarked cut made `goal_coverage` report ideas
        # as absent from a resource that covers them past the cut, so the
        # warning named present material as missing and the candidate was told
        # to find another source.
        mark = TRUNCATION_MARK % TEXT_MAX_CHARS
        keep = text[:TEXT_MAX_CHARS - len(mark)].rsplit("\n", 1)[0]
        return keep + mark
    return text


def quality(text):
    """Measure whether an extraction reads like prose. No judgement, just numbers."""
    text = str(text or "")
    chars = len(text)
    if not chars:
        return {"chars": 0, "words": 0, "word_ratio": 0.0, "mean_word_len": 0.0,
                "space_ratio": 0.0, "replacement_ratio": 0.0, "longest_run": 0,
                "alpha_ratio": 0.0}
    collapsed = _RUN_OF_SPACES.sub(" ", text) or text
    tokens = _TOKEN.findall(text)
    words = _WORD.findall(text)
    letters = sum(1 for ch in text if ch.isalpha())
    runs = [len(t) for t in tokens] or [0]
    return {
        "chars": chars,
        "words": len(words),
        # The share of whitespace-separated tokens that look like English words.
        # Decoded font tables score near zero here and nothing else does.
        "word_ratio": (len(words) / len(tokens)) if tokens else 0.0,
        "mean_word_len": (sum(len(w) for w in words) / len(words)) if words else 0.0,
        # Measured on a copy with runs of horizontal whitespace collapsed. The
        # extraction keeps its layout: `pdftotext -layout` is what puts headings
        # on their own lines, and `looks_like_heading` depends on that. But the
        # padding it inserts is not the document's spacing, and measuring it as
        # such refused 6 of 16 real PDFs sampled on this machine, every one of
        # which sits at 0.13 without -layout. The refusal named "a spacing ratio
        # of 0.44, outside the 0.08-0.32 band" for a clean extraction of a good
        # document, with nothing the candidate could act on.
        "space_ratio": collapsed.count(" ") / len(collapsed),
        "replacement_ratio": text.count("�") / chars,
        # A CID extraction with no space mapping produces one enormous token.
        "longest_run": max(runs),
        # ...and that token is most of the document. A single long URL, DOI or
        # base64 line is not, and `longest_run` being a maximum meant one of
        # them refused an otherwise clean extraction. This says how much of the
        # text is inside over-long runs, which is the property that separates
        # the two.
        "long_run_share": sum(n for n in runs if n > GATE["max_longest_run"]) / chars,
        "alpha_ratio": letters / chars,
    }


# Calibrated against real extractions rather than chosen: see
# tests/test_ingest.py, which asserts a genuine NIST-shaped page passes and four
# named garbling modes fail. Loosening one of these numbers without adding the
# extraction that needed it loosened is how garbage gets in.
GATE = {
    "min_chars": 400,
    "min_word_ratio": 0.55,
    "min_mean_word_len": 2.8,
    "max_mean_word_len": 9.5,
    "min_space_ratio": 0.08,
    "max_space_ratio": 0.32,
    "max_replacement_ratio": 0.002,
    "max_longest_run": 120,
    # A CID blob is one token that is nearly the whole document. A long URL in
    # an otherwise clean 50,000-character extraction is a fraction of a percent.
    "max_long_run_share": 0.05,
    "min_alpha_ratio": 0.45,
}


def gate(text, what="the file"):
    """Return (ok, reason, metrics). A refusal names the measurement that failed."""
    m = quality(text)
    if m["chars"] < GATE["min_chars"]:
        return (False, "%s produced only %d characters of text. A scanned page "
                "with no text layer does this; so does an empty document."
                % (what, m["chars"]), m)
    if m["replacement_ratio"] > GATE["max_replacement_ratio"]:
        return (False, "%s decoded with %.1f%% unreadable characters, so the "
                "encoding is wrong." % (what, 100 * m["replacement_ratio"]), m)
    if (m["longest_run"] > GATE["max_longest_run"]
            and m["long_run_share"] > GATE["max_long_run_share"]):
        return (False, "%s contains a %d-character run with no space in it, and "
                "%.0f%% of the text sits in runs that long, which means word "
                "boundaries were lost during extraction."
                % (what, m["longest_run"], 100 * m["long_run_share"]), m)
    if m["word_ratio"] < GATE["min_word_ratio"]:
        return (False, "only %.0f%% of %s reads as words. Below %.0f%% the text "
                "is font tables or symbols, not prose."
                % (100 * m["word_ratio"], what, 100 * GATE["min_word_ratio"]), m)
    if not (GATE["min_space_ratio"] <= m["space_ratio"] <= GATE["max_space_ratio"]):
        return (False, "%s has a spacing ratio of %.2f, outside the %.2f-%.2f "
                "band that ordinary prose occupies."
                % (what, m["space_ratio"], GATE["min_space_ratio"],
                   GATE["max_space_ratio"]), m)
    if not (GATE["min_mean_word_len"] <= m["mean_word_len"]
            <= GATE["max_mean_word_len"]):
        return (False, "the mean word length in %s is %.1f characters, outside "
                "the %.1f-%.1f band that prose occupies."
                % (what, m["mean_word_len"], GATE["min_mean_word_len"],
                   GATE["max_mean_word_len"]), m)
    if m["alpha_ratio"] < GATE["min_alpha_ratio"]:
        return (False, "only %.0f%% of %s is letters, so it is closer to a table "
                "of numbers than to something to learn from."
                % (100 * m["alpha_ratio"], what), m)
    return (True, "", m)


# ---- PDF -------------------------------------------------------------------
# Two paths, deliberately. `pdftotext` (poppler) is a mature text extractor and
# is preferred whenever it is present and passes the same ownership check the
# provider CLIs pass. The stdlib path is the floor: it keeps the feature working
# on a machine without poppler, and it is modest on purpose. Both outputs go
# through the same gate, so neither is trusted because of where it came from.
PDFTOTEXT_FALLBACKS = ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext",
                       "/opt/local/bin/pdftotext")

def _pdf_streams(raw):
    """Yield each stream body. Linear in the file, on hostile input too.

    The regex this replaced, `rb"stream\r?\n(.*?)\r?\nendstream"` with re.S,
    rescanned to end of file for every `stream` token with no `endstream` after
    it. Bounding that rescan to one stream's worth was not enough, and the
    measurement is why: PDF_MAX_STREAMS caps SUCCESSES, not attempts, so a file
    of `stream\n` repeated made ~150,000 attempts per MiB and each one scanned
    up to 8 MiB looking for a terminator that is not there.

    Measured on 2026-09-09, before this rewrite: 1 MiB of that input took
    **60.6 seconds** and yielded nothing. FILE_MAX_BYTES is 67 MiB, 64 times
    larger, and this is the extraction path the owner's own file chooser drives.
    That is a denial of service on a hand-picked file, not a theoretical one.

    Two properties make it linear now. `endstream` positions only move forward,
    so the search for one resumes where the last search ENDED rather than
    restarting at each `stream` token, and the scanned ranges never overlap.
    And a file with no `endstream` left anywhere is finished: no later token can
    succeed where an earlier one already searched to the end and failed.
    """
    pos, found, seen = 0, 0, 0
    next_end = -1                # last known endstream position, or -1
    while found < PDF_MAX_STREAMS:
        seen += 1
        if seen > PDF_MAX_TOKENS:
            # A bound on the Python-level loop itself, not on the scanning. At
            # one token per 7 bytes a 67 MiB file is ten million iterations of
            # cheap work, which is still tens of seconds of nothing useful.
            return
        start = raw.find(b"stream", pos)
        if start < 0:
            return
        head = start + 6
        if raw[start - 3:start] == b"end":        # the tail of "endstream"
            pos = head
            continue
        if raw[head:head + 2] == b"\r\n":
            head += 2
        elif raw[head:head + 1] == b"\n":
            head += 1
        else:
            pos = head
            continue
        if next_end < head:
            next_end = raw.find(b"endstream", head)
            if next_end < 0:
                # Nothing after `head` terminates a stream, and every remaining
                # token starts after `head`. Searching again for each of them is
                # the whole quadratic term.
                return
        if next_end - head > PDF_STREAM_MAX:
            pos = head
            continue
        found += 1
        yield raw[head:next_end].rstrip(b"\r\n")
        pos = next_end + 9


PDF_MAX_STREAMS = 20_000
# A bound on the Python-level loop, and a modest one. Measured on 8 MiB of
# `endstream` repeated, which is the shape that makes the loop spin without ever
# calling the expensive search: 0.173 s without this cap, 0.041 s with it. It is
# a backstop, not the fix. The fix is the forward cursor in _pdf_streams, which
# took the same class of input from 60.6 s to 0.001 s.
#
# It is stated this way on purpose. A constant whose comment implies it is doing
# the work is how a guard survives long after it stopped doing any, which this
# tree has already paid for three times over in dead status='done' checks.
#
# No conforming document reaches it: PDF_MAX_STREAMS caps a real file at 20,000
# streams, so the stream cap binds first by an order of magnitude.
PDF_MAX_TOKENS = 200_000
_PDF_TEXT_OP = re.compile(rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>")
_PDF_SHOW = re.compile(rb"(?:Tj|TJ|'|\")")
# One left-to-right pass, not six sequential substitutions. The old form was
# wrong twice over. Its last rule was `re.sub(rb"\\\\", b"\\", piece)`, and a lone
# backslash is an incomplete re.sub REPLACEMENT template, which re.sub parses
# before it looks for a match: every call raised re.error, so `_pdf_via_stdlib`
# crashed on every text-bearing PDF and the poppler-free path advertised as the
# floor had never once run. And sequential rules decode their own output: in
# `\\n` the escaped backslash comes first, but the newline rule matched the
# second backslash and produced a newline where PDF means backslash-then-n.
_PDF_ESCAPE = re.compile(rb"\\(?:([0-7]{1,3})|(.))", re.S)
_PDF_ESCAPE_MAP = {b"n": b"\n", b"r": b"\n", b"t": b"\t", b"b": b"\b",
                   b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}


def _pdf_unescape(piece):
    """Decode the escapes inside one PDF literal string."""
    def one(match):
        octal, char = match.group(1), match.group(2)
        if octal is not None:
            return bytes(bytearray([int(octal, 8) & 0xFF]))
        if char in (b"\n", b"\r"):
            return b""            # a backslash before a newline continues the line
        return _PDF_ESCAPE_MAP.get(char, char)
    return _PDF_ESCAPE.sub(one, piece)


def pdftotext_bin():
    """The poppler binary, if it is present and no other local account can swap it."""
    return SEC.trusted_executable("pdftotext", PDFTOTEXT_FALLBACKS)


def _pdf_via_poppler(path, binary):
    """Extract with poppler. -layout keeps headings on their own lines."""
    # stdout goes to a temporary FILE, not a pipe. `stdout=PIPE` reads the
    # child to EOF into memory, and PDF text output is not proportional to file
    # size, so a pathological document could exhaust memory before
    # TEXT_MAX_CHARS -- which is applied as the last statement of `normalise` --
    # ever ran. A file also cannot deadlock against a chatty stderr.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            done = subprocess.run(
                [binary, "-layout", "-nopgbrk", "-enc", "UTF-8", path, "-"],
                stdout=out, stderr=err, timeout=PDF_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            raise IngestRefused(
                "pdftotext did not finish within %d seconds on this file."
                % PDF_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            raise IngestRefused("pdftotext could not run: %s" % (exc,))
        size = out.tell()
        if size > PDF_OUTPUT_MAX_BYTES:
            raise IngestRefused(
                "pdftotext produced %.0f MB of text from this file, past the "
                "%.0f MB ceiling." % (size / 1e6, PDF_OUTPUT_MAX_BYTES / 1e6))
        out.seek(0)
        body = out.read(PDF_OUTPUT_MAX_BYTES)
        if done.returncode != 0 and not body:
            err.seek(0)
            detail = _decode(err.read(4096)).strip()[:200] or "no output"
            raise IngestRefused("pdftotext failed on this file: %s" % detail)
    return _decode(body)


def _pdf_via_stdlib(raw):
    """A modest stdlib extraction: inflate the streams, read the show operators.

    This handles the common case of a text-bearing PDF with a byte-per-character
    font encoding. It does NOT handle CID fonts, and on one it produces text that
    fails the gate rather than text that is quietly wrong. That is the intended
    outcome: the refusal tells the candidate to install poppler.
    """
    out, produced = [], 0
    for blob in _pdf_streams(raw):
        try:
            # Bounded inflate. `unconsumed_tail` is non-empty exactly when the
            # stream had more to give than the ceiling allows, so an oversized
            # stream is skipped rather than allocated.
            engine = zlib.decompressobj()
            body = engine.decompress(blob, PDF_STREAM_MAX)
            if engine.unconsumed_tail:
                continue
        except zlib.error:
            body = blob if b"Tj" in blob or b"TJ" in blob else b""
        if not body or not _PDF_SHOW.search(body):
            continue
        produced += len(body)
        if produced > PDF_OUTPUT_MAX_BYTES:
            break
        for chunk in _PDF_TEXT_OP.findall(body):
            if chunk.startswith(b"<"):
                digits = re.sub(rb"[^0-9A-Fa-f]", b"", chunk)
                if len(digits) % 2:
                    digits = digits[:-1]
                try:
                    piece = bytes.fromhex(digits.decode("ascii"))
                except ValueError:
                    continue
                # Identity-H pairs are big-endian UTF-16 often enough to be worth
                # trying; when it is wrong the gate says so.
                out.append(piece.decode("utf-16-be", errors="replace")
                           if len(piece) % 2 == 0 else piece.decode("latin-1"))
                continue
            piece = _pdf_unescape(chunk[1:-1])
            out.append(piece.decode("latin-1"))
        out.append("\n")
    return "".join(out)


def read_pdf(path, raw):
    binary = pdftotext_bin()
    if binary:
        return _pdf_via_poppler(path, binary), "pdftotext"
    text = _pdf_via_stdlib(raw)
    ok, _reason, _metrics = gate(text, "this PDF")
    if not ok:
        raise IngestRefused(
            "This PDF needs a real text extractor and none is installed. The "
            "built-in reader could not get clean text out of it. Install poppler "
            "(`brew install poppler`), or open the PDF, copy the part you want to "
            "study, and paste it in as text.")
    return text, "stdlib"


# ---- Office ----------------------------------------------------------------
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
X_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
R_ID_ATTR = ("{http://schemas.openxmlformats.org/officeDocument/2006/"
             "relationships}id")
_SHEET_PART = re.compile(r"xl/worksheets/sheet(\d+)\.xml$")


def _sheet_number(member):
    """The digits in a worksheet part name, for a numeric sort."""
    found = _SHEET_PART.search(str(member))
    return int(found.group(1)) if found else 0
ZIP_MEMBER_MAX = 33_554_432


def _zip_member(archive, name):
    """Read one member, refusing a declared size that would exhaust memory."""
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > ZIP_MEMBER_MAX:
        raise IngestRefused("%s inside this file is %d bytes, which is past the "
                            "%d-byte limit." % (name, info.file_size, ZIP_MEMBER_MAX))
    return archive.read(name)


def read_docx(raw):
    """Word text, with its real heading styles preserved as markdown headings.

    A .docx knows which paragraphs are headings, which is a better outline than
    any heuristic can recover from flat text. Using it is why a Word document
    chunks more accurately than the same content as a PDF.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError):
        raise IngestRefused("This .docx is not a readable Word file.")
    with archive:
        body = _zip_member(archive, "word/document.xml")
        if body is None:
            raise IngestRefused("This .docx has no word/document.xml inside it.")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise IngestRefused("The Word file's XML is malformed: %s" % (exc,))
    # A table row is emitted as one " | " joined line, the same shape read_xlsx
    # and read_table produce. Word models every cell as its own w:p, so walking
    # paragraphs alone turned a two-column sheet into a column of short lines.
    # That matters twice over. looks_like_heading refuses any line containing
    # " | " on purpose, and the comment there names the artefacts it protects:
    # a Control/Status matrix, a Term/Definition glossary. Without the join a
    # .docx never produced one, so a repeated status cell like "Not Applicable"
    # was heading-shaped, and five of them made it page furniture that
    # strip_running deleted from every row. The answer column vanished and the
    # document still read as clean prose, which the gate cannot detect.
    intable = set()
    for table in root.iter(W_NS + "tbl"):
        for para in table.iter(W_NS + "p"):
            intable.add(id(para))
    lines = []
    for node in root.iter():
        if node.tag == W_NS + "tr":
            cells = []
            for cell in node.iter(W_NS + "tc"):
                joined = "".join(t.text or "" for t in cell.iter(W_NS + "t"))
                cells.append(" ".join(joined.split()))
            # Word pads rows to the table width, so trailing empties are layout
            # rather than data. Interior blanks stay: dropping one shifts every
            # later cell under the wrong header, which is the defect already
            # fixed once in read_xlsx.
            while cells and not cells[-1]:
                cells.pop()
            if any(cells):
                lines.append(" | ".join(cells))
            continue
        if node.tag != W_NS + "p" or id(node) in intable:
            continue
        para = node
        text = "".join(t.text or "" for t in para.iter(W_NS + "t")).strip()
        if not text:
            continue
        style = ""
        for pstyle in para.iter(W_NS + "pStyle"):
            style = (pstyle.get(W_NS + "val") or "").lower()
            break
        depth = 0
        match = re.match(r"heading(\d)", style)
        if match:
            depth = min(int(match.group(1)), 6)
        elif style in ("title", "subtitle"):
            depth = 1
        lines.append(("#" * depth + " " + text) if depth else text)
    return "\n\n".join(lines)


def _col_of(ref):
    """'BC12' -> 54. Column order is what makes a sheet read left to right."""
    letters = re.match(r"([A-Z]+)", str(ref or ""))
    if not letters:
        return 0
    index = 0
    for ch in letters.group(1):
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def read_xlsx(raw):
    """Every sheet as a markdown table: one `## ` per sheet, one row per line.

    A spreadsheet is taught from as a table of facts, so the shape is preserved.
    The row cap exists because past a couple of thousand rows a sheet is data to
    query, not material to study, and the selection step downstream would be
    choosing between rows rather than between ideas.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, OSError):
        raise IngestRefused("This .xlsx is not a readable Excel file.")
    with archive:
        shared = []
        blob = _zip_member(archive, "xl/sharedStrings.xml")
        if blob:
            try:
                for item in ET.fromstring(blob).iter(X_NS + "si"):
                    shared.append("".join(t.text or "" for t in item.iter(X_NS + "t")))
            except ET.ParseError:
                shared = []
        # A sheet's name and its worksheet part are joined through the
        # relationship id, never by position. Two orderings used to be assumed
        # equal: `names` was keyed by position in workbook.xml while the parts
        # were matched by position in a LEXICOGRAPHIC sort of the member list.
        # "sheet10" sorts before "sheet2", so from the tenth sheet on every name
        # landed on the wrong worksheet. A heading is what a citation names and
        # what `score_sections` weights at 3.0, so the tutor taught one sheet's
        # rows under another sheet's title, confidently.
        targets = {}
        rels = _zip_member(archive, "xl/_rels/workbook.xml.rels")
        if rels:
            try:
                for rel in ET.fromstring(rels).iter(REL_NS + "Relationship"):
                    part = (rel.get("Target") or "").lstrip("/")
                    if part:
                        targets[rel.get("Id")] = (
                            part if part.startswith("xl/") else "xl/" + part)
            except ET.ParseError:
                targets = {}
        present = set(archive.namelist())
        # NUMERICALLY sorted, so sheet10 does not sort before sheet2. This is
        # the fallback ordering and it is also what the old code got wrong.
        parts = sorted((n for n in present if _SHEET_PART.match(n)),
                       key=_sheet_number)
        declared = []
        book = _zip_member(archive, "xl/workbook.xml")
        if book:
            try:
                declared = [(sheet.get(R_ID_ATTR), sheet.get("name") or "")
                            for sheet in ET.fromstring(book).iter(X_NS + "sheet")]
            except ET.ParseError:
                declared = []
        sheets = [(targets[rid], name or "Sheet %d" % (i + 1))
                  for i, (rid, name) in enumerate(declared)
                  if rid and targets.get(rid) in present]
        if not sheets and declared:
            # A workbook.xml with no r:id, or no relationships part. Some
            # generators emit that. Pair by position against the numerically
            # sorted parts, which keeps the author's names and still avoids the
            # lexicographic mispairing this whole block exists to fix.
            sheets = [(part, name or "Sheet %d" % (i + 1))
                      for i, (part, (_rid, name))
                      in enumerate(zip(parts, declared))]
        if not sheets:
            sheets = [(m, "Sheet %d" % _sheet_number(m)) for m in parts]
        if not sheets:
            raise IngestRefused("This .xlsx contains no worksheets.")
        out = []
        for member, sheet_name in sheets:
            body = _zip_member(archive, member)
            if not body:
                continue
            try:
                root = ET.fromstring(body)
            except ET.ParseError:
                continue
            out.append("## " + sheet_name)
            for row_count, row in enumerate(root.iter(X_NS + "row")):
                if row_count >= SHEET_MAX_ROWS:
                    out.append("[TRUNCATED: sheet continues past %d rows]"
                               % SHEET_MAX_ROWS)
                    break
                # Keyed by declared column, not by arrival order. Excel omits
                # a <c> element for a cell that was never given a value, so
                # appending in document order shifted every later cell left: a
                # row of (A="Access review", C="Open") stored as
                # "Access review | Open", which says the OWNER of the access
                # review is "Open". Every quotation from that table is then
                # wrong and nothing downstream can tell.
                cells, next_column = {}, 0
                for cell in row.iter(X_NS + "c"):
                    ref = cell.get("r")
                    column = _col_of(ref) if ref else next_column
                    next_column = column + 1
                    if column >= SHEET_MAX_COLS:
                        continue
                    value = cell.find(X_NS + "v")
                    raw_value = (value.text or "") if value is not None else ""
                    if cell.get("t") == "s":
                        try:
                            raw_value = shared[int(raw_value)]
                        except (ValueError, IndexError):
                            raw_value = ""
                    elif cell.get("t") == "inlineStr":
                        raw_value = "".join(
                            t.text or "" for t in cell.iter(X_NS + "t"))
                    cells[column] = " ".join(str(raw_value).split())
                if any(cells.values()):
                    out.append(" | ".join(cells.get(i, "")
                                          for i in range(max(cells) + 1)))
        return "\n".join(out)


def read_table(raw, delimiter):
    """CSV and TSV as pipe-separated rows, header first."""
    text = _decode(raw)
    rows = []
    try:
        for count, row in enumerate(
                csv.reader(io.StringIO(text), delimiter=delimiter)):
            if count >= SHEET_MAX_ROWS:
                rows.append("[TRUNCATED: file continues past %d rows]"
                            % SHEET_MAX_ROWS)
                break
            cells = [" ".join(str(c).split()) for c in row[:SHEET_MAX_COLS]]
            if any(cells):
                rows.append(" | ".join(cells))
    except csv.Error as exc:
        # `_csv.Error` is not an OSError and not a ValueError, so it escaped the
        # whole handler chain and the request died with no reply written. Every
        # other refusal in this module names what failed; so does this one.
        raise IngestRefused(
            "This file could not be read as a table: %s" % (str(exc)[:160],))
    return "\n".join(rows)


# ---- structure -------------------------------------------------------------
# Headings are what make a resource teachable: they are the author's own answer
# to "what is this part about", and they become both the section headings a
# citation names and the text `score_sections` weights at 3.0. Recovering them
# from flat text is heuristic, so every rule here is conservative and the
# fallback is honest rather than clever.
_MD_HEAD = re.compile(r"^(#{1,6})\s+(\S.*)$")
_NUM_HEAD = re.compile(r"^\s*(\d+(?:\.\d+){0,3})[.)]?\s+(\S.{2,90})$")
_APPENDIX = re.compile(r"^\s*((?:appendix|annex|chapter|part|section|module|unit"
                       r"|lesson|table|figure)\s+[A-Z0-9][\w.-]*)[:.]?\s*(.{0,90})$",
                       re.I)
_SENTENCE_END = re.compile(r"[.!?;,:]$")
_PROSE_BREAK = re.compile(r"[.!?]\s+[A-Z\u201c\u2018]")
HEADING_MAX_CHARS = 96


def looks_like_heading(line):
    """(level, text) when this line is a heading on its own, else None."""
    stripped = line.strip()
    if not stripped or len(stripped) > HEADING_MAX_CHARS:
        return None
    # A long line carrying a sentence boundary is prose, whatever it starts with.
    # "Part 2 comprises the Core of the Framework. It describes four functions"
    # opens exactly like a heading and is a paragraph, and admitting it produced
    # a citation heading cut off mid-word.
    if len(stripped) > 45 and _PROSE_BREAK.search(stripped):
        return None
    md = _MD_HEAD.match(stripped)
    if md:
        return (len(md.group(1)), md.group(2).strip())
    # A table row is not a heading. `read_xlsx` and `read_table` join cells with
    # " | ", and a two-column Title Case sheet ("Access Enforcement | Partially
    # Implemented") matched the Title Case rule on EVERY row, so `outline` gave
    # every line an empty body, the trailing `if bd` filter dropped all of them,
    # and `sections_from` returned nothing. The refusal then said the file had
    # "no headings, and nothing that reads as one", which is the opposite of
    # what happened, and a Control/Status matrix or a Term/Definition glossary
    # -- the artefacts a candidate actually brings -- was destroyed silently.
    # The explicit markdown check above still runs first, which is how each
    # sheet keeps its own "## " heading.
    if " | " in stripped:
        return None
    match = _APPENDIX.match(stripped)
    if match:
        label = " ".join((match.group(1) + " " + (match.group(2) or "")).split())
        return (1, label[:HEADING_MAX_CHARS])
    match = _NUM_HEAD.match(stripped)
    if match and not _SENTENCE_END.search(stripped):
        # "1.2.3 Something" is a heading. "1985 was the year that ..." is not,
        # which is why a terminal period disqualifies and the tail must not read
        # as a sentence fragment running past the line.
        return (min(1 + match.group(1).count("."), 6), stripped)
    words = stripped.split()
    if not (1 <= len(words) <= 12) or _SENTENCE_END.search(stripped):
        return None
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return None
    if all(c.isupper() for c in letters) and len(letters) > 3:
        return (2, stripped)
    if (stripped[0].isupper()
            and sum(1 for w in words if w[:1].isupper()) >= max(2, len(words) - 1)):
        return (3, stripped)
    return None


RUNNING_MIN_REPEATS = 5
RUNNING_MAX_CHARS = 80


def strip_running(text):
    """Drop page furniture: the short line a PDF repeats on every page.

    `pdftotext` emits the running header and footer once per page, so a 40-page
    standard carries its own title 40 times. Those lines are heading-shaped, land
    in the outline, and produce citations that name a page header instead of a
    section. Only lines that would otherwise be READ AS
    HEADINGS are removed. That is the whole harm: a repeated body line is
    ordinary duplication and costs a little space, while a repeated
    heading-shaped line becomes a section heading a citation names, forty times
    over. Restricting the rule to heading-shaped lines means a checklist that
    legitimately repeats "not applicable" keeps it, which a plain repeat-count
    rule did not: the first version of this deleted the body of its own test
    fixture.
    """
    lines = text.split("\n")
    seen = {}
    for line in lines:
        flat = " ".join(line.split())
        if flat and len(flat) <= RUNNING_MAX_CHARS and looks_like_heading(flat):
            seen[flat] = seen.get(flat, 0) + 1
    furniture = {k for k, n in seen.items() if n >= RUNNING_MIN_REPEATS}
    if not furniture:
        return text
    return "\n".join(
        "" if " ".join(ln.split()) in furniture else ln for ln in lines)


def outline(text):
    """Split text into [(level, heading, body)] on detected headings.

    Text before the first heading is kept under a synthetic "Opening" heading
    rather than dropped: `corpus.parse_loose` drops it because nothing can cite
    it, and losing the first page of a resource that opens with its own summary
    is a real loss.
    """
    rows, level, head, buf = [], 0, "", []
    for line in strip_running(normalise(text)).split("\n"):
        found = looks_like_heading(line)
        if found:
            body = "\n".join(buf).strip()
            if head or body:
                rows.append((level or 1, head or "Opening", body))
            level, head, buf = found[0], " ".join(found[1].split()), []
        else:
            buf.append(line)
    body = "\n".join(buf).strip()
    if head or body:
        rows.append((level or 1, head or "Opening", body))
    return [(lv, hd, bd) for lv, hd, bd in rows if bd]


# Moved to corpus.split_body on 2026-09-28 so research.keep_lead can split a
# fetched page's lead the same way; the old name stays for its tests.
_split_body = CORPUS.split_body


def sections_from(text):
    """[(heading, body)] inside the per-section cap, in the resource's own order."""
    out = []
    for _level, heading, body in outline(text):
        out.extend(_split_body(heading, body))
    return out


# ---- the cut ---------------------------------------------------------------
def _as_index(sections, resource_title, vetting, trust):
    """Shape candidate sections like a corpus index so one scorer ranks both."""
    # `doc_title` is deliberately blank. Inside ONE resource the title is the
    # same string on every candidate section, so it carries no signal about
    # which section answers the goal -- but `score_sections` weights a title hit
    # at 2.0 AND sets `labelled`, which bypasses the MIN_TERMS floor. Every
    # section of a "Kubernetes Security Handbook" therefore entered the ranking
    # for the goal "kubernetes security", and `_by_density` turned that constant
    # bonus into a very high density for the shortest bodies: front matter
    # (Contents, Preface, Index, Colophon) outranked every chapter, and
    # "Network policy in depth" was dropped for scoring 17% of the best match.
    # The title still earns its 2.0 where it discriminates, which is ranking
    # ACROSS the documents of a corpus in `curriculum.corpus_index`.
    return [{"doc_id": "C%04d" % i, "sec_id": "s01",
             "cite": "C%04d" % i, "heading": heading, "concept": "",
             "doc_title": "", "trust": int(trust),
             "vetting": vetting, "body": body}
            for i, (heading, body) in enumerate(sections)]


COVERAGE_THIN = 0.5


DEPTHS = {"focused": 0.40, "balanced": SELECT_FLOOR, "broad": 0.06}
THIN_KEPT_RATIO = 0.05


def floor_for(depth):
    """The score floor a named depth uses. Unknown names fall back to balanced.

    Three named depths rather than a number in the page, because the number is
    meaningless to read and the choice behind it is not: focused takes the part
    of a resource squarely on the goal, broad takes anything that touches it,
    and balanced sits between them.

    `focused` is the default. On the NIST AI RMF it keeps 20 of 172 sections, a
    twelfth of the document, which is the cut this product exists to make: the
    fraction of a resource that carries most of what one candidate needs before
    one interview. A candidate who wants the whole book can say so.
    """
    return DEPTHS.get(str(depth or "").strip().lower(), SELECT_FLOOR)


def _coverage_warning(cover, name, kept=None, total=None):
    """A sentence when the resource does not cover what was asked, else "".

    Silence here is the failure mode. A resource that answers two words of a
    ten-word goal still ingests, still produces documents, and still teaches
    confidently from them, so the candidate has to be told that the other eight
    words are not in the book. Naming the missing terms is what lets them decide
    whether to supply a second resource or let the app go looking.
    """
    thin_cut = (total and kept is not None
                and (kept / float(total)) < THIN_KEPT_RATIO)
    thin_cover = (cover.get("goal_terms")
                  and cover.get("ratio", 1.0) < COVERAGE_THIN)
    # The cut can drop an idea the resource does contain. Silence there was the
    # worse half of this defect: "covers everything you asked" printed beside a
    # teaching list missing the idea reads as a working cut.
    lost = [w for w in (cover.get("kept_missing") or [])
            if w in (cover.get("found") or [])]
    if not (thin_cut or thin_cover or lost):
        return ""
    missing = cover.get("missing") or []
    parts = []
    if lost and not thin_cover:
        parts.append("contains %s, but the cut kept no section that does, so "
                     "nothing taught from it will cover %s"
                     % (", ".join(lost[:6]) + ("..." if len(lost) > 6 else ""),
                        "them" if len(lost) > 1 else "it"))
    if thin_cover:
        parts.append("covers %d of the %d ideas in what you asked to learn, and "
                     "says nothing about: %s"
                     % (len(cover.get("found") or []), cover["goal_terms"],
                        ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "")))
    if thin_cut:
        parts.append("matched on only %d of its %d sections" % (kept, total))
    return ("%s %s. Everything taught from it will be grounded, but it cannot "
            "teach what it does not contain." % (name, "; it ".join(parts)))


def _by_density(scored):
    """Re-rank by relevance per unit of length, keeping the scorer intact.

    `score_sections` sums term hits over a body, so a 900-character section
    outscores a 90-character one on length alone. Inside a pack that is harmless
    because sections are all near the cap. Across a whole book it is not: the
    longest chapter wins every goal, and a short section that is precisely on
    topic loses to a long one that mentions the word twice. Dividing by the
    square root of the body's term count is the ordinary correction and is
    applied HERE rather than inside `score_sections`, because the pack's ranking
    is settled behaviour and this is a different question.
    """
    out = []
    for score, sec in scored:
        # Heading terms count toward the length too. They are scored at 3.0, so
        # measuring density against the body alone let a long heading over a
        # one-line body read as very dense.
        length = max(1, len(CURR.terms(sec["heading"])) + len(CURR.terms(sec["body"])))
        out.append((score / (length ** 0.5), sec))
    out.sort(key=lambda pair: (-pair[0], pair[1]["doc_id"], pair[1]["sec_id"]))
    return out


def goal_coverage(sections, goal, kept=None):
    """Which parts of the goal this resource actually talks about.

    A resource can pass the prose gate, ingest cleanly, and still not cover what
    the candidate asked to learn. `select` already drops what does not match, but
    dropping quietly reads as "the cut worked" when the truth is "this book is
    about something else". So the terms of the goal are checked against the whole
    resource and the ones that appear nowhere are named.

    This is the same idea `research.coverage` applies to a fetched page, moved to
    the file the candidate supplied, because the candidate is likelier to be
    wrong about what is inside a 300-page handbook than about what is on a page
    they just read.

    Pass `kept` to also measure what SURVIVED the cut. Without it the report
    answers "is this book about my goal", which is not the same question as
    "will I be taught my goal", and the panel prints the two side by side.
    """
    wanted = sorted(set(CURR.terms(goal)))
    if not wanted:
        return {"found": [], "missing": [], "ratio": 1.0, "goal_terms": 0}
    haystack = set()
    for heading, body in sections:
        haystack.update(CURR.terms(heading))
        haystack.update(CURR.terms(body))
    found = [w for w in wanted if w in haystack]
    missing = [w for w in wanted if w not in haystack]
    out = {"found": found, "missing": missing,
           "ratio": len(found) / float(len(wanted)), "goal_terms": len(wanted)}
    if kept is None:
        return out
    # The same measurement over what will actually be TAUGHT. Reporting only the
    # first was measurably misleading: the panel printed 100% coverage beside a
    # "what it will teach from" list that did not contain the idea, because the
    # resource covered it and the cut dropped it.
    #
    # Both numbers are kept rather than one replacing the other, because the two
    # failure modes need different actions and collapsing them destroys that.
    # "The book is about something else" means find another source. "The book
    # covers it and the cut dropped it" means widen the cut. One ratio cannot
    # say which.
    taught = set()
    for heading, body in kept:
        taught.update(CURR.terms(heading))
        taught.update(CURR.terms(body))
    kept_found = [w for w in wanted if w in taught]
    out["kept_found"] = kept_found
    out["kept_missing"] = [w for w in wanted if w not in taught]
    out["kept_ratio"] = len(kept_found) / float(len(wanted))
    return out


def select(sections, goal, resource_title="", vetting="community", trust=3,
           limit=None, floor=SELECT_FLOOR):
    """Keep the part of a resource that answers the goal. Returns (kept, dropped).

    This is the cut the candidate asked for: roughly the fifth of a resource
    carrying most of what they need, chosen by the same `score_sections` that
    later picks the sections of a teaching pack, so what is stored and what is
    taught cannot rank sources differently.

    With no goal there is nothing to rank against, so the resource is kept in its
    own order up to the cap. That is a real mode, not a degraded one: "teach me
    this handbook" is a legitimate request.

    `kept` comes back in the resource's reading order, not in score order. A
    chapter still reads forwards after the cut.
    """
    limit = RESOURCE_MAX_DOCS * C.MAX_SECTIONS_PER_DOC if limit is None else int(limit)
    if not sections:
        return ([], [])
    index = _as_index(sections, resource_title, vetting, trust)
    goal_terms = CURR.terms(goal)
    if not goal_terms and str(goal or "").strip():
        # A goal was typed and it scores nothing. `curriculum.terms` drops words
        # of two characters or fewer, so "AI and ML" reduces to nothing at all,
        # and the no-goal branch below would then keep the first 240 sections
        # with an empty `dropped` list and a coverage ratio of 1.0. The
        # candidate asked for a cut, got none, and was told everything matched.
        raise IngestRefused(
            "\"%s\" gives nothing to match on. Every word in it is either two "
            "letters or fewer, or too common to carry meaning, and both kinds "
            "are skipped when scoring. Write it out in full words, for example "
            "\"machine learning evaluation\" rather than \"ML eval\". Leave the "
            "goal empty to keep the whole resource."
            % str(goal).strip()[:80])
    if not goal_terms:
        keep = list(range(min(len(sections), limit)))
        dropped = [(sections[i][0], "past the %d-section cap for one resource"
                    % limit) for i in range(len(keep), len(sections))]
        return ([sections[i] for i in keep], dropped)
    need = CURR.term_floor(goal)
    scored = _by_density(CURR.score_sections(index, goal))
    if not scored:
        # Per section, not one blanket claim. "Shares no vocabulary" was asserted
        # for every section of a refused file, and it was false for any section
        # that shared one word and failed only the count. Telling a candidate
        # their own source has nothing to do with their own goal is the kind of
        # wrong that makes someone stop trusting the tool.
        wanted = set(goal_terms)
        out = []
        for heading, body in sections:
            hits = sorted(wanted & set(CURR.terms(heading + " " + body)))
            if hits:
                out.append((heading, "mentions %s but nothing else from the goal,"
                            " and a section needs %s to count as being about it"
                            % (", ".join(hits[:3]), _floor_phrase(need))))
            else:
                out.append((heading, "shares no vocabulary with the goal"))
        return ([], out)
    best = scored[0][0]
    ranked = [(score, int(sec["doc_id"][1:])) for score, sec in scored]
    keep = sorted(i for score, i in ranked[:limit] if score >= best * floor)
    kept_set = set(keep)
    dropped = []
    scores = {i: score for score, i in ranked}
    wanted = set(goal_terms)
    for i, (heading, body) in enumerate(sections):
        if i in kept_set:
            continue
        if i not in scores:
            # `score_sections` omits a section failing "matched < MIN_TERMS and
            # not labelled", so absence from `scores` is not the same as sharing
            # no vocabulary. A section using "idempotency" six times, with a
            # heading that shares nothing, was reported as sharing none of the
            # goal's words -- and with a one-word goal that verdict is
            # unreachable by any section, so a whole file could be refused for
            # the candidate's wording when the wording was fine.
            hits = sorted(wanted & set(CURR.terms(heading + " " + body)))
            if not hits:
                dropped.append((heading, "shares no vocabulary with the goal"))
            else:
                dropped.append((heading,
                                "mentions %s but nothing else from the goal, and"
                                " a section needs %s to count as being about it"
                                % (", ".join(hits[:3]), _floor_phrase(need))))
        elif scores[i] < best * floor:
            dropped.append((heading, "scored %.0f%% of the best match, under the "
                            "%.0f%% floor" % (100 * scores[i] / best, 100 * floor)))
        else:
            dropped.append((heading, "past the %d-section cap for one resource"
                            % limit))
    return ([sections[i] for i in keep], dropped)


def _floor_phrase(need):
    """State the floor that actually applied, not a hardcoded "two".

    The message said "two of its words" whatever the goal was, so a candidate
    with a two-word goal was told to satisfy a rule they already had, and one
    with a single-word goal was told to satisfy a rule that cannot exist.
    """
    return ("one of its words, or one in its heading" if need <= 1
            else "%d of its words, or one in its heading" % need)


# ---- grouping into documents ----------------------------------------------
DOC_BODY_BUDGET = 8_500      # under DOC_MAX_BYTES with room for the stored header


def group(sections, resource_title, max_docs=None):
    """Pack selected sections into documents inside the per-document caps.

    Returns (documents, overflow_sections). `select` caps the SECTION count at
    RESOURCE_MAX_DOCS * MAX_SECTIONS_PER_DOC, but grouping also splits on
    DOC_BODY_BUDGET, so long sections produced more documents than that section
    cap implies and one resource could claim most of MAX_DOCS_PER_TRACK. The
    overflow is returned rather than dropped, so the report can say how much of
    the resource did not fit.
    """
    max_docs = RESOURCE_MAX_DOCS if max_docs is None else max(1, int(max_docs))
    docs, current, used = [], [], 0
    for heading, body in sections:
        size = len(heading.encode("utf-8")) + len(body.encode("utf-8")) + 16
        if current and (len(current) >= C.MAX_SECTIONS_PER_DOC
                        or used + size > DOC_BODY_BUDGET):
            docs.append(current)
            current, used = [], 0
        current.append((heading, body))
        used += size
    if current:
        docs.append(current)
    overflow = [pair for extra in docs[max_docs:] for pair in extra]
    docs = docs[:max_docs]
    titled = []
    for i, pairs in enumerate(docs, 1):
        title = resource_title if len(docs) == 1 else "%s (part %d of %d)" % (
            resource_title, i, len(docs))
        titled.append((title[:200], pairs))
    return (titled, overflow)


# ---- reading a file --------------------------------------------------------
def sniff(path):
    """The extension this file will be read as, refusing what cannot be read."""
    ext = os.path.splitext(str(path or ""))[1].lower()
    if ext in EXTENSIONS:
        return ext
    raise IngestRefused(
        "Prepwright reads %s. It cannot read %s. Save it as one of those, or "
        "paste the part you want to study in as text."
        % (", ".join(EXTENSIONS), ext or "a file with no extension"))


def inside_storage(real):
    """True when `real` is config.HOME or anything under it, however it is spelt.

    A string prefix test was not enough and the difference was reachable by
    typing. `os.path.realpath` resolves symlinks but does NOT canonicalise case,
    and the default macOS APFS volume is case-insensitive, so
    `~/.Prepwright/tracks/.../corpus/x.md` opens the same bytes as
    `~/.prepwright/...` and fails `startswith`. One track's corpus could then
    re-enter another as a "supplied resource" wearing fresh provenance, which is
    the single thing this refusal exists to stop.

    Three tests, cheapest first. The exact prefix, then a casefolded prefix
    (which is what a case-insensitive volume actually means), then an ancestor
    walk comparing `(st_dev, st_ino)`, which is what "the same directory" means
    on any filesystem and also catches an alias no string test can see.
    """
    home = os.path.realpath(C.HOME)
    if real == home or real.startswith(home + os.sep):
        return True
    folded, home_folded = real.casefold(), home.casefold()
    if folded == home_folded or folded.startswith(home_folded + os.sep):
        return True
    try:
        home_stat = os.stat(home)
    except OSError:
        return False              # no storage root yet, so nothing is inside it
    node = real
    while True:
        try:
            if os.path.samestat(os.stat(node), home_stat):
                return True
        except OSError:
            pass
        parent = os.path.dirname(node)
        if parent == node:
            return False
        node = parent


def resolve(path):
    """The real path of an owner-supplied file, or a refusal that says why.

    A path inside `config.HOME` is refused. Everything under there is Prepwright's
    own storage, and reading one track's corpus file back in as "a resource the
    candidate supplied" would move bytes across the isolation boundary the whole
    store is built to hold, wearing fresh provenance that says they came from the
    candidate. There is no legitimate use for it.
    """
    raw = os.path.expanduser(str(path or "").strip())
    if not raw:
        raise IngestRefused("No file was named.")
    real = os.path.realpath(raw)
    if inside_storage(real):
        raise IngestRefused(
            "That file is inside Prepwright's own storage. A track's corpus "
            "cannot be re-ingested as a supplied resource.")
    if not os.path.exists(real):
        raise IngestRefused("There is no file at %s." % raw)
    if os.path.isdir(real):
        raise IngestRefused("%s is a folder. Name one file." % raw)
    if not os.path.isfile(real):
        raise IngestRefused("%s is not an ordinary file." % raw)
    size = os.path.getsize(real)
    if size == 0:
        raise IngestRefused("%s is empty." % raw)
    if size > FILE_MAX_BYTES:
        raise IngestRefused("%s is %.1f MB, past the %.0f MB limit."
                            % (raw, size / 1e6, FILE_MAX_BYTES / 1e6))
    return real


def extract(path):
    """(text, how) for one file. Raises IngestRefused with the reason."""
    real = resolve(path)
    ext = sniff(real)
    with open(real, "rb") as handle:
        raw = handle.read(FILE_MAX_BYTES)
    if ext == ".pdf":
        text, how = read_pdf(real, raw)
    elif ext == ".docx":
        text, how = read_docx(raw), "docx"
    elif ext == ".xlsx":
        text, how = read_xlsx(raw), "xlsx"
    elif ext in TABLE_EXT:
        text = read_table(raw, "\t" if ext == ".tsv" else ",")
        how = ext.lstrip(".")
    elif ext in HTML_EXT:
        text, how = RESEARCH.html_to_text(_decode(raw)), "html"
    else:
        text, how = _decode(raw), "text"
    return (normalise(text), how)


def preview(path, goal="", vetting="community", trust=3, depth="focused"):
    """What ingesting this file would store, without storing anything.

    The page calls this first so the candidate sees the cut before it is
    committed: how many sections the resource has, how many answer the goal, and
    what was dropped. Costs no tokens and writes nothing.
    """
    real = resolve(path)
    text, how = extract(real)
    ok, reason, metrics = gate(text, os.path.basename(real))
    report = {"path": real, "name": os.path.basename(real), "how": how,
              "ok": ok, "reason": reason, "metrics": metrics,
              "sections": 0, "kept": 0, "documents": 0, "dropped": [],
              "title": "", "published_on": None}
    if not ok:
        return report
    title = _title_for(text, real)
    sections = sections_from(text)
    kept, dropped = select(sections, goal, title, vetting, trust,
                           floor=floor_for(depth))
    cover = goal_coverage(sections, goal, kept=kept)
    report.update({
        "title": title,
        "coverage": cover,
        "warning": _coverage_warning(cover, os.path.basename(real),
                                     len(kept), len(sections)),
        "sections": len(sections),
        "kept": len(kept),
        "documents": len(group(kept, title)[0]),
        "overflow": len(group(kept, title)[1]),
        "dropped": [{"heading": h, "why": w} for h, w in dropped[:40]],
        "dropped_total": len(dropped),
        "published_on": RESEARCH.published_on_from(text),
        "headings": [h for h, _b in kept[:40]],
    })
    return report


_TITLE_LINE = re.compile(r"^[#\s]*(\S.{3,120}?)\s*$")


def _title_for(text, real):
    """The resource's own title if it states one, else its file name.

    The first heading-shaped line of the first page is the document's title far
    more often than not. A file name is the honest fallback and is never wrong,
    only unhelpful.
    """
    for line in normalise(text).split("\n")[:40]:
        found = looks_like_heading(line)
        if found and len(found[1]) > 8:
            return found[1][:200]
        match = _TITLE_LINE.match(line)
        if match and len(match.group(1).split()) >= 2:
            return match.group(1)[:200]
    return os.path.splitext(os.path.basename(real))[0].replace("_", " ")[:200]


def ingest_file(handle, path, goal="", title=None, vetting="community",
                trust=3, run_id=None, depth="focused"):
    """Read one owner-supplied file into this track's corpus. Returns a report.

    `vetting` defaults to the weakest label for the same reason it does in
    `corpus.ingest_text`: a file the candidate happened to have is not a vetted
    source, and calling it primary because it arrived by hand would let it
    outrank a standards body in every pack it competes in. The candidate can
    raise it deliberately, which is a different act from it defaulting high.
    """
    real = resolve(path)
    text, how = extract(real)
    ok, reason, metrics = gate(text, os.path.basename(real))
    if not ok:
        raise IngestRefused(reason)
    resource_title = title or _title_for(text, real)
    sections = sections_from(text)
    if not sections:
        raise IngestRefused(
            "%s has text but no structure this can cite: no headings, and "
            "nothing that reads as one." % os.path.basename(real))
    kept, dropped = select(sections, goal, resource_title, vetting, trust,
                           floor=floor_for(depth))
    if not kept:
        raise IngestRefused(
            "Nothing in %s matches what you asked to learn. It has %d sections "
            "and none of them share vocabulary with your goal. Either the goal "
            "names something this resource does not cover, or it is worded in "
            "different language than the resource uses."
            % (os.path.basename(real), len(sections)))
    published_on = RESEARCH.published_on_from(text)
    origin = "file://" + real
    digest = hashlib.sha256()
    with open(real, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    stored, failed = [], []
    documents, overflow = group(kept, resource_title)
    for index, (doc_title, pairs) in enumerate(documents, 1):
        body = "# %s\n\n" % doc_title + "\n\n".join(
            "## %s\n%s" % (heading or "continued", section_body)
            for heading, section_body in pairs)
        try:
            doc_id = CORPUS.ingest_text(
                handle, body, origin,
                slug=CORPUS.slug_for("%s-%02d" % (resource_title, index)),
                title=doc_title, vetting=vetting, trust=int(trust),
                final_url=origin, publisher=os.path.basename(real),
                published_on=published_on, run_id=run_id,
                # The hash of the FILE, alongside the hash of the markdown the
                # extractor built from it. Without it, re-verifying this
                # document needs the same extractor version, so a poppler
                # upgrade would make every supplied document look tampered with.
                source_sha256=digest.hexdigest())
        except Exception as exc:                              # noqa: BLE001
            # A cap refusal partway through is a real outcome on a big resource:
            # the documents already written stay, and the report says how many
            # did not fit rather than the whole ingest failing.
            failed.append({"title": doc_title, "why": type(exc).__name__ + ": "
                           + str(exc)[:160]})
            break
        if doc_id:
            stored.append({"doc_id": doc_id, "title": doc_title,
                           "sections": len(pairs)})
    cover = goal_coverage(sections, goal, kept=kept)
    return {
        # `ok` is not decoration. `preview` carries it and the page branches on
        # it, so a stored report without one rendered a successful ingest as
        # "Not stored. That file could not be read." while the documents sat in
        # the corpus. The two reports have to answer the same question.
        "ok": True,
        "path": real, "name": os.path.basename(real), "how": how,
        "coverage": cover,
        "warning": _coverage_warning(cover, os.path.basename(real),
                                     len(kept), len(sections)),
        "title": resource_title, "sha256": digest.hexdigest(),
        "bytes": os.path.getsize(real), "chars": len(text),
        "sections": len(sections), "kept": len(kept), "stored": stored,
        "documents": len(stored), "failed": failed,
        # Sections the cut chose that grouping could not fit inside one
        # resource's share of the track. Reported, never silently dropped.
        "overflow": len(overflow),
        "published_on": published_on, "metrics": metrics,
        "dropped": [{"heading": h, "why": w} for h, w in dropped[:40]],
        "dropped_total": len(dropped),
    }
