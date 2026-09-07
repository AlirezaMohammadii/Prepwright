"""The properties of curriculum generation: order, slices, tiers and the cut.

The acceptance property is one sentence: every step pins the sections it teaches
from, and no step is pinned material it does not teach from. `corpus.pin_all`
satisfied neither half. It pinned every section of every ready document to every
step, and `build_pack` then took the first ten by `ord` and stopped, so the pack
was whichever sections happened to be written first.

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
from prepwright import corpus as CO          # noqa: E402
from prepwright import curriculum as K       # noqa: E402
from prepwright import diagnose as D         # noqa: E402
from prepwright import intake as I           # noqa: E402
from prepwright import state as S            # noqa: E402


DOCS = {
    "iso-42001": """# ISO/IEC 42001 AI management systems
## Scope of an AI management system
ISO/IEC 42001 specifies requirements for establishing an AI management system.
Clause 4 fixes the organisational context and the scope statement.
## The Annex A controls
Annex A lists controls for AI policy, roles, impact assessment, data governance
and third-party AI, stated control by control in a Statement of Applicability.
""",
    "nist-ai-rmf": """# NIST AI Risk Management Framework
## The four functions
The NIST AI RMF organises risk work into Govern, Map, Measure and Manage.
## Measure and metrics
Measure covers evaluation, red-teaming and monitoring of trustworthiness.
""",
    "au-privacy": """# Australian Privacy Principles
