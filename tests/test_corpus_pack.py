"""The properties of corpus ingest and the evidence pack.

Every test runs against a real track database in a temporary storage root. The
grounding claim is what these protect: a pack carries only this track's bytes, a
credential in a source never reaches a prompt, and a citation the pack did not
supply is reported as invented rather than accepted.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import importlib
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prepwright import config as C           # noqa: E402
from prepwright import corpus as X           # noqa: E402
from prepwright import state as S            # noqa: E402
from prepwright import track as T            # noqa: E402


LOOSE = """# HTTP caching

Prose before the first heading is not citable and is dropped.

## Cache-Control basics
`max-age` sets how long a response stays fresh in seconds.

## ETag and revalidation
The server sends an `ETag`. A match gets a 304 with no body.

## A secret that must never reach a prompt
api_key: "AKIAIOSFODNN7EXAMPLE1234"
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-corpus-")
        # Registered first so it runs last: unittest runs every addCleanup after
        # tearDown, and a handle closed by a later cleanup must not be able to
        # open the candidate's real ~/.prepwright.
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

    def a_track(self, title="Backend role"):
        track_id = T.create_track(title)
        handle = S.open_track(track_id)
        handle.add_step("st1", 1, "HTTP caching", "Explain revalidation")
        self.addCleanup(handle.close)
        return track_id, handle

    def a_dir(self, **files):
        d = os.path.join(self.tmp, "seed")
        os.makedirs(d, exist_ok=True)
        for name, text in files.items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
        return d


# ============================================================================
class Redaction(Base):
    """A credential in a source must not survive into the store, let alone a prompt."""

    def test_a_credential_line_is_redacted_at_ingest_not_at_render(self):
        _tid, handle = self.a_track()
        doc_id = X.ingest_text(handle, LOOSE, origin_url="https://example.org/x")
        self.assertIsNotNone(doc_id)
        X.pin_all(handle, "st1", [doc_id])
        pack = X.build_pack(handle, "st1")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", pack["text"])
        self.assertIn("REDACTED", pack["text"])
        # And on disk, so a later reader of the file cannot recover it either.
        on_disk = ""
        for name in os.listdir(handle.corpus_root):
            with open(os.path.join(handle.corpus_root, name)) as fh:
                on_disk += fh.read()
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", on_disk)

    def test_b_an_unterminated_pem_block_swallows_the_rest(self):
        out = X.redact("keep\n-----BEGIN RSA PRIVATE KEY-----\nMIIkey\nmore key")
        self.assertIn("keep", out)
        self.assertNotIn("MIIkey", out)
        self.assertNotIn("more key", out)


