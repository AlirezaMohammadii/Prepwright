"""Capacity: a full-length plan must carry every approved gap to a step or name it.

The owner's ruling on 2026-09-08, recorded in HANDOFF §3: the 20/80 cut applies
to the MATERIAL INSIDE a source, never to WHICH gaps get studied. Every approved
gap may need to be learned. The economy comes from teaching each gap from the
smallest sufficient evidence. Any cap, tier or budget that silently drops an
approved gap is a defect.

So this file has one acceptance property and everything else supports it:

    the approved gaps == the gaps that got a step + the gaps named as deferred

Set equality, both directions, with no gap in both halves. A gap no source covers
is a legitimate outcome; it must be NAMED with a reason, not lost. A gap that
vanishes because a cap fired mid-write is the defect this file exists to catch.

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
from prepwright import curriculum as K       # noqa: E402
from prepwright import diagnose as D         # noqa: E402
from prepwright import ingest as G           # noqa: E402
from prepwright import intake as I           # noqa: E402
from prepwright import state as S            # noqa: E402


# Twenty-six subjects with deliberately disjoint vocabulary. Twenty-five get a
# matching section in the supplied resource; the twenty-sixth never appears
# anywhere, because "a gap no source covers is named and carried" needs a gap no
# source covers.
SUBJECTS = [
    ("orchestration", "orchestrator supervisor delegation handoff routing"),
    ("toolcalling", "tool schema argument validation invocation dispatch"),
    ("retrieval", "retrieval chunking embedding index recall passage"),
    ("evaluation", "evaluation rubric benchmark scoring regression harness"),
    ("guardrails", "guardrail refusal filter moderation boundary policy"),
    ("prompting", "prompt template instruction system role formatting"),
    ("memory", "memory episodic summarisation window persistence recall"),
    ("planning", "planner decomposition subgoal sequencing replanning"),
    ("observability", "tracing span telemetry dashboard latency percentile"),
    ("costing", "token budget throughput pricing spend forecast"),
    ("deployment", "deployment rollout canary rollback release pipeline"),
    ("privacy", "privacy consent minimisation retention deletion subject"),
    ("governance", "governance accountability oversight committee mandate"),
    ("riskmapping", "hazard likelihood severity register mitigation owner"),
    ("redteaming", "adversarial jailbreak injection probe exploit attacker"),
    ("dataquality", "provenance lineage labelling annotation duplication"),
    ("finetuning", "finetune adapter checkpoint epoch learning gradient"),
    ("caching", "cache invalidation key hit miss staleness eviction"),
    ("concurrency", "concurrency thread lock semaphore contention deadlock"),
    ("versioning", "versioning semver deprecation compatibility migration"),
    ("procurement", "procurement vendor contract clause supplier diligence"),
    ("stakeholders", "stakeholder workshop interview facilitation alignment"),
    ("reporting", "report finding recommendation evidence appendix summary"),
    ("assurance", "assurance audit attestation control testing sampling"),
    ("incident", "incident triage escalation postmortem remediation timeline"),
    ("uncovered", "kombucha fermentation scoby brewing bottling carbonation"),
]

COVERED = SUBJECTS[:-1]              # 25 subjects the resource teaches
UNCOVERED = SUBJECTS[-1]            # 1 subject nothing in the corpus mentions


def _section(slug, words, filler=6):
    """One resource section: a heading and a body that use only its own words.

    NOTHING is shared between headings, on purpose. MIN_TERMS is 2, but a hit in
    a heading sets `labelled` and one word is then enough (curriculum.py:144,
    a deliberate rule with a stated reason). An earlier version of this fixture
    headed every section "<subject> in practice", so the word "practice" made
    every section a labelled match for every gap, and a gap about kombucha was
    pinned to twenty-five sections about agents. The fixture was wrong, not the
    scorer, but it is worth knowing that one generic heading word defeats
    MIN_TERMS on any corpus.
    """
    head = words.split()[0].capitalize()
    body = " ".join([words] * filler)
    return "## %s\n%s\n" % (head, body[:C.SECTION_MAX_CHARS - 40])


def resource_text(subjects, title="Practitioner Handbook"):
    return "# %s\n\n" % title + "\n".join(
        _section(slug, words) for slug, words in subjects)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-capacity-")
        # Registered first so it runs last: unittest runs every addCleanup after
        # tearDown, so a later cleanup must not reach the candidate's real store.
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

    def _file(self, name, text):
        """A supplied resource on disk, OUTSIDE the storage root.

        ingest.resolve() refuses any path under config.HOME, so a fixture that
        wrote the resource into the track's own directory would be testing the
        refusal, not the ingest.
        """
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _track(self, subjects, resource=None, goal="", depth="broad"):
        """A track with one supplied resource and one approved gap per subject."""
        track_id, _ = I.intake_from_text(
            "Requirements\n\n- Build and evaluate agentic AI systems.\n",
            employer="Example", role_title="Consultant")
        handle = S.open_track(track_id, client_label="capacity-test")
        self.addCleanup(handle.close)
        report = None
        if resource is not None:
            report = G.ingest_file(handle, self._file("handbook.md", resource),
                                   goal=goal, depth=depth,
                                   vetting="primary", trust=5)
        for i, (slug, words) in enumerate(subjects, start=1):
            handle.add_gap("g%02d" % i, i, words, words,
                           level="none", jd_span="%d:%d" % (i, i + 10))
            D.approve(handle, "g%02d" % i)
        return handle, report


class EveryApprovedGapSurvivesTheBuild(Base):
    """The acceptance property. Nothing else in this file matters if it fails."""

    def _assert_conserved(self, handle, built):
        approved = {g["gap_id"] for g in D.approved(handle)}
        planned = [s["gap_id"] for s in built["steps"]]
        deferred = [d["gap_id"] for d in built["deferred"]]
        self.assertEqual(len(planned), len(set(planned)),
                         "a gap got two steps: %r" % planned)
        self.assertEqual(len(deferred), len(set(deferred)),
                         "a gap was deferred twice: %r" % deferred)
        self.assertEqual(set(planned) & set(deferred), set(),
                         "a gap is both planned and deferred")
        lost = approved - set(planned) - set(deferred)
        self.assertEqual(lost, set(),
                         "%d approved gap(s) vanished with no step and no "
                         "reason: %s" % (len(lost), sorted(lost)))
        for entry in built["deferred"]:
            self.assertTrue((entry.get("reason") or "").strip(),
                            "%s was deferred with no reason" % entry["gap_id"])
            self.assertTrue((entry.get("label") or "").strip(),
                            "%s was deferred with no label" % entry["gap_id"])

    def test_twenty_five_approved_gaps_and_a_supplied_resource(self):
        """The owner's stated case: every proposed gap approved, one big file."""
        handle, report = self._track(COVERED, resource=resource_text(COVERED))
        self.assertTrue(report["ok"])
        self.assertGreater(report["documents"], 1,
                           "the fixture produced one document, so this test is "
                           "not exercising a multi-document resource")
        self.assertEqual(report["failed"], [],
                         "the resource did not fit: %r" % report["failed"])
        built = K.build(handle, D.approved(handle))
        self._assert_conserved(handle, built)
        self.assertEqual(len(built["steps"]), len(COVERED),
                         "the resource covers every gap, so every gap should "
                         "have got a step; deferred: %r" % built["deferred"])

    def test_a_gap_no_source_covers_is_named_not_dropped(self):
        """The economy is in the evidence, never in the gap list."""
        handle, _ = self._track(SUBJECTS, resource=resource_text(COVERED))
        built = K.build(handle, D.approved(handle))
        self._assert_conserved(handle, built)
        last = "g%02d" % len(SUBJECTS)
        self.assertIn(last, [d["gap_id"] for d in built["deferred"]])
        self.assertIn(last, [d["gap_id"] for d in built["deferred"]])

    def test_no_corpus_at_all_defers_every_gap_with_a_reason(self):
        """The floor case. Twenty-five gaps, nothing to teach from, nothing lost."""
        handle, _ = self._track(COVERED, resource=None)
        built = K.build(handle, D.approved(handle))
        self._assert_conserved(handle, built)
        self.assertEqual(built["steps"], [])
        self.assertEqual(len(built["deferred"]), len(COVERED))

    def test_every_step_carries_the_evidence_it_teaches_from(self):
        """A step with no pinned slice is a step that cannot be taught."""
        handle, _ = self._track(COVERED, resource=resource_text(COVERED))
        built = K.build(handle, D.approved(handle))
        self.assertTrue(built["steps"])
        for step in built["steps"]:
            stored = K.slices_of(handle, step["step_id"])
            self.assertTrue(stored, "%s has no evidence" % step["step_id"])
            self.assertLessEqual(len(stored), C.PACK_MAX_SECTIONS)
            pack = handle.build_pack(step["step_id"])
            self.assertTrue(pack["cites"],
                            "%s builds an empty pack" % step["step_id"])

    def test_the_written_plan_matches_the_returned_plan(self):
        """`built['written']` is the receipt. A partial write must not report a
        whole one."""
        handle, _ = self._track(COVERED, resource=resource_text(COVERED))
        built = K.build(handle, D.approved(handle))
        self.assertEqual(built["written"],
                         [s["step_id"] for s in built["steps"]])
        self.assertEqual([r["step_id"] for r in K.steps_of(handle)],
                         built["written"])


