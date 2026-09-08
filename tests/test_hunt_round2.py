"""Round 2 of the adversarial hunt: the defects it confirmed, one test each.

Every test here fails when its fix is reverted. That is the only reason a test
in this file exists, so each one names the defect in its docstring rather than
describing the feature.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import importlib
import os
import re
import shutil
import sys
import tempfile
import io
import time
import unittest
import zipfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prepwright import config as C           # noqa: E402
from prepwright import curriculum as K       # noqa: E402
from prepwright import curriculum as CURR    # noqa: E402
from prepwright import ingest as G           # noqa: E402
from prepwright import corpus as CO          # noqa: E402
from prepwright import diagnose as D         # noqa: E402
from prepwright import intake as I           # noqa: E402
from prepwright import security as SEC       # noqa: E402
from prepwright import state as S            # noqa: E402


def _pdf(content):
    """A one-stream PDF carrying `content` as a deflated content stream."""
    return (b"%PDF-1.4\nstream\n" + zlib.compress(content)
            + b"\nendstream\n%%EOF")


class TheStdlibPdfReaderActuallyRuns(unittest.TestCase):
    """`_PDF_ESCAPES` ended with (rb"\\\\\\\\", b"\\\\"). As a re.sub REPLACEMENT
    template a lone backslash is an incomplete escape, and re.sub parses the
    template whether or not the pattern matches, so `_pdf_via_stdlib` raised
    re.error on every text-bearing PDF it was handed.

    Nothing caught it because poppler is installed on the development machine,
    so `read_pdf` never reached the fallback. The fallback exists precisely for
    machines without poppler, which means the path advertised as the floor had
    never once run to completion. The failure was also an uncaught re.error
    rather than an IngestRefused, so the candidate got no named reason and no
    instruction to install poppler.
    """

    def test_decoding_never_raises_on_any_escape(self):
        """The regression itself. The old form raised re.error before it even
        looked for a match, so this passes trivially now and fails loudly if the
        sequential-re.sub shape is ever restored."""
        for body in (b"", b"no escapes at all", br"\n \r \t \b \f",
                     br"\( \) \\", br"\101\102\103", b"\\", b"trailing \\"):
            try:
                G._pdf_unescape(body)
            except Exception as exc:                        # noqa: BLE001
                self.fail("_pdf_unescape(%r) raised %s: %s"
                          % (body, type(exc).__name__, exc))

    def test_octal_escapes_decode(self):
        self.assertEqual(G._pdf_unescape(br"\101\102\103"), b"ABC")

    def test_a_backslash_before_a_newline_continues_the_line(self):
        self.assertEqual(G._pdf_unescape(b"one\\\ntwo"), b"onetwo")

    def test_a_plain_pdf_extracts_its_text(self):
        text = G._pdf_via_stdlib(
            _pdf(b"BT /F1 12 Tf (Hello world this is a sentence.) Tj ET"))
        self.assertIn("Hello world", text)

    def test_a_pdf_carrying_backslash_escapes_extracts_them(self):
        text = G._pdf_via_stdlib(
            _pdf(br"BT (a backslash \\ and a paren \( here) Tj ET"))
        self.assertIn("backslash \\ and", text)
        self.assertIn("paren ( here", text)

    def test_the_escapes_are_not_applied_twice(self):
        r"""\\n is a literal backslash followed by n, not a newline. Applying
        the \n rule first would turn it into a newline and lose the backslash."""
        text = G._pdf_via_stdlib(_pdf(br"BT (before \\n after) Tj ET"))
        self.assertIn(r"before \n after", text)


class StorageCannotBeReIngestedUnderAnotherSpelling(unittest.TestCase):
    """`resolve()` refused a supplied path inside config.HOME with
    `real.startswith(C.HOME + os.sep)`. os.path.realpath resolves symlinks but
    does not canonicalise case, and the default macOS APFS volume is
    case-insensitive, so `~/.Prepwright/tracks/.../corpus/x.md` opens the same
    byte for byte and fails the prefix test. One track's corpus could re-enter
    another as a "supplied resource" wearing fresh provenance, which is the one
    thing the refusal exists to stop.

    HANDOFF §4 listed the refusal as verified. It was verified on the exact
    spelling only.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-hunt2-")
        self.addCleanup(self._restore_home)
        os.environ["PREPWRIGHT_HOME"] = os.path.join(self.tmp, "home")
        importlib.reload(C)
        os.makedirs(C.HOME, exist_ok=True)

    def _restore_home(self):
        self.assertTrue(
            os.path.realpath(C.HOME).startswith(os.path.realpath(self.tmp)),
            "storage root escaped the temporary directory: %s" % C.HOME)
        os.environ.pop("PREPWRIGHT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(C)

    def _inside(self, name="corpus-file.md"):
        path = os.path.join(C.HOME, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# A track's own corpus\n\nSentences that read as prose.\n")
        return path

    def test_the_exact_spelling_is_refused(self):
        with self.assertRaises(G.IngestRefused) as caught:
            G.resolve(self._inside())
        self.assertIn("Prepwright's own storage", str(caught.exception))

    def test_a_differently_cased_spelling_is_refused_too(self):
        self._inside()
        head, tail = os.path.split(C.HOME)
        alt = os.path.join(head, tail.upper(), "corpus-file.md")
        if not os.path.exists(alt):
            self.skipTest("this filesystem is case-sensitive, so the alias "
                          "does not name the same file")
        with self.assertRaises(G.IngestRefused) as caught:
            G.resolve(alt)
        self.assertIn("Prepwright's own storage", str(caught.exception))

    def test_a_symlink_into_storage_is_refused(self):
        """The same containment, reached the other way."""
        inside = self._inside()
        link = os.path.join(self.tmp, "looks-innocent.md")
        os.symlink(inside, link)
        with self.assertRaises(G.IngestRefused) as caught:
            G.resolve(link)
        self.assertIn("Prepwright's own storage", str(caught.exception))

    def test_a_file_outside_storage_still_resolves(self):
        """The refusal must not swallow the legitimate case."""
        path = os.path.join(self.tmp, "supplied.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# A book the candidate owns\n\nProse.\n")
        self.assertEqual(G.resolve(path), os.path.realpath(path))


class NoOtherAccountCanSubstituteTheBinary(unittest.TestCase):
    """`trusted_executable` promises to reject a binary "another local account
    could replace". It walked the ancestors of the RESOLVED path only, so the
    directory holding a symlink that leads to the binary was never stat'ed.
    Verified live before the fix: pdftotext was accepted at
    /opt/homebrew/Cellar/poppler/.../pdftotext, reached through
    /opt/homebrew/bin/pdftotext, a symlink in a group-writable directory that
    does not appear anywhere on the resolved path's chain.

    The rule is now the threat as stated rather than a proxy for it.
    World-writable always fails. Group-writable fails only when the group has a
    member who is a real login account other than root and this user, because
    refusing every group-writable directory disables poppler on every ordinary
    Homebrew Mac while removing no risk, and `chmod g-w /opt/homebrew/bin`
    breaks `brew` itself.
    """

    def setUp(self):
        # realpath both sides: on macOS /var resolves to /private/var (trap 14).
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="prepwright-trust-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _tool(self, directory, name="faketool"):
        os.makedirs(directory, mode=0o755, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)
        return path

    def test_a_clean_binary_is_accepted(self):
        binary = self._tool(os.path.join(self.tmp, "clean"))
        self.assertEqual(SEC.trusted_executable("faketool", (binary,)), binary)

    def test_a_world_writable_binary_is_rejected(self):
        binary = self._tool(os.path.join(self.tmp, "loose"))
        os.chmod(binary, 0o757)
        self.assertIsNone(SEC.trusted_executable("faketool", (binary,)))

    def test_a_world_writable_containing_directory_is_rejected(self):
        directory = os.path.join(self.tmp, "wide")
        binary = self._tool(directory)
        self.assertEqual(SEC.trusted_executable("faketool", (binary,)), binary)
        os.chmod(directory, 0o777)
        self.assertIsNone(SEC.trusted_executable("faketool", (binary,)),
                          "a world-writable containing directory was accepted")

    def test_a_symlink_in_a_writable_directory_is_rejected(self):
        """Finding 17 exactly. The target sits in a clean directory and passes
        on its own; the LINK sits somewhere anyone can write, so anyone can
        repoint it at another already-trusted binary and have it run with this
        program's arguments. Before the fix the link's directory was not on the
        resolved path's ancestor chain and was never looked at."""
        target = self._tool(os.path.join(self.tmp, "cellar"))
        linkdir = os.path.join(self.tmp, "bin")
        os.makedirs(linkdir, mode=0o755)
        link = os.path.join(linkdir, "faketool")
        os.symlink(target, link)
        self.assertEqual(SEC.trusted_executable("faketool", (link,)), target)
        os.chmod(linkdir, 0o777)
        self.assertIsNone(SEC.trusted_executable("faketool", (link,)),
                          "a symlink in a world-writable directory was accepted")
        # The target itself is untouched and still trustworthy on its own path.
        self.assertEqual(SEC.trusted_executable("faketool", (target,)), target)

    def test_group_writable_is_allowed_when_the_group_has_no_other_account(self):
        """The Homebrew case. /opt/homebrew/bin is drwxrwxr-x group admin, whose
        members here are root, this user, and a disabled setup account."""
        directory = os.path.join(self.tmp, "brewish")
        binary = self._tool(directory)
        os.chmod(directory, 0o775)
        self.assertEqual(SEC.trusted_executable("faketool", (binary,)), binary)

    def test_group_writable_is_rejected_when_another_account_is_in_the_group(self):
        directory = os.path.join(self.tmp, "shared")
        binary = self._tool(directory)
        os.chmod(directory, 0o775)
        original = SEC._other_login_accounts
        try:
            import grp
            gid = os.stat(directory).st_gid
            members = set(grp.getgrgid(gid).gr_mem) or {"someone-else"}
            SEC._other_login_accounts = lambda: members
            self.assertIsNone(SEC.trusted_executable("faketool", (binary,)))
        finally:
            SEC._other_login_accounts = original

    def test_unreadable_group_membership_fails_closed(self):
        directory = os.path.join(self.tmp, "opaque")
        binary = self._tool(directory)
        os.chmod(directory, 0o775)
        original = SEC._other_login_accounts
        try:
            SEC._other_login_accounts = lambda: None
            self.assertIsNone(SEC.trusted_executable("faketool", (binary,)),
                              "an unreadable group list was treated as safe")
        finally:
            SEC._other_login_accounts = original


class ThePlanCutIsACeilingNotACliff(unittest.TestCase):
    """`plan(max_steps=N)` took N on trust while the store enforces MAX_STEPS
    mid-write with no transaction across the batch, so a caller asking for more
    than 40 committed 40 steps and their slices, then raised, and `build` never
    returned the report naming what was cut. The gaps past the cap had neither a
    step nor a deferral: silently lost, which is the one outcome the owner ruled
    out."""

    def test_plan_never_returns_more_steps_than_the_store_will_take(self):
        gaps = [{"gap_id": "g%02d" % i, "label": "subject %d" % i, "why": "",
                 "level": "none", "jd_span": "1:2"}
                for i in range(1, C.MAX_STEPS + 6)]
        index = [{"doc_id": "D01", "sec_id": "s%02d" % i,
                  "doc_title": "Handbook", "heading": "subject %d" % i,
                  "concept": "subject %d" % i, "body": "subject %d body" % i,
                  "vetting": "primary", "trust": 5}
                 for i in range(1, C.MAX_STEPS + 6)]
        built = K.plan(gaps, index, max_steps=C.MAX_STEPS + 5)
        self.assertLessEqual(len(built["steps"]), C.MAX_STEPS)
        accounted = ({s["gap_id"] for s in built["steps"]}
                     | {d["gap_id"] for d in built["deferred"]})
        self.assertEqual({g["gap_id"] for g in gaps} - accounted, set())


# ---- fixtures the existing suite does not have -----------------------------
XML_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _sheet_xml(rows):
    """rows: [[(column_letter, value), ...], ...] — a column may be OMITTED."""
    body = []
    for r, cells in enumerate(rows, 1):
        xml = "".join('<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>'
                      % (col, r, value) for col, value in cells)
        body.append('<row r="%d">%s</row>' % (r, xml))
    return ('<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>'
            % (XML_MAIN, "".join(body)))


def _xlsx_book(sheets, with_rels=True):
    """A faithful .xlsx: workbook.xml with r:id, and the relationships part.

    The suite's existing `_xlsx` helper writes neither, and writes only
    sheet1.xml, so it cannot express either Excel defect this file tests.
    """
    declared, rels, parts = [], [], []
    for i, (name, rows) in enumerate(sheets, 1):
        rid = "rId%d" % i
        part = "worksheets/sheet%d.xml" % i
        declared.append('<sheet name="%s" sheetId="%d" r:id="%s"/>' % (name, i, rid))
        rels.append('<Relationship Id="%s" Target="%s" Type="x"/>' % (rid, part))
        parts.append(("xl/" + part, _sheet_xml(rows)))
    book = ('<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>'
            % (XML_MAIN, XML_REL, "".join(declared)))
    rel_xml = ('<Relationships xmlns="%s">%s</Relationships>'
               % (PKG_REL, "".join(rels)))
    if not with_rels:
        # Some generators emit no relationships part and no r:id. The names are
        # then paired to the parts by position, which is what the original code
        # always did and where the lexicographic sort broke.
        book = book.replace(' xmlns:r="%s"' % XML_REL, "")
        for rid in ["rId%d" % i for i in range(1, len(sheets) + 1)]:
            book = book.replace(' r:id="%s"' % rid, "")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", book)
        if with_rels:
            z.writestr("xl/_rels/workbook.xml.rels", rel_xml)
        for path, xml in parts:
            z.writestr(path, xml)
    return buf.getvalue()


class ASpreadsheetSaysWhatItSays(unittest.TestCase):
    """Two defects, both silent, both making the tutor teach a wrong fact from a
    source it correctly cites."""

    def test_an_omitted_cell_keeps_its_column(self):
        r"""Excel omits the <c> element for a cell that was never given a value.
        Cells were appended in document order and joined with " | ", so a row of
        (A="Access review", C="Open") stored as "Access review | Open" — which
        says the OWNER of the access review is "Open"."""
        data = _xlsx_book([("Register", [
            [("A", "Control"), ("B", "Owner"), ("C", "Status")],
            [("A", "Access review"), ("C", "Open")],
            [("B", "Security"), ("C", "Closed")],
        ])])
        text = G.read_xlsx(data)
        self.assertIn("Access review |  | Open", text)
        self.assertIn(" | Security | Closed", text)
        self.assertNotIn("Access review | Open", text)

    def test_sheet_names_survive_ten_sheets(self):
        """`members = sorted(...)` is lexicographic: sheet1, sheet10, sheet11,
        sheet2. Names were keyed by position in workbook.xml and parts by
        position in that sort, so from the tenth sheet on every heading named
        the wrong worksheet. A heading is what a citation names and what
        score_sections weights at 3.0."""
        names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
                 "Golf", "Hotel", "India", "Juliett", "Kilo"]
        self._assert_paired(names, with_rels=True)

    def test_sheet_names_survive_ten_sheets_without_a_relationships_part(self):
        """The same eleven sheets with no r:id and no rels: the names are then
        paired to the parts by POSITION, and `sorted()` gave sheet1, sheet10,
        sheet11, sheet2. This is the shape the original code was always in."""
        names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
                 "Golf", "Hotel", "India", "Juliett", "Kilo"]
        self._assert_paired(names, with_rels=False)

    def test_a_renumbered_workbook_is_resolved_through_the_relationship(self):
        """Deleting and adding sheets leaves workbook order and part numbering
        disagreeing: sheet order [Gamma, Alpha, Beta] over parts
        [sheet7, sheet3, sheet5]. Position cannot recover that pairing at all,
        whatever it is sorted by. Only the r:id can, which is why the fix reads
        xl/_rels/workbook.xml.rels rather than sorting harder."""
        pairs = [("Gamma", 7), ("Alpha", 3), ("Beta", 5)]
        declared, rels, buf = [], [], io.BytesIO()
        for i, (name, number) in enumerate(pairs, 1):
            rid = "rId%d" % i
            declared.append('<sheet name="%s" sheetId="%d" r:id="%s"/>'
                            % (name, i, rid))
            rels.append('<Relationship Id="%s" Target="worksheets/sheet%d.xml"'
                        ' Type="x"/>' % (rid, number))
        book = ('<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>'
                % (XML_MAIN, XML_REL, "".join(declared)))
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/workbook.xml", book)
            z.writestr("xl/_rels/workbook.xml.rels",
                       '<Relationships xmlns="%s">%s</Relationships>'
                       % (PKG_REL, "".join(rels)))
            for name, number in pairs:
                z.writestr("xl/worksheets/sheet%d.xml" % number,
                           _sheet_xml([[("A", "row from %s" % name)]]))
        text = G.read_xlsx(buf.getvalue())
        for name, _number in pairs:
            block = text.split("## " + name + "\n", 1)
            self.assertEqual(len(block), 2, "%s has no heading" % name)
            self.assertTrue(block[1].startswith("row from %s" % name),
                            "## %s is followed by %r"
                            % (name, block[1].split("\n")[0]))

    def _assert_paired(self, names, with_rels):
        data = _xlsx_book([(n, [[("A", "row from %s" % n)]]) for n in names],
                          with_rels=with_rels)
        text = G.read_xlsx(data)
        for name in names:
            block = text.split("## " + name + "\n", 1)
            self.assertEqual(len(block), 2, "%s has no heading" % name)
            self.assertTrue(block[1].startswith("row from %s" % name),
                            "## %s is followed by %r"
                            % (name, block[1].split("\n")[0]))


