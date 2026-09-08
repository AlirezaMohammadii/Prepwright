"""Owner-supplied resources: what gets in, what is refused, and what is cut.

The app now reads files the candidate points at. That puts arbitrary bytes at
the front of the pipeline that fills a corpus the tutor treats as ground truth,
so the tests here are mostly about refusal:

  * a PDF whose text layer decoded badly must be refused, not stored, because a
    confident lesson taught from garbled text is the failure this product cannot
    have;
  * a file inside Prepwright's own storage must be refused, or one track's
    corpus re-enters another wearing fresh provenance;
  * a resource that does not answer the goal must be cut, with the reason
    recorded, rather than spending a track's 48-document budget on it.

No network, no model, no provider. Every fixture is built in memory.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import io
import os
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prepwright import config as C            # noqa: E402
from prepwright import ingest as I            # noqa: E402


PROSE = (
    "## Govern\n"
    "The Govern function cultivates a culture of risk management within "
    "organizations designing, developing, deploying, evaluating, or acquiring AI "
    "systems. Policies, processes, procedures, and practices across the "
    "organization are transparent and implemented effectively.\n\n"
    "## Map\n"
    "The Map function establishes the context to frame risks related to an AI "
    "system. Context is established and understood, and the categorization of "
    "the AI system is performed. Interdependencies among activities are "
    "recognized and documented.\n\n"
    "## Measure\n"
    "The Measure function employs quantitative, qualitative, or mixed-method "
    "tools, techniques, and methodologies to analyze, assess, benchmark, and "
    "monitor AI risk and related impacts. Appropriate methods and metrics are "
    "identified and applied.\n\n"
    "## Manage\n"
    "The Manage function entails allocating risk resources to mapped and "
    "measured risks on a regular basis and as defined by the Govern function. "
    "Risk treatment comprises plans to respond to, recover from, and communicate "
    "about incidents or events.\n"
)


def _tmp(name, data, binary=False):
    """Write a fixture into a directory cleaned up by the caller's addCleanup."""
    directory = tempfile.mkdtemp(prefix="prepwright-ingest-")
    path = os.path.join(directory, name)
    with open(path, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as fh:
        fh.write(data)
    return path


class AnExtractionThatIsNotProseIsRefused(unittest.TestCase):
    """The gate is the whole reason PDF ingestion is safe to offer at all."""

    def test_ordinary_standards_prose_passes(self):
        ok, reason, _m = I.gate(PROSE * 2)
        self.assertTrue(ok, "real prose was refused: %s" % reason)

    def test_a_cid_font_with_no_space_mapping_is_refused(self):
        # The classic Identity-H failure: every glyph decoded, no spaces at all.
        ok, reason, _m = I.gate(PROSE.replace(" ", "") * 2)
        self.assertFalse(ok)
        self.assertIn("word boundaries", reason)

    def test_a_decoded_font_table_is_refused(self):
        ok, reason, _m = I.gate(" ".join([""] * 400))
        self.assertFalse(ok)

    def test_a_wrong_encoding_is_refused(self):
        ok, reason, _m = I.gate("�" * 900)
        self.assertFalse(ok)
        self.assertIn("encoding", reason)

    def test_a_scanned_page_with_no_text_layer_is_refused(self):
        ok, reason, _m = I.gate("  \n \n 3 \n")
        self.assertFalse(ok)
        self.assertIn("scanned", reason)

    def test_a_wall_of_numbers_is_refused(self):
        ok, reason, _m = I.gate(" ".join(["%d.%02d" % (i, i % 97)
                                          for i in range(400)]))
        self.assertFalse(ok)

    def test_the_refusal_names_a_measurement_every_time(self):
        for bad in (PROSE.replace(" ", "") * 2, "�" * 900, "abc"):
            ok, reason, _m = I.gate(bad)
            self.assertFalse(ok)
            self.assertTrue(any(ch.isdigit() for ch in reason),
                            "refusal quoted no number: %r" % reason)


class TheStoreCannotBeReIngestedAsASuppliedResource(unittest.TestCase):
    """Isolation is a mechanism here too, not a promise."""

    def test_a_path_inside_prepwright_home_is_refused(self):
        with self.assertRaises(I.IngestRefused) as caught:
            I.resolve(os.path.join(C.HOME, "tracks", "t-000000000000",
                                   "corpus", "D01__x.doc.md"))
        self.assertIn("Prepwright's own storage", str(caught.exception))

    def test_home_itself_is_refused(self):
        with self.assertRaises(I.IngestRefused):
            I.resolve(C.HOME)

    def test_a_sibling_of_home_is_not_refused_for_that_reason(self):
        # The guard must match the directory, not a string prefix of its name.
        with self.assertRaises(I.IngestRefused) as caught:
            I.resolve(C.HOME + "-notes.md")
        self.assertNotIn("Prepwright's own storage", str(caught.exception))

    def test_a_folder_is_refused_by_name(self):
        directory = tempfile.mkdtemp(prefix="prepwright-ingest-")
        with self.assertRaises(I.IngestRefused) as caught:
            I.resolve(directory)
        self.assertIn("folder", str(caught.exception))

    def test_an_unreadable_format_names_what_is_readable(self):
        path = _tmp("notes.pages", "x" * 100)
        with self.assertRaises(I.IngestRefused) as caught:
            I.sniff(path)
        self.assertIn(".pdf", str(caught.exception))
        self.assertIn(".pages", str(caught.exception))


class AResourceIsCutToWhatWasAsked(unittest.TestCase):
    """The 20/80 cut, and the record of what it dropped."""

    SECTIONS = [
        ("Agent orchestration", "Planning and tool calling across many agents."),
        ("Memory for agents", "Short and long term memory in an agent loop."),
        ("Payroll tax schedules", "Quarterly remittance thresholds by state."),
        ("Office parking policy", "Bays are allocated by seniority each March."),
    ]

    def test_the_goal_decides_what_is_kept(self):
        kept, dropped = I.select(self.SECTIONS,
                                 "agent orchestration tool calling memory")
        headings = [h for h, _b in kept]
        self.assertIn("Agent orchestration", headings)
        self.assertIn("Memory for agents", headings)
        self.assertNotIn("Office parking policy", headings)
        self.assertTrue(dropped)

    def test_every_drop_carries_its_reason(self):
        _kept, dropped = I.select(self.SECTIONS, "agent orchestration memory")
        for heading, why in dropped:
            self.assertTrue(why.strip(), "%s was dropped with no reason" % heading)

    def test_kept_sections_stay_in_reading_order(self):
        kept, _dropped = I.select(
            [("A", "agent memory"), ("B", "unrelated parking"),
             ("C", "agent orchestration memory tools")],
            "agent memory orchestration")
        self.assertEqual([h for h, _b in kept], sorted(h for h, _b in kept))

    def test_no_goal_keeps_the_resource_in_its_own_order(self):
        kept, dropped = I.select(self.SECTIONS, "")
        self.assertEqual([h for h, _b in kept], [h for h, _b in self.SECTIONS])
        self.assertEqual(dropped, [])

    def test_a_goal_nothing_matches_keeps_nothing_and_says_so(self):
        kept, dropped = I.select(self.SECTIONS, "photosynthesis chlorophyll")
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), len(self.SECTIONS))


