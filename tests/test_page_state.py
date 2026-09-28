"""The delta protocol: what /api/state promises now that it takes changes.

The page used to POST a whole document behind a revision hash and a heuristic
shrink-detector. It now posts ops, and these are the properties that replaced
that heuristic with a structural one, each run against the real thing: a real
subprocess killed inside a transaction, two real processes writing at once, two
real track databases, and a real bridge answering real HTTP.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import importlib
import http.cookiejar
import io
import re
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prepwright import config as C           # noqa: E402
from prepwright import pagestate as P        # noqa: E402
from prepwright import state as S            # noqa: E402
from prepwright import track as T            # noqa: E402


DOCUMENT = {
    "topics": {"T1": {"done": True}, "T2": {"done": False}},
    "practice": {"P1": {"status": "done", "notes": "shipped it"}},
    "checks": {"C1": {"result": "pass"}},
    "questions": {"Q1": {"asked": True}},
    "assess": {"1:topic:T1": {"mastery": 0.8}},
    "recap": {"c1": {"got": 2, "missed": 1}},
    "qa": [{"id": "q1", "q": "why revalidate", "a": "to skip the body"}],
    "sessions": [{"id": "s1", "title": "Stage 1"}],
    "recapBank": [{"id": "c1", "q": "what is max-age", "a": "freshness seconds"}],
    "theme": "dark", "lastView": "stages", "provider": "claude",
    "model": "claude-opus-5", "models": {"claude": "claude-opus-5"},
    "effort": {}, "assessAt": 0, "assessCost": "", "tutorChosen": True,
    "stepLog": {
        "1:topic:T1": [
            {"ts": 1, "role": "me", "text": "what does max-age promise?"},
            {"ts": 2, "role": "assistant", "text": "freshness, in seconds",
             "provider": "claude", "model": "claude-opus-5",
             "usage": {"in": 12, "out": 8, "cost": 0.002, "costKnown": True}},
            {"ts": 3, "role": "sys", "text": "the bridge went away"},
            {"ts": 4, "role": "session", "text": "",
             "session": {"covered": "caching", "recap": [{"q": "a", "a": "b"}]}},
        ]
    },
}


class Base(unittest.TestCase):
    """A fresh storage root per test, so no test can pass on another's leftovers."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-page-")
        # Registered FIRST so it runs LAST: unittest runs every addCleanup after
        # tearDown, and a handle closed by a later cleanup must never find the
        # candidate's real ~/.prepwright on the environment.
        self.addCleanup(self._restore_home)
        os.environ["PREPWRIGHT_HOME"] = os.path.join(self.tmp, "home")
        importlib.reload(C)
        importlib.reload(P)      # its caps come from C, which just moved
        S.ensure_home()

    def _restore_home(self):
        self.assertTrue(
            os.path.realpath(C.HOME).startswith(os.path.realpath(self.tmp)),
            "storage root escaped the temporary directory: %s" % C.HOME)
        os.environ.pop("PREPWRIGHT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(C)
        importlib.reload(P)

    def a_track(self, title="Backend role"):
        track_id = T.create_track(title)
        handle = S.open_track(track_id)
        self.addCleanup(handle.close)
        return track_id, handle


# ============================================================================
class WholeDocumentRoundTrip(Base):
    """Every field the page holds survives the store and comes back identical."""

    def test_every_field_and_every_chat_role_round_trips(self):
        _, handle = self.a_track()
        ops = P.validate_ops(P.ops_from_document(DOCUMENT, "rt"))
        P.apply_ops(handle, ops)
        back = P.materialise(handle)

        for field in DOCUMENT:
            if field == "stepLog":
                continue
            self.assertEqual(back.get(field), DOCUMENT[field],
                             "field %r did not survive the round trip" % field)

        log = back["stepLog"]["1:topic:T1"]
        self.assertEqual([e["role"] for e in log],
                         ["me", "assistant", "sys", "session"])
        self.assertEqual([e["text"] for e in log],
                         [e["text"] for e in DOCUMENT["stepLog"]["1:topic:T1"]])
        # The end-of-session review is stored as `system`, so the compactor,
        # which only ever empties role='tutor', can never reach it.
        roles = [r["role"] for r in handle.conn.execute(
            "SELECT role FROM turn ORDER BY seq")]
        self.assertEqual(roles, ["user", "tutor", "system", "system"])
        self.assertEqual(log[3]["session"]["covered"], "caching")
        self.assertEqual(log[1]["usage"]["cost"], 0.002)

    def test_re_sending_the_same_document_writes_no_new_rows(self):
        _, handle = self.a_track()
        ops = P.validate_ops(P.ops_from_document(DOCUMENT, "rt"))
        P.apply_ops(handle, ops)
        revision = handle.revision()
        rows = (handle.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"],
                handle.conn.execute("SELECT COUNT(*) c FROM mark").fetchone()["c"])

        second = P.apply_ops(handle, ops)
        self.assertEqual(second["marks"], 0)
        self.assertEqual(
            (handle.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"],
             handle.conn.execute("SELECT COUNT(*) c FROM mark").fetchone()["c"]),
            rows, "a retried write duplicated rows")
        self.assertEqual(handle.revision(), revision,
                         "a write that changed nothing moved the revision")

    def test_a_value_that_goes_backwards_supersedes_and_never_erases(self):
        """The store keeps the undo history, which is what makes the loss
        recoverable instead of merely refused."""
        _, handle = self.a_track()
        handle.append_mark("topic", "T1", '{"done":true}', "op-1")
        handle.append_mark("topic", "T1", '{"done":false}', "op-2")
        self.assertEqual(P.materialise(handle)["topics"]["T1"], {"done": False})
        history = [r["value"] for r in handle.conn.execute(
            "SELECT value FROM mark WHERE kind='topic' AND key='T1' ORDER BY seq")]
        self.assertEqual(history, ['{"done":true}', '{"done":false}'],
                         "the superseded value was not kept")

    def test_a_mark_row_cannot_be_updated_or_deleted(self):
        _, handle = self.a_track()
        handle.append_mark("topic", "T1", '{"done":true}', "op-1")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            handle.conn.execute("UPDATE mark SET value='{}' WHERE key='T1'")
        with self.assertRaises(sqlite3.IntegrityError):
            handle.conn.execute("DELETE FROM mark WHERE key='T1'")


# ============================================================================
class OpValidation(Base):
    """Client input, refused before a single op is applied.

    Every case here is forced with the dangerous value in place rather than
    asserted around, because a security test that passes on the first try is
    usually passing for a reason that has nothing to do with the guard.
    """

    def test_a_step_key_outside_the_contract_is_refused(self):
        for bad in ("../../etc/passwd", "1:topic:", "99:topic:T1/../x",
                    "1:evil:T1", "", "1:topic:T1\n"):
            with self.assertRaises(P.OpRejected, msg="accepted step key %r" % bad):
                P.validate_ops([{"op": "turn", "id": "a", "step": bad,
                                 "pageRole": "me", "text": "x"}])
        # and the good one is accepted, so the refusals above are the guard
        # firing and not the whole call failing for some other reason
        self.assertEqual(len(P.validate_ops(
            [{"op": "turn", "id": "a", "step": "1:topic:T1",
              "pageRole": "me", "text": "x"}])), 1)

    def test_an_unknown_mark_kind_is_refused(self):
        for bad in ("__proto__", "constructor", "prototype", "turn", "unknown"):
            with self.assertRaises(P.OpRejected, msg="accepted kind %r" % bad):
                P.validate_ops([{"op": "mark", "id": "a", "kind": bad,
                                 "key": "k", "value": 1}])

    def test_a_preference_key_this_build_does_not_serve_is_refused(self):
        with self.assertRaises(P.OpRejected):
            P.validate_ops([{"op": "mark", "id": "a", "kind": "pref",
                             "key": "__proto__", "value": 1}])
        with self.assertRaises(P.OpRejected):
            P.validate_ops([{"op": "mark", "id": "a", "kind": "pref",
                             "key": "stepLog", "value": {}}])

    def test_an_id_that_would_not_fit_its_column_is_refused(self):
        for bad in ("a b", "a/../b", "x" * 81, "", "a\x00b"):
            with self.assertRaises(P.OpRejected, msg="accepted id %r" % bad):
                P.validate_ops([{"op": "mark", "id": bad, "kind": "topic",
                                 "key": "k", "value": 1}])

    def test_an_oversize_value_is_refused_before_it_reaches_the_store(self):
        with self.assertRaises(P.OpRejected):
            P.validate_ops([{"op": "mark", "id": "a", "kind": "topic",
                             "key": "k", "value": "x" * (C.MARK_MAX_BYTES + 1)}])

    def test_the_pages_slice_budget_fits_inside_the_bridges_delta_cap(self):
        """The page slices a large save; the bridge caps one delta. If a slice
        the page can build exceeds the cap, that save is refused every time it
        is retried and never lands at all.

        The page's budget lives in index.html as SLICE_OPS and SLICE_BYTES, so
        this reads them out of the page rather than restating them, which is the
        only version of this test that catches the two drifting apart.
        """
        page = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
        found = re.search(r"const SLICE_OPS=(\d+), SLICE_BYTES=(\d+);", page)
        self.assertIsNotNone(found, "index.html no longer declares a slice budget")
        slice_ops, slice_bytes = int(found.group(1)), int(found.group(2))

        self.assertLessEqual(slice_ops, P.MAX_OPS_PER_WRITE,
                             "a full slice carries more ops than the bridge accepts")
        # One op is allowed past the byte budget, because a slice is never empty.
        worst = slice_bytes + C.TURN_MAX_BYTES + C.TURN_META_MAX_BYTES + 400
        self.assertLessEqual(
            worst, P.MAX_DELTA_BYTES,
            "a slice the page can build (%d bytes worst case) exceeds the "
            "bridge's %d byte cap on one delta, so it would be refused on every "
            "retry" % (worst, P.MAX_DELTA_BYTES))

    def test_too_many_ops_in_one_write_are_refused(self):
        many = [{"op": "mark", "id": "i%d" % i, "kind": "topic",
                 "key": "k%d" % i, "value": i}
                for i in range(P.MAX_OPS_PER_WRITE + 1)]
        with self.assertRaises(P.OpRejected):
            P.validate_ops(many)

    def test_one_bad_op_refuses_the_whole_write(self):
        _, handle = self.a_track()
        good = {"op": "mark", "id": "good", "kind": "topic",
                "key": "T1", "value": {"done": True}}
        bad = {"op": "mark", "id": "bad", "kind": "nope", "key": "x", "value": 1}
        with self.assertRaises(P.OpRejected):
            P.apply_ops(handle, P.validate_ops([good, bad]))
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM mark").fetchone()["c"], 0,
            "a partly applied write left the good half behind")


