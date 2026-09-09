"""Who picks the model, for which job, and what happens when the answer is junk.

Before 2026-09-09 the model menu governed the tutor and nothing else, while
reading as though it governed the app. Three of the five model call sites --
the grader, the session reviewer and the diagnostic judge -- were pinned in the
source to claude-haiku-4-5 at effort "low", and research was pinned to sonnet.
That was never a quality decision anyone made: those paths had never once
completed on any machine until the max-turns fix in ADR 0005, so the pins were
inherited from code nobody had watched run.

Two properties are load-bearing here and each has its own test.

  1. ADOPTING THE CHANGE CHANGES NOTHING. Every shipped default reproduces the
     exact model and effort the old source pinned, so no grade and no bill moves
     until a control is moved. test_the_defaults_reproduce_the_old_pins is the
     property; if it goes red, someone has retuned the app by accident.

  2. IT FAILS CLOSED. A settings file naming a retired model must not reach the
     CLI, because the CLI answers an unknown --model with a warning on stderr
     and a normal exit, so the page would show one model and be graded by
     another. Every layer drops to the next instead of passing the value on.

No test here calls a model. run_cli is replaced with a recorder so the argument
that would have been sent is asserted directly.

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


class RoleBase(unittest.TestCase):
    """Each test gets its own settings root, and the read cache is cleared.

    The cache keys on (mtime_ns, size). Two temp files written in the same
    nanosecond with the same length would otherwise let one test read another's
    settings, which is exactly the kind of failure that shows up once in fifty
    runs and never when watched.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="pw-roles-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        real_home = bridge.PC.HOME
        bridge.PC.HOME = self.home
        self.addCleanup(setattr, bridge.PC, "HOME", real_home)
        bridge._SETTINGS_CACHE["key"] = None
        bridge._SETTINGS_CACHE["data"] = {"roles": {}}
        self.addCleanup(bridge._SETTINGS_CACHE.__setitem__, "key", None)


class TheShippedDefaultsAreTheOldBehaviour(RoleBase):
    """Property 1. This is the regression fence around every existing bill."""

    def test_the_defaults_reproduce_the_old_pins(self):
        expected = {
            ("claude", "tutor"): ("claude-opus-5", ""),
            ("claude", "assess"): ("claude-haiku-4-5", "low"),
            ("claude", "review"): ("claude-haiku-4-5", "low"),
            ("claude", "judge"): ("claude-haiku-4-5", "low"),
            ("claude", "discover"): ("claude-sonnet-5", "low"),
            ("codex", "tutor"): ("gpt-5.6-sol", ""),
            ("codex", "assess"): ("gpt-5.6-luna", "low"),
            ("codex", "review"): ("gpt-5.6-luna", "low"),
            ("codex", "judge"): ("gpt-5.6-luna", "low"),
            ("codex", "discover"): ("gpt-5.6-terra", "low"),
        }
        got = {(p, r): bridge._role_choice(p, r)
               for p in bridge.PROVIDER_MODELS for r in bridge.ROLES}
        self.assertEqual(got, expected)

    def test_the_tutor_default_still_sends_no_effort_flag(self):
        """"" is not the same as "low". The tutor sent no --effort before this
        existed and must still send none, because the CLI's own default is not
        this app's to override on a path nobody asked to change."""
        _model, effort = bridge._role_choice("claude", "tutor")
        self.assertEqual(effort, "")
        self.assertEqual(bridge._effort_flag(effort), [])

    def test_there_is_one_role_per_model_callsite(self):
        """Five roles, five run_cli calls. A sixth call site that forgets to
        name a role raises rather than borrowing another role's setting."""
        source = open(os.path.join(ROOT, "bridge.py"), encoding="utf-8").read()
        self.assertEqual(source.count("\n    data = run_cli(")
                         + source.count("\n            data = run_cli("), 5)
        self.assertEqual(len(bridge.ROLES), 5)