class ATableIsNotAPileOfHeadings(unittest.TestCase):
    """`read_table`/`read_xlsx` join cells with " | ". A two-column Title Case
    sheet matched the Title Case heading rule on EVERY row, so `outline` gave
    every line an empty body, the trailing `if bd` filter dropped all of them,
    and `sections_from` returned nothing — while the refusal said the file had
    "no headings, and nothing that reads as one"."""

    CSV = (b"Control,Status\n"
           + b"Access Enforcement,Partially Implemented\n" * 13)

    def test_a_control_matrix_keeps_its_rows(self):
        text = G.read_table(self.CSV, ",")
        self.assertIn("Access Enforcement | Partially Implemented", text)
        sections = G.sections_from(text)
        self.assertTrue(sections, "every row was read as a heading")
        joined = " ".join(body for _heading, body in sections)
        self.assertIn("Access Enforcement", joined)

    def test_a_pipe_row_is_never_a_heading(self):
        self.assertIsNone(
            G.looks_like_heading("Access Enforcement | Partially Implemented"))

    def test_an_explicit_markdown_heading_still_wins(self):
        """Each sheet keeps its own "## " heading, which is emitted before the
        rows and must not be caught by the same rule."""
        self.assertEqual(G.looks_like_heading("## Register"), (2, "Register"))