class TheStoreCapIsACeilingNotACliff(Base):
    """MAX_STEPS is enforced by the store mid-write, with no transaction across
    the batch. A caller that asks for more than the store will take must be cut
    in `plan`, where the cut is reported, rather than dying on step 41 with the
    first forty already committed and no report returned at all."""

    def _many(self, n):
        subjects = [("subject%02d" % i,
                     "alpha%02d beta%02d gamma%02d delta%02d epsilon%02d"
                     % (i, i, i, i, i)) for i in range(1, n + 1)]
        return subjects

    def test_asking_for_more_steps_than_the_store_allows_still_reports_every_gap(self):
        n = C.MAX_STEPS + 5
        subjects = self._many(n)
        handle, _ = self._track(subjects, resource=resource_text(subjects))
        built = K.build(handle, D.approved(handle), max_steps=n)
        approved = {g["gap_id"] for g in D.approved(handle)}
        accounted = ({s["gap_id"] for s in built["steps"]}
                     | {d["gap_id"] for d in built["deferred"]})
        self.assertEqual(approved - accounted, set(),
                         "gaps past the store cap were neither planned nor "
                         "deferred")
        self.assertLessEqual(len(built["steps"]), C.MAX_STEPS)
        self.assertEqual([r["step_id"] for r in K.steps_of(handle)],
                         built["written"])

    def test_the_cut_says_it_was_the_store_cap(self):
        n = C.MAX_STEPS + 5
        subjects = self._many(n)
        handle, _ = self._track(subjects, resource=resource_text(subjects))
        built = K.build(handle, D.approved(handle), max_steps=n)
        reasons = " ".join(d["reason"] for d in built["deferred"])
        self.assertIn(str(C.MAX_STEPS), reasons,
                      "the deferral does not name the cap that caused it: %r"
                      % built["deferred"])