## APP 11 security of personal information
APP 11 requires reasonable steps to protect personal information from misuse,
interference, loss and unauthorised access, and to destroy it when not needed.
## Cross-border disclosure under APP 8
APP 8 makes an entity accountable for personal information disclosed overseas.
""",
}

GAPS = [
    ("g01", "Support compliance with ISO 42001 and emerging AI regulation",
     "never applied a management-system standard to a live AI system", "none", "1:20"),
    ("g02", "Awareness of AI governance frameworks (NIST AI RMF)",
     "read about it, never used it to structure an assessment", "shaky", "30:60"),
    ("g03", "Familiarity with data protection and privacy principles",
     "no privacy-law work on record", "none", "70:99"),
    ("g04", "Sole engineer of a real-time voice detection service",
     "a claim an interviewer will push on", "shaky", None),
]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-curriculum-")
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

    def _track(self, docs=None, gaps=GAPS, approve=True):
        track_id, _ = I.intake_from_text(
            "Skills\n\n- Support compliance with ISO 42001.\n",
            employer="Example", role_title="Analyst")
        handle = S.open_track(track_id, client_label="test")
        self.addCleanup(handle.close)
        for slug, text in sorted((docs if docs is not None else DOCS).items()):
            CO.ingest_text(handle, text, origin_url="https://example.test/" + slug,
                           slug=slug, vetting="primary", trust=5)
        for i, (gid, label, why, level, span) in enumerate(gaps, start=1):
            handle.add_gap(gid, i, label, why, level=level, jd_span=span)
            if approve:
                D.approve(handle, gid)
        return handle


class ASliceIsChosenForTheStepThatTeachesFromIt(Base):
    def test_the_document_title_decides_between_documents(self):
        """The defect this test exists for. "data protection and privacy
        principles" shares no word with "APP 11 security of personal
        information", so scoring headings and bodies alone pinned ISO 42001's
        Annex A controls while an Australian Privacy Principles document sat in
        the same corpus unread. Its TITLE shares two words with the query."""
        handle = self._track()
        built = K.build(handle, D.approved(handle))
        privacy = [s for s in built["steps"] if s["gap_id"] == "g03"][0]
        self.assertEqual(privacy["slices"][0]["doc_title"],
                         "Australian Privacy Principles")

    def test_each_step_gets_the_document_it_is_about(self):
        handle = self._track()
        built = K.build(handle, D.approved(handle))
        first = {s["gap_id"]: s["slices"][0]["doc_title"] for s in built["steps"]}
        self.assertEqual(first["g01"], "ISO/IEC 42001 AI management systems")
        self.assertEqual(first["g02"], "NIST AI Risk Management Framework")
        self.assertEqual(first["g03"], "Australian Privacy Principles")

    def test_a_gap_with_no_usable_corpus_is_deferred_not_pinned_to_a_near_miss(self):
        """A step pinned to material that barely scores is a step teaching from
        the wrong thing, and the candidate has no way to tell."""
        handle = self._track()
        built = K.build(handle, D.approved(handle))
        self.assertNotIn("g04", [s["gap_id"] for s in built["steps"]])
        self.assertIn("g04", [d["gap_id"] for d in built["deferred"]])
        self.assertIn("no corpus", built["deferred"][0]["reason"])

    def test_no_step_is_pinned_more_than_the_pack_can_carry(self):
        """build_pack walks step_slice by ord and stops at the cap, so anything
        past it is invisible while still looking pinned."""
        handle = self._track()
        built = K.build(handle, D.approved(handle))
        for step in built["steps"]:
            self.assertLessEqual(len(K.slices_of(handle, step["step_id"])),
                                 C.PACK_MAX_SECTIONS)

    def test_the_stored_slice_is_exactly_what_the_step_chose(self):
        handle = self._track()
        built = K.build(handle, D.approved(handle))
        for step in built["steps"]:
            stored = [(r["doc_id"], r["sec_id"])
                      for r in K.slices_of(handle, step["step_id"])]
            chosen = [(s["doc_id"], s["sec_id"]) for s in step["slices"]]
            self.assertEqual(stored, chosen,
                             "%s teaches from something it did not choose"
                             % step["step_id"])

    def test_the_pack_a_turn_sees_is_the_slice_in_relevance_order(self):
        handle = self._track()
        built = K.build(handle, D.approved(handle))
        for step in built["steps"]:
            pack = handle.build_pack(step["step_id"])
            self.assertEqual(pack["cites"], [s["cite"] for s in step["slices"]])

    def test_the_same_corpus_and_gaps_always_choose_the_same_slice(self):
        """Ties break on (doc_id, sec_id) ascending, so a plan is reproducible."""
        handle = self._track()
        index = K.corpus_index(handle)
        once = K.choose_slices(index, "privacy principles", "personal information")
        twice = K.choose_slices(index, "privacy principles", "personal information")
        self.assertEqual([s["cite"] for s in once], [s["cite"] for s in twice])

    def test_a_query_that_matches_nothing_chooses_nothing(self):
        handle = self._track()
        index = K.corpus_index(handle)
        self.assertEqual(
            K.choose_slices(index, "quantum chromodynamics", "lattice gauge"), [])

    def _wide_corpus(self, n_docs=4, per_doc=5):
        """More matching sections than one pack can carry, all on one topic."""
        docs = {}
        for d in range(n_docs):
            body = ["# Governance handbook volume %d" % d]
            for i in range(per_doc):
                body.append("## Governance control %d-%d" % (d, i))
                body.append("This section covers governance control practice,"
                            " governance evidence and governance review for"
                            " control number %d in volume %d." % (i, d))
            docs["gov-%d" % d] = "\n".join(body)
        return docs

    def test_the_pack_cap_bounds_what_is_pinned_even_when_more_matches(self):
        """The fixture corpus is smaller than the cap, so this needs a wider one
        or the assertion is true for a reason that has nothing to do with the
        rule."""
        handle = self._track(docs=self._wide_corpus())
        index = K.corpus_index(handle)
        self.assertGreater(len(index), C.PACK_MAX_SECTIONS,
                           "the fixture cannot exercise the cap")
        chosen = K.choose_slices(index, "governance control", "governance review")
        self.assertEqual(len(chosen), C.PACK_MAX_SECTIONS)
        built = K.build(handle, D.approved(handle))
        for step in built["steps"]:
            self.assertLessEqual(len(K.slices_of(handle, step["step_id"])),
                                 C.PACK_MAX_SECTIONS)

    def test_a_section_far_below_the_best_match_is_not_pinned_beside_it(self):
        """RELATIVE_FLOOR. Two sections can both clear MIN_TERMS while one is
        the answer and the other shares two words by accident. Pinning both
        fills the pack with near-misses that push real sections past the cap."""
        handle = self._track(docs={
            "strong": "# Australian Privacy Principles\n"
                      "## APP 11 and the privacy principles for personal data\n"
                      "The privacy principles require reasonable steps to protect"
                      " personal data. Privacy principles apply to every entity"
                      " handling personal data under these privacy principles.\n",
            "weak": "# Release engineering notes\n"
                    "## Build pipelines\n"
                    "A build pipeline runs tests. Privacy of build logs is"
                    " handled by principles of least access in the runner.\n"
                    "## Rollback\n"
                    "Rollback restores the previous artefact from the registry.\n",
        })
        index = K.corpus_index(handle)
        scored = K.score_sections(index, "privacy principles personal data")
        self.assertGreaterEqual(len(scored), 2,
                                "the fixture cannot exercise the floor")
        kept = K.relevant(scored)
        self.assertEqual([s["doc_title"] for s in kept],
                         ["Australian Privacy Principles"])

    def test_one_shared_common_word_in_a_body_is_not_a_match(self):
        """MIN_TERMS. A single common word is a coincidence, and pinning on it
        fills the pack with near-misses that push the real sections past the
        section cap."""
        handle = self._track()
        index = K.corpus_index(handle)
        self.assertEqual(K.choose_slices(index, "requirements", ""), [],
                         "one body word was enough to pin a section")


class OrderIsByDependencyAndIsDeterministic(Base):
    def test_a_prerequisite_is_taught_first(self):
        handle = self._track()
        built = K.build(handle, D.approved(handle),
                        edges=[{"gap_id": "g01", "needs_first": "g02"}])
        order = [s["gap_id"] for s in built["steps"]]
        self.assertLess(order.index("g02"), order.index("g01"))

    def test_without_edges_the_candidates_own_order_is_kept(self):
        handle = self._track()
        built = K.build(handle, D.approved(handle))
        self.assertEqual([s["gap_id"] for s in built["steps"]],
                         ["g01", "g02", "g03"])

    def test_a_cycle_is_broken_in_the_open_rather_than_hanging(self):
        """A cycle is what a model produces when it declares importance as
        dependency. A plan that silently reorders itself around a contradiction
        is worse than one that says which contradiction it found."""
        handle = self._track()
        built = K.build(handle, D.approved(handle),
                        edges=[{"gap_id": "g01", "needs_first": "g02"},
                               {"gap_id": "g02", "needs_first": "g01"}])
        self.assertEqual(len(built["steps"]), 3)
        self.assertTrue(built["edges"]["cycles_broken"])

    def test_an_edge_naming_a_gap_that_does_not_exist_is_dropped_and_named(self):
        handle = self._track()
        built = K.build(handle, D.approved(handle),
                        edges=[{"gap_id": "g01", "needs_first": "g77"}])
        self.assertEqual(len(built["steps"]), 3)
        self.assertTrue(any("not on the list" in d["why"]
                            for d in built["edges"]["dropped"]))

    def test_a_self_edge_is_dropped_rather_than_deadlocking(self):
        handle = self._track()
        built = K.build(handle, D.approved(handle),
                        edges=[{"gap_id": "g01", "needs_first": "g01"}])
        self.assertEqual(len(built["steps"]), 3)

    def test_a_malformed_edge_cannot_take_the_plan_down(self):
        handle = self._track()
        built = K.build(handle, D.approved(handle),
                        edges=[None, "g01", 7, {}, {"gap_id": "g01"}])
        self.assertEqual(len(built["steps"]), 3)


class TiersAndTheCut(Base):
    def test_an_unlearned_posting_requirement_is_core(self):
        self.assertEqual(
            K.tier_for({"level": "none", "jd_span": "1:2"}), "core")

    def test_an_undefended_resume_claim_is_never_core(self):
        """It loses the room once you are already in it, which is later than the
        screen, so it never outranks a stated requirement."""
        self.assertEqual(K.tier_for({"level": "none", "jd_span": None}), "depth")
        self.assertEqual(K.tier_for({"level": "shaky", "jd_span": None}), "reference")

    def test_something_already_solid_is_reference(self):
        self.assertEqual(K.tier_for({"level": "solid", "jd_span": "1:2"}), "reference")

    def test_the_cut_takes_from_the_tail_so_no_prerequisite_is_orphaned(self):
        handle = self._track()
        built = K.build(handle, D.approved(handle), max_steps=2,
                        edges=[{"gap_id": "g01", "needs_first": "g02"}])
        self.assertEqual(len(built["steps"]), 2)
        order = [s["gap_id"] for s in built["steps"]]
        self.assertEqual(order[0], "g02")
        self.assertIn("beyond the 2-step plan",
                      [d["reason"] for d in built["deferred"]])

    def test_a_cut_gap_keeps_its_row(self):
        """Deferral is never deletion. DESIGN-state-corpus.md:405 forbids any
        automatic transition from dropping a gap."""
        handle = self._track()
        K.build(handle, D.approved(handle), max_steps=1)
        self.assertEqual(len(D.gap_list(handle)), len(GAPS))


class NothingIsPlannedFromAGapTheCandidateHasNotApproved(Base):
    def test_a_proposed_gap_is_refused_by_name_before_any_write(self):
        """step.gap_id carries a foreign key and foreign keys are on, so an
        unchecked gap fails inside add_step with "FOREIGN KEY constraint failed"
        and nothing else, halfway through a batch that has no enclosing
        transaction, leaving the track holding half a curriculum."""
        handle = self._track(approve=False)
        with self.assertRaises(K.CurriculumRefused) as cm:
            K.build(handle, [{"gap_id": g[0]} for g in GAPS])
        self.assertIn("has not approved", str(cm.exception))
        self.assertEqual(K.steps_of(handle), [])

    def test_a_gap_that_is_not_on_this_track_is_refused_by_name(self):
        handle = self._track()
        with self.assertRaises(K.CurriculumRefused) as cm:
            K.build(handle, [{"gap_id": "g99", "label": "x", "why": "y",
                              "level": "none"}])
        self.assertIn("not on this track", str(cm.exception))
        self.assertEqual(K.steps_of(handle), [])

    def test_a_declined_gap_is_not_planned(self):
        handle = self._track(approve=False)
        for gid, _l, _w, _v, _s in GAPS:
            D.decline(handle, gid)
        with self.assertRaises(K.CurriculumRefused):
            K.build(handle, [{"gap_id": g[0]} for g in GAPS])

    def test_an_empty_gap_list_is_refused(self):
        handle = self._track()
        with self.assertRaises(K.CurriculumRefused):
            K.build(handle, [])

    def test_an_empty_corpus_defers_everything_rather_than_writing_empty_steps(self):
        """A step with nothing to teach from is not a step. Created anyway it
        would sit at evidence_state 'full' and refuse itself at the moment the
        candidate opened it."""
        handle = self._track(docs={})
        built = K.build(handle, D.approved(handle))
        self.assertEqual(built["steps"], [])
        self.assertEqual(len(built["deferred"]), len(GAPS))
        self.assertEqual(K.steps_of(handle), [])


if __name__ == "__main__":
    unittest.main()