class AChapterStillReadsForwards(unittest.TestCase):
    """`_split_body` appended an over-long paragraph's chunks straight to `out`
    while earlier paragraphs sat unflushed in `current`, so the middle of a long
    paragraph came first and the opening paragraph, usually the definition, was
    demoted to a "(cont. N)" section. The heading is assigned to out[0], so the
    citation named the chapter and pointed at its middle."""

    def test_the_first_piece_starts_with_the_first_paragraph(self):
        opening = ("An access control policy states who may reach what, and "
                   "under which conditions the answer changes.")
        long_para = " ".join("Filler sentence number %d." % i for i in range(120))
        pieces = G._split_body("Chapter One", opening + "\n\n" + long_para)
        self.assertGreater(len(pieces), 1, "the fixture did not split")
        self.assertEqual(pieces[0][0], "Chapter One")
        self.assertTrue(pieces[0][1].startswith("An access control policy"),
                        "the chapter opens with %r" % pieces[0][1][:60])

    def test_nothing_is_lost_across_the_split(self):
        opening = "Opening paragraph that defines the term."
        long_para = " ".join("Filler sentence number %d." % i for i in range(120))
        pieces = G._split_body("Chapter One", opening + "\n\n" + long_para)
        self.assertIn("Opening paragraph", " ".join(b for _h, b in pieces))


