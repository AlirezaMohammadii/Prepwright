"""Rehearsal steps: the interview asked, graded against the candidate's evidence,
and delivered only by a passing grade (ADR 0008). No model is called here.
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
from prepwright import corpus as CO         # noqa: E402
from prepwright import diagnose as D        # noqa: E402
from prepwright import intake as I          # noqa: E402
from prepwright import rehearse as R        # noqa: E402
from prepwright import state as S           # noqa: E402
from prepwright import teach as TEACH       # noqa: E402

BASE = "Research_Fellow_University_Of_Example"

FIT = """# Fit report

## Requirement matrix

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | PhD in Computer Science or related discipline | MISSING | PhD candidate; thesis submission planned within one month |
| 2 | First-authored publications in venues such as CCS or S&P | PARTIAL | First-author IEEE Internet Computing 2025 paper; NDSS submission under review |
| 3 | Rigorous data analysis, including statistical modelling | MET | BenchA, BenchB, BenchC, and SensorBench evaluations with stated protocols |
| 4 | Support and mentor HDR students or junior researchers | MISSING | No traceable supervision or mentoring evidence |
| 5 | Valid Australian work rights without sponsorship | MET | Hobart-based with full Australian work rights, stated by the applicant |
| 6 | Submit resume, cover letter, and selection-criteria responses | PARTIAL | Selection-criteria responses remain required |

## Steelman

Direct domain overlap: drift and fault detection for sensor networks.

## Red team

