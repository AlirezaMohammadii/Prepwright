"""The corpus boundary is the whole basis of the grounding claim.

Every byte the tutor is allowed to assert comes through corpus_evidence(). If a
path escapes that boundary, the tutor can be fed anything on this machine and
will cite it confidently. These tests use real files, real traversal strings and
a real symlink, because a mocked filesystem cannot fail the way a real one does.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bridge  # noqa: E402


DOC = """# HTTP caching

## Cache-Control basics
`max-age` sets how long a response stays fresh. `no-store` forbids storage.

## A credential that must never reach a prompt
api_key: "AKIAIOSFODNN7EXAMPLE1234"
"""


class CorpusBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-test-")
        self.corpus = os.path.join(self.tmp, "corpus")
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.corpus)
        os.makedirs(self.outside)
        with open(os.path.join(self.corpus, "D01__caching.doc.md"), "w") as fh:
            fh.write(DOC)
        self.secret_file = os.path.join(self.outside, "private.doc.md")
        with open(self.secret_file, "w") as fh:
            fh.write("## Outside\nTHIS_MUST_NEVER_BE_READ\n")
        os.symlink(self.secret_file, os.path.join(self.corpus, "L01__link.doc.md"))
        self._saved = bridge.CORPUS_DIR
        bridge.CORPUS_DIR = self.corpus

    def tearDown(self):
        bridge.CORPUS_DIR = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plain_document_resolves(self):
        self.assertIsNotNone(bridge.corpus_path("D01__caching.doc.md"))

    def test_traversal_is_refused(self):
        for bad in ("../bridge.py", "../../../../etc/passwd", "/etc/passwd",
                    "..", "./../outside/private.doc.md", "", "   ", "a\x00b"):
            with self.subTest(path=bad):
                self.assertIsNone(bridge.corpus_path(bad))

    def test_symlink_is_refused_not_followed(self):
        self.assertIsNone(bridge.corpus_path("L01__link.doc.md"))
        # and one whose target resolves back INSIDE the corpus, which is the
        # case the containment test alone would let through
        inside = os.path.join(self.corpus, "L02__inside.doc.md")
        os.symlink(os.path.join(self.corpus, "D01__caching.doc.md"), inside)
        self.assertIsNone(bridge.corpus_path("L02__inside.doc.md"),
                          "a link resolving back inside the corpus was followed")

    def test_symlinked_content_never_enters_a_pack(self):
        pack = bridge.corpus_evidence(
            {"title": "Outside", "prompt": "outside"}, "L01__link.doc.md", "outside")
        self.assertNotIn("THIS_MUST_NEVER_BE_READ", pack)

    def test_credentials_are_redacted_inside_an_allowed_document(self):
        pack = bridge.corpus_evidence(
            {"title": "Credential", "prompt": "credential prompt reach never must"},
            "D01__caching.doc.md", "credential")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE1234", pack)
        self.assertIn("REDACTED", pack)

    def test_real_content_is_still_retrieved(self):
        pack = bridge.corpus_evidence(
            {"title": "Caching", "prompt": "explain max-age freshness"},
            "", "how does max-age work?")
        self.assertIn("max-age", pack)

    def test_empty_corpus_says_so_instead_of_inventing(self):
        bridge.CORPUS_DIR = os.path.join(self.tmp, "no-such-dir")
        pack = bridge.corpus_evidence({}, "", "anything at all")
        self.assertIn("empty", pack.lower())

    def test_pack_respects_the_byte_budget(self):
        big = "## Section %d\n" + "x" * 2000 + "\n"
        with open(os.path.join(self.corpus, "D02__big.doc.md"), "w") as fh:
            fh.write("# Big\n" + "".join(big % i for i in range(40)))
        pack = bridge.corpus_evidence(
            {"title": "Big", "prompt": "xxx section"}, "D02__big.doc.md", "xxx")
        # The only legitimate overshoot is the join between blocks, at most
        # two bytes per gap. 2048 bytes of slack made the assertion
        # unreachable: removing the budget entirely still passed it.
        self.assertLessEqual(
            len(pack),
            bridge.CORPUS_PACK_MAX_BYTES + 2 * (bridge.CORPUS_MAX_SECTIONS - 1))

    def test_retrieval_never_raises_onto_the_teaching_path(self):
        bridge.CORPUS_DIR = None          # forced fault
        self.assertIsInstance(bridge.corpus_evidence({}, "", "x"), str)


if __name__ == "__main__":
    unittest.main()