class FrontMatterDoesNotOutrankChapters(unittest.TestCase):
    """`_as_index` stamped the resource title on every candidate section. Inside
    one resource that title is a constant, but score_sections weights a title
    hit at 2.0 AND sets `labelled`, bypassing the MIN_TERMS floor, so every
    section entered the ranking. `_by_density` then turned the constant bonus
    into a very high density for the shortest bodies."""

    SECTIONS = [
        ("Contents", "Chapter one. Chapter two. Chapter three."),
        ("Preface", "Written over two years."),
        ("Index", "Terms and page numbers."),
        ("Colophon", "Set in Minion."),
        ("Network policy in depth", "A network policy in Kubernetes selects pods "
         "and states which ingress and egress the cluster permits for them. "
         "Security review of a namespace starts here."),
        ("Admission control", "Admission controllers in Kubernetes intercept "
         "requests to the API server and can reject them, which is where "
         "security policy is enforced before an object is persisted."),
        ("Pod security standards", "The Kubernetes pod security standards define "
         "baseline and restricted profiles for workload security."),
    ]

    FRONT = ("Contents", "Preface", "Index", "Colophon")
    CHAPTERS = ("Network policy in depth", "Admission control",
                "Pod security standards")

    def test_every_chapter_outranks_every_piece_of_front_matter(self):
        """The defect stated exactly. Before the fix the densities were
        Preface/Index/Colophon 4.633, Contents 3.783, and the three chapters
        2.749 / 0.846 / 0.772, so "Colophon" — body "Set in Minion." — outranked
        every chapter on the goal "kubernetes security"."""
        index = G._as_index(self.SECTIONS, "Kubernetes Security Handbook",
                            "community", 3)
        ranked = G._by_density(CURR.score_sections(index, "kubernetes security"))
        order = [sec["heading"] for _score, sec in ranked]
        for chapter in self.CHAPTERS:
            self.assertIn(chapter, order, "%s did not score at all" % chapter)
        for stub in self.FRONT:
            if stub not in order:
                continue            # better still: it scores nothing at all
            self.assertGreater(order.index(stub), max(order.index(c)
                                                      for c in self.CHAPTERS),
                               "%s outranks a chapter" % stub)

    def test_front_matter_is_not_stored_while_a_chapter_is_dropped(self):
        kept, _dropped = G.select(self.SECTIONS, "kubernetes security",
                                  resource_title="Kubernetes Security Handbook",
                                  floor=G.floor_for("broad"))
        headings = [h for h, _b in kept]
        for chapter in self.CHAPTERS:
            self.assertIn(chapter, headings)
        for stub in self.FRONT:
            self.assertNotIn(stub, headings)

    def test_the_resource_title_does_not_change_the_ranking(self):
        """The title is the same string on every section, so it cannot say which
        section answers the goal. Two different titles must rank identically."""
        a, _ = G.select(self.SECTIONS, "kubernetes security",
                        resource_title="Kubernetes Security Handbook",
                        floor=G.floor_for("focused"))
        b, _ = G.select(self.SECTIONS, "kubernetes security",
                        resource_title="Untitled File",
                        floor=G.floor_for("focused"))
        self.assertEqual([h for h, _ in a], [h for h, _ in b])