# ============================================================================
class LooseParsing(Base):
    def test_a_prose_before_the_first_heading_is_dropped(self):
        title, pairs = X.parse_loose(LOOSE)
        self.assertEqual(title, "HTTP caching")
        self.assertEqual([h for h, _ in pairs],
                         ["Cache-Control basics", "ETag and revalidation",
                          "A secret that must never reach a prompt"])
        self.assertNotIn("not citable", "\n".join(b for _, b in pairs))

    def test_b_a_body_line_starting_with_the_delimiter_is_indented_not_refused(self):
        text = "# T\n\n## H\n§ this line starts with the delimiter\nrest\n"
        sections = X.fit_sections(X.parse_loose(text)[1])
        self.assertEqual(len(sections), 1)
        for line in sections[0]["body"].split("\n"):
            self.assertFalse(line.startswith("§"))

    def test_c_an_oversized_section_is_truncated_visibly(self):
        text = "# T\n\n## H\n" + ("word " * (C.SECTION_MAX_CHARS // 2)) + "\n"
        sections = X.fit_sections(X.parse_loose(text)[1])
        self.assertLessEqual(len(sections[0]["body"]), C.SECTION_MAX_CHARS)
        self.assertIn("TRUNCATED", sections[0]["body"])

    def test_d_a_document_with_no_citable_section_writes_nothing(self):
        _tid, handle = self.a_track()
        self.assertIsNone(X.ingest_text(handle, "# Title only, no sections\n",
                                        origin_url="https://example.org/x"))
        n = handle.conn.execute("SELECT COUNT(*) c FROM doc").fetchone()["c"]
        self.assertEqual(n, 0)


# ============================================================================
class DirectorySeed(Base):
    def test_a_seeding_twice_does_not_duplicate(self):
        _tid, handle = self.a_track()
        d = self.a_dir(**{"D01__http-caching.doc.md": LOOSE})
        first = X.seed_from_directory(handle, d, pin_to_step="st1")
        self.assertEqual(len(first), 1)
        second = X.seed_from_directory(handle, d, pin_to_step="st1")
        self.assertEqual(second, [], "a second seed duplicated the corpus")
        n = handle.conn.execute("SELECT COUNT(*) c FROM doc").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_b_a_symlink_in_the_seed_directory_is_not_followed(self):
        _tid, handle = self.a_track()
        d = self.a_dir(**{"real.md": LOOSE})
        outside = os.path.join(self.tmp, "outside.md")
        with open(outside, "w") as fh:
            fh.write("# Outside\n\n## H\nsecret material\n")
        os.symlink(outside, os.path.join(d, "link.md"))
        X.seed_from_directory(handle, d)
        titles = [r["title"] for r in
                  handle.conn.execute("SELECT title FROM doc").fetchall()]
        self.assertEqual(titles, ["HTTP caching"])

    def test_c_a_missing_directory_is_not_an_error(self):
        _tid, handle = self.a_track()
        self.assertEqual(X.seed_from_directory(handle, "/no/such/dir"), [])


# ============================================================================
class Pack(Base):
    """The pack is prompt-ready in every case, including the empty ones."""

    def test_a_an_empty_corpus_yields_the_honest_refusal_not_an_empty_string(self):
        _tid, handle = self.a_track()
        pack = X.build_pack(handle, "st1")
        self.assertFalse(pack["grounded"])
        self.assertEqual(pack["text"], X.EMPTY_CORPUS)
        self.assertTrue(X.pack_text(handle, "st1").strip())

    def test_b_a_corpus_with_nothing_pinned_says_so_differently(self):
        _tid, handle = self.a_track()
        X.ingest_text(handle, LOOSE, origin_url="https://example.org/x")
        pack = X.build_pack(handle, "st1")
        self.assertFalse(pack["grounded"])
        self.assertEqual(pack["text"], X.NO_SLICE)

    def test_c_a_pinned_corpus_is_grounded_and_cites_this_track(self):
        _tid, handle = self.a_track()
        doc_id = X.ingest_text(handle, LOOSE, origin_url="https://example.org/x")
        X.pin_all(handle, "st1", [doc_id])
        pack = X.build_pack(handle, "st1")
        self.assertTrue(pack["grounded"])
        self.assertTrue(pack["cites"])
        for cite in pack["cites"]:
            self.assertTrue(cite.startswith(doc_id))
        self.assertIn("max-age", pack["text"])

    def test_d_a_bad_step_id_degrades_to_a_refusal_and_does_not_raise(self):
        _tid, handle = self.a_track()
        pack = X.build_pack(handle, "no-such-step")
        self.assertFalse(pack["grounded"])
        self.assertTrue(pack["text"].strip())

    def test_e_one_tracks_pack_carries_no_other_tracks_bytes(self):
        _a, ha = self.a_track("Role A")
        _b, hb = self.a_track("Role B")
        da = X.ingest_text(ha, LOOSE, origin_url="https://example.org/a")
        db = X.ingest_text(hb, "# Other\n\n## Only here\nBEEF_MARKER_B\n",
                           origin_url="https://example.org/b")
        X.pin_all(ha, "st1", [da])
        X.pin_all(hb, "st1", [db])
        # Both tracks call their first document D01: the same token read through
        # two handles must return two different documents.
        self.assertEqual(da, db)
        pa = X.build_pack(ha, "st1")
        pb = X.build_pack(hb, "st1")
        self.assertNotIn("BEEF_MARKER_B", pa["text"])
        self.assertIn("BEEF_MARKER_B", pb["text"])
        self.assertNotEqual(pa["pack_sha16"], pb["pack_sha16"])


# ============================================================================
class SeedingPinsToAStepTheTrackDoesNotHaveYet(Base):
    """The path every fresh track takes, and the one no fixture took.

    `Base.a_track` creates step "st1" by hand, so every other test in this file
    pins to a step that already exists. `bridge.evidence_pack` does not: it
    seeds on the first teaching turn, naming the step key from the request,
    before anything has created that step row.

    `step_slice.step_id` carries a foreign key to `step` and foreign keys are
    on, so the pin raised IntegrityError from inside the seed. evidence_pack
    catches it, correctly, because a seed failure must not end a lesson. But the
    documents were already written, so `n_docs` was no longer zero and the seed
    never ran again. The first turn on a fresh track was ungrounded, and so was
    every turn after it, permanently, with one line on stderr.
    """

    def _bare_track(self):
        track_id = T.create_track("no steps yet")
        handle = S.open_track(track_id)
        self.addCleanup(handle.close)
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM step").fetchone()["c"], 0,
            "the fixture must start with no step, or it cannot see this bug")
        return handle

    def test_the_seed_grounds_a_track_that_has_no_steps(self):
        handle = self._bare_track()
        directory = self.a_dir(**{"D01__caching.doc.md": LOOSE})
        written = X.seed_from_directory(handle, directory, pin_to_step="1:topic:T1")
        self.assertTrue(written, "nothing was ingested")
        pack = X.build_pack(handle, "1:topic:T1")
        self.assertTrue(pack["grounded"],
                        "the seed wrote documents and pinned nothing")
        self.assertTrue(pack["cites"])

    def test_pinning_creates_the_step_rather_than_raising(self):
        handle = self._bare_track()
        doc_id = X.ingest_text(handle, LOOSE, origin_url="https://example.org/x")
        X.pin_all(handle, "1:topic:T9", [doc_id])
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM step WHERE step_id=?",
                                ("1:topic:T9",)).fetchone()["c"], 1)
        self.assertTrue(X.build_pack(handle, "1:topic:T9")["cites"])

    def test_a_second_seed_does_not_duplicate_the_step_or_the_documents(self):
        handle = self._bare_track()
        directory = self.a_dir(**{"D01__caching.doc.md": LOOSE})
        X.seed_from_directory(handle, directory, pin_to_step="1:topic:T1")
        again = X.seed_from_directory(handle, directory, pin_to_step="1:topic:T1")
        self.assertEqual(again, [], "the seed ingested the same document twice")
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM step").fetchone()["c"], 1)