class AChoiceBeatsADefaultAndARequestBeatsAChoice(RoleBase):
    """Three layers, in that order, for model and for effort alike."""

    def _save(self, role, provider, **row):
        bridge._write_settings({"roles": {role: {provider: row}}})

    def test_a_saved_model_replaces_the_default(self):
        self._save("assess", "claude", model="claude-opus-5")
        self.assertEqual(bridge._role_choice("claude", "assess")[0],
                         "claude-opus-5")

    def test_a_saved_effort_replaces_the_default(self):
        self._save("assess", "claude", effort="max")
        self.assertEqual(bridge._role_choice("claude", "assess")[1], "max")

    def test_a_request_argument_beats_the_saved_choice(self):
        self._save("tutor", "claude", model="claude-haiku-4-5")
        self.assertEqual(
            bridge._role_choice("claude", "tutor", model="claude-opus-5")[0],
            "claude-opus-5")

    def test_saving_one_role_leaves_the_others_alone(self):
        """The panel writes the whole document. A save that reset every other
        role to its default would silently undo earlier choices."""
        self._save("assess", "claude", model="claude-opus-5")
        self.assertEqual(bridge._role_choice("claude", "review")[0],
                         "claude-haiku-4-5")

    def test_an_explicitly_saved_empty_effort_is_a_real_choice(self):
        """Choosing "default effort" for the grader means send no flag, which
        is different from never having chosen. Storing it as absent would let
        the shipped "low" reappear and read as the candidate's own pick."""
        self._save("assess", "claude", effort="")
        self.assertEqual(bridge._role_choice("claude", "assess")[1], "")