class TheGateMeasuresTheDocumentNotTheExtractor(unittest.TestCase):
    """`_pdf_via_poppler` asks for `-layout`, which pads columns so headings
    land on their own lines, and `looks_like_heading` depends on that. The gate
    then measured that padding as the document's own spacing and refused it. On
    a 16-PDF sample from this machine six were refused with "a spacing ratio of
    0.44, outside the 0.08-0.32 band", and every one of the six sat at 0.13
    without `-layout`. The candidate got a refusal they could not act on for a
    clean extraction of a good document.

    The fix measures on a space-collapsed copy. The stored text keeps its
    layout, so heading detection is untouched."""

    PROSE = ("The access control policy states who may reach what, and under "
             "which conditions the answer changes. ") * 30

    @staticmethod
    def _layout_page(gutter=40):
        """A narrow text column on a wide page, which is what -layout emits.
        Measured raw spacing 0.467, well past the 0.32 ceiling; collapsed 0.153,
        which is where the six real PDFs sat without -layout."""
        line = "Access enforcement is reviewed each quarter by the owner."
        return "\n".join(line.ljust(58) + " " * gutter for _ in range(90))

    def test_column_padding_does_not_fail_the_spacing_gate(self):
        text = G.normalise(self._layout_page() + "\n\n" + self.PROSE)
        raw = text.count(" ") / len(text)
        self.assertGreater(raw, G.GATE["max_space_ratio"],
                           "the fixture is not padded enough to test anything")
        ok, why, _m = G.gate(text, "report.pdf")
        self.assertTrue(ok, "a padded but clean extraction was refused: %s" % why)

    def test_the_gate_still_refuses_a_wall_of_spaces(self):
        """Collapsing must not turn the ceiling off. Text that is mostly single
        spaces between one-letter tokens is still not prose."""
        ok, _why, _m = G.gate(G.normalise(" ".join("a" for _ in range(3000))), "f")
        self.assertFalse(ok)

    def test_the_four_named_garbling_modes_are_still_refused(self):
        """The gate's own calibration. Loosening a number without adding the
        extraction that needed it loosened is how garbage gets in."""
        cases = {
            "a CID font with no space mapping":
                "".join("%04X" % (i % 65536) for i in range(4000)),
            "a decoded font table": " ".join("g%d" % i for i in range(3000)),
            "a scanned page with no text layer": "  \n \n  ",
            "a wall of numbers": " ".join(str(i) for i in range(3000)),
        }
        for label, text in cases.items():
            ok, _why, _m = G.gate(G.normalise(text), "f")
            self.assertFalse(ok, "%s now passes the gate" % label)


class OneLongTokenIsNotAGarbledDocument(unittest.TestCase):
    """`longest_run` is a maximum, so a single long URL, DOI or base64 line
    refused an otherwise clean extraction. What separates a CID blob from a URL
    is not the longest run but how much of the document sits inside runs that
    long: a CID blob is one token that is nearly the whole file."""

    PROSE = ("The access control policy states who may reach what, and under "
             "which conditions the answer changes. ") * 40

    def test_a_long_url_does_not_refuse_a_clean_document(self):
        text = G.normalise(self.PROSE + "https://example.test/" + "a" * 300
                           + " " + self.PROSE)
        ok, why, m = G.gate(text, "paper.pdf")
        self.assertGreater(m["longest_run"], G.GATE["max_longest_run"])
        self.assertTrue(ok, "one long URL refused the document: %s" % why)

    def test_a_cid_blob_is_still_refused(self):
        text = G.normalise("".join("%04X" % (i % 65536) for i in range(4000)))
        ok, why, m = G.gate(text, "scan.pdf")
        self.assertFalse(ok)
        self.assertGreater(m["long_run_share"], G.GATE["max_long_run_share"])


class OneBadByteDoesNotRewriteTheDocument(unittest.TestCase):
    r"""`_decode` tried whole-file strict decodes, so a single undecodable byte
    re-read the entire file as cp1252 and rewrote every multi-byte character.
    The gate is structurally unable to see it: neither cp1252 nor latin-1 emits
    U+FFFD, and `normalise` deletes the C1 bytes latin-1 produces."""

    CLEAN = "R\u00e9union \u201cquoted\u201d prose that runs on. " * 60

    def test_utf8_with_one_bad_byte_stays_utf8(self):
        decoded = G._decode(self.CLEAN.encode("utf-8") + b"\x93")
        self.assertIn("R\u00e9union", decoded)
        self.assertIn("\u201cquoted\u201d", decoded)
        self.assertNotIn("\u00c3\u00a9", decoded)     # the mojibake for e-acute

    def test_a_file_that_is_really_cp1252_still_decodes_as_cp1252(self):
        """The fallback must survive. A genuinely cp1252 file is far past the
        slip ratio, so it is not mistaken for damaged UTF-8."""
        raw = "R\u00e9union \u201cquoted\u201d prose. ".encode("cp1252") * 60
        self.assertIn("R\u00e9union", G._decode(raw))