class ASuppliedResourceReportsWhatDidNotFit(Base):
    """A cap refusal partway through a big resource is a real outcome. What is
    not acceptable is the report claiming it stored what it did not."""

    def test_the_report_and_the_store_agree_on_what_was_stored(self):
        handle, report = self._track(COVERED, resource=resource_text(COVERED))
        rows = handle.conn.execute("SELECT doc_id FROM doc").fetchall()
        stored = {d["doc_id"] for d in report["stored"]}
        self.assertTrue(stored)
        self.assertTrue(stored.issubset({r["doc_id"] for r in rows}),
                        "the report names documents the store does not have")
        self.assertEqual(report["documents"], len(report["stored"]))

    def test_a_resource_past_the_track_document_cap_says_so(self):
        """MAX_DOCS_PER_TRACK is 48 and one resource may claim up to
        RESOURCE_MAX_DOCS. Filling the track first must produce a named refusal,
        never a silent partial ingest reported as whole."""
        subjects = [("topic%03d" % i, "zeta%03d eta%03d theta%03d" % (i, i, i))
                    for i in range(1, 200)]
        handle, report = self._track([], resource=resource_text(subjects))
        self.assertTrue(report["failed"] or
                        report["documents"] <= C.MAX_DOCS_PER_TRACK)
        if report["failed"]:
            self.assertIn("CapExceeded", report["failed"][0]["why"])
        self.assertEqual(report["documents"], len(report["stored"]))
        self.assertLessEqual(
            handle.conn.execute("SELECT COUNT(*) c FROM doc").fetchone()["c"],
            C.MAX_DOCS_PER_TRACK)


if __name__ == "__main__":
    unittest.main()