# ============================================================================
class TheGroundingRecordReachesItsOwnColumns(Base):
    """`turn.pack_sha16`, `turn.citations` and `turn.ungrounded` had no writer.

    `apply_ops` handed the page's blob to `client_meta` and left the three
    columns beside it null, so a query of the columns built to answer "what did
    this turn cite" returned nothing. The facts were never lost, because the
    blob carries them, but `client_meta` is presentation metadata with its own
    cap that may be trimmed, and the typed columns are the durable record.

    Found on 2026-09-10 by reading the store after a real teaching turn on the
    Wingtip track: the reply cited D03§s02 and D07§s06 while `citations` read `[]`
    and `pack_sha16` was null.
    """

    META = json.dumps({"role": "assistant", "grounded": True,
                       "cites": ["D01§s01", "D01§s02"],
                       "cited": ["D01§s01"], "invented": [],
                       "packSha16": "7e54093720db5dad"})

    def _apply(self, meta):
        _tid, handle = self.a_track()
        ops = P.validate_ops([{"op": "turn", "id": "t1", "step": "1:topic:T1",
                               "pageRole": "assistant", "text": "A reply.",
                               "meta": json.loads(meta) if meta else None}])
        P.apply_ops(handle, ops)
        return handle.conn.execute(
            "SELECT pack_sha16, citations, ungrounded, client_meta FROM turn"
            " WHERE client_turn_id='t1'").fetchone()

    def test_the_pack_hash_and_the_citations_land_in_their_columns(self):
        row = self._apply(self.META)
        self.assertEqual(row["pack_sha16"], "7e54093720db5dad")
        self.assertEqual(json.loads(row["citations"]), ["D01§s01"])
        self.assertEqual(int(row["ungrounded"]), 0)

    def test_an_ungrounded_reply_is_flagged_in_its_own_column(self):
        """Only an explicit False sets it. A blob with no `grounded` key is not
        a claim that the turn was ungrounded."""
        meta = json.dumps({"role": "assistant", "grounded": False,
                           "cited": [], "packSha16": ""})
        row = self._apply(meta)
        self.assertEqual(int(row["ungrounded"]), 1)
        silent = self._apply(json.dumps({"role": "assistant", "cited": []}))
        self.assertEqual(int(silent["ungrounded"]), 0)

    def test_a_malformed_blob_costs_the_columns_and_never_the_turn(self):
        """Best effort, deliberately. The transcript is the thing the candidate
        cannot lose; the columns are an audit convenience."""
        _tid, handle = self.a_track()
        ops = P.validate_ops([{"op": "turn", "id": "t2", "step": "1:topic:T1",
                               "pageRole": "assistant", "text": "Still stored."}])
        P.apply_ops(handle, ops)
        row = handle.conn.execute(
            "SELECT body, pack_sha16, citations FROM turn"
            " WHERE client_turn_id='t2'").fetchone()
        self.assertEqual(row["body"], "Still stored.")
        self.assertIsNone(row["pack_sha16"])
        self.assertEqual(json.loads(row["citations"]), [])
        self.assertEqual(P._grounding_from_meta("{not json"), {})
        self.assertEqual(P._grounding_from_meta("[1,2,3]"), {})
        self.assertEqual(P._grounding_from_meta(None), {})