class StructureIsRecoveredWithoutInventingIt(unittest.TestCase):

    def test_a_sentence_is_not_a_heading(self):
        line = ("Part 2 comprises the Core of the Framework. It describes four "
                "specific functions to help organizations address risks.")
        self.assertIsNone(I.looks_like_heading(line))

    def test_a_numbered_section_is_a_heading(self):
        found = I.looks_like_heading("1.2.1    Risk Measurement")
        self.assertIsNotNone(found)
        self.assertIn("Risk Measurement", found[1])

    def test_running_page_furniture_is_removed(self):
        page = "NIST AI 100-1\nsome real content on this page here\n"
        text = I.strip_running(page * 8)
        self.assertNotIn("NIST AI 100-1", text)
        self.assertIn("some real content", text)

    def test_a_line_repeated_less_than_the_threshold_survives(self):
        page = "Kept Heading\nbody text under it\n"
        self.assertIn("Kept Heading", I.strip_running(page * 2))

    def test_repeated_body_text_is_never_removed(self):
        # The first version of strip_running counted every repeated short line
        # and deleted the body of a checklist along with its page header.
        page = "NIST AI 100-1\nnot applicable for this control\n"
        text = I.strip_running(page * 8)
        self.assertNotIn("NIST AI 100-1", text)
        self.assertIn("not applicable for this control", text)

    def test_an_overlong_section_is_split_not_truncated(self):
        body = "\n\n".join(["This is a full paragraph of real content. " * 6] * 8)
        pieces = I._split_body("Chapter One", body)
        self.assertGreater(len(pieces), 1)
        for _heading, text in pieces:
            self.assertLessEqual(len(text), C.SECTION_MAX_CHARS)
        rejoined = sum(len(t) for _h, t in pieces)
        self.assertGreater(rejoined, C.SECTION_MAX_CHARS,
                           "splitting lost the body instead of carrying it")
        self.assertIn("cont. 2", pieces[1][0])

    def test_text_before_the_first_heading_is_kept(self):
        rows = I.outline("An opening summary paragraph that matters.\n\n"
                         "## Later\nmore text here\n")
        self.assertTrue(any(h == "Opening" for _lv, h, _b in rows))

    def test_grouping_respects_the_per_document_caps(self):
        pairs = [("Heading %02d" % i, "body sentence here. " * 20)
                 for i in range(40)]
        docs = I.group(pairs, "A Resource")
        self.assertGreater(len(docs), 1)
        for _title, sections in docs:
            self.assertLessEqual(len(sections), C.MAX_SECTIONS_PER_DOC)
            size = sum(len(h.encode()) + len(b.encode()) for h, b in sections)
            self.assertLessEqual(size, I.DOC_BODY_BUDGET + 1024)


