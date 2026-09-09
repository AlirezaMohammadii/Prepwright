"""The writer that was missing, and the three symptoms it caused.

Nothing in production ever wrote step.status, step.score, step.review,
step.opened_utc or step.completed_utc. Verified on 2026-09-09: the only two
production UPDATE step statements set evidence_state and compacted, and the
three that touched status or review were all in tests. One gap, three measured
consequences:

  1. The transcript cap was UNRECOVERABLE. append_turn catches CapExceeded and
     calls compact_oldest_completed_step, whose selector needs status='done'
     AND review IS NOT NULL. It could never match, so every save past the cap
     failed forever. At 12,438 B for the worst legal single turn the floor is
     252 turns; typical verbosity puts it at 1,300 to 2,700.
  2. The assessment table was dead. Its only INSERT had two callers and both
     were tests.
  3. flow.curriculum.done was a permanent 0.

The headline test here is test_the_transcript_cap_is_recoverable_again. It fails
on the code as it stood, which is the only thing that makes it worth having.

sync_step_lifecycle is reconciliation, not an event hook, and these tests hold
that line: every fact it needs is already durable in the mark table, so a step
row can be rebuilt from marks written by a build that predates the method, and
running it twice must change nothing the first run did not.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prepwright import config as C          # noqa: E402
from prepwright import state as S           # noqa: E402
from prepwright import track as T           # noqa: E402


class Base(unittest.TestCase):
    """A fresh storage root per test, as tests/test_persistence.py does it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-life-")
        self.addCleanup(self._restore_home)
        os.environ["PREPWRIGHT_HOME"] = os.path.join(self.tmp, "home")
        importlib.reload(C)
        S.ensure_home()
        self.track_id = T.create_track("Lifecycle")
        self.h = S.open_track(self.track_id)
        self.addCleanup(self.h.close)
        self.h.add_gap("g01", 1, "Caching", "the posting asks for it")
        self.h.add_step("1:topic:S01", 1, "HTTP caching", "Explain revalidation",
                        gap_id="g01")

    def _restore_home(self):
        self.assertTrue(
            os.path.realpath(C.HOME).startswith(os.path.realpath(self.tmp)),
            "storage root escaped the temporary directory: %s" % C.HOME)
        os.environ.pop("PREPWRIGHT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(C)

    def step(self, step_id="1:topic:S01"):
        return self.h.conn.execute(
            "SELECT * FROM step WHERE step_id=?", (step_id,)).fetchone()

    def tick(self, done=True, gap="g01", op=None):
        self.h.append_mark("topic", gap, json.dumps({"done": done}),
                           op or ("op-%s-%s" % (gap, done)))


class ATickIsAStepFinished(Base):
    """The candidate's tick is the fact. Under the ruling taken on 2026-09-09,
    "finished" means the written plan, so the join is a topic mark on the
    step's GAP id, which survives a re-cut of the plan when an ordinal does
    not."""

    def test_before_the_reconciler_a_ticked_step_is_still_ready(self):
        """The bug, stated as a test. The mark lands; the step does not move
        until something reconciles it."""
        self.tick()
        self.assertEqual(self.step()["status"], "ready")

    def test_a_tick_completes_the_step(self):
        self.tick()
        moved = self.h.sync_step_lifecycle()
        row = self.step()
        self.assertEqual(row["status"], "done")
        self.assertTrue(row["completed_utc"])
        self.assertTrue(row["opened_utc"])
        self.assertEqual(moved["completed"], 1)

    def test_an_untick_reopens_it_and_clears_the_completion_time(self):
        """An untick is a real event: the candidate decided they cannot explain
        it after all. It goes back to open rather than ready, because the
        transcript is still there."""
        self.tick(op="t1")
        self.h.sync_step_lifecycle()
        self.tick(done=False, op="t2")
        moved = self.h.sync_step_lifecycle()
        row = self.step()
        self.assertEqual(row["status"], "open")
        self.assertIsNone(row["completed_utc"])
        self.assertTrue(row["opened_utc"], "opening is not undone by an untick")
        self.assertEqual(moved["reopened"], 1)

    def test_a_turn_opens_a_step_that_was_only_ready(self):
        self.h.append_turn("1:topic:S01", "user", "what does max-age promise?",
                           "c1")
        self.h.sync_step_lifecycle()
        row = self.step()
        self.assertEqual(row["status"], "open")
        self.assertTrue(row["opened_utc"])

    def test_an_untouched_step_is_left_alone(self):
        self.h.sync_step_lifecycle()
        row = self.step()
        self.assertEqual(row["status"], "ready")
        self.assertIsNone(row["opened_utc"])

    def test_a_skipped_step_is_never_derived_over(self):
        """'skipped' is the one status a person sets to mean "not for me". A
        reconciler that treats it as derivable erases that on the next save."""
        self.h.conn.execute(
            "UPDATE step SET status='skipped' WHERE step_id='1:topic:S01'")
        self.h.conn.commit()
        self.tick()
        self.h.sync_step_lifecycle()
        self.assertEqual(self.step()["status"], "skipped")

    def test_running_it_twice_changes_nothing_the_first_run_did_not(self):
        self.tick()
        first = self.h.sync_step_lifecycle()
        second = self.h.sync_step_lifecycle()
        self.assertEqual(first["completed"], 1)
        self.assertEqual(sum(second.values()), 0)

    def test_it_rebuilds_from_marks_an_older_build_wrote(self):
        """The reason this is reconciliation and not an event hook: every live
        track already carries these marks and no step row to match."""
        self.tick(op="old-1")
        self.h.append_mark("assess", "1:topic:S01",
                           json.dumps({"mastery": 0.75, "reason": "solid"}), "a1")
        self.h.sync_step_lifecycle()
        row = self.step()
        self.assertEqual(row["status"], "done")
        self.assertAlmostEqual(row["score"], 0.75)


class AGradeAndAReviewReachTheStepRow(Base):

    def test_an_assess_mark_becomes_the_step_score(self):
        self.h.append_mark("assess", "1:topic:S01",
                           json.dumps({"mastery": 0.4, "reason": "partial"}), "a1")
        moved = self.h.sync_step_lifecycle()
        self.assertAlmostEqual(self.step()["score"], 0.4)
        self.assertEqual(moved["scored"], 1)

    def test_a_mastery_outside_the_column_check_is_dropped_not_raised(self):
        """step.score CHECKs 0..1. Letting a bad value through would abort the
        whole transaction and take an unrelated page save down with it."""
        self.h.append_mark("assess", "1:topic:S01",
                           json.dumps({"mastery": 7.5}), "a1")
        self.h.sync_step_lifecycle()
        self.assertIsNone(self.step()["score"])

    def test_a_session_mark_becomes_the_step_review(self):
        self.h.append_mark("session", "sess1", json.dumps({
            "stepKey": "1:topic:S01", "covered": "revalidation",
            "explainBack": "they got it", "next": "try ETags"}), "s1")
        moved = self.h.sync_step_lifecycle()
        review = self.step()["review"]
        self.assertIn("revalidation", review)
        self.assertIn("they got it", review)
        self.assertEqual(moved["reviewed"], 1)

    def test_the_newest_session_wins(self):
        """Sessions are keyed by their own id, so several can name one step."""
        for i, covered in enumerate(("first pass", "second pass")):
            self.h.append_mark("session", "sess%d" % i, json.dumps({
                "stepKey": "1:topic:S01", "covered": covered}), "s%d" % i)
        self.h.sync_step_lifecycle()
        self.assertIn("second pass", self.step()["review"])

    def test_a_session_naming_no_step_is_ignored(self):
        self.h.append_mark("session", "sess1",
                           json.dumps({"covered": "something"}), "s1")
        self.h.sync_step_lifecycle()
        self.assertIsNone(self.step()["review"])


class TheThreeSymptoms(Base):
    """Each one measured, each one now closed."""

    def _finish_with_a_review(self):
        self.h.append_turn("1:topic:S01", "user", "q", "c1")
        self.h.append_turn("1:topic:S01", "tutor", "a long tutor answer", "c2")
        self.tick()
        self.h.append_mark("session", "sess1", json.dumps({
            "stepKey": "1:topic:S01", "covered": "revalidation"}), "s1")
        self.h.sync_step_lifecycle()

    def test_the_transcript_cap_is_recoverable_again(self):
        """Symptom 1, and the reason this whole change matters. Before the
        writer existed, compact_oldest_completed_step could never match a row,
        so append_turn's recovery path was dead and every save past the cap
        failed forever."""
        self.assertEqual(self.h.compact_oldest_completed_step(), 0,
                         "nothing is compactable before a step is finished")
        self._finish_with_a_review()
        self.assertGreater(self.h.compact_oldest_completed_step(), 0,
                           "the cap recovery path is still dead")

    def test_a_students_own_words_are_never_compacted(self):
        """The guarantee the recovery path is allowed to keep."""
        self._finish_with_a_review()
        self.h.compact_oldest_completed_step()
        body = self.h.conn.execute(
            "SELECT body FROM turn WHERE role='user'").fetchone()["body"]
        self.assertEqual(body, "q")

    def test_the_assessment_table_can_be_reached_from_a_finished_step(self):
        """Symptom 2. The FK made this unreachable while no step was ever
        'done': assessment.step_id references step(step_id) and
        PRAGMA foreign_keys is on at every open."""
        self._finish_with_a_review()
        self.h.add_assessment("1:topic:S01", 0.6, "claude/claude-haiku-4-5")
        n = self.h.conn.execute(
            "SELECT COUNT(*) c FROM assessment").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_a_finished_step_is_countable(self):
        """Symptom 3. flow.curriculum.done counts status='done'."""
        self._finish_with_a_review()
        n = self.h.conn.execute(
            "SELECT COUNT(*) c FROM step WHERE status='done'").fetchone()["c"]
        self.assertEqual(n, 1)


class TheLadderHasAnEnd(Base):
    """Until 2026-09-09 a track that had been worked through completely still
    reported stage "learn", which is a screen asking you to keep going, forever.

    The terminal stage is derived from the same kind of fact as every other one:
    a count in the step table. That count only became real when the
    step-lifecycle writer landed, which is why this could not have been added
    before it.

    Owner ruling, 2026-09-09: "finished" means THE WRITTEN PLAN. The page
    invents practice and check items client-side that have no step row, and
    those are deliberately not counted.
    """

    def setUp(self):
        Base.setUp(self)
        sys.path.insert(0, ROOT)
        import bridge                                        # noqa: PLC0415
        self.bridge = bridge
        # Every rung below "learn" is a precondition, and flow_state checks them
        # in order, so all of them have to hold before the last one is reachable.
        self.h.set_intake("pasted",
                          "We need someone who understands HTTP caching.")
        gap = self.h.conn.execute(
            "SELECT rev FROM gap WHERE gap_id='g01'").fetchone()
        self.h.set_gap_status("g01", "approved", gap["rev"])
        self.h.write_doc(
            "http-caching", "HTTP caching",
            [{"sec_id": "s01", "heading": "what-max-age-promises",
              "body": "max-age sets how long a stored response stays fresh.",
              "origin_span": "max-age sets how long a stored response"}],
            origin_url="https://example.org/caching", origin_bytes=412880,
            origin_sha256="c" * 64, extract_sha256="9" * 64)

    def _finish(self, step_id, status="done"):
        self.h.conn.execute("UPDATE step SET status=? WHERE step_id=?",
                            (status, step_id))
        self.h.conn.commit()

    def test_prepared_is_the_last_rung(self):
        self.assertEqual(self.bridge.STAGES[-1], "prepared")
        self.assertEqual(self.bridge.STAGES[-2], "learn")

    def test_a_plan_with_work_left_is_still_learn(self):
        self.h.add_step("2:topic:S02", 2, "ETags", "Explain them")
        self._finish("1:topic:S01")
        self.assertEqual(self._stage(), "learn")

    def test_every_step_delivered_reaches_the_end(self):
        self._finish("1:topic:S01")
        self.assertEqual(self._stage(), "prepared")

    def test_a_skipped_step_counts_as_settled(self):
        """A plan that can never end once anything is skipped would punish the
        candidate for deciding a step is not for them."""
        self.h.add_step("2:topic:S02", 2, "ETags", "Explain them")
        self._finish("1:topic:S01", "done")
        self._finish("2:topic:S02", "skipped")
        self.assertEqual(self._stage(), "prepared")

    def test_skipping_everything_does_not_claim_preparation(self):
        """At least one step has to be actually delivered. Otherwise a track
        where nothing was learned would announce that you are ready."""
        self._finish("1:topic:S01", "skipped")
        self.assertEqual(self._stage(), "learn")

    def _stage(self):
        """The real derivation, not a restatement of it.

        Building the whole precondition chain costs a few lines and is worth
        every one: a helper that re-implemented the predicate would go green
        against a rule the app had stopped applying, which is the exact failure
        the revert discipline exists to catch.
        """
        return self.bridge.flow_state(self.h)["stage"]


if __name__ == "__main__":
    unittest.main()