# ============================================================================
class TrackIsolation(Base):
    """One track's rows cannot appear in another track's document."""

    def test_a_second_track_sees_none_of_the_first_track_turns(self):
        first_id, first = self.a_track("First role")
        second_id, second = self.a_track("Second role")
        self.assertNotEqual(first_id, second_id)

        P.apply_ops(first, P.validate_ops([
            {"op": "turn", "id": "a1", "step": "1:topic:T1", "pageRole": "me",
             "text": "the first track's only turn"},
            {"op": "mark", "id": "m1", "kind": "topic", "key": "T1",
             "value": {"done": True}}]))
        P.apply_ops(second, P.validate_ops([
            {"op": "turn", "id": "b1", "step": "1:topic:T9", "pageRole": "me",
             "text": "the second track's only turn"}]))

        one, two = P.materialise(first), P.materialise(second)
        self.assertIn("1:topic:T1", one["stepLog"])
        self.assertNotIn("1:topic:T9", one["stepLog"])
        self.assertIn("1:topic:T9", two["stepLog"])
        self.assertNotIn("1:topic:T1", two["stepLog"])
        self.assertNotIn("topics", two)
        self.assertNotEqual(first.revision(), second.revision(),
                            "two tracks minted the same revision token")

        blob = json.dumps(two)
        self.assertNotIn("first track", blob)