def _xlsx(rows, sheet_name="Controls"):
    """A minimal but real .xlsx: shared strings, one sheet, a workbook part."""
    shared = []
    for row in rows:
        for cell in row:
            if cell not in shared:
                shared.append(cell)
    sst = ("<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
           + "".join("<si><t>%s</t></si>" % s for s in shared) + "</sst>")
    body = []
    for r, row in enumerate(rows, 1):
        cells = "".join(
            "<c r=\"%s%d\" t=\"s\"><v>%d</v></c>"
            % (chr(65 + c), r, shared.index(cell)) for c, cell in enumerate(row))
        body.append("<row r=\"%d\">%s</row>" % (r, cells))
    sheet = ("<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
             "<sheetData>" + "".join(body) + "</sheetData></worksheet>")
    book = ("<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">"
            "<sheets><sheet name=\"%s\" sheetId=\"1\"/></sheets></workbook>" % sheet_name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/workbook.xml", book)
    return buf.getvalue()


def _docx(paragraphs):
    """A minimal but real .docx, with genuine heading styles."""
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = []
    for style, text in paragraphs:
        style_xml = ("<w:pPr><w:pStyle w:val=\"%s\"/></w:pPr>" % style) if style else ""
        body.append("<w:p>%s<w:r><w:t>%s</w:t></w:r></w:p>" % (style_xml, text))
    doc = ("<w:document xmlns:w=\"%s\"><w:body>%s</w:body></w:document>"
           % (ns, "".join(body)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


class OfficeFormatsAreReadWithoutADependency(unittest.TestCase):

    def test_a_spreadsheet_becomes_rows_under_a_sheet_heading(self):
        data = _xlsx([["Control", "Owner"], ["Access review", "Security"]],
                     sheet_name="Register")
        text = I.read_xlsx(data)
        self.assertIn("## Register", text)
        self.assertIn("Access review | Security", text)

    def test_a_word_heading_style_becomes_a_markdown_heading(self):
        data = _docx([("Heading1", "Agent Orchestration"),
                      ("", "Tool calling and planning in a loop."),
                      ("Heading2", "Memory")])
        text = I.read_docx(data)
        self.assertIn("# Agent Orchestration", text)
        self.assertIn("## Memory", text)
        self.assertIn("Tool calling", text)

    def test_word_heading_styles_survive_into_the_outline(self):
        data = _docx([("Heading1", "Agent Orchestration"),
                      ("", "Tool calling and planning inside one bounded loop."),
                      ("Heading2", "Memory"),
                      ("", "Short and long term recall across turns.")])
        headings = [h for _lv, h, _b in I.outline(I.read_docx(data))]
        self.assertIn("Agent Orchestration", headings)
        self.assertIn("Memory", headings)

    def test_a_corrupt_office_file_is_refused_by_name(self):
        with self.assertRaises(I.IngestRefused) as caught:
            I.read_docx(b"not a zip at all")
        self.assertIn("Word", str(caught.exception))
        with self.assertRaises(I.IngestRefused) as caught:
            I.read_xlsx(b"not a zip at all")
        self.assertIn("Excel", str(caught.exception))


class ReadingAFileCostsNoModelCall(unittest.TestCase):
    """The economic claim the feature rests on, asserted rather than assumed."""

    def test_the_module_imports_no_provider(self):
        with open(os.path.join(ROOT, "prepwright", "ingest.py"),
                  encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("run_cli", "chat_via_cli", "anthropic", "openai",
                          "urlopen", "HTTPSConnection"):
            self.assertNotIn(forbidden, source,
                             "ingest reached for %s; ingestion must cost no "
                             "tokens and open no socket" % forbidden)

    def test_both_reports_answer_the_same_questions(self):
        """The page branches on keys, so a missing one is a wrong answer.

        A stored report without `ok` rendered a successful ingest as "Not
        stored. That file could not be read.", with the documents in the corpus
        the whole time.
        """
        import inspect
        preview_keys = {"ok", "path", "name", "how", "title", "coverage",
                        "warning", "sections", "kept", "documents", "dropped"}
        path = _tmp("handbook.md", ("# Handbook\n\n" + PROSE) * 4)
        report = I.preview(path, "measure risk metrics")
        self.assertTrue(preview_keys.issubset(set(report)),
                        preview_keys - set(report))
        source = inspect.getsource(I.ingest_file)
        for key in preview_keys:
            self.assertIn('"%s"' % key, source,
                          "the stored report omits %r, which the page reads "
                          "off the preview report" % key)

    def test_a_whole_resource_is_read_and_cut_locally(self):
        path = _tmp("handbook.md", ("# Handbook\n\n" + PROSE) * 6)
        report = I.preview(path, "measure risk metrics benchmark monitor")
        self.assertTrue(report["ok"], report["reason"])
        self.assertGreater(report["sections"], 0)
        self.assertLessEqual(report["kept"], report["sections"])
        self.assertGreater(report["documents"], 0)


if __name__ == "__main__":
    unittest.main()