class TruncationIsMarked(unittest.TestCase):
    """`normalise` cut past TEXT_MAX_CHARS with no marker, so `goal_coverage`
    reported ideas as absent from a resource that covers them past the cut and
    the warning named present material as missing."""

    def test_the_cut_is_visible_and_stays_inside_the_cap(self):
        out = G.normalise("word " * (G.TEXT_MAX_CHARS // 4))
        self.assertIn("TRUNCATED", out)
        self.assertLessEqual(len(out), G.TEXT_MAX_CHARS)

    def test_a_document_under_the_cap_is_not_marked(self):
        self.assertNotIn("TRUNCATED", G.normalise("word " * 100))


class ExtractionIsBounded(unittest.TestCase):
    """`zlib.decompress` with no max_length measured 1029:1 on repetitive
    bytes, and around a thousand such streams fit inside FILE_MAX_BYTES, so one
    conforming PDF could demand tens of gigabytes in a single allocation. The
    module already knew the pattern: `_zip_member` refuses on `info.file_size`.
    """

    def test_a_flate_bomb_is_skipped_not_inflated(self):
        """The bomb carries a real text operator, so an unbounded inflate would
        return its contents. A fixture without one returns "" either way and
        cannot tell the fix from the defect."""
        # Sized BETWEEN the two ceilings on purpose: past PDF_STREAM_MAX so the
        # per-stream bound must skip it, but under PDF_OUTPUT_MAX_BYTES so the
        # per-file bound cannot mask the per-stream one and claim the credit.
        payload = b"BT (" + b"A" * (G.PDF_STREAM_MAX * 2) + b") Tj ET"
        self.assertLess(len(payload), G.PDF_OUTPUT_MAX_BYTES)
        self.assertGreater(len(payload), G.PDF_STREAM_MAX)
        bomb = zlib.compress(payload)
        self.assertLess(len(bomb), 1 << 20, "the fixture did not compress")
        pdf = b"%PDF-1.4\nstream\n" + bomb + b"\nendstream\n%%EOF"
        self.assertEqual(G._pdf_via_stdlib(pdf), "",
                         "an oversized stream was inflated instead of skipped")

    def test_the_whole_extraction_stops_at_its_ceiling(self):
        """One stream under the per-stream ceiling, repeated past the per-file
        one. `out` accumulated across streams with no total bound."""
        one = zlib.compress(b"BT (" + b"word " * 200_000 + b") Tj ET")
        many = (b"%PDF-1.4\n"
                + (b"stream\n" + one + b"\nendstream\n") * 60 + b"%%EOF")
        self.assertLessEqual(len(G._pdf_via_stdlib(many)),
                             G.PDF_OUTPUT_MAX_BYTES + G.PDF_STREAM_MAX)

    def test_a_stream_under_the_ceiling_still_reads(self):
        pdf = _pdf(b"BT (Ordinary text in an ordinary stream.) Tj ET")
        self.assertIn("Ordinary text", G._pdf_via_stdlib(pdf))


class StreamScanningIsBounded(unittest.TestCase):
    """`stream(.*?)endstream` with re.S rescans to end of file for every
    `stream` token that has no `endstream` after it: quadratic in file size, on
    the one extraction path with no time bound. Two bounds replace it, and both
    are asserted here rather than by timing, which is flaky and needs a fixture
    large enough to be slow on purpose."""

    def test_the_search_for_endstream_is_bounded(self):
        far = (b"stream\n" + b"x" * (G.PDF_STREAM_MAX + 4096)
               + b"\nendstream\n")
        self.assertEqual(list(G._pdf_streams(far)), [],
                         "an endstream past the ceiling was still searched for")

    def test_a_stream_inside_the_ceiling_is_found(self):
        near = b"stream\n" + b"x" * 4096 + b"\nendstream\n"
        self.assertEqual(list(G._pdf_streams(near)), [b"x" * 4096])

    def test_the_number_of_streams_is_bounded(self):
        raw = b"stream\nbody\nendstream\n" * (G.PDF_MAX_STREAMS + 50)
        self.assertEqual(len(list(G._pdf_streams(raw))), G.PDF_MAX_STREAMS)

    def test_unterminated_streams_terminate(self):
        raw = b"%PDF-1.4\n" + b"stream\nnothing here\n" * 4000 + b"%%EOF"
        self.assertEqual(G._pdf_via_stdlib(raw), "")

    def test_endstream_is_not_read_as_a_stream_start(self):
        """"endstream" contains "stream", so the scanner must not treat its tail
        as the beginning of the next one."""
        bodies = list(G._pdf_streams(
            b"stream\nfirst\nendstream\nstream\nsecond\nendstream\n"))
        self.assertEqual(bodies, [b"first", b"second"])


class ACsvFaultIsANamedRefusal(unittest.TestCase):
    """`_csv.Error` is neither an OSError nor a ValueError, so it escaped the
    whole handler chain and the request died with no reply written. Every other
    refusal in this module names what failed."""

    def test_a_field_past_the_csv_limit_is_refused_by_name(self):
        import csv as _csv
        limit = _csv.field_size_limit()
        try:
            _csv.field_size_limit(1024)
            with self.assertRaises(G.IngestRefused) as caught:
                G.read_table(b"a,b\n" + b"x" * 4096 + b",c\n", ",")
            self.assertIn("could not be read as a table", str(caught.exception))
        finally:
            _csv.field_size_limit(limit)


class OneResourceCannotClaimTheWholeTrack(unittest.TestCase):
    """`select` caps the SECTION count, but grouping also splits on
    DOC_BODY_BUDGET, so long sections produced more documents than that cap
    implies and one resource could claim most of MAX_DOCS_PER_TRACK."""

    def test_grouping_stops_at_the_per_resource_document_cap(self):
        # RESOURCE_MAX_DOCS * MAX_SECTIONS_PER_DOC is `select`'s section cap, so
        # this is the largest a resource can legally be. At 1,200 bytes a
        # section it packs about seven per document, which is well past the
        # 24-document share one resource may claim.
        pairs = [("Heading %03d" % i, "body sentence here. " * 60)
                 for i in range(G.RESOURCE_MAX_DOCS * C.MAX_SECTIONS_PER_DOC)]
        docs, overflow = G.group(pairs, "A Large Resource")
        self.assertLessEqual(len(docs), G.RESOURCE_MAX_DOCS)
        self.assertTrue(overflow, "sections past the cap were dropped silently")
        self.assertEqual(len(overflow) + sum(len(p) for _t, p in docs),
                         len(pairs), "sections went missing across the cap")


# ---- the seven defects HANDOFF section 10 left open ------------------------
class Round0Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-round0-")
        self.addCleanup(self._restore_home)
        os.environ["PREPWRIGHT_HOME"] = os.path.join(self.tmp, "home")
        importlib.reload(C)
        S.ensure_home()

    def _restore_home(self):
        self.assertTrue(
            os.path.realpath(C.HOME).startswith(os.path.realpath(self.tmp)),
            "storage root escaped the temporary directory: %s" % C.HOME)
        os.environ.pop("PREPWRIGHT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(C)

    def _track(self, docs):
        track_id, _ = I.intake_from_text(
            "Skills\n\n- Assess AI systems.\n", employer="X", role_title="Y")
        handle = S.open_track(track_id, client_label="round0-test")
        self.addCleanup(handle.close)
        for slug, text in sorted(docs.items()):
            CO.ingest_text(handle, text, origin_url="https://example.test/" + slug,
                           slug=slug, vetting="primary", trust=5)
        return handle


class RestoringADocumentRestoresItsFile(Round0Base):
    """HANDOFF section 10, defect 1. `_quarantine_doc` MOVES the file into
    `quarantine/`; `set_doc_status(id, 'ready')` only flipped the row. The
    document then counted as ready and served zero sections for the rest of the
    track's life, with nothing saying why."""

    DOC = {"guide": ("# A Guide\n## First section\n"
                     "Sentences that read as prose and carry meaning.\n"
                     "## Second section\nMore ordinary prose in a section.\n")}

    def test_a_hash_quarantined_document_comes_back_serving_sections(self):
        handle = self._track(self.DOC)
        doc_id = handle.conn.execute("SELECT doc_id FROM doc").fetchone()["doc_id"]
        name = handle.conn.execute(
            "SELECT file_name FROM doc WHERE doc_id=?", (doc_id,)).fetchone()["file_name"]
        path = handle.corpus_path(name)
        original = io.open(path, "rb").read()
        before = len(K.corpus_index(handle))
        self.assertTrue(before)

        with open(path, "wb") as fh:                 # rewrite the whole file
            fh.write(b"# A Guide\n## First section\nCompletely different text.\n")
        self.assertFalse(handle.rescan_doc(doc_id))
        self.assertFalse(os.path.exists(path), "the file was not moved aside")
        self.assertEqual(len(K.corpus_index(handle)), 0)

        # Put the real bytes back where quarantine holds them, then restore.
        aside = os.path.join(handle.dir, "quarantine", name + ".badhash")
        with open(aside, "wb") as fh:
            fh.write(original)
        handle.set_doc_status(doc_id, "ready")
        self.assertTrue(os.path.exists(path), "the file was not put back")
        self.assertEqual(len(K.corpus_index(handle)), before,
                         "the document is ready and still serves nothing")

    def test_a_document_whose_bytes_are_still_wrong_is_refused_by_name(self):
        handle = self._track(self.DOC)
        doc_id = handle.conn.execute("SELECT doc_id FROM doc").fetchone()["doc_id"]
        name = handle.conn.execute(
            "SELECT file_name FROM doc WHERE doc_id=?", (doc_id,)).fetchone()["file_name"]
        path = handle.corpus_path(name)
        with open(path, "wb") as fh:
            fh.write(b"# A Guide\n## First section\nCompletely different text.\n")
        self.assertFalse(handle.rescan_doc(doc_id))
        with self.assertRaises(S.StoreError) as caught:
            handle.set_doc_status(doc_id, "ready")
        self.assertIn("still does not match", str(caught.exception))
        self.assertEqual(handle.conn.execute(
            "SELECT status FROM doc WHERE doc_id=?", (doc_id,)).fetchone()["status"],
            "quarantined")

    def test_the_ordinary_distrust_and_restore_still_works(self):
        """The candidate distrusting a source never moves the file, and that
        path must not start demanding a quarantine copy that does not exist."""
        handle = self._track(self.DOC)
        doc_id = handle.conn.execute("SELECT doc_id FROM doc").fetchone()["doc_id"]
        handle.set_doc_status(doc_id, "quarantined")
        self.assertEqual(len(K.corpus_index(handle)), 0)
        handle.set_doc_status(doc_id, "ready")
        self.assertTrue(K.corpus_index(handle))


class ThePackHashIsTheHashOfWhatWasSent(Round0Base):
    """HANDOFF section 10, defect 3. `TrackHandle.build_pack` hashed the raw
    text and `corpus.build_pack` redacted it afterwards, so `pack_sha16` was the
    hash of bytes that were never sent, and `sections[].body` stayed raw.

    Scope, stated honestly: today `corpus.fit_sections` redacts on the way IN,
    so the store holds no unredacted credential and pack-time redaction is a
    no-op. The defect was latent, not exploitable. It was correct by accident,
    resting on an invariant enforced two modules away with nothing asserting the
    connection. These tests write a section through `write_doc` directly, which
    is the path that does not pass through `fit_sections`, so the property is
    tested rather than the coincidence.
    """

    SECRET = "aws_secret_access_key = AKIAIOSFODNN7EXAMPLEKEYVALUE"

    def _leaky_track(self):
        track_id, _ = I.intake_from_text(
            "Skills\n\n- Assess AI systems.\n", employer="X", role_title="Y")
        handle = S.open_track(track_id, client_label="round0-test")
        self.addCleanup(handle.close)
        body = ("The runbook records the endpoint and the credential.\n"
                + self.SECRET
                + "\nThen the operator confirms the connection is healthy.")
        raw = body.encode("utf-8")
        handle.write_doc(
            slug="ops-notes", title="Operations Notes",
            sections=[{"sec_id": "s01", "heading": "Connecting to the service",
                       "body": body}],
            origin_url="https://example.test/ops", origin_bytes=len(raw),
            origin_sha256=S.sha256_hex(raw), extract_sha256=S.sha256_hex(raw),
            vetting="primary", trust=5)
        return handle

    def _pack(self):
        handle = self._leaky_track()
        handle.add_gap("g01", 1, "Connecting to the service",
                       "runbook endpoint credential connection", level="none",
                       jd_span="1:2")
        D.approve(handle, "g01")
        built = K.build(handle, D.approved(handle))
        self.assertTrue(built["steps"], built["deferred"])
        return handle, CO.build_pack(handle, built["steps"][0]["step_id"])

    def test_the_stored_section_really_does_carry_the_credential(self):
        """Otherwise both tests below pass for the wrong reason: with nothing to
        redact, the raw text and the redacted text are the same string and the
        two hashes agree however the code is written."""
        handle = self._leaky_track()
        row = handle.conn.execute("SELECT doc_id, sec_id FROM section").fetchone()
        section = handle.read_section(row["doc_id"], row["sec_id"])
        self.assertIn("AKIAIOSFODNN7EXAMPLE", section["body"])

    def test_the_hash_matches_the_redacted_text(self):
        _handle, pack = self._pack()
        self.assertEqual(pack["pack_sha16"], CO._sha16(pack["text"]))

    def test_the_credential_is_absent_from_the_text_and_the_sections(self):
        _handle, pack = self._pack()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", pack["text"])
        for section in pack["sections"]:
            self.assertNotIn("AKIAIOSFODNN7EXAMPLE", section["body"],
                             "a section body carries the credential the text "
                             "had already removed")


class WhatIsPinnedIsWhatThePackCanCarry(Round0Base):
    """HANDOFF section 10, defect 2. `relevant` enforced PACK_MAX_SECTIONS and
    nothing enforced PACK_MAX_BYTES, and ten sections of SECTION_MAX_CHARS fit
    the byte cap in ASCII and in nothing else. `build_pack` walks `step_slice`
    by `ord` and stops at the cap, so the sections past it still looked pinned
    and were invisible."""

    @staticmethod
    def _wide_corpus():
        """Sections that are legal by every per-document cap and together past
        the pack's byte cap.

        The overrun needs SEVERAL documents. DOC_MAX_BYTES is 12,288 and one
        document holds at most MAX_SECTIONS_PER_DOC sections, so a single
        document can never exceed PACK_MAX_BYTES on its own. A step pinned
        across four documents can, which is the ordinary case once research has
        stored more than one source on a topic.

        Each body is padded with em dashes, three bytes each in UTF-8, so the
        sections stay well under SECTION_MAX_CHARS by the character count that
        `fit_sections` enforces while being wide in the bytes `build_pack`
        counts. The English sentence in front of the padding is what scores.
        """
        sentence = ("The governance of the system is reviewed each quarter by "
                    "its owner and the record is kept. ")
        body = sentence * 2 + "\u2014" * 400          # 582 chars, 1,382 bytes
        assert len(body) < C.SECTION_MAX_CHARS
        assert len(body.encode("utf-8")) * C.PACK_MAX_SECTIONS > C.PACK_MAX_BYTES
        docs = {}
        for d in range(4):
            docs["wide%d" % d] = "# Wide Source %d\n" % d + "".join(
                "## Governance section %d-%02d\n%s\n" % (d, i, body)
                for i in range(4))
        return docs

    def test_every_pinned_slice_reaches_the_pack(self):
        handle = self._track(self._wide_corpus())
        handle.add_gap("g01", 1, "Governance of the system",
                       "governance system reviewed quarter owner record",
                       level="none", jd_span="1:2")
        D.approve(handle, "g01")
        built = K.build(handle, D.approved(handle))
        self.assertTrue(built["steps"], built["deferred"])
        for step in built["steps"]:
            pinned = [(r["doc_id"], r["sec_id"])
                      for r in K.slices_of(handle, step["step_id"])]
            self.assertTrue(pinned)
            pack = CO.build_pack(handle, step["step_id"])
            served = [(s["doc_id"], s["sec_id"]) for s in pack["sections"]]
            self.assertEqual(
                served, pinned,
                "%d of %d pinned sections never reach the pack"
                % (len(pinned) - len(served), len(pinned)))

    def test_the_pack_still_fits_its_byte_cap(self):
        handle = self._track(self._wide_corpus())
        handle.add_gap("g01", 1, "Governance of the system",
                       "governance system reviewed quarter owner record",
                       level="none", jd_span="1:2")
        D.approve(handle, "g01")
        built = K.build(handle, D.approved(handle))
        for step in built["steps"]:
            pack = CO.build_pack(handle, step["step_id"])
            self.assertLessEqual(len(pack["text"].encode("utf-8")),
                                 C.PACK_MAX_BYTES)


class TheStageIsDerivedAndNothingElseClaimsIt(Round0Base):
    """HANDOFF section 10, defect 7. `track.phase` was written once at creation
    and never read or updated, so every track said 'intake' forever, including
    ones with twenty taught steps. `flow_state` derives the real stage from the
    data, which is why nothing ever broke: the column was an
    authoritative-looking second source of truth that was always wrong, and its
    CHECK constraint made it look maintained."""

    def test_a_new_library_has_no_phase_column(self):
        lib = S.open_library()
        self.addCleanup(lib.close)
        cols = {r["name"] for r in lib.execute("PRAGMA table_info(track)")}
        self.assertNotIn("phase", cols)

    def test_a_track_is_created_without_it(self):
        track_id, _ = I.intake_from_text(
            "Skills\n\n- Assess AI systems.\n", employer="X", role_title="Y")
        lib = S.open_library()
        self.addCleanup(lib.close)
        row = lib.execute("SELECT lifecycle FROM track WHERE track_id=?",
                          (track_id,)).fetchone()
        self.assertEqual(row["lifecycle"], "active")

    def test_an_existing_library_is_migrated_and_keeps_its_rows(self):
        """The column has to come off a library that already has it, without
        losing a track."""
        import sqlite3
        if sqlite3.sqlite_version_info < (3, 35, 0):
            self.skipTest("this SQLite cannot DROP COLUMN")
        track_id, _ = I.intake_from_text(
            "Skills\n\n- Assess AI systems.\n", employer="X", role_title="Y")
        lib = S.open_library()
        lib.execute("ALTER TABLE track ADD COLUMN phase TEXT NOT NULL"
                    " DEFAULT 'intake'")
        lib.commit()
        self.assertIn("phase", {r["name"] for r in
                                lib.execute("PRAGMA table_info(track)")})
        lib.close()

        lib = S.open_library()                 # the migration runs on open
        self.addCleanup(lib.close)
        self.assertNotIn("phase", {r["name"] for r in
                                   lib.execute("PRAGMA table_info(track)")})
        self.assertEqual(lib.execute(
            "SELECT COUNT(*) c FROM track WHERE track_id=?",
            (track_id,)).fetchone()["c"], 1, "the migration lost a track")


class TheTrackBudgetArithmeticHolds(unittest.TestCase):
    """`TRACK_DB_CAP` is enforced by nothing, and does not need to be: it is the
    sum of four caps that ARE enforced inside the write transaction that causes
    them. What it needs is for that arithmetic to stay true. Raising one
    component cap without raising this one would leave a budget that every
    individual write respects and the whole cannot."""

    def test_the_component_caps_still_fit_inside_it(self):
        parts = {
            "turns": C.TURNS_BYTES_CAP,
            "corpus": C.CORPUS_BYTES_CAP,
            "cards": C.CARDS_BYTES_CAP,
            "marks": C.MARKS_BYTES_CAP,
        }
        total = sum(parts.values())
        self.assertLessEqual(
            total, C.TRACK_DB_CAP,
            "the content caps total %d bytes, past the %d-byte track budget: %r"
            % (total, C.TRACK_DB_CAP, parts))

    def test_the_budget_leaves_room_for_sqlite_itself(self):
        """Page slack, free pages and the index are not content. A budget with
        no headroom would be met exactly by a track that is already full."""
        content = (C.TURNS_BYTES_CAP + C.CORPUS_BYTES_CAP
                   + C.CARDS_BYTES_CAP + C.MARKS_BYTES_CAP)
        self.assertGreaterEqual(C.TRACK_DB_CAP - content, 256 * 1024,
                                "less than 256 KiB of headroom for SQLite")

    def test_one_document_cannot_exceed_what_a_pack_can_carry_by_much(self):
        """The relationship that made defect 2 subtle: DOC_MAX_BYTES sits just
        above PACK_MAX_BYTES, so a single document can never overrun a pack and
        the overrun only appears once a step draws on several."""
        self.assertGreater(C.DOC_MAX_BYTES, C.PACK_MAX_BYTES)
        self.assertLess(C.DOC_MAX_BYTES - C.PACK_MAX_BYTES, 1024)


if __name__ == "__main__":
    unittest.main()