# ============================================================================
class KilledMidDelta(Base):
    """A process killed applying a delta leaves the store readable and clean."""

    def test_a_kill_inside_apply_ops_leaves_no_half_written_delta(self):
        track_id, handle = self.a_track()
        P.apply_ops(handle, P.validate_ops([
            {"op": "turn", "id": "keep", "step": "1:topic:T1",
             "pageRole": "me", "text": "this one is committed"}]))
        before_turns = handle.conn.execute(
            "SELECT COUNT(*) c FROM turn").fetchone()["c"]
        before_marks = handle.conn.execute(
            "SELECT COUNT(*) c FROM mark").fetchone()["c"]
        before_bytes = handle.conn.execute(
            "SELECT bytes_marks b FROM track_meta").fetchone()["b"]
        handle.close()

        script = textwrap.dedent("""
            import os, sys
            sys.path.append(sys.argv[1])
            os.environ["PREPWRIGHT_HOME"] = sys.argv[2]
            import importlib
            from prepwright import config as C
            importlib.reload(C)
            from prepwright import state as S, pagestate as P
            importlib.reload(P)
            handle = S.open_track(sys.argv[3], take_lease=False)
            # Inside the transaction, past the cap charge and the INSERT, with
            # no COMMIT: exactly where a laptop lid closing lands.
            with handle.lock, S._Txn(handle.conn):
                handle.conn.execute(
                    "INSERT INTO mark(at_utc,kind,key,value,value_bytes,client_op_id)"
                    " VALUES ('2026-01-01T00:00:00Z','topic','GHOST','{}',2,'ghost')")
                handle.conn.execute(
                    "UPDATE track_meta SET bytes_marks = bytes_marks + 999999")
                sys.stdout.write("uncommitted")
                sys.stdout.flush()
                os._exit(9)
        """)
        proc = subprocess.run(
            [sys.executable, "-c", script, ROOT, C.HOME, track_id],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 9, proc.stderr)
        self.assertIn("uncommitted", proc.stdout)

        reopened = S.open_track(track_id)
        self.addCleanup(reopened.close)
        self.assertTrue(S.quick_check(reopened.conn),
                        "the store did not survive the kill")
        self.assertEqual(
            reopened.conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"],
            before_turns)
        self.assertEqual(
            reopened.conn.execute("SELECT COUNT(*) c FROM mark").fetchone()["c"],
            before_marks)
        self.assertEqual(
            reopened.conn.execute("SELECT bytes_marks b FROM track_meta").fetchone()["b"],
            before_bytes, "an uncommitted counter update survived the kill")
        self.assertIsNone(reopened.conn.execute(
            "SELECT 1 FROM mark WHERE key='GHOST'").fetchone())
        # readable AND writable: the page must be able to carry on saving
        doc = P.materialise(reopened)
        self.assertEqual(doc["stepLog"]["1:topic:T1"][0]["text"],
                         "this one is committed")
        P.apply_ops(reopened, P.validate_ops([
            {"op": "mark", "id": "after", "kind": "topic", "key": "T1",
             "value": {"done": True}}]))
        self.assertEqual(P.materialise(reopened)["topics"]["T1"], {"done": True})