The PhD-in-hand wording is the clearest rejection risk because candidature is not completion. The application also remains incomplete until selection-criteria responses are prepared.
"""

TEX = r"""\documentclass{article}
\newcommand{\entry}[4]{#1 #2 #3 #4}
\begin{document}
{\Huge Alex Example}\\
alex.example@example.com | +61 400 000 000 | linkedin.com/in/alex-example
\section*{SUMMARY}
PhD candidate in signal processing with 3 years of doctoral research on sensor networks.
\section*{EXPERIENCE}
\entry{Signal Researcher (PhD Research)}{Jul 2023 -- Present}{Trey University}{Exampleton}
\begin{itemize}
  \item At a 5\% drift rate, faulty-sensor recall rose from 41\% to 88\% on BenchA and from 37\% to 84\% on BenchB.
  \item Evaluated the filter on SensorBench 2019 with EER as the stated measure.
  \item Data access requests go to alex.example@example.com or https://example.org/alex-data today.
\end{itemize}
\end{document}
"""

POSTING = ("Research Fellow, University of Example.\n"
           "Focus: data privacy, ML security and LLM safety.\n"
           "You will: lead and conduct independent research in ML security.\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-rehearse-")
        self.addCleanup(self._restore_home)
        os.environ["PREPWRIGHT_HOME"] = os.path.join(self.tmp, "home")
        importlib.reload(C)
        S.ensure_home()

    def _restore_home(self):
        self.assertTrue(os.path.realpath(C.HOME).startswith(os.path.realpath(self.tmp)))
        os.environ.pop("PREPWRIGHT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(C)

    def _folder(self):
        root = os.path.join(self.tmp, "applications", "2026-09-24__" + BASE)
        os.makedirs(root)
        for suffix, body in (("_FitReport.md", FIT), ("_A_Mohammadi.tex", TEX),
                             ("_JobDescription.md",
                              "# Job description\n\n## Provenance\n\n```json\n%s\n```\n\n"
                              "## Posting\n\n%s" % (json.dumps(
                                  {"position": "Research Fellow",
                                   "company": "University of Example"}), POSTING))):
            with open(os.path.join(root, BASE + suffix), "w", encoding="utf-8") as fh:
                fh.write(body)
        return root

    def _track_with_a_plan(self):
        track_id, _rep = I.intake_from_application(self._folder())
        h = S.open_track(track_id)
        self.addCleanup(h.close)
        h.add_gap("g01", 1, "Rigorous data analysis", "the posting asks for it")
        D.approve(h, "g01")
        doc = CO.ingest_text(h, "# Study\n\n## Evaluation protocols\n\nEER is the equal error"
                                " rate, reported on SensorBench.\n", "https://x.test/a",
                             title="Study notes")
        h.add_step("1:topic:S01", 1, "Evaluation protocols", "Explain EER", gap_id="g01")
        h.pin_slice("1:topic:S01", doc, "s01", 0)
        fit = D.claims_from_fit_report(FIT)
        return h, fit


class TheRehearsalIsPlannedFromTheApplication(Base):
    """Prepwright taught concepts and stopped there: no mock interview, no
    behavioural or objection practice, and the tutor never saw the application.
    On the 2026-09-24 walk it coached the candidate to say "SensorBench wasn't run
    at all" about a resume that claims SensorBench evaluations, and the review put
    the sentence in his recap bank. The fit report's logistics row ("Submit
    resume, cover letter...") and the work-rights row were probed as if they
    were competencies."""

    def test_the_panel_s_questions_lead_with_the_objection_and_skip_the_paperwork(self):
        qs = R.questions(D.claims_from_fit_report(FIT))
        self.assertEqual([q["qid"] for q in qs], ["R%02d" % i for i in range(1, len(qs) + 1)])
        self.assertEqual(qs[0]["kind"], "objection",
                         "an objection that mentions paperwork was screened out")
        about = " ".join(q["about"] for q in qs).lower()
        self.assertNotIn("submit resume", about)
        self.assertNotIn("work rights", about)
        kinds = {q["kind"] for q in qs}
        self.assertEqual(kinds, {"objection", "gap", "claim", "behavioural"})
        story = next(q for q in qs if q["kind"] == "behavioural")
        self.assertIn("“Support and mentor HDR students or junior researchers”",
                      story["question"])
        self.assertLessEqual(len(R.questions(D.claims_from_fit_report(FIT), limit=2)), 2)

    def test_the_resume_evidence_never_carries_the_contact_block(self):
        md = I.resume_evidence(TEX)
        for leak in ("@", "example.com", "example.org", "+61", "400 000", "linkedin",
                     "Alex Example"):
            self.assertNotIn(leak, md)
        self.assertIn("## Experience: Signal Researcher (PhD Research), Trey University"
                      " (Jul 2023 – Present)", md)
        self.assertIn("- At a 5% drift rate, faulty-sensor recall rose from 41% to 88%",
                      md)
        self.assertIn("SensorBench 2019", md)

    def test_plan_adds_cited_steps_once_and_the_pack_carries_his_evidence(self):
        h, fit = self._track_with_a_plan()
        added, qs = R.plan(h, fit, [], employer="University of Example",
                           role="Research Fellow")
        self.assertEqual(added, len(qs))
        self.assertGreater(added, 3)
        self.assertEqual(R.plan(h, fit, []), (0, []), "a second plan moved the denominator")
        docs = {r["title"]: r["vetting"] for r in h.conn.execute(
            "SELECT title, vetting FROM doc WHERE title LIKE 'Your application%'")}
        self.assertEqual(docs, {R.FIT_EVIDENCE_TITLE: "primary",
                                R.RESUME_EVIDENCE_TITLE: "primary"})
        record = {(r["doc_id"], r["sec_id"]) for r in h.conn.execute(
            "SELECT s.doc_id, s.sec_id FROM section s JOIN doc d ON d.doc_id = s.doc_id"
            " WHERE d.title LIKE 'Your application%'")}
        self.assertLess(len(record), C.PACK_MAX_SECTIONS)
        for q in qs:
            pinned = {(r["doc_id"], r["sec_id"]) for r in h.conn.execute(
                "SELECT doc_id, sec_id FROM step_slice WHERE step_id=?", (q["stepKey"],))}
            self.assertLessEqual(record, pinned,
                                 "%s left part of his record out of a pack with room"
                                 % q["qid"])
        claim = next(q for q in qs if "SensorBench" in q["about"])
        pack = CO.build_pack(h, claim["stepKey"])
        self.assertIn("41% to 88%", pack["text"], "his own numbers are not in the pack")
        self.assertIn("SensorBench 2019", pack["text"])
        step = h.conn.execute("SELECT objective, tier FROM step WHERE step_id=?",
                              (qs[0]["stepKey"],)).fetchone()
        self.assertTrue(step["objective"].startswith(
            "The selection panel for Research Fellow at University of Example asks: "))
        valid = CO.check_citations("I ran it [%s]." % pack["cites"][0], pack)
        self.assertEqual((valid["valid"], valid["invented"]), ([pack["cites"][0]], []))

    def test_the_study_plan_is_counted_apart_from_the_rehearsal(self):
        h, fit = self._track_with_a_plan()
        R.plan(h, fit, [])
        flow = TEACH.flow_state(h)
        cur = flow["curriculum"]
        self.assertEqual(cur["tiers"]["core"], 1)
        self.assertEqual(cur["rehearse"], cur["steps"] - 1)
        self.assertEqual(flow["stage"], "learn")
        self.assertEqual(len(flow["stepList"]), cur["steps"])
        self.assertEqual([s["rehearse"] for s in flow["stepList"]],
                         [False] + [True] * cur["rehearse"])


class ARehearsalStepIsDeliveredByAGradeNotATick(Base):
    """`prepared` was reached by ticking steps, which proves nothing about the
    room. A rehearsal step is delivered only when a graded answer reaches
    C.REHEARSAL_PASS; a tick, or a failing grade, leaves it open."""

    def test_only_a_passing_rehearsal_grade_delivers_it(self):
        h, fit = self._track_with_a_plan()
        _n, qs = R.plan(h, fit, [])
        key = qs[0]["stepKey"]

        def status():
            h.sync_step_lifecycle()
            return h.conn.execute("SELECT status FROM step WHERE step_id=?",
                                  (key,)).fetchone()["status"]

        h.append_mark("topic", qs[0]["qid"], json.dumps({"done": True}), "m-tick")
        self.assertNotEqual(status(), "done", "a tick delivered a rehearsal step")
        h.add_assessment(key, 0.9, "claude/claude-haiku-4-5")
        self.assertNotEqual(status(), "done", "a teaching grade delivered a rehearsal step")
        h.add_assessment(key, 0.625, C.REHEARSAL_RUBRIC + "claude/claude-sonnet-5")
        self.assertNotEqual(status(), "done")
        h.add_assessment(key, 0.75, C.REHEARSAL_RUBRIC + "claude/claude-sonnet-5")
        self.assertEqual(status(), "done")
        h.add_assessment(key, 0.25, C.REHEARSAL_RUBRIC + "claude/claude-sonnet-5")
        self.assertEqual(status(), "done", "a later weak answer undid a demonstrated one")

    def test_the_track_is_not_prepared_while_a_rehearsal_step_is_open(self):
        h, fit = self._track_with_a_plan()
        _n, qs = R.plan(h, fit, [])
        h.append_mark("topic", "g01", json.dumps({"done": True}), "m-study")
        h.sync_step_lifecycle()
        self.assertEqual(TEACH.flow_state(h)["stage"], "learn")
        for q in qs:
            h.add_assessment(q["stepKey"], 1.0, C.REHEARSAL_RUBRIC + "claude/x")
        h.sync_step_lifecycle()
        self.assertEqual(TEACH.flow_state(h)["stage"], "prepared")


class TheGradeIsTheRubricNotAFeeling(unittest.TestCase):
    """The rubric is stated and three of its four criteria are the grader's; the
    fourth, length, is counted. The strong answer is checked for invented
    citations exactly as a teaching turn is."""

    PACK = {"cites": ["D02§s01", "D02§s03"], "grounded": True, "pack_sha16": "abc"}

    def test_concise_is_counted_and_the_pass_needs_no_zero(self):
        short = " ".join(["word"] * 200)
        got = R.grade_result({"grounded": 2, "specific": 2, "structured": 2, "sinks": "x",
                              "strongAnswer": "I did it [D02§s01].", "followUp": "Why?"},
                             short, self.PACK)
        self.assertEqual(got["scores"]["concise"], 2)
        self.assertEqual((got["total"], got["ready"]), (1.0, True))
        long_ = " ".join(["word"] * 400)
        got = R.grade_result({"grounded": 2, "specific": 2, "structured": 2, "sinks": "",
                              "strongAnswer": "", "followUp": ""}, long_, self.PACK)
        self.assertEqual((got["scores"]["concise"], got["points"], got["total"], got["ready"]),
                         (0, 6, R.CAP_WITH_A_ZERO, False),
                         "6 of 8 with a zero delivered the step the page calls not ready")
        self.assertIn("A criterion at 0 caps it at 5.", R.feedback_text(got))

    def test_an_invented_citation_is_reported_and_never_scored(self):
        got = R.grade_result({"grounded": 1, "specific": 2, "structured": 2, "sinks": "",
                              "strongAnswer": "It held [D02§s01] and [D09§s09].",
                              "followUp": ""}, "a b c d e f", self.PACK)
        self.assertEqual(got["citations"]["invented"], ["D09§s09"])
        self.assertEqual(got["total"], 0.875)

    def test_a_malformed_reply_scores_zero_rather_than_passing(self):
        got = R.grade_result({"grounded": True, "specific": 7}, "a b c d e", self.PACK)
        self.assertEqual((got["scores"]["grounded"], got["scores"]["specific"]), (0, 0))
        self.assertFalse(got["ready"])

    def test_a_grade_that_ran_out_of_turns_is_asked_once_more_and_only_once(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("Claude tutor CLI failed (exit 1) — CLI reported"
                                   " error_max_turns")
            return {"result": "{}"}

        with self.assertRaises(RuntimeError):
            R.retry_on_max_turns(flaky)
        self.assertEqual(len(calls), 2)
        calls[:] = [1]
        self.assertEqual(R.retry_on_max_turns(flaky), {"result": "{}"})
        calls[:] = []

        def disabled():
            calls.append(1)
            raise RuntimeError("Model calls are disabled in this process")

        with self.assertRaises(RuntimeError):
            R.retry_on_max_turns(disabled)
        self.assertEqual(len(calls), 1, "a refusal that is not a turn limit was retried")

    def test_the_brief_reads_the_strong_answer_back(self):
        result = R.grade_result({"grounded": 2, "specific": 2, "structured": 2, "sinks": "",
                                 "strongAnswer": "My thesis is due in October [D02§s01].",
                                 "followUp": "When exactly?"}, "a b c d e", self.PACK)
        text = R.feedback_text(result)
        self.assertEqual(R._strong_answer_of(text), "My thesis is due in October [D02§s01].")


class TheDayBeforeBriefIsBuiltFromTheStore(Base):
    """The one page for the day before: top concepts, five strongest stories,
    the likeliest objections with the answer he rehearsed, and questions to
    ask them, each resting on something the store holds."""

    def test_the_brief_names_his_numbers_the_objection_and_a_cited_question(self):
        h, fit = self._track_with_a_plan()
        _n, qs = R.plan(h, fit, [])
        text = R.feedback_text(R.grade_result(
            {"grounded": 2, "specific": 2, "structured": 2, "sinks": "",
             "strongAnswer": "Submission is due next month [D02§s01].", "followUp": ""},
            "a b c d e", {"cites": ["D02§s01"]}))
        h.append_turn(qs[0]["stepKey"], "tutor", text, "t-1")
        md = R.brief(h, fit, POSTING, employer="University of Example",
                     role="Research Fellow")
        self.assertTrue(md.startswith("# The day before: Research Fellow at University of"
                                      " Example"))
        self.assertIn("- Evaluation protocols [", md)
        self.assertIn("41% to 88%", md)
        self.assertIn("The PhD-in-hand wording", md)
        self.assertIn("Your answer: Submission is due next month [D02§s01].", md)
        self.assertIn("The posting says “Focus: data privacy, ML security and LLM safety.”", md)
        self.assertNotIn("Submit resume", md)
        stories = md.split("## Your five strongest stories", 1)[1].split("## ", 1)[0]
        self.assertNotIn("PhD candidate in signal processing", stories,
                         "the resume summary was listed as a story")
        asks = md.split("## Questions to ask them", 1)[1]
        self.assertNotIn("PhD in Computer Science", asks,
                         "a credential he lacks became a question to ask them")
        self.assertIn("“Support and mentor HDR students or junior researchers”", asks)


if __name__ == "__main__":
    unittest.main()
