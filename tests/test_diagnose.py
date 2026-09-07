"""The properties of the knowledge probe and the gap list it proposes.

Two rules decide most of these tests. A requirement the candidate explains
unprompted is not a gap. A resume claim they cannot defend is a gap even when
the posting never mentions it, and the second is the one that is easy to lose:
it is lost the moment the posting's own requirements are allowed to fill the
whole conversation.

Nothing here calls a model. `verdicts_without_a_model` is the pessimistic
fallback and the judgement path is exercised by feeding it verdicts directly.

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
from prepwright import diagnose as D         # noqa: E402
from prepwright import intake as I           # noqa: E402
from prepwright import state as S            # noqa: E402


POSTING = """About Example Corp

Example Corp builds assurance tooling with a team of 400 engineers.

Key Accountabilities

- Give proportionate risk guidance and controls so AI projects can ship.
- Find and manage the security risks AI brings in through third-party tools.
- Keep the work compliant with ISO 42001, SOC2 and AI regulation.

Additional Responsibilities

- Help with wider information security and assurance work.

Skills, Experience & Role Fit

- Hands-on experience in information security, governance or compliance.
- Working knowledge of data protection and privacy rules (e.g., GDPR, APPs).
- Knows AI governance frameworks (NIST AI RMF - highly regarded).

Relevant certifications (desirable): CISSP, CISM, or AI governance certifications.

Send a resume and a short note if this fits you.
Example Corp is an equal opportunity employer.
"""

FIT = """# Fit report - Analyst, Example Corp

## Requirement matrix

| # | Requirement | Verdict | Evidence |
|---|---|---|---|
| 1 | Completion of a relevant degree | **MISSING (hard filter)** | Candidate is mid-thesis; the degree is not conferred. |
| 2 | Unrestricted work rights | **UNVERIFIED** | No work-rights fact appears anywhere in the sources. |
| 3 | AI governance frameworks | PARTIAL | Read ISO 42001; never applied it to a live system. |
| 4 | Software development and system design | MET | Sole engineer of a real-time detection service: FastAPI, Docker, scoped auth, 1,200+ tests, blocking scans in CI. |
| 5 | Publication record | MET | IEEE Internet Computing 2025, IEEE Networking Letters 2026, both published and first-author. |

## Steelman

He is the security half of the role already built, and has shipped the thing.

## Red team

1. The degree is a conferral gate and a thesis under examination is not a degree.
2. Nothing in the sources establishes work rights, so a screener treats it as risk.
3. Two IEEE journal papers do not read as a governance record to a governance panel.