class JunkNeverReachesTheCommandLine(RoleBase):
    """Property 2. Every layer drops to the next; none forwards the value."""

    # A retired model id is refused at three independent layers, so one test
    # per layer. A single test spanning all three passes while any one of them
    # works, which is how a write-side check can be deleted and stay green:
    # measured on 2026-09-09, reverting the whitelist in _clean_settings left
    # the combined test passing because _role_choice caught it downstream.

    def test_layer_one_a_retired_model_id_is_never_written(self):
        kept = bridge._write_settings(
            {"roles": {"assess": {"claude": {"model": "claude-sonnet-3"}}}})
        self.assertEqual(kept["roles"], {})
        on_disk = json.load(open(os.path.join(self.home, "settings.json"),
                                 encoding="utf-8"))
        self.assertEqual(on_disk["roles"], {})

    def test_layer_two_a_retired_model_id_hand_edited_in_is_cleaned_on_read(self):
        """Bypasses the writer entirely, the way a stale file left by an older
        build or a text editor would."""
        with open(os.path.join(self.home, "settings.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"roles": {"assess": {"claude":
                                            {"model": "claude-sonnet-3"}}}}, fh)
        self.assertEqual(bridge._read_settings()["roles"], {})

    def test_layer_three_the_resolver_refuses_it_even_if_handed_over(self):
        """The last line of defence, isolated by feeding _role_choice a settings
        document that never passed either cleaner."""
        real = bridge._read_settings
        self.addCleanup(setattr, bridge, "_read_settings", real)
        bridge._read_settings = lambda: {
            "roles": {"assess": {"claude": {"model": "claude-sonnet-3"}}}}
        self.assertEqual(bridge._role_choice("claude", "assess")[0],
                         "claude-haiku-4-5")

    def test_an_unknown_effort_level_is_never_stored(self):
        """Dropped at the write, so the role keeps its own default rather than
        falling to "no flag". A typo should not quietly change the depth the
        grader reasons at in either direction."""
        kept = bridge._write_settings(
            {"roles": {"assess": {"claude": {"effort": "turbo"}}}})
        self.assertEqual(kept["roles"], {})
        self.assertEqual(bridge._role_choice("claude", "assess")[1], "low")

    def test_an_unknown_effort_on_the_request_falls_back_not_to_nothing(self):
        """The tutor route forwards whatever the page sent. Treating a typo as
        "send no flag" would be a third distinct depth, silently."""
        bridge._write_settings(
            {"roles": {"assess": {"claude": {"effort": "max"}}}})
        self.assertEqual(
            bridge._role_choice("claude", "assess", effort="turbo")[1], "max")

    def test_a_claude_model_saved_under_codex_is_dropped(self):
        """Choices are stored per provider for this reason. One flat model
        field would carry a Claude id into a Codex run the moment the provider
        changed."""
        kept = bridge._write_settings(
            {"roles": {"assess": {"codex": {"model": "claude-opus-5"}}}})
        self.assertEqual(kept["roles"], {})
        self.assertEqual(bridge._role_choice("codex", "assess")[0],
                         "gpt-5.6-luna")

    def test_a_hand_edited_file_that_is_not_a_dict_is_ignored(self):
        with open(os.path.join(self.home, "settings.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("[1, 2, 3]")
        self.assertEqual(bridge._read_settings(), {"roles": {}})

    def test_a_corrupt_file_is_ignored_rather_than_raising(self):
        with open(os.path.join(self.home, "settings.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(bridge._role_choice("claude", "assess")[0],
                         "claude-haiku-4-5")

    def test_an_unknown_role_raises_rather_than_guessing(self):
        with self.assertRaises(ValueError):
            bridge._role_choice("claude", "summarise")

    def test_an_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            bridge._role_choice("gemini", "tutor")

    def test_an_edit_made_outside_this_process_is_picked_up(self):
        """The read is cached against (mtime_ns, size). A cache that never
        noticed an external edit would pin the app to whatever it read first."""
        self.assertEqual(bridge._role_choice("claude", "assess")[0],
                         "claude-haiku-4-5")
        with open(os.path.join(self.home, "settings.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"roles": {"assess": {"claude":
                                            {"model": "claude-opus-5"}}}}, fh)
        os.utime(os.path.join(self.home, "settings.json"), (1, 1))
        self.assertEqual(bridge._role_choice("claude", "assess")[0],
                         "claude-opus-5")


class TheGraderActuallyUsesWhatWasChosen(RoleBase):
    """The point of the whole change, asserted at the argument boundary.

    _role_choice returning the right pair proves nothing on its own: the bug
    being fixed was a call site that computed a model and then ignored it.
    These record what run_cli was handed.
    """

    def setUp(self):
        RoleBase.setUp(self)
        self.seen = []
        real = bridge.run_cli
        self.addCleanup(setattr, bridge, "run_cli", real)

        def recorder(provider, model, system, prompt, effort="", **kw):
            self.seen.append({"model": model, "effort": effort})
            return {"result": json.dumps(
                {"steps": [{"key": "8:topic:S08", "mastery": 0.5,
                            "reason": "ok"}],
                 "recap": [{"q": "q", "a": "a"}]}),
                    "usage": {}, "total_cost_usd": 0}

        bridge.run_cli = recorder

    def test_the_grader_sends_the_model_the_candidate_chose(self):
        bridge._write_settings({"roles": {"assess": {"claude": {
            "model": "claude-opus-5", "effort": "xhigh"}}}})
        bridge._assess_batch("claude", [{"key": "8:topic:S08"}])
        self.assertEqual(self.seen[-1],
                         {"model": "claude-opus-5", "effort": "xhigh"})

    def test_the_grader_sends_haiku_at_low_when_nothing_was_chosen(self):
        bridge._assess_batch("claude", [{"key": "8:topic:S08"}])
        self.assertEqual(self.seen[-1],
                         {"model": "claude-haiku-4-5", "effort": "low"})

    def test_the_reviewer_has_its_own_setting_not_the_graders(self):
        """Grouping review with assess would have been the tidy choice and the
        wrong one: a candidate who raises the grader to Opus has not asked to
        pay Opus rates for every recap card."""
        bridge._write_settings({"roles": {"assess": {"claude": {
            "model": "claude-opus-5"}}}})
        bridge.review_via_cli("claude", {"key": "8:topic:S08"},
                              [{"role": "user", "content": "q"},
                               {"role": "assistant", "content": "a"}])
        self.assertEqual(self.seen[-1]["model"], "claude-haiku-4-5")


class OneChoiceServesBothTools(RoleBase):
    """Resume Studio and this app share one model preference through one file.

    The two spell exactly one id differently -- Resume Studio pins the dated
    claude-haiku-4-5-20251001 where this app uses claude-haiku-4-5 -- so the
    alias is the difference between a shared preference and a preference that
    is silently discarded every time the other tool writes it.
    """

    def setUp(self):
        RoleBase.setUp(self)
        real = bridge.SHARED_PREFS_PATH
        self.shared = os.path.join(self.home, "model-prefs.json")
        bridge.SHARED_PREFS_PATH = self.shared
        self.addCleanup(setattr, bridge, "SHARED_PREFS_PATH", real)

    def _shared(self, **kw):
        with open(self.shared, "w", encoding="utf-8") as fh:
            json.dump(kw, fh)

    def test_the_other_tools_choice_becomes_this_apps_tutor_default(self):
        self._shared(provider="claude", model="claude-sonnet-5", effort="high")
        self.assertEqual(bridge._role_choice("claude", "tutor"),
                         ("claude-sonnet-5", "high"))

    def test_it_does_not_move_the_grader(self):
        """"Which model do I want these tools to use" is a statement about the
        one that talks to you. Moving the grader too would be a bill nobody
        asked for."""
        self._shared(provider="claude", model="claude-opus-5", effort="max")
        self.assertEqual(bridge._role_choice("claude", "assess"),
                         ("claude-haiku-4-5", "low"))

    def test_the_dated_haiku_id_is_normalised_on_the_way_in(self):
        self._shared(provider="claude", model="claude-haiku-4-5-20251001")
        self.assertEqual(bridge._role_choice("claude", "tutor")[0],
                         "claude-haiku-4-5")

    def test_a_choice_made_here_is_written_in_the_spelling_they_read(self):
        bridge._write_shared_prefs("claude", "claude-haiku-4-5", "low")
        with open(self.shared, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["model"],
                             "claude-haiku-4-5-20251001")

    def test_a_choice_saved_in_this_app_outranks_the_shared_one(self):
        self._shared(provider="claude", model="claude-sonnet-5")
        bridge._write_settings(
            {"roles": {"tutor": {"claude": {"model": "claude-opus-5"}}}})
        self.assertEqual(bridge._role_choice("claude", "tutor")[0],
                         "claude-opus-5")

    def test_a_preference_for_the_other_provider_is_left_alone(self):
        """A Codex choice must not reach into a Claude run and be rejected
        there, leaving a default the candidate never picked."""
        self._shared(provider="codex", model="gpt-5.6-luna", effort="max")
        self.assertEqual(bridge._role_choice("claude", "tutor"),
                         ("claude-opus-5", ""))

    def test_a_model_this_app_does_not_have_still_lets_the_effort_through(self):
        """Each key is dropped on its own. A partly-unusable file is still
        partly usable."""
        self._shared(provider="claude", model="gpt-4o", effort="xhigh")
        self.assertEqual(bridge._role_choice("claude", "tutor"),
                         ("claude-opus-5", "xhigh"))

    def test_no_file_at_all_is_the_normal_case(self):
        self.assertEqual(bridge._read_shared_prefs(), {})
        self.assertEqual(bridge._role_choice("claude", "tutor"),
                         ("claude-opus-5", ""))

    def test_a_corrupt_file_does_not_take_the_tutor_down(self):
        with open(self.shared, "w", encoding="utf-8") as fh:
            fh.write("{{{")
        self.assertEqual(bridge._role_choice("claude", "tutor")[0],
                         "claude-opus-5")

    def test_an_unwritable_destination_is_reported_not_raised(self):
        """Picking a tutor model must not fail because a sibling tool's
        directory is not writable."""
        bridge.SHARED_PREFS_PATH = os.path.join(self.home, "nope", "x", "p.json")
        os.makedirs(os.path.dirname(os.path.dirname(bridge.SHARED_PREFS_PATH)),
                    exist_ok=True)
        os.chmod(os.path.dirname(os.path.dirname(bridge.SHARED_PREFS_PATH)), 0o500)
        self.addCleanup(
            os.chmod,
            os.path.dirname(os.path.dirname(bridge.SHARED_PREFS_PATH)), 0o700)
        self.assertFalse(
            bridge._write_shared_prefs("claude", "claude-opus-5", "low"))


class AGradeSaysWhatProducedIt(unittest.TestCase):
    """Once the grader is a choice, an unattributed grade is a claim.

    A chat turn has always carried "Tutor - Claude - Opus 5". A grade carried
    nothing, which was survivable while the grader was pinned in the source and
    stops being survivable the moment the candidate can move it: two numbers on
    the same screen, produced by two different models, and no way to tell.
    """

    def test_the_page_can_persist_which_model_graded(self):
        from prepwright import pagestate
        self.assertEqual(pagestate.FIELDS.get("assessModel"),
                         ("pref", "scalar"))

    def test_the_grading_route_reports_the_model_it_resolved(self):
        """Not the one the page asked for. They differ whenever the whitelist
        refused a saved choice, and the number on screen belongs to whichever
        model actually ran."""
        source = open(os.path.join(ROOT, "bridge.py"), encoding="utf-8").read()
        self.assertIn('"model": _role_choice(provider, "assess")[0]', source)


# ---- the route, over real HTTP ----------------------------------------------

class Bridge(object):
    """A live bridge on its own port and storage root, as test_back_half does.

    A different port base from test_back_half's on purpose: both files derive a
    port from this pid, and the same base would make the two collide whenever
    they ran in one process.
    """

    def __init__(self):
        self.home = tempfile.mkdtemp(prefix="pw-rolehttp-")
        self.port = 9500 + (os.getpid() % 400)
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
            with urllib.request.urlopen(req, timeout=60) as r:
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


class TheSettingsRouteHoldsItsBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = Bridge()

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()

    def test_reading_needs_a_session(self):
        status, _ = self.b.call("GET", "/api/settings", cookie="")
        self.assertEqual(status, 403)

    def test_writing_needs_a_session(self):
        status, _ = self.b.call("POST", "/api/settings", {"roles": {}},
                                cookie="")
        self.assertEqual(status, 403)

    def test_it_describes_every_role(self):
        status, body = self.b.call("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual([r["id"] for r in body["roles"]],
                         list(bridge.ROLES))
        for role in body["roles"]:
            self.assertIn("claude", role["resolved"])
            self.assertIn("model", role["resolved"]["claude"])

    def test_a_saved_choice_comes_back_resolved(self):
        status, body = self.b.call("POST", "/api/settings", {"roles": {
            "judge": {"claude": {"model": "claude-opus-5", "effort": "high"}}}})
        self.assertEqual(status, 200)
        judge = [r for r in body["roles"] if r["id"] == "judge"][0]
        self.assertEqual(judge["resolved"]["claude"],
                         {"model": "claude-opus-5", "effort": "high"})
        # And it survives a fresh read, so it reached the disk.
        _s, again = self.b.call("GET", "/api/settings")
        judge = [r for r in again["roles"] if r["id"] == "judge"][0]
        self.assertEqual(judge["resolved"]["claude"]["model"], "claude-opus-5")

    def test_a_rejected_value_is_visible_as_rejected(self):
        """The response is the state that was kept, not an echo of the request,
        so a value the whitelist dropped cannot look accepted in the page."""
        _s, body = self.b.call("POST", "/api/settings", {"roles": {
            "discover": {"claude": {"model": "claude-sonnet-3"}}}})
        disc = [r for r in body["roles"] if r["id"] == "discover"][0]
        self.assertEqual(disc["saved"]["claude"], {})
        self.assertEqual(disc["resolved"]["claude"]["model"],
                         "claude-sonnet-5")

    def test_a_body_that_is_not_an_object_is_refused_at_the_reader(self):
        """_read_json_body already requires an object, so the route never sees
        a list. _clean_settings still guards against one, because the file on
        disk reaches it without passing through that reader."""
        status, _ = self.b.call("POST", "/api/settings", [1, 2, 3])
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
