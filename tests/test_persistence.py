"""The properties the persistence layer exists to have.

Each one is the failure it prevents, run for real: a process killed inside a
transaction, two processes writing at once, a scribbled-on database file, a
delete attempted while a session is open, one track's corpus offered to another
track's prompt, a document that cannot survive its own format, a half-registered
write, the transcript cap, the append-only triggers and the eviction ladder. A
mocked filesystem and a mocked SQLite cannot fail the way the real ones do, so
none is used here.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import importlib
import json
import os
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)   # the repo under test wins over any installed copy

from prepwright import config as C           # noqa: E402
from prepwright import state as S            # noqa: E402
from prepwright import track as T            # noqa: E402
from prepwright import keep as K             # noqa: E402


SECTIONS = [
    {"sec_id": "s01", "heading": "what-max-age-promises",
     "body": "max-age sets how long a stored response stays fresh, in seconds.",
     "origin_span": "max-age sets how long a stored response"},
    {"sec_id": "s02", "heading": "what-revalidation-costs",
     "body": "A matching ETag returns 304 with no body, and the missing body is "
             "the whole saving.",
     "origin_span": "A matching ETag returns 304 with no body"},
]


class Base(unittest.TestCase):
    """A fresh storage root per test, so no test can pass on another's leftovers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-store-")
        # Registered FIRST so it runs LAST. unittest runs every addCleanup after
        # tearDown, so resetting the environment in tearDown let a handle closed
        # by a later cleanup open the candidate's REAL ~/.prepwright and write a
        # registry into it. A test suite must not be able to touch live storage.
        self.addCleanup(self._restore_home)
        os.environ["PREPWRIGHT_HOME"] = os.path.join(self.tmp, "home")
        importlib.reload(C)          # every path in the package derives from here
        S.ensure_home()

    def _restore_home(self):
        # realpath both sides: C.HOME is resolved at import, and on macOS /var
        # resolves to /private/var, so a raw prefix test fails on a correct path.
        self.assertTrue(
            os.path.realpath(C.HOME).startswith(os.path.realpath(self.tmp)),
            "storage root escaped the temporary directory: %s" % C.HOME)
        os.environ.pop("PREPWRIGHT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(C)

    # -- helpers ------------------------------------------------------------
    def a_track(self, title="Backend role", with_step=True):
        track_id = T.create_track(title)
        handle = S.open_track(track_id)
        if with_step:
            handle.add_step("st1", 1, "HTTP caching", "Explain revalidation")
        return track_id, handle

    def a_doc(self, handle, slug="http-caching", body_marker=None):
        sections = [dict(s) for s in SECTIONS]
        if body_marker:
            sections[0]["body"] = sections[0]["body"] + " " + body_marker
        return handle.write_doc(
            slug, "HTTP caching", sections,
            origin_url="https://example.org/caching", origin_bytes=412880,
            origin_sha256="c" * 64, extract_sha256="9" * 64)


# ============================================================================
class WriteKilledMidTransaction(Base):
    """A process killed between BEGIN and COMMIT must leave nothing behind.

    This is the property `synchronous=FULL` plus WAL replay is bought for, and
    the reason there is no document being rewritten anywhere in the design:
    there is no torn-document failure mode to recover from.
    """

    def test_a_write_killed_mid_transaction_leaves_the_file_intact(self):
        track_id, handle = self.a_track()
        handle.append_turn("st1", "user", "what does max-age promise?", "c1")
        before = handle.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"]
        before_bytes = handle.conn.execute(
            "SELECT bytes_turns b FROM track_meta").fetchone()["b"]
        handle.close()

        db = os.path.join(C.TRACKS_ROOT, track_id, "track.db")
        script = textwrap.dedent("""
            import os, sqlite3, sys
            conn = sqlite3.connect(sys.argv[1], isolation_level=None)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO turn(step_id,at_utc,role,body,body_bytes,body_sha16,"
                " client_turn_id) VALUES ('st1','2026-01-01T00:00:00Z','user',"
                " 'this turn must not survive',26,'deadbeefdeadbeef','ghost')")
            conn.execute("UPDATE track_meta SET bytes_turns = bytes_turns + 999999")
            sys.stdout.write("inserted-uncommitted")
            sys.stdout.flush()
            os._exit(9)                      # no COMMIT, no rollback, no cleanup
        """)
        proc = subprocess.run([sys.executable, "-c", script, db],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 9)
        self.assertIn("inserted-uncommitted", proc.stdout)

        reopened = S.open_track(track_id)
        self.addCleanup(reopened.close)
        self.assertTrue(S.quick_check(reopened.conn), "database did not survive the kill")
        after = reopened.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"]
        after_bytes = reopened.conn.execute(
            "SELECT bytes_turns b FROM track_meta").fetchone()["b"]
        self.assertEqual(after, before, "an uncommitted turn survived the kill")
        self.assertEqual(after_bytes, before_bytes,
                         "an uncommitted counter update survived the kill")
        self.assertIsNone(
            reopened.conn.execute(
                "SELECT 1 FROM turn WHERE client_turn_id='ghost'").fetchone())
        # and the track is still writable afterwards, not merely readable
        seq = reopened.append_turn("st1", "tutor", "It stays fresh for that long.", "c2")
        self.assertGreater(seq, 0)


# ============================================================================
class TwoWritersRacing(Base):
    """Two processes appending at once. Both land, and the counter is exact.

    Sequence numbers are allocated by the server inside the write transaction,
    so interleaving cannot produce a duplicate or a gap that matters, and
    ordering is by seq rather than by either process's clock.
    """

    def test_two_processes_appending_at_once_both_land(self):
        track_id, handle = self.a_track()
        handle.close()

        writer = textwrap.dedent("""
            import os, sys
            sys.path.append(sys.argv[1])
            os.environ["PREPWRIGHT_HOME"] = sys.argv[2]
            import importlib
            from prepwright import config as C
            importlib.reload(C)
            from prepwright import state as S
            handle = S.open_track(sys.argv[3], take_lease=False)
            tag = sys.argv[4]
            for i in range(25):
                handle.append_turn("st1", "user", "%s message %d" % (tag, i),
                                   "%s-%d" % (tag, i))
            handle.close()
            sys.stdout.write("done")
        """)
        procs = [subprocess.Popen(
            [sys.executable, "-c", writer, ROOT, C.HOME, track_id, tag],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for tag in ("alpha", "beta")]
        outs = [p.communicate(timeout=120) for p in procs]
        for (out, err), p in zip(outs, procs):
            self.assertEqual(p.returncode, 0, "writer failed: %s" % err[-2000:])
            self.assertEqual(out.strip(), "done")

        handle = S.open_track(track_id)
        self.addCleanup(handle.close)
        rows = handle.conn.execute(
            "SELECT seq, body, body_bytes FROM turn ORDER BY seq").fetchall()
        self.assertEqual(len(rows), 50, "a concurrent append was lost")
        seqs = [r["seq"] for r in rows]
        self.assertEqual(len(set(seqs)), 50, "two turns share a sequence number")
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(sum(1 for r in rows if r["body"].startswith("alpha")), 25)
        self.assertEqual(sum(1 for r in rows if r["body"].startswith("beta")), 25)

        counted = handle.conn.execute(
            "SELECT bytes_turns b FROM track_meta").fetchone()["b"]
        expected = sum(r["body_bytes"] + 150 for r in rows)
        self.assertEqual(counted, expected,
                         "the in-transaction byte counter drifted under concurrency")

    def test_a_retried_append_is_a_no_op_not_a_duplicate(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        first = handle.append_turn("st1", "user", "sent once", "retry-me")
        second = handle.append_turn("st1", "user", "sent once", "retry-me")
        self.assertEqual(first, second)
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"], 1)


# ============================================================================
class CorruptDatabaseFile(Base):
    """A scribbled-on track.db is moved aside, never deleted, then restored."""

    def test_a_corrupt_database_is_quarantined_and_restored_from_backup(self):
        track_id, handle = self.a_track()
        for i in range(6):
            handle.append_turn("st1", "user", "turn number %d" % i, "t%d" % i)
        handle.close()
        backup = K.backup_track(track_id)
        self.assertTrue(os.path.isfile(backup))

        db = os.path.join(C.TRACKS_ROOT, track_id, "track.db")
        size = os.path.getsize(db)
        with open(db, "r+b") as fh:                 # scribble over the b-tree
            fh.seek(size // 2)
            fh.write(b"\xff" * 4096)
        conn = sqlite3.connect(db)
        self.addCleanup(conn.close)      # or it still holds the file we quarantine
        with self.assertRaises(sqlite3.DatabaseError):
            conn.execute("PRAGMA integrity_check").fetchall()
            conn.execute("SELECT COUNT(*) FROM turn").fetchone()

        handle = K.open_track_or_recover(track_id)
        self.addCleanup(handle.close)
        self.assertTrue(S.quick_check(handle.conn))
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"], 6)

        quarantine = os.path.join(C.TRACKS_ROOT, track_id, "quarantine")
        kept = os.listdir(quarantine)
        self.assertTrue(kept, "the corrupt file was deleted instead of quarantined")

        told = handle.conn.execute(
            "SELECT detail FROM recovery_log ORDER BY seq DESC LIMIT 1").fetchone()
        self.assertIn("restored", told["detail"])

    def test_a_corrupt_database_with_no_backup_is_never_silently_replaced(self):
        track_id, handle = self.a_track()
        handle.append_turn("st1", "user", "the only turn", "only")
        handle.close()                       # which now writes a backup
        backups = os.path.join(C.TRACKS_ROOT, track_id, "backup")
        for name in os.listdir(backups):     # take it away again
            os.remove(os.path.join(backups, name))
        db = os.path.join(C.TRACKS_ROOT, track_id, "track.db")
        with open(db, "r+b") as fh:
            fh.seek(os.path.getsize(db) // 2)
            fh.write(b"\xff" * 4096)
        with self.assertRaises(S.CorruptStore):
            K.open_track_or_recover(track_id)
        # left in place, not moved: with nowhere to restore from, emptying the
        # directory would destroy the only copy of the candidate's work
        self.assertTrue(os.path.isfile(db),
                        "the only copy was moved away with nothing to restore")


# ============================================================================
class DeleteWhileOpen(Base):
    """Delete and archive both refuse while a session holds the lease."""

    def test_a_track_cannot_be_deleted_while_its_chat_is_open(self):
        track_id, handle = self.a_track()
        handle.append_turn("st1", "user", "mid-conversation", "c1")
        lib = S.open_library()
        self.addCleanup(lib.close)
        code = T.get(lib, track_id)["short_code"]

        with self.assertRaises(T.LeaseHeld):
            T.trash_track(track_id, code, lib=lib)
        with self.assertRaises(T.LeaseHeld):
            T.archive_track(track_id, lib=lib)
        self.assertTrue(os.path.isdir(os.path.join(C.TRACKS_ROOT, track_id)))
        self.assertEqual(T.get(lib, track_id)["lifecycle"], "active")

        # the wrong confirmation code deletes nothing either
        T.close_session(lib, track_id, reason="test closed the session")
        # derived from the real code, because a hard-coded literal is inside the
        # four-hex-character space this code is drawn from
        wrong = "z" * len(code)
        with self.assertRaises(T.LifecycleError):
            T.trash_track(track_id, wrong, lib=lib)
        self.assertTrue(os.path.isdir(os.path.join(C.TRACKS_ROOT, track_id)))

        # closing the chat has to free the track, not leave it locked for two
        # minutes behind a client that has gone
        handle.close()
        self.assertIsNone(S.live_lease(lib, track_id),
                          "closing the handle did not release the lease")
        dest = T.trash_track(track_id, code, lib=lib)
        self.assertTrue(os.path.isfile(dest))
        self.assertEqual(T.get(lib, track_id)["lifecycle"], "trashed")
        # the record of what was prepared for outlives the material
        history = lib.execute(
            "SELECT kind FROM track_history WHERE track_id=? ORDER BY seq",
            (track_id,)).fetchall()
        self.assertIn("created", [h["kind"] for h in history])
        self.assertIn("trashed", [h["kind"] for h in history])

    def test_a_handle_taken_before_a_forced_close_cannot_write(self):
        track_id, handle = self.a_track()
        lib = S.open_library()
        self.addCleanup(lib.close)
        T.close_session(lib, track_id, reason="the candidate closed it on the phone")
        with self.assertRaises(S.TrackMoved):
            handle.assert_live(lib)
        # the guard is only real if it stands in front of the writes
        for label, call in (
                ("append_turn", lambda: handle.append_turn(
                    "st1", "user", "written after the forced close", "after")),
                ("add_step", lambda: handle.add_step("st9", 9, "T", "O")),
                ("add_assessment", lambda: handle.add_assessment("st1", 0.5, "r")),
                ("write_doc", lambda: handle.write_doc(
                    "x", "X", [{"sec_id": "s01", "heading": "h", "body": "b",
                                "origin_span": "b"}],
                    origin_url="https://x", origin_bytes=1,
                    origin_sha256="a" * 64, extract_sha256="b" * 64))):
            with self.subTest(write=label):
                with self.assertRaises(S.TrackMoved):
                    call()
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"], 0)
        handle.close()


# ============================================================================
class CrossTrackIsolation(Base):
    """Track A's corpus must not reach a prompt built for track B.

    Three of the design's eight layers are exercised here: separate files, the
    single open_track gate with its realpath containment, and the composite
    foreign key that cannot cross database files.

    The fourth layer the design names, the handle-scoped pack assertion, is NOT
    exercised and cannot be: build_pack tags each section with self.track_id and
    then compares it against self.track_id, so it is a tautology today. It earns
    its place only as a tripwire for a future change that sets that tag from
    somewhere else, and it should not be counted as a layer that is standing.
    """

    def test_one_tracks_corpus_cannot_reach_another_tracks_prompt(self):
        a_id, a = self.a_track("Track A")
        b_id, b = self.a_track("Track B")
        self.addCleanup(a.close)
        self.addCleanup(b.close)

        a_doc, a_file = self.a_doc(a, "http-caching", "SECRET-OF-TRACK-A")
        b_doc, b_file = self.a_doc(b, "http-caching", "material-of-track-b")
        a.pin_slice("st1", a_doc, "s01", 0)
        b.pin_slice("st1", b_doc, "s01", 0)

        a_pack = a.build_pack("st1")
        b_pack = b.build_pack("st1")
        self.assertIn("SECRET-OF-TRACK-A", a_pack["text"])
        self.assertNotIn("SECRET-OF-TRACK-A", b_pack["text"],
                         "track A's material reached track B's prompt")
        self.assertEqual(b_pack["track_id"], b_id)
        self.assertTrue(all(s["track_id"] == b_id for s in b_pack["sections"]))

        # Citation tokens are allocated per track, so both tracks call their
        # first document D01. That is the point: the same token read through
        # two handles returns two different documents, and neither handle can
        # reach the other's bytes. A validator therefore has to check a
        # citation against the pack that was sent, never against "a valid id".
        self.assertEqual(a_doc, b_doc)
        self.assertIn("SECRET-OF-TRACK-A", a.read_section(a_doc, "s01")["body"])
        self.assertNotIn("SECRET-OF-TRACK-A", b.read_section(b_doc, "s01")["body"])

        # a token neither track has is refused rather than resolved
        with self.assertRaises(S.IsolationError):
            b.read_section("D42", "s01")
        with self.assertRaises(sqlite3.IntegrityError):
            b.pin_slice("st1", "D42", "s01", 9)   # the composite foreign key

    def test_a_path_cannot_walk_out_of_its_own_corpus(self):
        a_id, a = self.a_track("Track A")
        b_id, b = self.a_track("Track B")
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        _doc, a_file = self.a_doc(a, "http-caching", "SECRET-OF-TRACK-A")

        escapes = [
            "../../%s/corpus/%s" % (a_id, a_file),
            "../../../etc/passwd",
            "/etc/passwd",
            "..",
            "",
            "   ",
            "a\x00b",
            a_file,                       # right name, wrong track
        ]
        for bad in escapes:
            with self.subTest(path=bad):
                self.assertIsNone(b.corpus_path(bad))

        # A symlink out of the corpus is refused. So is one whose target lands
        # back INSIDE it: the stated property is "refused rather than followed",
        # and without the islink test the containment check alone would let the
        # second one through, which is the version that passes for the wrong
        # reason.
        link = os.path.join(b.corpus_root, "D98__link.doc.md")
        os.symlink(os.path.join(a.corpus_root, a_file), link)
        self.assertIsNone(b.corpus_path("D98__link.doc.md"))

        _bdoc, b_file = self.a_doc(b, "database-indexing", "material-of-track-b")
        inside = os.path.join(b.corpus_root, "D99__inside.doc.md")
        os.symlink(os.path.join(b.corpus_root, b_file), inside)
        self.assertIsNone(b.corpus_path("D99__inside.doc.md"),
                          "a link resolving back inside the corpus was followed")

    def test_a_track_directory_carrying_the_wrong_id_is_refused(self):
        track_id, handle = self.a_track()
        handle.close()
        marker = os.path.join(C.TRACKS_ROOT, track_id, "TRACK_ID")
        S.atomic_write(marker, "t-000000000000\n")
        with self.assertRaises(S.IsolationError):
            S.open_track(track_id)

    def test_a_track_id_that_is_not_one_never_becomes_a_path(self):
        for bad in ("../../etc", "t-XYZ", "t-abc", "", "t-" + "a" * 13, None, 7):
            with self.subTest(track_id=bad):
                with self.assertRaises(S.IsolationError):
                    S.track_dir(bad)

    def test_a_trailing_newline_does_not_make_an_id_or_a_doc_name_valid(self):
        """`$` in a Python regex also matches before a trailing newline.

        `track_dir` matches the raw argument with no strip, so with `$` rather
        than `\\Z` the id "t-93c97fdd6d77\\n" passed the gate and os.path.join
        received a newline. That half is a real behaviour change: revert the
        `\\Z` in config.py and the first assertion below fails.

        `DOC_NAME_RE` is defence in depth and is stated as such rather than
        overclaimed. Its one unstripped caller, `write_doc`, builds the name
        itself, and `corpus_path` strips before matching (state.py), so no
        caller reaches the anchor with a trailing newline today. The pattern is
        asserted directly, because that is the level the guard actually lives
        at, and a test that pretended otherwise would pass for the wrong reason.
        """
        real_id, handle = self.a_track()
        try:
            _doc_id, file_name = self.a_doc(handle, "anchors", "material")
        finally:
            handle.close()

        with self.assertRaises(S.IsolationError):
            S.track_dir(real_id + "\n")
        self.assertTrue(S.track_dir(real_id), "the real id stopped resolving")

        self.assertIsNone(S._DOC_NAME.match(file_name + "\n"),
                          "DOC_NAME_RE accepts a name carrying a trailing newline")
        self.assertIsNotNone(S._DOC_NAME.match(file_name),
                             "DOC_NAME_RE stopped accepting a real doc name")
        self.assertIsNone(S._TRACK_ID.match(real_id + "\n"),
                          "TRACK_ID_RE accepts an id carrying a trailing newline")


# ============================================================================
class CapsAndAppendOnly(Base):
    """The two properties the caps and the triggers exist to guarantee."""

    def test_a_cap_is_enforced_by_the_write_that_would_exceed_it(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        handle.conn.execute(
            "UPDATE track_meta SET bytes_turns = ?", (C.TURNS_BYTES_CAP - 10,))
        with self.assertRaises(S.CapExceeded) as caught:
            handle.append_turn("st1", "user", "one turn too many", "over")
        self.assertEqual(caught.exception.cap, C.TURNS_BYTES_CAP)
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"], 0,
            "the refused turn was written anyway")
        self.assertEqual(
            handle.conn.execute("SELECT bytes_turns b FROM track_meta").fetchone()["b"],
            C.TURNS_BYTES_CAP - 10,
            "a refused write still moved the counter")

    def test_an_oversized_turn_is_truncated_with_its_overflow_recorded(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        big = "x" * (C.TURN_MAX_BYTES + 5000)
        seq = handle.append_turn("st1", "tutor", big, "big")
        row = handle.conn.execute(
            "SELECT body, body_bytes, overflow_bytes, overflow_sha256 FROM turn"
            " WHERE seq=?", (seq,)).fetchone()
        self.assertLessEqual(row["body_bytes"], C.TURN_MAX_BYTES)
        self.assertGreater(row["overflow_bytes"], 0)
        self.assertTrue(row["overflow_sha256"])
        # a turn that was cut has to say so, or the candidate reads a sentence
        # that stops mid-thought and nothing tells them why
        self.assertIn("[truncated at the turn cap]", row["body"])
        self.assertEqual(len(row["body"].encode("utf-8")), row["body_bytes"])

    def test_a_student_turn_cannot_be_deleted_or_emptied_by_any_path(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        seq = handle.append_turn("st1", "user", "what I actually said", "mine")
        with self.assertRaises(sqlite3.IntegrityError):
            handle.conn.execute("DELETE FROM turn WHERE seq=?", (seq,))
        with self.assertRaises(sqlite3.IntegrityError):
            handle.conn.execute("UPDATE turn SET body='' WHERE seq=?", (seq,))
        with self.assertRaises(S.StoreError):
            handle.compact_turn(seq)
        self.assertEqual(
            handle.conn.execute("SELECT body FROM turn WHERE seq=?",
                                (seq,)).fetchone()["body"],
            "what I actually said")

    def test_tutor_prose_is_compactable_only_with_a_receipt_and_a_review(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        seq = handle.append_turn("st1", "tutor", "a long explanation", "prose")
        with self.assertRaises(S.StoreError):
            handle.compact_turn(seq)         # no step review yet
        handle.conn.execute(
            "UPDATE step SET status='done', review='we covered revalidation'"
            " WHERE step_id='st1'")
        freed = handle.compact_turn(seq)
        self.assertGreater(freed, 0)
        row = handle.conn.execute(
            "SELECT body FROM turn WHERE seq=?", (seq,)).fetchone()
        self.assertEqual(row["body"], "")
        receipt = handle.conn.execute(
            "SELECT orig_bytes, orig_sha16 FROM turn_compaction WHERE turn_seq=?",
            (seq,)).fetchone()
        self.assertEqual(receipt["orig_bytes"], freed)
        self.assertTrue(receipt["orig_sha16"])

    def test_an_assessment_is_append_only(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        handle.add_assessment("st1", 0.6, "engaged, one gap")
        with self.assertRaises(sqlite3.IntegrityError):
            handle.conn.execute("UPDATE assessment SET score=1.0")
        with self.assertRaises(sqlite3.IntegrityError):
            handle.conn.execute("DELETE FROM assessment")

    def test_a_card_must_cite_a_section_that_exists_here(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        doc_id, _f = self.a_doc(handle)
        handle.add_card("c1", "What does max-age promise?", "Freshness in seconds.",
                        "%s§s01" % doc_id, "max-age sets how long")
        with self.assertRaises(S.IsolationError):
            handle.add_card("c2", "invented", "invented", "D99§s09", "nothing")


# ============================================================================
class DocumentFormat(Base):
    """A document has to survive its own format, and a re-scan proves it.

    The writer computes offsets and never parses, so a body carrying the
    section delimiter at the start of a line reads back perfectly until the
    first re-scan, which is what every restore triggers. It then loses every
    offset after that line and a byte-identical document is quarantined.
    """

    KW = dict(origin_url="https://example.org/spec", origin_bytes=1,
              origin_sha256="a" * 64, extract_sha256="b" * 64)

    def test_a_section_that_cannot_round_trip_is_refused_at_the_write(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        with self.assertRaises(ValueError):
            handle.write_doc("spec", "Spec", [
                {"sec_id": "s01", "heading": "where-the-rule-lives",
                 "body": "the rule is\n§s02|not-a-section\nand it applies",
                 "origin_span": "the rule is"}], **self.KW)
        with self.assertRaises(ValueError):
            handle.write_doc("spec", "Spec", [
                {"sec_id": "s01", "heading": "two\nlines", "body": "fine",
                 "origin_span": "fine"}], **self.KW)
        # neither refusal may leave a number allocated or a row behind
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM doc").fetchone()["c"], 0)
        self.assertEqual(
            handle.conn.execute(
                "SELECT next_doc_no n FROM track_meta").fetchone()["n"], 1)

    def test_the_delimiter_inside_a_line_survives_a_rescan(self):
        track_id, handle = self.a_track()
        doc_id, name = handle.write_doc("spec", "Spec", [
            {"sec_id": "s01", "heading": "where-the-rule-lives",
             "body": "see §4.2 of the spec for the rule",
             "origin_span": "see the spec"}], **self.KW)
        handle.close()
        path = os.path.join(C.TRACKS_ROOT, track_id, "corpus", name)
        with open(path, "rb") as fh:
            raw = fh.read()
        os.remove(path)                      # identical bytes, new mtime
        with open(path, "wb") as fh:
            fh.write(raw)

        reopened = S.open_track(track_id)    # open_track re-scans
        self.addCleanup(reopened.close)
        self.assertEqual(
            reopened.conn.execute("SELECT status FROM doc WHERE doc_id=?",
                                  (doc_id,)).fetchone()["status"], "ready")
        self.assertIn("§4.2", reopened.read_section(doc_id, "s01")["body"])

    def test_a_document_whose_text_moved_is_quarantined_not_taught_from(self):
        track_id, handle = self.a_track()
        doc_id, name = handle.write_doc("spec", "Spec", [
            {"sec_id": "s01", "heading": "the-rule", "body": "caches must revalidate",
             "origin_span": "caches must"}], **self.KW)
        handle.pin_slice("st1", doc_id, "s01", 0)
        handle.close()
        path = os.path.join(C.TRACKS_ROOT, track_id, "corpus", name)
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace("caches must revalidate", "caches may serve stale"))

        reopened = S.open_track(track_id)
        self.addCleanup(reopened.close)
        self.assertEqual(
            reopened.conn.execute("SELECT status FROM doc WHERE doc_id=?",
                                  (doc_id,)).fetchone()["status"], "quarantined")
        self.assertEqual(
            reopened.conn.execute(
                "SELECT evidence_state e FROM step WHERE step_id='st1'"
            ).fetchone()["e"], "degraded")
        self.assertEqual(reopened.build_pack("st1")["sections"], [],
                         "a document whose text moved still reached a prompt")


class OrphanedWrites(Base):
    """write_doc is two-phase. Anything that fails between the two transactions
    leaves a file and a 'writing' row, and housekeep has to take both."""

    def test_a_half_registered_document_is_swept_and_its_number_is_not_reused(self):
        track_id, handle = self.a_track()
        kw = dict(origin_url="https://example.org/a", origin_bytes=1,
                  origin_sha256="a" * 64, extract_sha256="b" * 64)
        sections = [{"sec_id": "s01", "heading": "h", "body": "x" * 800,
                     "origin_span": "x" * 40}]
        # the corpus counter just under its cap, so txn B refuses AFTER the file
        # has been written and renamed
        handle.conn.execute("UPDATE track_meta SET bytes_corpus=?",
                            (C.CORPUS_BYTES_CAP - 10,))
        with self.assertRaises(S.CapExceeded):
            handle.write_doc("too-big", "Too big", sections, **kw)

        corpus = os.path.join(C.TRACKS_ROOT, track_id, "corpus")
        self.assertEqual(len(os.listdir(corpus)), 1, "the file was never written")
        self.assertEqual(
            handle.conn.execute(
                "SELECT status s FROM doc").fetchone()["s"], "writing")
        # age it past the write window, then sweep
        handle.conn.execute("UPDATE doc SET fetched_utc='2000-01-01T00:00:00Z'")
        handle.conn.execute("UPDATE track_meta SET bytes_corpus=0")
        handle.close()
        lib = S.open_library()
        self.addCleanup(lib.close)
        S.release_lease(lib, track_id)

        report = K.housekeep(lib=lib)
        rung1 = [r for r in report["rungs"] if r["rung"] == 1][0]
        self.assertTrue(rung1["orphans"], "the orphan was not reported")
        self.assertEqual(os.listdir(corpus), [], "the orphan file survived")

        reopened = S.open_track(track_id, lib=lib)
        self.addCleanup(reopened.close)
        self.assertEqual(
            reopened.conn.execute("SELECT COUNT(*) c FROM doc").fetchone()["c"], 0)
        self.assertEqual(
            reopened.conn.execute(
                "SELECT next_doc_no n FROM track_meta").fetchone()["n"], 2,
            "a citation token was made available for reuse")
        told = reopened.conn.execute(
            "SELECT kind FROM recovery_log ORDER BY seq DESC LIMIT 1").fetchone()
        self.assertEqual(told["kind"], "orphan_swept")

    def test_a_document_still_inside_its_write_window_is_left_alone(self):
        track_id, handle = self.a_track()
        kw = dict(origin_url="https://example.org/a", origin_bytes=1,
                  origin_sha256="a" * 64, extract_sha256="b" * 64)
        handle.conn.execute("UPDATE track_meta SET bytes_corpus=?",
                            (C.CORPUS_BYTES_CAP - 10,))
        with self.assertRaises(S.CapExceeded):
            handle.write_doc("fresh", "Fresh", [
                {"sec_id": "s01", "heading": "h", "body": "x" * 800,
                 "origin_span": "x" * 40}], **kw)
        handle.close()
        lib = S.open_library()
        self.addCleanup(lib.close)
        S.release_lease(lib, track_id)
        K.housekeep(lib=lib)          # the row is seconds old, not an hour
        corpus = os.path.join(C.TRACKS_ROOT, track_id, "corpus")
        self.assertEqual(len(os.listdir(corpus)), 1,
                         "a document another process may still be writing was deleted")


class TranscriptCap(Base):
    """At the transcript cap the track keeps working, at the price of tutor prose."""

    def test_the_cap_compacts_the_oldest_finished_step_instead_of_freezing(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        handle.add_step("st0", 0, "An earlier step", "Already finished")
        mine = handle.append_turn("st0", "user", "what I said in the old step", "u0")
        prose = handle.append_turn("st0", "tutor", "a long explanation " * 40, "t0")
        handle.conn.execute(
            "UPDATE step SET status='done', review='we covered it' WHERE step_id='st0'")

        handle.conn.execute("UPDATE track_meta SET bytes_turns=?",
                            (C.TURNS_BYTES_CAP - 20,))
        seq = handle.append_turn("st1", "user", "the turn that hits the cap", "u1")
        self.assertGreater(seq, 0, "the write was refused instead of making room")

        self.assertEqual(
            handle.conn.execute("SELECT body FROM turn WHERE seq=?",
                                (prose,)).fetchone()["body"], "",
            "the tutor's prose was not the thing that gave way")
        self.assertEqual(
            handle.conn.execute("SELECT body FROM turn WHERE seq=?",
                                (mine,)).fetchone()["body"],
            "what I said in the old step",
            "a student turn was compacted, which no path may do")
        self.assertIsNotNone(
            handle.conn.execute("SELECT 1 FROM turn_compaction WHERE turn_seq=?",
                                (prose,)).fetchone(),
            "prose was emptied with no receipt")
        told = handle.conn.execute(
            "SELECT kind FROM recovery_log ORDER BY seq DESC LIMIT 1").fetchone()
        self.assertEqual(told["kind"], "compacted_at_cap",
                         "the candidate was not told what was given up")

    def test_with_auto_compact_off_the_cap_refuses_and_names_the_remedy(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        handle.conn.execute("UPDATE track_meta SET bytes_turns=?",
                            (C.TURNS_BYTES_CAP - 20,))
        with self.assertRaises(S.CapExceeded) as caught:
            handle.append_turn("st1", "user", "too much", "u1", auto_compact=False)
        self.assertEqual(caught.exception.cap, C.TURNS_BYTES_CAP)

    def test_with_nothing_safe_to_compact_the_cap_still_refuses(self):
        track_id, handle = self.a_track()
        self.addCleanup(handle.close)
        # a finished step with no review is not compactable: emptying its prose
        # would take the conclusion with the words
        handle.append_turn("st1", "tutor", "prose with no review behind it", "t0")
        handle.conn.execute("UPDATE step SET status='done' WHERE step_id='st1'")
        handle.conn.execute("UPDATE track_meta SET bytes_turns=?",
                            (C.TURNS_BYTES_CAP - 20,))
        with self.assertRaises(S.CapExceeded):
            handle.append_turn("st1", "user", "no room and nothing to free", "u1")


class LadderAndRecovery(Base):
    """The ladder is published, age-driven, and stops rather than improvising."""

    def test_housekeep_runs_every_rung_and_reports_what_it_did(self):
        track_id, handle = self.a_track()
        handle.append_turn("st1", "user", "some work", "w1")
        handle.close()
        report = K.housekeep()
        rungs = [r["rung"] for r in report["rungs"]]
        self.assertEqual(rungs, list(range(10)), "a rung was skipped silently")
        self.assertEqual(
            report["freed"],
            sum(r.get("freed", 0) for r in report["rungs"]),
            "the total freed does not match what the rungs reported")
        self.assertFalse(report["clock_suspect"])

    def test_a_suspect_clock_suspends_every_age_driven_step(self):
        track_id, handle = self.a_track()
        handle.close()
        lib = S.open_library()
        self.addCleanup(lib.close)
        lib.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('last_seen_utc',?)",
                    ("2099-01-01T00:00:00Z",))
        report = K.housekeep(lib=lib)
        self.assertTrue(report["clock_suspect"])
        self.assertTrue(any("clock" in n for n in report["notices"]))
        # the name says every age-driven step is suspended, so check the steps
        by_rung = {r["rung"]: r for r in report["rungs"]}
        self.assertFalse(by_rung[4]["freed"], "rung 4 dropped backups under a bad clock")
        self.assertFalse(by_rung[5]["tracks"], "rung 5 vacuumed under a bad clock")
        self.assertFalse(by_rung[6]["turns"], "rung 6 compacted under a bad clock")
        self.assertFalse(by_rung[7]["tracks"], "rung 7 archived under a bad clock")
        self.assertFalse(by_rung[8]["files"], "rung 8 purged under a bad clock")

    def test_rung_8_never_purges_inside_the_undo_window(self):
        track_id, handle = self.a_track("Delete me")
        handle.append_turn("st1", "user", "work I might want back", "w1")
        handle.close()
        lib = S.open_library()
        self.addCleanup(lib.close)
        code = T.get(lib, track_id)["short_code"]
        T.trash_track(track_id, code, lib=lib)
        pwk = os.path.join(C.TRASH_ROOT, "%s.pwk" % track_id)
        self.assertTrue(os.path.isfile(pwk))

        # a stray file over the cap must not buy its deletion
        with open(os.path.join(C.TRASH_ROOT, "stray.bin"), "wb") as fh:
            fh.write(b"\0" * (C.TRASH_CAP + 4096))
        purged, early = K._purge_trash()
        self.assertEqual(purged, [], "a stray file forced a purge inside the window")
        self.assertTrue(os.path.isfile(pwk))
        os.remove(os.path.join(C.TRASH_ROOT, "stray.bin"))

        # real pressure purges early, and says which and how much was left
        with open(pwk, "ab") as fh:
            fh.write(b"\0" * (C.TRASH_CAP + 4096))
        purged, early = K._purge_trash()
        self.assertEqual(purged, [track_id])
        self.assertEqual(len(early), 1)
        self.assertGreater(early[0]["days_left"], 0)
        self.assertFalse(os.path.isfile(pwk))

    def test_an_orphan_directory_is_quarantined_and_never_deleted(self):
        stray = os.path.join(C.TRACKS_ROOT, "t-0123456789ab")
        S.ensure_dir(stray)
        S.atomic_write(os.path.join(stray, "TRACK_ID"), "t-0123456789ab\n")
        notes = T.reconcile()
        self.assertTrue(any("orphan" in n for n in notes))
        self.assertFalse(os.path.isdir(stray))
        self.assertTrue(os.listdir(C.QUARANTINE_ROOT))

    def test_a_damaged_archive_is_refused_rather_than_half_restored(self):
        track_id, handle = self.a_track("Damaged archive")
        doc_id, name = self.a_doc(handle, body_marker="MUST-SURVIVE")
        handle.append_turn("st1", "user", "work that must survive", "a1")
        handle.close()
        lib = S.open_library()
        self.addCleanup(lib.close)
        pwk = T.archive_track(track_id, lib=lib)
        T.restore_track(track_id, lib=lib)
        T.archive_track(track_id, lib=lib)

        # flip a byte inside the compressed archive, then restore
        with open(pwk, "r+b") as fh:
            fh.seek(os.path.getsize(pwk) // 2)
            fh.write(bytes([fh.read(1)[0] ^ 0xFF]))
        with self.assertRaises(Exception):
            T.restore_track(track_id, lib=lib)
        # whatever it raised, the archive is still there to try again with
        self.assertTrue(os.path.isfile(pwk),
                        "a failed restore deleted the archive it failed to read")

    def test_archive_verifies_every_member_before_it_deletes_anything(self):
        track_id, handle = self.a_track("Archive me")
        doc_id, _f = self.a_doc(handle, body_marker="ARCHIVED-CONTENT")
        handle.append_turn("st1", "user", "before archiving", "a1")
        handle.close()
        lib = S.open_library()
        self.addCleanup(lib.close)
        S.release_lease(lib, track_id)

        pwk = T.archive_track(track_id, lib=lib)
        self.assertTrue(os.path.isfile(pwk))
        self.assertFalse(os.path.isdir(os.path.join(C.TRACKS_ROOT, track_id)))
        self.assertEqual(T.get(lib, track_id)["lifecycle"], "archived")
        self.assertTrue(os.path.isfile(
            os.path.join(C.ARCHIVE_ROOT, "%s.meta.json" % track_id)))

        T.restore_track(track_id, lib=lib)
        restored = S.open_track(track_id, lib=lib)
        self.addCleanup(restored.close)
        self.assertEqual(
            restored.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"], 1)
        pack_source = restored.read_section(doc_id, "s01")
        self.assertIn("ARCHIVED-CONTENT", pack_source["body"])


if __name__ == "__main__":
    unittest.main()
