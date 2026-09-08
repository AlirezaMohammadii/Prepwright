"""The pipeline routes, driven over real HTTP against a real bridge.

Every test here starts a bridge on its own port with its own storage root and
speaks to it the way the page does: the session cookie from GET /, the custom
header, an exact Origin and an exact Host. Nothing is imported and called
directly, because the thing being protected is the request boundary as much as
the route body, and a direct call skips it.

No test reaches the network. The research route is exercised through URLs its
own guard refuses before a socket is opened.

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

POSTING = """About the Role

You will assess AI systems.

Key Accountabilities

- Give proportionate risk guidance and controls so AI projects can ship.
- Find and manage the security risks AI brings in through third-party tools.
- Support compliance with standards including ISO 42001, SOC2 and regulation.

Skills, Experience & Role Fit

- Hands-on experience in information security, governance or compliance.
- Working knowledge of data protection and privacy rules (e.g., GDPR, APPs).
- Knows AI governance frameworks (NIST AI RMF - highly regarded).
"""


class Bridge(object):
    """A live bridge on its own port, with its own storage root."""

    def __init__(self):
        self.home = tempfile.mkdtemp(prefix="pw-routes-")
        # A port derived from the pid, so two suites on one machine do not race
        # for the same one and neither sees the other's tracks.
        self.port = 8100 + (os.getpid() % 700)
        # No test spends the candidate's money or waits on a network round
        # trip. The routes that call a model all report honestly that they could
        # not reach one, which is the behaviour under test here anyway.
        env = dict(os.environ, PREPWRIGHT_HOME=self.home,
                   PREPWRIGHT_PORT=str(self.port),
                   PREPWRIGHT_NO_MODEL="1",
                   # A file chooser is a modal window on a real screen. A
                   # test that opened one would park the suite until a
                   # human clicked it, which is the same failure as a test
                   # that calls a model, and is guarded the same way.
                   PREPWRIGHT_NO_DIALOG="1")
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


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = Bridge()

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()

    def _intake(self):
        status, body = self.b.call("POST", "/api/intake", {
            "text": POSTING, "employer": "Example Corp", "roleTitle": "Analyst"})
        self.assertEqual(status, 200, body)
        return body

    def _propose(self):
        self._intake()
        status, body = self.b.call("POST", "/api/diagnose", {"action": "propose"})
        self.assertEqual(status, 200, body)
        return body["gaps"]


class TheBoundaryStillHoldsForEveryNewRoute(Base):
    def test_no_pipeline_route_answers_without_a_session(self):
        for method, path in (("GET", "/api/flow"), ("GET", "/api/tracks"),
                             ("POST", "/api/intake"), ("POST", "/api/diagnose"),
                             ("POST", "/api/gap"), ("POST", "/api/research"),
                             ("POST", "/api/curriculum"), ("POST", "/api/track")):
            status, _ = self.b.call(method, path, {} if method == "POST" else None,
                                    cookie="")
            self.assertEqual(status, 403, "%s %s answered without a cookie"
                             % (method, path))

    def test_an_unknown_post_route_is_404_and_not_the_tutor(self):
        """/api/chat is the residual branch of do_POST. A route missing from the
        allowlist is not merely unrouted, it would be answered by the tutor."""
        status, body = self.b.call("POST", "/api/intakee", {"text": "x"})
        self.assertEqual(status, 404, body)

    def test_a_pipeline_route_refuses_a_body_that_is_not_json(self):
        req = urllib.request.Request(
            self.b.base + "/api/intake", data=b"not json", method="POST",
            headers={"Content-Type": "application/json", "X-Tutor-Bridge": "1",
                     "Host": "127.0.0.1:%d" % self.b.port,
                     "Origin": self.b.base, "Cookie": self.b.cookie})
        try:
            urllib.request.urlopen(req, timeout=20)
            self.fail("a non-JSON body was accepted")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)


class TheHealthPillTellsTheTruth(Base):
    """/api/health is outside the session gate on purpose: the launcher reads it
    over loopback to tell a remote-capable bridge from a local-only one. It
    answered 200 to any caller, so a page left open across a bridge restart
    showed a green live pill while every gated route returned 403: a healthy
    looking app that did nothing."""

    def test_health_answers_without_a_session(self):
        status, body = self.b.call("GET", "/api/health", cookie="")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])

    def test_health_says_the_session_is_not_valid(self):
        status, body = self.b.call("GET", "/api/health", cookie="")
        self.assertIs(body.get("session"), False,
                      "health does not report that this caller has no session")

    def test_health_says_the_session_is_valid(self):
        status, body = self.b.call("GET", "/api/health")
        self.assertIs(body.get("session"), True)


class DecliningEverythingIsNotATrap(Base):
    """Declining every gap satisfied `decided`, so the stage advanced to
    research, where discovery refuses ("nothing is approved") and the curriculum
    refuses ("there are no approved gaps"). Pasting sources did not help. The
    candidate was left on a screen where every action said no."""

    def test_the_stage_stays_on_approve_when_nothing_is_approved(self):
        gaps = self._propose()
        self.assertTrue(gaps)
        for gap in gaps:
            status, _ = self.b.call("POST", "/api/gap",
                                    {"gapId": gap["gap_id"], "status": "declined"})
            self.assertEqual(status, 200)
        status, flow = self.b.call("GET", "/api/flow")
        self.assertEqual(status, 200, flow)
        self.assertEqual(flow["stage"], "approve",
                         "declining everything advanced past the only screen "
                         "that can undo it")

    def test_approving_one_afterwards_moves_it_on(self):
        gaps = self._propose()
        for gap in gaps:
            self.b.call("POST", "/api/gap",
                        {"gapId": gap["gap_id"], "status": "declined"})
        status, body = self.b.call("POST", "/api/gap",
                                   {"gapId": gaps[0]["gap_id"],
                                    "status": "approved"})
        self.assertEqual(status, 200, body)
        status, flow = self.b.call("GET", "/api/flow")
        self.assertEqual(flow["stage"], "research",
                         "a declined gap could not be approved after the fact")


class IntakeOverHttp(Base):
    def test_an_empty_request_says_what_to_do(self):
        status, body = self.b.call("POST", "/api/intake", {})
        self.assertEqual(status, 400)
        self.assertIn("job link", body["error"])

    def test_the_fetch_guard_reaches_this_route(self):
        """The route inherits research.py's guard rather than carrying a second
        copy, so this is the check that the wiring is real."""
        for url in ("http://example.com/", "https://127.0.0.1/x",
                    "https://169.254.169.254/latest/", "file:///etc/passwd"):
            status, body = self.b.call("POST", "/api/intake", {"url": url})
            self.assertEqual(status, 400, "%s was not refused" % url)
            self.assertTrue(body.get("error"))

    def test_a_pasted_posting_creates_a_track_and_switches_to_it(self):
        body = self._intake()
        self.assertTrue(body["track_id"].startswith("t-"))
        self.assertEqual(body["title"], "Analyst at Example Corp")
        status, flow = self.b.call("GET", "/api/flow")
        self.assertEqual(status, 200)
        self.assertEqual(flow["trackId"], body["track_id"])


class TheFlowIsDerivedFromTheStoreNotStored(Base):
    def test_it_walks_intake_diagnose_approve_research(self):
        _status, flow = self.b.call("GET", "/api/flow")
        first = flow["stage"]
        self.assertIn(first, ("intake", "diagnose"))
        self._intake()
        self.assertEqual(self.b.call("GET", "/api/flow")[1]["stage"], "diagnose")
        gaps = self.b.call("POST", "/api/diagnose",
                           {"action": "propose"})[1]["gaps"]
        self.assertEqual(self.b.call("GET", "/api/flow")[1]["stage"], "approve")
        for gap in gaps:
            self.b.call("POST", "/api/gap",
                        {"gapId": gap["gap_id"], "status": "approved"})
        self.assertEqual(self.b.call("GET", "/api/flow")[1]["stage"], "research")

    def test_the_posting_excerpt_is_bounded(self):
        self._intake()
        _s, flow = self.b.call("GET", "/api/flow")
        self.assertLessEqual(len(flow["posting"]["excerpt"]), 600)
        self.assertGreater(flow["posting"]["bytes"], 0)


class DiagnoseOverHttp(Base):
    def test_a_plan_is_built_from_the_posting_that_was_stored(self):
        self._intake()
        status, body = self.b.call("POST", "/api/diagnose", {"action": "plan"})
        self.assertEqual(status, 200)
        self.assertGreater(body["requirements"], 4)
        self.assertTrue(body["probes"])
        self.assertFalse(body["fitReport"])

    def test_grading_without_a_model_says_so(self):
        """The judge runs by default. When it cannot, the reply admits it.

        The fallback grades on answer length and never awards `solid`, so a plan
        built from it is pessimistic: it will teach things the candidate already
        knows. That is the safe direction to be wrong in, but only if the
        candidate is told, which is what `gradedBy` is for.
        """
        self._intake()
        _s, body = self.b.call("POST", "/api/diagnose", {"action": "propose"})
        self.assertIn("length only", body["gradedBy"])
        self.assertIn("disabled", body["gradedBy"])

    def test_verdicts_supplied_by_the_caller_are_used_as_given(self):
        """A caller that graded the probes itself is not re-graded.

        This is the seam the page uses when the candidate answers probes in one
        session and proposes in another, and it is the only path that can award
        `solid` without a model.
        """
        self._intake()
        _s, plan = self.b.call("POST", "/api/diagnose", {"action": "plan"})
        probes = plan["probes"]
        self.assertTrue(probes)
        verdicts = [{"probe_id": p["probe_id"], "level": "solid",
                     "why": "answered with specifics"} for p in probes]
        _s, body = self.b.call("POST", "/api/diagnose",
                               {"action": "propose", "verdicts": verdicts})
        self.assertIn("supplied by the caller", body["gradedBy"])
        # Every probe graded solid is a probe that becomes no gap at all.
        self.assertEqual(body["proposed"], 0)

    def test_proposing_twice_is_refused_rather_than_appending(self):
        """gap_id is a primary key. A second list would fail partway through
        with rows from the first still in place."""
        self._propose()
        status, body = self.b.call("POST", "/api/diagnose", {"action": "propose"})
        self.assertEqual(status, 400)
        self.assertIn("already has a gap list", body["error"])

    def test_an_unknown_action_is_refused(self):
        self._intake()
        status, _ = self.b.call("POST", "/api/diagnose", {"action": "delete"})
        self.assertEqual(status, 400)


class GapDecisionsOverHttp(Base):
    def test_only_approved_or_declined_are_accepted(self):
        gaps = self._propose()
        status, body = self.b.call("POST", "/api/gap", {
            "gapId": gaps[0]["gap_id"], "status": "covered"})
        self.assertEqual(status, 400)
        self.assertIn("approved or declined", body["error"])

    def test_a_gap_that_is_not_on_the_track_is_refused(self):
        self._propose()
        status, _ = self.b.call("POST", "/api/gap",
                                {"gapId": "g99", "status": "approved"})
        self.assertEqual(status, 400)

    def test_a_stale_revision_is_a_conflict_not_a_silent_overwrite(self):
        gaps = self._propose()
        status, body = self.b.call("POST", "/api/gap", {
            "gapId": gaps[0]["gap_id"], "status": "approved", "rev": 99})
        self.assertEqual(status, 409, body)


class CurriculumOverHttp(Base):
    def test_it_refuses_before_any_gap_is_approved(self):
        self._propose()
        status, body = self.b.call("POST", "/api/curriculum", {})
        self.assertEqual(status, 400)
        self.assertIn("No gap has been approved", body["error"])

    def test_an_empty_corpus_is_a_refusal_and_not_a_plan_of_zero_steps(self):
        """200 with no steps reads as success and leaves the candidate looking
        at an empty curriculum with nothing saying why."""
        gaps = self._propose()
        for gap in gaps:
            self.b.call("POST", "/api/gap",
                        {"gapId": gap["gap_id"], "status": "approved"})
        status, body = self.b.call("POST", "/api/curriculum", {})
        self.assertEqual(status, 400, body)
        self.assertIn("no corpus", body["error"])


class ResearchOverHttp(Base):
    def test_it_will_not_run_without_urls_from_a_person(self):
        self._intake()
        for payload in ({}, {"urls": []}, {"urls": "https://example.com"}):
            status, body = self.b.call("POST", "/api/research", payload)
            self.assertEqual(status, 400, body)

    def test_one_refused_url_does_not_fail_the_batch(self):
        self._intake()
        status, body = self.b.call("POST", "/api/research", {"urls": [
            "https://169.254.169.254/latest/", "http://example.com/",
            "https://this-host-does-not-exist.invalid/"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["stored"], 0)
        self.assertEqual(len(body["results"]), 3)
        for result in body["results"]:
            self.assertFalse(result["ok"])
            self.assertTrue(result["why"], "a refusal carried no reason")

    def test_the_batch_is_bounded(self):
        self._intake()
        status, body = self.b.call("POST", "/api/research", {
            "urls": ["https://h%d.invalid/" % i for i in range(40)]})
        self.assertEqual(status, 200)
        self.assertLessEqual(len(body["results"]), 12)


class TrackSwitchingOverHttp(Base):
    def test_an_unknown_track_is_refused(self):
        status, _ = self.b.call("POST", "/api/track",
                                {"trackId": "t-000000000000"})
        self.assertEqual(status, 400)

    def test_a_string_that_is_not_a_track_id_is_refused_before_any_lookup(self):
        for bad in ("../../etc", "t-XYZ", "", "t-0a69f756e38f\n"):
            status, _ = self.b.call("POST", "/api/track", {"trackId": bad})
            self.assertEqual(status, 400, "%r was accepted" % bad)

    def test_switching_changes_what_flow_reports(self):
        first = self._intake()["track_id"]
        second = self._intake()["track_id"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.b.call("GET", "/api/flow")[1]["trackId"], second)
        status, body = self.b.call("POST", "/api/track", {"trackId": first})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.b.call("GET", "/api/flow")[1]["trackId"], first)

    def test_the_track_list_names_the_current_one(self):
        track_id = self._intake()["track_id"]
        status, body = self.b.call("GET", "/api/tracks")
        self.assertEqual(status, 200)
        self.assertEqual(body["current"], track_id)
        self.assertIn(track_id, [t["track_id"] for t in body["tracks"]])


if __name__ == "__main__":
    unittest.main()


HANDBOOK = """# Agent Engineering Handbook