# ============================================================================
class Citations(Base):
    """A citation is checked against the pack that was sent, not against the track."""

    def test_a_a_token_the_pack_did_not_supply_is_invented(self):
        result = X.check_citations("As D01§s01 says, and D01§s09 too.",
                                   {"cites": ["D01§s01"]})
        self.assertEqual(result["valid"], ["D01§s01"])
        self.assertEqual(result["invented"], ["D01§s09"])

    def test_b_a_token_this_track_owns_but_did_not_send_is_still_invented(self):
        _tid, handle = self.a_track()
        doc_id = X.ingest_text(handle, LOOSE, origin_url="https://example.org/x")
        # Pin only the first section, then cite the second.
        handle.pin_slice("st1", doc_id, "s01", 0)
        pack = X.build_pack(handle, "st1")
        self.assertEqual(pack["cites"], ["%s§s01" % doc_id])
        result = X.check_citations("see %s§s02" % doc_id, pack)
        self.assertEqual(result["invented"], ["%s§s02" % doc_id],
                         "a section the track owns but did not supply was accepted")

    def test_c_a_reply_with_no_citation_reports_none(self):
        self.assertEqual(X.check_citations("no tokens here", {"cites": ["D01§s01"]}),
                         {"named": [], "valid": [], "invented": []})


if __name__ == "__main__":
    unittest.main()