# ============================================================================
class TwoWritersRacingDeltas(Base):
    """Two clients saving at once produce a union, never a clobber.

    This is the case the old whole-document write could not survive without a
    heuristic: both tabs held a full document, so whichever wrote last decided
    what the other's work had been. An op cannot express that.
    """

    def test_two_processes_applying_disjoint_deltas_both_land(self):
        track_id, handle = self.a_track()
        handle.close()

        writer = textwrap.dedent("""
            import os, sys
            sys.path.append(sys.argv[1])
            os.environ["PREPWRIGHT_HOME"] = sys.argv[2]
            import importlib
            from prepwright import config as C
            importlib.reload(C)
            from prepwright import state as S, pagestate as P
            importlib.reload(P)
            handle = S.open_track(sys.argv[3], take_lease=False)
            tag = sys.argv[4]
            ops = []
            for i in range(20):
                ops.append({"op": "turn", "id": "%s-t%d" % (tag, i),
                            "step": "1:topic:T1", "pageRole": "me",
                            "text": "%s message %d" % (tag, i)})
                ops.append({"op": "mark", "id": "%s-m%d" % (tag, i),
                            "kind": "topic", "key": "%s%d" % (tag, i),
                            "value": {"done": True}})
            P.apply_ops(handle, P.validate_ops(ops))
            handle.close()
            sys.stdout.write("done")
        """)
        procs = [subprocess.Popen(
            [sys.executable, "-c", writer, ROOT, C.HOME, track_id, tag],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for tag in ("alpha", "beta")]
        outs = [p.communicate(timeout=180) for p in procs]
        for out, err in outs:
            self.assertIn("done", out, err)

        reopened = S.open_track(track_id)
        self.addCleanup(reopened.close)
        doc = P.materialise(reopened)
        log = doc["stepLog"]["1:topic:T1"]
        texts = {e["text"] for e in log}
        for tag in ("alpha", "beta"):
            for i in range(20):
                self.assertIn("%s message %d" % (tag, i), texts,
                              "%s lost a turn to the other writer" % tag)
                self.assertIn("%s%d" % (tag, i), doc["topics"],
                              "%s lost a mark to the other writer" % tag)
        self.assertEqual(len(log), 40, "a turn was duplicated or dropped")
        # Order is by server-allocated seq, so neither process's clock decides it.
        seqs = [r["seq"] for r in reopened.conn.execute(
            "SELECT seq FROM turn ORDER BY seq")]
        self.assertEqual(seqs, sorted(set(seqs)))


# ============================================================================
def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class BridgeStateApi(unittest.TestCase):
    """The real bridge, over real HTTP, answering the real page's protocol."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="prepwright-bridge-")
        cls.port = _free_port()
        # A legacy document on disk, so the import path runs in the same start.
        cls.legacy_dir = os.path.join(ROOT, "progress")
        cls.legacy_file = os.path.join(cls.legacy_dir, "state.json")
        cls.made_legacy = not os.path.exists(cls.legacy_file)
        if not cls.made_legacy:
            raise unittest.SkipTest(
                "progress/state.json already exists; refusing to touch it")
        os.makedirs(cls.legacy_dir, exist_ok=True)
        with open(cls.legacy_file, "w", encoding="utf-8") as fh:
            json.dump({"topics": {"T1": {"done": True}},
                       "stepLog": {"1:topic:T1": [
                           {"ts": 1, "role": "me", "text": "a legacy turn"}]}}, fh)

        env = dict(os.environ)
        env["PREPWRIGHT_HOME"] = os.path.join(cls.tmp, "home")
        env["PREPWRIGHT_PORT"] = str(cls.port)
        cls.proc = subprocess.Popen(
            [sys.executable, "-I", "-S", os.path.join(ROOT, "bridge.py")],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True)
        cls.base = "http://localhost:%d" % cls.port
        cls.jar = http.cookiejar.CookieJar()
        cls.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cls.jar))
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                cls.opener.open(cls.base + "/api/health", timeout=2).read()
                break
            except Exception:
                if cls.proc.poll() is not None:
                    raise RuntimeError("bridge exited: %s" % cls.proc.communicate()[1])
                time.sleep(0.2)
        else:
            raise RuntimeError("bridge never came up")
        cls.opener.open(cls.base + "/", timeout=5).read()   # sets the cookie

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=10)
        except Exception:
            cls.proc.kill()
        for name in os.listdir(cls.legacy_dir):
            if name.startswith("state.json"):
                os.remove(os.path.join(cls.legacy_dir, name))
        try:
            os.rmdir(cls.legacy_dir)
        except OSError:
            pass
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _get(self):
        req = urllib.request.Request(self.base + "/api/state",
                                     headers={"X-Tutor-Bridge": "1"})
        with self.opener.open(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _post(self, body, expect=200):
        req = urllib.request.Request(
            self.base + "/api/state", method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"X-Tutor-Bridge": "1", "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=20) as resp:
                self.assertEqual(resp.status, expect)
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            # Read ONCE. Reading the body a second time returns b"", which is
            # how the first version of this helper turned every refusal reply
            # into an empty dict and asserted nothing about it.
            body = err.read()
            err.close()
            self.assertEqual(err.code, expect, body[:400])
            return json.loads(body or b"{}")

    def test_a_the_legacy_document_was_imported_once_and_renamed(self):
        got = self._get()
        self.assertEqual(got["state"]["topics"]["T1"], {"done": True})
        self.assertEqual(got["state"]["stepLog"]["1:topic:T1"][0]["text"],
                         "a legacy turn")
        self.assertFalse(os.path.exists(self.legacy_file),
                         "the legacy file was left where a second start would"
                         " import it again")
        kept = [n for n in os.listdir(self.legacy_dir)
                if n.startswith("state.json.imported-")]
        self.assertTrue(kept, "the legacy file was deleted rather than renamed")

    def test_b_get_serves_the_field_map_and_a_revision(self):
        got = self._get()
        self.assertTrue(got["trackId"].startswith("t-"))
        self.assertEqual(len(got["revision"]), 16)
        self.assertEqual(set(got["fields"]), set(P.FIELDS))

    def test_c_a_delta_applies_and_a_retry_of_it_does_not_duplicate(self):
        base = self._get()
        ops = [{"op": "turn", "id": "http-t1", "step": "1:topic:T2",
                "pageRole": "me", "text": "over real http"},
               {"op": "mark", "id": "http-m1", "kind": "topic", "key": "T2",
                "value": {"done": True}}]
        first = self._post({"trackId": base["trackId"],
                            "revision": base["revision"], "ops": ops})
        self.assertEqual(first["applied"]["turns"], 1)
        self.assertEqual(first["applied"]["marks"], 1)
        self.assertNotEqual(first["revision"], base["revision"])

        again = self._post({"trackId": base["trackId"],
                            "revision": base["revision"], "ops": ops})
        self.assertEqual(again["revision"], first["revision"],
                         "a retried delta moved the revision")
        self.assertEqual(again["applied"]["marks"], 0)
        self.assertTrue(again["stale"])
        self.assertEqual(
            len(again["state"]["stepLog"]["1:topic:T2"]), 1,
            "a retried delta wrote the turn twice")

    def test_d_a_replace_from_a_stale_view_is_refused_with_the_real_document(self):
        base = self._get()
        out = self._post({"trackId": base["trackId"],
                          "revision": "0000000000000000",
                          "intent": "replace",
                          "ops": [{"op": "mark", "id": "stale-1",
                                   "kind": "topic", "key": "T3",
                                   "value": {"done": True}}]},
                         expect=409)
        self.assertTrue(out["regression"])
        self.assertIn("state", out)
        self.assertEqual(out["revision"], base["revision"])
        self.assertNotIn("T3", self._get()["state"].get("topics", {}),
                         "the refused replace was applied anyway")

    def test_e_an_update_from_a_stale_view_applies_and_returns_the_merge(self):
        base = self._get()
        out = self._post({"trackId": base["trackId"],
                          "revision": "0000000000000000",
                          "ops": [{"op": "mark", "id": "stale-2",
                                   "kind": "topic", "key": "T4",
                                   "value": {"done": True}}]})
        self.assertTrue(out["stale"])
        self.assertEqual(out["state"]["topics"]["T4"], {"done": True})
        self.assertIn("T1", out["state"]["topics"],
                      "the merge handed back did not carry what was already there")

    def test_f_a_write_naming_another_track_is_refused(self):
        out = self._post({"trackId": "t-000000000000", "revision": None,
                          "ops": [{"op": "mark", "id": "wrong-track",
                                   "kind": "topic", "key": "T5",
                                   "value": {"done": True}}]},
                         expect=409)
        self.assertTrue(out["regression"])
        self.assertNotIn("T5", self._get()["state"].get("topics", {}))

    def test_g_a_malformed_op_is_refused_and_nothing_is_written(self):
        before = self._get()
        self._post({"trackId": before["trackId"], "revision": before["revision"],
                    "ops": [{"op": "turn", "id": "evil", "step": "../../etc/passwd",
                             "pageRole": "me", "text": "x"}]},
                   expect=400)
        self.assertEqual(self._get()["revision"], before["revision"])

    def test_h_an_unauthorized_write_is_refused(self):
        req = urllib.request.Request(
            self.base + "/api/state", method="POST",
            data=b'{"ops":[]}',
            headers={"Content-Type": "application/json"})   # no X-Tutor-Bridge
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(caught.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