## Agent orchestration
An orchestrator decomposes a request into steps and dispatches each to a tool.
Planning happens before dispatch so the sequence can be inspected and bounded.

## Tool calling
A tool call is a structured request the model emits and the runtime executes.
The runtime validates arguments against a schema before anything runs.

## Memory in an agent loop
Short term memory is the current transcript. Long term memory is retrieved and
must be cited, so an agent can say where a remembered fact came from.

## Office parking policy
Bays are allocated by seniority each March and reviewed by the facilities team.

## Catering arrangements
Sandwich platters are ordered on Tuesdays for the Wednesday leadership meeting.
"""


class ASuppliedResourceIsReadWithoutAModel(Base):
    """The file-ingest actions on /api/research, over real HTTP.

    These run with PREPWRIGHT_NO_MODEL=1 like every other route test, which is
    the point: reading a resource and cutting it to a goal must complete with
    every provider call failing closed, because ingestion is not allowed to
    depend on one.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.mkdtemp(prefix="prepwright-route-file-")
        cls.book = os.path.join(cls.tmp, "handbook.md")
        with open(cls.book, "w", encoding="utf-8") as handle:
            handle.write(HANDBOOK)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def _docs(self):
        status, body = self.b.call("GET", "/api/ledger")
        self.assertEqual(status, 200, body)
        return body

    def test_the_commit_reply_carries_the_flow(self):
        """Every sibling mutating route returns `flow_state(handle)`; this one
        did not, and the page's `flow = d.flow || flow` kept the stale object.
        After a successful ingest the stage stayed "research" and the corpus
        count stayed 0, so "Build the plan" was unreachable until some later
        flow-returning action or a reload."""
        self._intake()
        status, body = self.b.call("POST", "/api/research", {
            "action": "ingest_file", "path": self.book,
            "goal": "agent orchestration tool calling memory"})
        self.assertEqual(status, 200, body)
        self.assertIn("flow", body, "the commit reply carries no flow")
        self.assertGreater((body["flow"].get("corpus") or {}).get("documents", 0), 0,
                           "the flow it returned still reports an empty corpus")

    def test_a_goal_of_only_short_words_is_refused_by_name(self):
        """`curriculum.terms` drops words of two characters or fewer, so
        "AI and ML" reduced to nothing and `select` silently entered no-goal
        mode: everything kept, nothing dropped, coverage reported as 1.0. The
        candidate asked for a cut, got none, and was told it all matched."""
        self._intake()
        status, body = self.b.call("POST", "/api/research", {
            "action": "preview_file", "path": self.book, "goal": "AI and ML"})
        self.assertEqual(status, 400, body)
        self.assertIn("two letters", body["error"])
        self.assertIn("ML eval", body["error"],
                      "the refusal does not show what to write instead")

    def test_an_empty_goal_still_keeps_the_whole_resource(self):
        """"Teach me this handbook" is a real request, not a degraded one. The
        refusal above must fire only when a goal was typed and scored nothing."""
        self._intake()
        status, body = self.b.call("POST", "/api/research", {
            "action": "preview_file", "path": self.book, "goal": ""})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["report"]["ok"], body["report"].get("reason"))
        self.assertGreater(body["report"]["kept"], 0)

    def test_the_ledger_row_for_a_file_run_is_parseable_json(self):
        """`_trim_report` shrank only the discard list, so a base body over the
        per-mark cap was returned whole and the store applied a CHARACTER slice
        to finished JSON. The only reader parses it, so a run that dropped
        hundreds of sections displayed as one that dropped none."""
        self._intake()
        status, _body = self.b.call("POST", "/api/research", {
            "action": "ingest_file", "path": self.book,
            "goal": "agent orchestration tool calling memory"})
        self.assertEqual(status, 200)
        ledger = self._docs()
        runs = [r for r in ledger.get("runs", []) if r.get("queries")]
        self.assertTrue(runs, "the ingest recorded no run")
        for run in runs:
            report = json.loads(run["queries"])     # raises if it was sliced
            self.assertEqual(report.get("kind"), "file")
            self.assertIsInstance(report.get("discarded"), list)

    def test_the_chooser_never_opens_when_it_is_disabled(self):
        """The guard itself. If this ever stops refusing, the suite starts
        waiting on a human and the wall clock is the only thing that says so."""
        status, body = self.b.call("POST", "/api/research",
                                   {"action": "pick_file"})
        self.assertEqual(status, 400, body)
        self.assertIn("PREPWRIGHT_NO_DIALOG", body["error"])

    def test_the_chooser_action_is_on_the_allowlisted_route(self):
        """Trap 20: /api/chat is the residual branch at the end of do_POST, so a
        new POST path is answered by the tutor rather than 404. Actions on an
        existing route are how every other new verb was added."""
        status, body = self.b.call("POST", "/api/pick", {"action": "pick_file"})
        self.assertEqual(status, 404, body)

    def test_a_preview_writes_nothing(self):
        self._intake()
        before = json.dumps(self._docs())
        status, body = self.b.call("POST", "/api/research", {
            "action": "preview_file", "path": self.book,
            "goal": "agent orchestration tool calling memory"})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["committed"])
        self.assertTrue(body["report"]["ok"], body["report"].get("reason"))
        self.assertEqual(before, json.dumps(self._docs()),
                         "preview changed the store")

    def test_the_goal_cuts_the_resource_before_it_is_stored(self):
        self._intake()
        status, body = self.b.call("POST", "/api/research", {
            "action": "preview_file", "path": self.book,
            "goal": "agent orchestration tool calling memory", "depth": "focused"})
        self.assertEqual(status, 200, body)
        headings = " ".join(body["report"]["headings"]).lower()
        self.assertIn("orchestration", headings)
        self.assertNotIn("parking", headings)
        self.assertNotIn("catering", headings)

    def test_ingesting_stores_documents_and_records_the_run(self):
        self._intake()
        status, body = self.b.call("POST", "/api/research", {
            "action": "ingest_file", "path": self.book,
            "goal": "agent orchestration tool calling memory"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["committed"])
        self.assertGreater(body["report"]["documents"], 0)
        self.assertEqual(len(body["report"]["stored"]),
                         body["report"]["documents"])
        ledger = json.dumps(self._docs())
        self.assertIn("handbook.md", ledger,
                      "a supplied file did not reach the source ledger")

    def test_a_file_inside_the_store_is_refused_over_http(self):
        self._intake()
        status, body = self.b.call("POST", "/api/research", {
            "action": "ingest_file",
            "path": os.path.join(self.b.home, "library.db"), "goal": "anything"})
        self.assertEqual(status, 400, body)
        self.assertIn("storage", body["error"])

    def test_a_missing_file_is_a_400_that_names_the_path(self):
        self._intake()
        status, body = self.b.call("POST", "/api/research", {
            "action": "ingest_file", "path": os.path.join(self.tmp, "nope.md"),
            "goal": "anything"})
        self.assertEqual(status, 400, body)
        self.assertIn("nope.md", body["error"])

    def test_an_unreadable_format_is_a_400_not_a_500(self):
        self._intake()
        bad = os.path.join(self.tmp, "slides.key")
        with open(bad, "w", encoding="utf-8") as handle:
            handle.write("x" * 600)
        status, body = self.b.call("POST", "/api/research", {
            "action": "ingest_file", "path": bad, "goal": "anything"})
        self.assertEqual(status, 400, body)
        self.assertIn(".key", body["error"])

    def test_an_empty_path_says_what_to_do(self):
        self._intake()
        status, body = self.b.call("POST", "/api/research", {
            "action": "ingest_file", "path": "  "})
        self.assertEqual(status, 400, body)
        self.assertIn("Name the file", body["error"])
