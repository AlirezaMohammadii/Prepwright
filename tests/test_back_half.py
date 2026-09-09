"""The third of the product that had never executed: assess, review, recap.

Written after the first end-to-end walk of the back half on 2026-09-09, against
the contract that walk revealed rather than the one the source implies. Every
test here corresponds to something that was observed failing in a browser, not
to something inferred from reading.

The headline is the one this file exists for. Three of the four model-backed
features -- the progress grader, the end-of-session review and the diagnostic
judge -- had never once succeeded on any machine, because CLI_BASE pins
--max-turns 1 and a --json-schema reply needs two: the model answers, then it
emits the structured output. The CLI exits 1 with subtype error_max_turns and a
null result, which reached the browser as "The grader returned nothing -- try
again", an instruction that can never work.

No test here calls a model or opens a socket to one. The route tests run under
PREPWRIGHT_NO_MODEL=1 and assert that the routes fail CLOSED and SAY SO, which
is the behaviour the walk showed was missing.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bridge  # noqa: E402
from prepwright import assess as ASSESS  # noqa: E402
# Aliased PROV, not `provider`: several helpers here take a
# parameter called `provider`, which would shadow the module.
from prepwright import provider as PROV  # noqa: E402


class _Proc(object):
    """The two fields _cli_reason reads off a finished subprocess."""

    def __init__(self, stdout="", stderr=""):
        self.stdout = stdout
        self.stderr = stderr


def _argv_max_turns(**kw):
    cmd = PROV._build_claude_cmd("claude-haiku-4-5", "low", **kw)
    return cmd[cmd.index("--max-turns") + 1]


class ASchemaConstrainedCallGetsTheTurnItNeeds(unittest.TestCase):
    """B1. The bug that kept the whole back half from ever running once."""

    def test_a_schema_call_is_allowed_the_second_turn(self):
        self.assertEqual(_argv_max_turns(schema="{}", search=False), "2")

    def test_a_free_text_call_keeps_its_tight_bound_of_one(self):
        """The bound is a safety property, not an accident. Teaching has no
        tools and no schema, so one API call per reply is still correct there,
        and relaxing it globally to fix a schema bug would have widened a
        guarantee that had nothing to do with the failure."""
        self.assertEqual(_argv_max_turns(schema=None, search=False), "1")

    def test_the_search_path_is_untouched(self):
        for schema in (None, "{}"):
            self.assertEqual(_argv_max_turns(schema=schema, search=True), "6")

    def test_the_flag_appears_exactly_once(self):
        """Appending a second --max-turns instead of replacing the first would
        leave the CLI to pick, and which one it picks is not this repo's to
        assume."""
        cmd = PROV._build_claude_cmd("claude-haiku-4-5", "low", schema="{}")
        self.assertEqual(cmd.count("--max-turns"), 1)

    def test_the_shared_base_list_was_not_mutated(self):
        """cmd += CLI_BASE copies the elements, so writing into cmd is safe.
        If that ever stops being true, every later call inherits the 2."""
        PROV._build_claude_cmd("claude-haiku-4-5", "low", schema="{}")
        i = PROV.CLI_BASE.index("--max-turns")
        self.assertEqual(PROV.CLI_BASE[i + 1], "1")


class AFailureBeforeAnyProseStillNamesItself(unittest.TestCase):
    """B2. What made B1 undiagnosable for as long as it lasted."""

    ENVELOPE = json.dumps({
        "is_error": True, "subtype": "error_max_turns", "result": None,
        "terminal_reason": "max_turns", "num_turns": 2,
    })

    def test_a_null_result_still_yields_a_reason(self):
        reason = PROV._cli_reason(_Proc(stdout=self.ENVELOPE))
        self.assertIn("error_max_turns", reason)

    def test_prose_in_result_still_wins_when_it_is_there(self):
        env = json.dumps({"is_error": True, "subtype": "error_during_execution",
                          "result": "Not logged in. Please run /login"})
        self.assertIn("Please run /login", PROV._cli_reason(_Proc(stdout=env)))

    def test_a_successful_envelope_contributes_no_false_reason(self):
        env = json.dumps({"is_error": False, "subtype": "success", "result": ""})
        self.assertEqual(PROV._cli_reason(_Proc(stdout=env)), "")

    def test_stderr_is_still_the_fallback(self):
        self.assertIn("boom", PROV._cli_reason(_Proc(stdout="", stderr="boom")))


class AGradeIsBoundedToWhatAGradeCanMean(unittest.TestCase):
    """B3. Observed live: the first real grading call this project ever made
    returned mastery 45 where the scale is 0..1."""

    def test_the_schema_states_the_scale_it_wants(self):
        schema = json.loads(ASSESS.ASSESS_SCHEMA)
        m = schema["properties"]["steps"]["items"]["properties"]["mastery"]
        self.assertEqual((m.get("minimum"), m.get("maximum")), (0, 1))

    def test_a_percent_written_as_a_whole_number_is_rescaled(self):
        rows = ASSESS._clean_rows([{"key": "8:topic:S08", "mastery": 45,
                                    "reason": "partial"}])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["mastery"], 0.45)

    def test_it_is_rescaled_rather_than_clamped_upward(self):
        """Clamping 45 to 1.0 would turn a partial answer into a perfect score
        and arm the Mark-done button, which is gated on mastery >= 0.85. The
        grader's own system prompt calls an unearned pass the expensive
        mistake, so ambiguity resolves downward."""
        rows = ASSESS._clean_rows([{"key": "k", "mastery": 45, "reason": ""}])
        self.assertLess(rows[0]["mastery"], 0.85)

    def test_an_in_range_grade_is_left_exactly_alone(self):
        for value in (0.0, 0.3, 0.85, 1.0):
            rows = ASSESS._clean_rows([{"key": "k", "mastery": value, "reason": ""}])
            self.assertEqual(rows[0]["mastery"], value)

    def test_a_grade_off_the_scale_is_dropped_not_invented(self):
        """A confused grader gets no vote. The step then reads as ungraded,
        which is honest, rather than as a score nobody meant."""
        for bad in (-30, -0.5, 101, 1e9, float("nan")):
            self.assertEqual(
                ASSESS._clean_rows([{"key": "k", "mastery": bad, "reason": ""}]),
                [], "mastery %r survived" % (bad,))

    def test_a_row_that_is_not_a_row_is_dropped(self):
        self.assertEqual(ASSESS._clean_rows(["nope", None, 7, []]), [])

    def test_a_row_with_no_key_is_dropped(self):
        self.assertEqual(
            ASSESS._clean_rows([{"key": "", "mastery": 0.5, "reason": "x"}]), [])

    def test_an_unparseable_mastery_is_dropped(self):
        self.assertEqual(
            ASSESS._clean_rows([{"key": "k", "mastery": "high", "reason": ""}]), [])

    def test_none_and_empty_are_safe(self):
        self.assertEqual(ASSESS._clean_rows(None), [])
        self.assertEqual(ASSESS._clean_rows([]), [])


class AnEmptyBatchIsAFailureNotAnEmptySuccess(unittest.TestCase):
    """B5. The old code reached `break` before the counter that owns `failed`,
    so a batch that produced nothing was reported as graded."""

    def setUp(self):
        self._real = ASSESS._assess_batch
        self.addCleanup(setattr, ASSESS, "_assess_batch", self._real)

    @staticmethod
    def _usage(cost=0.001):
        return {"in": 100, "cached": 0, "out": 10, "cost": cost, "costKnown": True}

    def _items(self, n):
        return [{"key": "k%d" % i, "title": "t", "task": "x", "turns": 1,
                 "said": ["s"], "tutor": "t"} for i in range(n)]

    def test_a_batch_that_returns_no_rows_is_counted_as_failed(self):
        ASSESS._assess_batch = lambda p, chunk: ([], self._usage())
        graded, _usage, failed = ASSESS.assess_via_cli("claude", self._items(3))
        self.assertEqual(graded, [])
        self.assertEqual(failed, 3, "an empty batch was reported as a success")

    def test_a_failed_batch_is_still_charged_for_both_attempts(self):
        """The call was made and billed whether or not it came back usable.
        The old order discarded the cost of every failed attempt, so the spend
        panel under-reported exactly when the candidate most needed to see it."""
        ASSESS._assess_batch = lambda p, chunk: ([], self._usage(0.002))
        _graded, usage, _failed = ASSESS.assess_via_cli("claude", self._items(2))
        self.assertAlmostEqual(usage["cost"], 0.004)
        self.assertEqual(usage["in"], 200)

    def test_a_good_batch_still_grades_and_charges_once(self):
        ASSESS._assess_batch = lambda p, chunk: (
            [{"key": it["key"], "mastery": 0.5, "reason": "ok"} for it in chunk],
            self._usage())
        graded, usage, failed = ASSESS.assess_via_cli("claude", self._items(3))
        self.assertEqual(len(graded), 3)
        self.assertEqual(failed, 0)
        self.assertAlmostEqual(usage["cost"], 0.001)

    def test_one_bad_batch_does_not_lose_the_good_ones(self):
        """Batching exists so a failure is partial. Five per batch, so seven
        items is two batches."""
        seen = {"n": 0}

        def flaky(_p, chunk):
            seen["n"] += 1
            if seen["n"] <= 2:          # both attempts at the first batch
                return [], self._usage()
            return ([{"key": it["key"], "mastery": 0.4, "reason": "ok"}
                     for it in chunk], self._usage())

        ASSESS._assess_batch = flaky
        graded, _usage, failed = ASSESS.assess_via_cli("claude", self._items(7))
        self.assertEqual(failed, 5)
        self.assertEqual(len(graded), 2)


class AMalformedGraderReplyDoesNotCostBothAttempts(unittest.TestCase):
    """B3/B5. A top-level JSON array parses fine and then makes .get raise
    AttributeError, which escaped the ValueError-only guard."""

    def setUp(self):
        self._real = ASSESS.run_cli
        self.addCleanup(setattr, ASSESS, "run_cli", self._real)

    def _returns(self, result):
        ASSESS.run_cli = lambda *a, **k: {"result": result, "usage": {},
                                          "total_cost_usd": 0}

    def test_a_top_level_array_yields_no_rows_instead_of_raising(self):
        self._returns("[{\"key\": \"k\", \"mastery\": 0.5}]")
        rows, _usage = ASSESS._assess_batch("claude", [{"key": "k"}])
        self.assertEqual(rows, [])

    def test_unparseable_text_yields_no_rows(self):
        self._returns("I graded them all, honestly.")
        rows, _usage = ASSESS._assess_batch("claude", [{"key": "k"}])
        self.assertEqual(rows, [])

    def test_the_batch_path_actually_applies_the_bound(self):
        """_clean_rows behaving correctly proves nothing unless _assess_batch
        calls it. Reverting only the call site left the direct unit test green,
        which is exactly the shape of a test that passes for the wrong reason,
        so this one goes through the batch."""
        self._returns(json.dumps({"steps": [{"key": "8:topic:S08",
                                             "mastery": 45, "reason": "partial"}]}))
        rows, _usage = ASSESS._assess_batch("claude", [{"key": "8:topic:S08"}])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["mastery"], 0.45)

    def test_a_well_formed_reply_still_comes_through(self):
        self._returns(json.dumps({"steps": [{"key": "8:topic:S08",
                                             "mastery": 0.6, "reason": "ok"}]}))
        rows, _usage = ASSESS._assess_batch("claude", [{"key": "8:topic:S08"}])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["mastery"], 0.6)


# ---- the two routes that had no coverage at all -----------------------------

class Bridge(object):
    """A live bridge on its own port and storage root, as test_pipeline_routes
    does it. Nothing is imported and called directly: the request boundary is
    as much under test as the route body."""

    def __init__(self):
        self.home = tempfile.mkdtemp(prefix="pw-backhalf-")
        self.port = 8800 + (os.getpid() % 700)
        env = dict(os.environ, PREPWRIGHT_HOME=self.home,
                   PREPWRIGHT_PORT=str(self.port),
                   PREPWRIGHT_NO_MODEL="1", PREPWRIGHT_NO_DIALOG="1")
        self.proc = subprocess.Popen(
            [sys.executable, "bridge.py"], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.base = "http://127.0.0.1:%d" % self.port
        self.cookie = ""
        if not self._wait():
            self.stop()
            raise RuntimeError("the bridge did not come up on %d" % self.port)
        req = urllib.request.Request(
            self.base + "/", headers={"Host": "127.0.0.1:%d" % self.port})
        with urllib.request.urlopen(req, timeout=20) as r:
            self.cookie = (r.headers.get("Set-Cookie") or "").split(";")[0]

    def _wait(self):
        for _ in range(120):
            if self.proc.poll() is not None:
                return False
            try:
                urllib.request.urlopen(self.base + "/api/health", timeout=2).read()
                return True
            except Exception:                                # noqa: BLE001
                time.sleep(0.25)
        return False

    def call(self, method, path, body=None, cookie=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json", "X-Tutor-Bridge": "1",
                   "Host": "127.0.0.1:%d" % self.port, "Origin": self.base}
        cookie = self.cookie if cookie is None else cookie
        if cookie:
            headers["Cookie"] = cookie
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except ValueError:
                return exc.code, {}

    def stop(self):
        try:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(timeout=15)
        except Exception:                                    # noqa: BLE001
            self.proc.kill()
        shutil.rmtree(self.home, ignore_errors=True)


class RouteBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = Bridge()

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()

    STEP = {"key": "8:topic:S08", "title": "A topic", "task": "Read D03."}
    ITEM = {"key": "8:topic:S08", "title": "A topic", "task": "Read D03.",
            "turns": 4, "said": ["what does it say"], "tutor": "it says"}
    MSGS = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]


class TheGradingRouteHoldsItsBoundary(RouteBase):
    def test_it_does_not_answer_without_a_session(self):
        status, _ = self.b.call("POST", "/api/assess", {"steps": [self.ITEM]},
                                cookie="")
        self.assertEqual(status, 403)

    def test_an_empty_step_list_is_refused_before_any_model_call(self):
        for body in ({}, {"steps": []}, {"steps": "all of them"},
                     {"steps": ["not a dict"]}):
            status, reply = self.b.call("POST", "/api/assess", body)
            self.assertEqual(status, 400, reply)
            self.assertIn("assess", reply.get("error", "").lower())

    def test_an_unknown_provider_is_refused(self):
        status, reply = self.b.call("POST", "/api/assess",
                                    {"provider": "oracle", "steps": [self.ITEM]})
        self.assertEqual(status, 400, reply)

    def test_it_fails_closed_and_names_the_real_reason(self):
        """A well-formed request on a machine with no reachable model is
        refused at provider selection, with the reason stated. Not a traceback,
        not a hung socket, and above all not a 200 carrying an empty grade
        list, which is what the candidate saw for the whole life of the
        max-turns bug."""
        status, reply = self.b.call("POST", "/api/assess", {"steps": [self.ITEM]})
        self.assertEqual(status, 400, reply)
        self.assertIn("prepwright_no_model", reply.get("error", "").lower())
        self.assertNotIn("steps", reply)

    def test_a_malformed_request_names_the_request_not_the_provider(self):
        """Ordering, asserted as behaviour. The payload check is local and
        free; provider selection consults the machine. Asking the machine first
        made every malformed request report "model calls are disabled", which
        sends someone looking for an environment fault that is not there."""
        _status, reply = self.b.call("POST", "/api/assess", {"steps": []})
        self.assertIn("assess", reply.get("error", "").lower())
        self.assertNotIn("prepwright_no_model", reply.get("error", "").lower())


class TheSessionReviewRouteHoldsItsBoundary(RouteBase):
    def test_it_does_not_answer_without_a_session(self):
        status, _ = self.b.call("POST", "/api/review",
                                {"stepKey": self.STEP["key"], "step": self.STEP,
                                 "messages": self.MSGS}, cookie="")
        self.assertEqual(status, 403)

    def test_a_step_key_the_curriculum_never_minted_is_refused(self):
        """STEP_KEY_RE is a two-sided contract: the curriculum mints against it
        and the bridge validates against it. /api/assess does NOT make this
        check, which is an asymmetry worth remembering rather than copying."""
        for bad in ("", "../../etc/passwd", "8:topic:S08; drop", "S08", "x" * 200):
            status, reply = self.b.call("POST", "/api/review",
                                        {"stepKey": bad, "step": self.STEP,
                                         "messages": self.MSGS})
            self.assertEqual(status, 400, "%r was accepted: %r" % (bad, reply))

    def test_a_step_body_that_disagrees_with_its_key_is_refused(self):
        status, reply = self.b.call("POST", "/api/review", {
            "stepKey": "8:topic:S08",
            "step": {"key": "9:topic:S09", "title": "Another"},
            "messages": self.MSGS})
        self.assertEqual(status, 400, reply)
        self.assertIn("match", reply.get("error", "").lower())

    def test_it_fails_closed_and_names_the_real_reason(self):
        status, reply = self.b.call("POST", "/api/review", {
            "stepKey": self.STEP["key"], "step": self.STEP,
            "messages": self.MSGS})
        self.assertEqual(status, 400, reply)
        self.assertIn("prepwright_no_model", reply.get("error", "").lower())

    def test_a_valid_key_gets_past_validation_and_reaches_the_provider(self):
        """The distinction that matters, and it is in the message rather than
        the status code: a rejected request names the step, an accepted one
        that cannot reach a model names the model. A regression that made every
        review fail at validation would otherwise be invisible, because both
        outcomes are 400 on a machine with no provider."""
        _s1, bad = self.b.call("POST", "/api/review", {
            "stepKey": "not-a-step", "step": {"key": "not-a-step"},
            "messages": self.MSGS})
        _s2, good = self.b.call("POST", "/api/review", {
            "stepKey": self.STEP["key"], "step": self.STEP,
            "messages": self.MSGS})
        self.assertIn("step", bad.get("error", "").lower())
        self.assertNotIn("step", good.get("error", "").lower())
        self.assertIn("model", good.get("error", "").lower())


if __name__ == "__main__":
    unittest.main()