## Scores
Nothing here should be parsed.
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-diagnose-")
        # Registered first so it runs last: unittest runs every addCleanup after
        # tearDown, so a later cleanup must not reach the candidate's real store.
        self.addCleanup(self._restore_home)
        os.environ["PREPWRIGHT_HOME"] = os.path.join(self.tmp, "home")
        importlib.reload(C)
        S.ensure_home()
        self.reqs = D.requirements_from_posting(POSTING)
        self.fit = D.claims_from_fit_report(FIT)

    def _restore_home(self):
        self.assertTrue(
            os.path.realpath(C.HOME).startswith(os.path.realpath(self.tmp)),
            "storage root escaped the temporary directory: %s" % C.HOME)
        os.environ.pop("PREPWRIGHT_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        importlib.reload(C)

    def _track(self):
        track_id, _ = I.intake_from_text(POSTING, employer="Example Corp",
                                         role_title="Analyst")
        handle = S.open_track(track_id, client_label="test")
        self.addCleanup(handle.close)
        return handle


class RequirementsTraceBackToThePosting(Base):
    def test_every_requirement_quotes_the_span_it_came_from(self):
        """A gap nobody can point at in the posting is a gap a model invented."""
        for req in self.reqs:
            start, end = (int(x) for x in req["span"].split(":"))
            self.assertEqual(POSTING[start:end].strip().rstrip("."),
                             req["text"].rstrip("."),
                             "%s does not quote its own span" % req["req_id"])

    def test_the_heading_decides_what_a_bullet_means(self):
        by_text = {r["text"][:24]: r for r in self.reqs}
        self.assertEqual(by_text["Help with wider informat"]["kind"], "responsibility")
        self.assertEqual(by_text["Hands-on experience in i"]["kind"], "requirement")
        self.assertEqual(by_text["Knows AI governance fram"]["kind"], "desirable")

    def test_a_requirement_stated_as_prose_is_not_lost(self):
        """The certifications line in a real posting is not a bullet. Dropping
        it loses an actionable gap."""
        self.assertTrue(any("CISSP" in r["text"] for r in self.reqs),
                        "the certifications line was dropped")

    def test_marketing_prose_never_becomes_a_requirement(self):
        """The other half of the same rule. A heading that did not match a word
        must not license its paragraph, or "About Example Corp" turns its blurb
        into something the employer asked for."""
        joined = " ".join(r["text"] for r in self.reqs)
        self.assertNotIn("400 engineers", joined)
        self.assertNotIn("love to hear", joined)
        self.assertNotIn("equal opportunity", joined.lower())

    def test_a_posting_with_no_bullets_yields_nothing_rather_than_guesses(self):
        self.assertEqual(
            D.requirements_from_posting("We are hiring. It will be great fun."), [])

    def test_a_bulleted_call_to_action_is_not_a_requirement_either(self):
        """Some boards bullet the closing lines. The noise rule has to apply on
        the bullet path too, not only where it was first needed."""
        reqs = D.requirements_from_posting(
            "Skills, Experience & Role Fit\n\n"
            "- Good practical experience in governance and compliance work.\n"
            "- Apply now with your resume and a brief covering note.\n"
            "- We would love to hear from you about this opportunity.\n"
            "- Example Corp is an equal opportunity employer here.\n")
        self.assertEqual([r["text"][:24] for r in reqs],
                         ["Good practical experienc"])


class TheFitReportIsReadAsData(Base):
    def test_the_matrix_verdicts_survive_their_bold_markup(self):
        verdicts = {r["n"]: r["verdict"] for r in self.fit["rows"]}
        self.assertEqual(verdicts, {1: "MISSING", 2: "UNVERIFIED", 3: "PARTIAL",
                                    4: "MET", 5: "MET"})

    def test_weak_and_met_are_split_on_the_verdict(self):
        self.assertEqual([r["n"] for r in self.fit["weak"]], [1, 2, 3])
        self.assertEqual([r["n"] for r in self.fit["met"]], [4, 5])

    def test_the_red_team_is_read_and_the_next_section_is_not(self):
        self.assertEqual(len(self.fit["red_team"]), 3)
        self.assertIn("conferral gate", self.fit["red_team"][0])
        self.assertNotIn("Nothing here should be parsed", " ".join(self.fit["red_team"]))

    def test_the_claims_to_defend_come_from_the_met_rows(self):
        claims = D.resume_claims(self.fit)
        self.assertEqual({c["claim_id"] for c in claims}, {"c04", "c05"})
        self.assertIn("1,200+ tests", claims[0]["claim"])

    def test_an_unreadable_verdict_is_marked_not_dropped(self):
        """Dropping a row shortens the gap list without saying so."""
        fit = D.claims_from_fit_report(
            "## Requirement matrix\n\n| # | R | V | E |\n|---|---|---|---|\n"
            "| 1 | Something | maybe? | some evidence here |\n")
        self.assertEqual([r["verdict"] for r in fit["rows"]], ["UNKNOWN"])


class BothSourcesGetRoomInTheConversation(Base):
    def test_a_resume_probe_survives_a_posting_with_many_requirements(self):
        """The defect this test exists for. Filled first-come, a posting with
        twenty requirements consumes every slot, no resume probe is ever asked,
        and the tool becomes a re-reading of the advertisement."""
        many = D.requirements_from_posting(
            POSTING + "\n\nSkills, Experience & Role Fit\n\n"
            + "".join("- Requirement number %d that must be satisfied here.\n" % i
                      for i in range(40)))
        self.assertGreater(len(many), 24)
        plan, _cut = D.probe_plan(many, self.fit, limit=12)
        sources = {p["source"] for p in plan}
        self.assertIn("resume", sources,
                      "the posting consumed every slot and no claim was probed")
        self.assertIn("posting", sources)

    def test_a_side_with_nothing_to_say_hands_its_room_back(self):
        """With no fit report there are no resume probes, so the reserved share
        must go back to the posting rather than shrinking the conversation."""
        askable = [r for r in self.reqs if r["kind"] != "responsibility"]
        plan, cut = D.probe_plan(self.reqs, None, limit=3)
        self.assertEqual(len(plan), 3, "the reserved share was held for nobody")
        self.assertEqual({p["source"] for p in plan}, {"posting"})
        plan, cut = D.probe_plan(self.reqs, None, limit=10)
        self.assertEqual(len(plan), len(askable))
        self.assertEqual(cut["total"], 0)

    def test_a_responsibility_is_context_and_is_never_probed(self):
        """"Help with wider assurance work" is what the job does,
        not something the candidate is short of. Probing it manufactures a gap
        out of a job description."""
        plan, _ = D.probe_plan(self.reqs, None, limit=40)
        self.assertNotIn("responsibility", {p["kind"] for p in plan})
        self.assertFalse(any("Help with wider" in p["about"] for p in plan))

    def test_what_does_not_fit_is_counted_per_source(self):
        plan, cut = D.probe_plan(self.reqs, self.fit, limit=4)
        self.assertEqual(len(plan), 4)
        self.assertEqual(cut["total"], cut["posting"] + cut["resume"])
        self.assertGreater(cut["total"], 0)

    def test_the_weak_rows_are_asked_hardest_first(self):
        plan, _ = D.probe_plan(self.reqs, self.fit, limit=24)
        kinds = [p["kind"] for p in plan if p["kind"] in
                 ("missing", "unverified", "partial")]
        self.assertEqual(kinds, ["missing", "unverified", "partial"])

    def test_the_same_thing_is_never_asked_twice(self):
        plan, _ = D.probe_plan(self.reqs + self.reqs, self.fit, limit=40)
        abouts = [p["about"] for p in plan]
        self.assertEqual(len(abouts), len(set(abouts)))
        self.assertEqual(len({p["probe_id"] for p in plan}), len(plan))


class GradingIsPessimisticWhenItCannotRead(Base):
    def test_the_fallback_never_awards_solid(self):
        """Awarding solid from a length check deletes a real gap from the plan
        on the strength of a long sentence."""
        plan, _ = D.probe_plan(self.reqs, self.fit, limit=6)
        answers = {p["probe_id"]: "x" * 500 for p in plan}
        levels = {v["level"] for v in D.verdicts_without_a_model(plan, answers)}
        self.assertEqual(levels, {"shaky"})

    def test_a_silence_grades_as_none(self):
        plan, _ = D.probe_plan(self.reqs, self.fit, limit=6)
        levels = {v["level"] for v in D.verdicts_without_a_model(plan, {})}
        self.assertEqual(levels, {"none"})

    def test_an_ungraded_probe_becomes_a_gap_rather_than_disappearing(self):
        plan, _ = D.probe_plan(self.reqs, self.fit, limit=6)
        rows = D.proposals_from(plan, [])
        self.assertEqual(len(rows), len(plan))
        self.assertEqual({r["level"] for r in rows}, {"none"})

    def test_a_forged_probe_id_in_a_verdict_is_ignored(self):
        plan, _ = D.probe_plan(self.reqs, self.fit, limit=4)
        rows = D.proposals_from(plan, [
            {"probe_id": "p99", "level": "solid", "why": "not a probe"},
            {"probe_id": plan[0]["probe_id"], "level": "banana", "why": "not a level"},
        ])
        self.assertEqual(len(rows), len(plan),
                         "a made-up verdict changed the gap list")
        # Counting rows is not enough. gap.level carries a CHECK constraint, so a
        # level that got this far reaches the store as an IntegrityError that
        # names nothing, mid-write, after earlier gaps are already committed.
        self.assertTrue(all(r["level"] in D.LEVELS for r in rows),
                        "a level the schema refuses got as far as the store")

    def test_a_verdict_that_is_not_an_object_cannot_crash_the_grading(self):
        plan, _ = D.probe_plan(self.reqs, self.fit, limit=3)
        rows = D.proposals_from(plan, [None, "solid", 42, {}, {"probe_id": None}])
        self.assertEqual(len(rows), len(plan))

    def test_a_solid_answer_produces_no_gap(self):
        """The whole point of asking first: what they can explain is not taught."""
        plan, _ = D.probe_plan(self.reqs, self.fit, limit=6)
        rows = D.proposals_from(plan, [
            {"probe_id": plan[0]["probe_id"], "level": "solid", "why": "specifics"}])
        self.assertNotIn(plan[0]["about"][:200], [r["label"] for r in rows])
        self.assertEqual(len(rows), len(plan) - 1)

    def test_unlearned_before_shaky_and_posting_before_resume(self):
        plan, _ = D.probe_plan(self.reqs, self.fit, limit=24)
        rows = D.proposals_from(plan, [
            {"probe_id": p["probe_id"],
             "level": "shaky" if p["source"] == "posting" else "none",
             "why": "x"} for p in plan])
        levels = [r["level"] for r in rows]
        self.assertEqual(levels, sorted(levels, key=lambda l: {"none": 0}.get(l, 1)))
        self.assertEqual([r["ord"] for r in rows], list(range(1, len(rows) + 1)))


class NothingActsOnAGapTheCandidateHasNotSeen(Base):
    def _propose(self, handle, limit=6):
        plan, _ = D.probe_plan(self.reqs, self.fit, limit=limit)
        rows = D.proposals_from(plan, D.verdicts_without_a_model(plan, {}))
        D.propose(handle, rows)
        return rows

    def test_every_written_gap_is_proposed_and_nothing_else(self):
        handle = self._track()
        self._propose(handle)
        self.assertTrue(all(g["status"] == "proposed" for g in D.gap_list(handle)))
        self.assertEqual(D.approved(handle), [])

    def test_the_gate_stays_shut_until_every_gap_is_decided(self):
        handle = self._track()
        rows = self._propose(handle)
        self.assertFalse(D.is_approved(handle))
        for row in rows[:-1]:
            D.approve(handle, row["gap_id"])
        self.assertFalse(D.is_approved(handle),
                         "one undecided gap and the plan was already buildable")
        D.decline(handle, rows[-1]["gap_id"])
        self.assertTrue(D.is_approved(handle))

    def test_an_empty_gap_list_is_not_an_approved_one(self):
        handle = self._track()
        self.assertFalse(D.is_approved(handle),
                         "a track with no diagnostic read as approved")

    def test_a_declined_gap_is_kept_and_is_not_workable(self):
        handle = self._track()
        rows = self._propose(handle)
        D.decline(handle, rows[0]["gap_id"])
        for row in rows[1:]:
            D.approve(handle, row["gap_id"])
        stored = {g["gap_id"]: g["status"] for g in D.gap_list(handle)}
        self.assertEqual(stored[rows[0]["gap_id"]], "declined")
        self.assertNotIn(rows[0]["gap_id"], [g["gap_id"] for g in D.approved(handle)])

    def test_every_decision_lands_in_the_append_only_audit(self):
        handle = self._track()
        rows = self._propose(handle)
        for row in rows:
            D.approve(handle, row["gap_id"])
        self.assertEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM gap_history").fetchone()["c"],
            len(rows))
        with self.assertRaises(Exception):
            handle.conn.execute("DELETE FROM gap_history")

    def test_a_stale_revision_is_refused(self):
        handle = self._track()
        rows = self._propose(handle)
        D.approve(handle, rows[0]["gap_id"])
        with self.assertRaises(S.TrackMoved):
            handle.set_gap_status(rows[0]["gap_id"], "declined", 1)

    def test_deciding_a_gap_that_does_not_exist_is_refused_by_name(self):
        handle = self._track()
        with self.assertRaises(D.DiagnoseRefused):
            D.approve(handle, "g99")

    def test_the_summary_counts_rather_than_describes(self):
        handle = self._track()
        rows = self._propose(handle)
        D.approve(handle, rows[0]["gap_id"])
        got = D.summary(handle)
        self.assertEqual(got["total"], len(rows))
        self.assertEqual(got["approved"], 1)
        self.assertFalse(got["decided"])
        self.assertEqual(sum(got["by_level"].values()), len(rows))


if __name__ == "__main__":
    unittest.main()
