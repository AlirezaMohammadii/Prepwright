"""Regressions for the defects the round-0 adversarial hunt confirmed.

Each test names the defect, fails against the code as it was found, and passes
once the fix lands. Written before the fixes on purpose: a regression test
authored after the patch tends to assert what the patch does rather than what
the defect was.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import calendar
import importlib
import io
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prepwright import config as C           # noqa: E402
from prepwright import pagestate as PS       # noqa: E402
from prepwright import state as S            # noqa: E402
from prepwright import track as T            # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-hunt-")
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

    def a_track(self):
        track_id = T.create_track("Backend role")
        handle = S.open_track(track_id)
        self.addCleanup(handle.close)
        return track_id, handle


# ============================================================================
class DeltaBudgetIsCountedInBytes(Base):
    """MAX_DELTA_BYTES is a byte cap. Charging it in characters undercounts.

    The same unit error was fixed once in state.py's _charge. These are the
    sites in pagestate.py that were missed: the cap CHECK a line above each one
    already encodes, so the two disagree inside a single function.
    """

    def test_a_a_multibyte_mark_value_is_charged_its_real_bytes(self):
        # One astral character is 4 UTF-8 bytes and 1 Python character.
        big = "\U0001F600" * 20_000                # 80_000 bytes, 20_000 chars
        ops = [{"op": "mark", "id": "m1", "kind": "topic", "key": "T1",
                "value": big}]
        # It must be refused by the per-mark cap, and the refusal must be the
        # named one rather than a database CHECK further down.
        with self.assertRaises(PS.OpRejected):
            PS.validate_ops(ops)

    def test_b_many_multibyte_marks_cannot_exceed_the_delta_cap(self):
        # Each value is under MARK_MAX_BYTES but four times its character count.
        per = "\U0001F600" * 500                   # 2_000 bytes, 500 chars
        n = (PS.MAX_DELTA_BYTES // 2_000) + 20     # comfortably over in bytes
        n = min(n, PS.MAX_OPS_PER_WRITE - 1)
        ops = [{"op": "mark", "id": "m%d" % i, "kind": "topic",
                "key": "T%d" % i, "value": per} for i in range(n)]
        charged = sum(len(PS._json_dump(o["value"]).encode("utf-8")) for o in ops)
        if charged <= PS.MAX_DELTA_BYTES:
            self.skipTest("op cap binds before the byte cap on this build")
        with self.assertRaises(PS.OpRejected):
            PS.validate_ops(ops)

    def test_c_turn_metadata_is_charged_its_real_bytes(self):
        src = io.open(os.path.join(ROOT, "prepwright", "pagestate.py"),
                      encoding="utf-8").read()
        # The charge line for a turn must encode both terms, not just the text.
        self.assertFalse(
            'len(text.encode("utf-8")) + len(meta_json or "")' in src,
            "turn metadata is charged in characters while the cap two lines "
            "above is checked in bytes")
        self.assertFalse(
            "total += len(value_json)\n" in src,
            "a mark value is charged in characters while its cap is checked "
            "in bytes")


# ============================================================================
class EveryPartOfATurnIsCharged(Base):
    """A field that is stored but charged nothing is a cap with a hole in it."""

    def test_a_an_enormous_step_key_cannot_ride_in_free(self):
        # STEP_KEY_RE's trailing [A-Za-z0-9]+ has no upper bound, and the turn
        # branch charges text and meta only, so the key is stored and free.
        key = "1:topic:" + ("A" * 300_000)
        ops = [{"op": "turn", "id": "t1", "step": key, "pageRole": "me",
                "text": "hi"}]
        with self.assertRaises(PS.OpRejected):
            PS.validate_ops(ops)

    def test_b_the_step_key_pattern_is_bounded(self):
        self.assertIsNone(PS.STEP_KEY_RE.match("1:topic:" + "A" * 5_000),
                          "the step-key pattern accepts an unbounded key")


# ============================================================================
class AnOversizedTurnIsRefusedBeforeAnythingIsWritten(Base):
    """apply_ops has no enclosing transaction, so a mid-batch failure commits.

    validate_ops promises "a malformed op refuses the whole write". A turn body
    over TURN_MAX_BYTES is not caught there, so it reaches the SQL CHECK on
    turn.body_bytes with earlier ops in the same batch already committed.
    """

    def test_a_validate_rejects_a_turn_body_over_the_column_cap(self):
        ops = [{"op": "turn", "id": "t1", "step": "1:topic:T1",
                "pageRole": "me", "text": "x" * (C.TURN_MAX_BYTES + 1)}]
        with self.assertRaises(PS.OpRejected):
            PS.validate_ops(ops)

    def test_b_a_batch_that_fails_late_writes_none_of_its_earlier_ops(self):
        _tid, handle = self.a_track()
        good = {"op": "mark", "id": "m1", "kind": "topic", "key": "T1",
                "value": "done"}
        bad = {"op": "turn", "id": "t1", "step": "1:topic:T1", "pageRole": "me",
               "text": "x" * (C.TURN_MAX_BYTES + 1)}
        try:
            PS.apply_ops(handle, PS.validate_ops([good, bad]))
        except Exception:                                    # noqa: BLE001
            pass
        marks = handle.conn.execute("SELECT COUNT(*) c FROM mark").fetchone()["c"]
        self.assertEqual(marks, 0,
                         "an op before the failing one was committed, so a "
                         "refused write was applied in part")


# ============================================================================
class UtcStringsAreParsedAsUtc(unittest.TestCase):
    """time.mktime reads a UTC struct as local time. track.py says so in prose.

    Four sites in this package use calendar.timegm. One does not, and during
    local DST it computes a document's birth time an hour early, which makes an
    in-flight document eligible for the sweep that deletes it.
    """

    def test_a_no_module_parses_a_utc_stamp_with_mktime(self):
        offenders = []
        pkg = os.path.join(ROOT, "prepwright")
        for name in sorted(os.listdir(pkg)):
            if not name.endswith(".py"):
                continue
            src = io.open(os.path.join(pkg, name), encoding="utf-8").read()
            for m in re.finditer(r"time\.mktime\s*\(\s*time\.strptime", src):
                line = src.count("\n", 0, m.start()) + 1
                offenders.append("%s:%d" % (name, line))
        self.assertEqual(offenders, [],
                         "these parse a UTC stamp as local time; use "
                         "calendar.timegm, as track.py's own docstring says")

    def test_b_the_two_idioms_actually_differ_under_a_dst_zone(self):
        stamp = "2026-07-01T12:00:00Z"
        prev = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Europe/London"    # BST in July: UTC+1
            time.tzset()
            correct = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
            wrong = time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
            self.assertNotEqual(correct, wrong,
                                "the probe cannot detect the bug in this zone")
            self.assertEqual(correct - wrong, 3600,
                             "the mktime idiom is one hour early under DST")
        finally:
            if prev is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = prev
            time.tzset()


# ============================================================================
class ThePageSlicesInBytes(unittest.TestCase):
    """SLICE_BYTES is a byte budget measured with a UTF-16 length.

    A slice of non-ASCII text is up to three times the size the page believes it
    sent, so it is refused by the bridge on every retry: the same never-saves
    failure a previous session fixed by making the budget byte-bounded.
    """

    def test_a_the_page_measures_a_slice_in_bytes(self):
        src = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        self.assertFalse(
            "const size=JSON.stringify(ops[end]).length;" in src,
            "index.html: the slice budget is named in bytes and measured in "
            "UTF-16 code units")

    def test_b_the_pages_budget_still_fits_the_bridges_delta_cap(self):
        src = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        m = re.search(r"SLICE_BYTES\s*=\s*([0-9_]+)", src)
        self.assertIsNotNone(m, "SLICE_BYTES vanished from index.html")
        self.assertLessEqual(int(m.group(1).replace("_", "")),
                             PS.MAX_DELTA_BYTES,
                             "the page can build a slice the bridge always refuses")


if __name__ == "__main__":
    unittest.main()


# ============================================================================
class RecoveryNeverDestroysTheOnlyCopy(Base):
    """recover_track's three confirmed defects, each as the loss it caused.

    The old order copied the newest backup over the live database and only then
    ran quick_check, took no account of older backups, and quarantined into a
    second-granularity name with shutil.move, which silently replaces.
    """

    def _corrupt(self, path):
        """Scribble over the whole file after the header.

        A fixed-offset scribble is not reliable corruption: whether quick_check
        notices depends on where it lands relative to the b-tree, which is how
        two earlier tests in this repository passed for the wrong reason and
        then broke when the schema grew.
        """
        size = os.path.getsize(path)
        with open(path, "r+b") as fh:
            fh.seek(100)
            fh.write(b"\x00\xff\xde\xad\xbe\xef" * ((size - 100) // 6))

    def a_track_with_backups(self, n=2):
        """A track holding n backups.

        The lease is held throughout on purpose: BACKUPS_ACTIVE is 1 and
        BACKUPS_LEASED is 2, so rotation keeps a second copy only while a lease
        is held. That is also the only state in which the older-backup fallback
        can engage at all, which is worth saying out loud.
        """
        from prepwright import keep as K
        track_id = T.create_track("Backend role")
        handle = S.open_track(track_id)
        handle.add_step("st1", 1, "HTTP caching", "Explain revalidation")
        handle.append_turn("st1", "user", "the oldest turn", "c0")
        made = []
        for i in range(n):
            made.append(handle.backup(force=True))
            handle.append_turn("st1", "user", "turn after backup %d" % i,
                               "c%d" % (i + 1))
        handle.close()
        return track_id, [m for m in made if m]

    def test_a_an_unreadable_newest_backup_falls_back_to_an_older_one(self):
        from prepwright import keep as K
        track_id, _made = self.a_track_with_backups(n=2)
        backups = K.all_backups(track_id)
        self.assertGreaterEqual(len(backups), 2, "need two backups for this test")
        self._corrupt(backups[0])                       # the newest one
        db = os.path.join(C.TRACKS_ROOT, track_id, "track.db")
        self._corrupt(db)
        result = K.recover_track(track_id)
        self.assertEqual(os.path.basename(result["restored_from"]),
                         os.path.basename(backups[1]),
                         "recovery did not fall back to the intact older backup")
        handle = S.open_track(track_id)
        self.addCleanup(handle.close)
        self.assertTrue(S.quick_check(handle.conn))

    def test_b_when_no_backup_verifies_the_live_file_is_left_alone(self):
        from prepwright import keep as K
        track_id, _made = self.a_track_with_backups(n=2)
        for b in K.all_backups(track_id):
            self._corrupt(b)
        db = os.path.join(C.TRACKS_ROOT, track_id, "track.db")
        self._corrupt(db)
        before = os.path.getsize(db)
        with self.assertRaises(S.CorruptStore):
            K.recover_track(track_id)
        self.assertTrue(os.path.isfile(db),
                        "the damaged live file was moved away with nowhere to land")
        self.assertEqual(os.path.getsize(db), before,
                         "the damaged live file was overwritten by a bad backup")

    def test_c_two_recoveries_in_one_second_keep_both_quarantines(self):
        from prepwright import keep as K
        track_id, _made = self.a_track_with_backups(n=2)
        db = os.path.join(C.TRACKS_ROOT, track_id, "track.db")
        quarantine = os.path.join(C.TRACKS_ROOT, track_id, "quarantine")
        self._corrupt(db)
        K.recover_track(track_id)
        first = sorted(os.listdir(quarantine))
        self._corrupt(db)
        K.recover_track(track_id)
        second = sorted(os.listdir(quarantine))
        self.assertEqual(len(second), len(first) + 1,
                         "the second recovery replaced the first quarantine "
                         "instead of adding to it")

    def test_d_a_quarantined_wal_stays_paired_with_its_database(self):
        from prepwright import keep as K
        track_id, _made = self.a_track_with_backups(n=1)
        db = os.path.join(C.TRACKS_ROOT, track_id, "track.db")
        self._corrupt(db)
        result = K.recover_track(track_id)
        for path in result["quarantined"]:
            if path.endswith("-wal") or path.endswith("-shm"):
                base = path.rsplit("-", 1)[0]
                self.assertTrue(
                    os.path.exists(base),
                    "SQLite pairs a log as '<db path>-wal'; %s names no database"
                    % os.path.basename(path))


# ============================================================================
class ClientMetadataCannotRewriteTheStore(Base):
    """client_meta is display data. It must not reach a store-owned field."""

    def test_a_meta_cannot_overwrite_the_stored_turn_text(self):
        _tid, handle = self.a_track()
        handle.add_step("1:topic:T1", 1, "HTTP caching", "Explain revalidation")
        PS.apply_ops(handle, PS.validate_ops([{
            "op": "turn", "id": "t1", "step": "1:topic:T1", "pageRole": "me",
            "text": "what the candidate actually typed",
            "meta": {"text": "FORGED", "seq": 999, "provider": "claude"},
        }]))
        doc = PS.materialise(handle)
        entry = doc["stepLog"]["1:topic:T1"][0]
        self.assertEqual(entry["text"], "what the candidate actually typed",
                         "client metadata overwrote the append-only body")
        self.assertNotEqual(entry.get("seq"), 999)
        self.assertEqual(entry.get("provider"), "claude",
                         "display metadata should still pass through")

    def test_b_an_unhashable_role_or_kind_is_refused_by_name(self):
        for op in ({"op": "turn", "id": "t1", "step": "1:topic:T1",
                    "pageRole": ["me"], "text": "x"},
                   {"op": "mark", "id": "m1", "kind": {"a": 1}, "key": "T1",
                    "value": "done"}):
            with self.subTest(op=op["op"]):
                with self.assertRaises(PS.OpRejected):
                    PS.validate_ops([op])
