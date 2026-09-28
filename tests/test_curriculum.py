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

    def test_every_step_id_is_a_step_key_the_teaching_route_accepts(self):
        """The contract that makes a plan teachable. The first version minted
        "s01", which pagestate.STEP_KEY_RE rejects, so every route that teaches
        refused the step before reading the body: a plan nothing could open."""
        from prepwright import pagestate as PS
        handle = self._track()
        built = K.build(handle, D.approved(handle))
        self.assertTrue(built["steps"], "the fixture built no steps")
        for step in built["steps"]:
            self.assertTrue(PS.STEP_KEY_RE.match(step["step_id"]),
                            "%r is not a step key" % step["step_id"])
        for step in K.steps_of(handle):
            self.assertTrue(PS.STEP_KEY_RE.match(step["step_id"]))

    def test_step_keys_stay_valid_to_the_store_cap(self):
        from prepwright import pagestate as PS
        for i in range(1, C.MAX_STEPS + 1):
            self.assertTrue(PS.STEP_KEY_RE.match(K.step_key(i)),
                            "step %d mints an unusable key" % i)

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
        """MIN_TERMS, as it still applies. A single common word is a
        coincidence, and pinning on it fills the pack with near-misses that
        push the real sections past the section cap.

        The query here carries two words on purpose. This test used a one-word
        query until 2026-09-09 and so encoded a floor that a one-word goal can
        never satisfy: see the sibling test below for why that was a defect
        rather than the rule working.
        """
        handle = self._track()
        index = K.corpus_index(handle)
        self.assertEqual(K.choose_slices(index, "requirements elephants", ""), [],
                         "one body word was enough to pin a section")

    def test_a_one_word_goal_can_still_match_something(self):
        """The floor scales to the goal, because a flat 2 is UNSATISFIABLE for a
        one-word query: no section can share two words of a one-word goal.

        The consequence was not a quiet miss. `select` refused the whole file and
        told the candidate their own resource "shares no vocabulary with the
        goal", which is false and unactionable: rewording cannot add a second
        word to a goal that is legitimately one word. "Kubernetes" is a
        reasonable thing to prepare for.
        """
        handle = self._track()
        index = K.corpus_index(handle)
        self.assertEqual(K.term_floor("requirements"), 1)
        self.assertEqual(K.term_floor("data protection principles"), 2)
        self.assertTrue(K.choose_slices(index, "requirements", ""),
                        "a one-word goal still matches nothing")


class RarityIsCountedOverTheFieldsThatAreScored(Base):
    """df must count doc_title, because score_sections weights doc_title.

    It did not, so a term living in document titles and nowhere else was absent
    from df entirely, `df.get(term, 1)` returned the sentinel meant for a term in
    exactly ONE section, and the rarity weight handed the corpus maximum to the
    word shared by the MOST documents. On a corpus of one multi-section standard
    every section cleared the relative floor together and filled the pack,
    evicting the sections that actually matched.
    """

    def test_a_term_in_every_document_title_is_not_treated_as_rare(self):
        index = [{"doc_id": "D%02d" % (i // 4), "sec_id": "s%02d" % (i % 4),
                  "heading": "APP 11 security of personal information",
                  "concept": "", "body": "Reasonable steps to protect holdings.",
                  "doc_title": "Australian Privacy Principles",
                  "vetting": "primary", "trust": 5}
                 for i in range(40)]
        df = K.document_frequency(index)
        self.assertGreaterEqual(
            df.get("privacy", 0), 40,
            "a term in forty document titles was counted fewer than forty times")
        # The sentinel `df.get(term, 1)` returns for an unmeasured term is 1,
        # which is also the count for a term in exactly one section. The whole
        # defect was that those two states were indistinguishable here.
        self.assertNotEqual(df.get("privacy", 1), 1,
                            "the title term was left on the one-section sentinel")

    def test_the_common_title_term_does_not_outrank_a_genuinely_rare_one(self):
        index = [{"doc_id": "D%02d" % i, "sec_id": "s01",
                  "heading": "APP 11 security of personal information",
                  "concept": "", "body": "Reasonable steps to protect holdings.",
                  "doc_title": "Australian Privacy Principles",
                  "vetting": "primary", "trust": 5} for i in range(10)]
        index.append({"doc_id": "D99", "sec_id": "s01",
                      "heading": "Reidentification of de-identified data",
                      "concept": "", "body": "Reidentification risk is assessed.",
                      "doc_title": "Australian Privacy Principles",
                      "vetting": "primary", "trust": 5})
        chosen = K.choose_slices(index, "privacy reidentification", "")
        self.assertTrue(chosen)
        self.assertEqual(chosen[0]["doc_id"], "D99",
                         "the genuine match did not win")
        self.assertEqual(
            len(chosen), 1,
            "ten copies of the same section cleared the floor and filled the pack")


class TheDiagnosticReasonIsNotTheLearningObjective(Base):
    """`why` says why this is a gap. It is not what the step teaches.

    Both jobs read it before this was separated: the tutor read it out as the
    task, and its words were concatenated into the query that decides which
    sections the step pins. For an ungraded probe that string is literally
    "No answer given.", so every step's task was that sentence and every step's
    retrieval query carried the words "no", "answer" and "given".
    """

    NOISE = [("g01", "Support compliance with ISO 42001 and SOC2",
              "No answer given.", "none", None)]
    REAL = [("g01", "Support compliance with ISO 42001 and SOC2",
             "Named the standard but could not say what an AI management system"
             " certification actually requires of an organisation.", "shaky", None)]

    def test_an_uninformative_why_reaches_neither_the_task_nor_the_query(self):
        handle = self._track(gaps=self.NOISE)
        built = K.build(handle, D.gap_list(handle))
        self.assertTrue(built["steps"], "nothing was planned at all")
        step = built["steps"][0]
        self.assertNotIn("No answer given", step["objective"])
        self.assertIn("Explain this in your own words", step["objective"])
        # The objective is the template alone. Nothing the diagnostic said about
        # why this was a gap survives into it, so nothing it said reaches the
        # tutor as the task or the scorer as query terms.
        self.assertEqual(
            step["objective"],
            "Explain this in your own words and answer one follow-up on it.")

    def test_an_informative_why_is_kept_because_it_is_about_the_topic(self):
        handle = self._track(gaps=self.REAL)
        built = K.build(handle, D.gap_list(handle))
        step = built["steps"][0]
        self.assertIn("What is thin about it now", step["objective"])
        self.assertIn("AI management system", step["objective"])


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


# A posting that comes back to g02's requirement twice, and fit reports shaped
# like the University of Example walk's (a requirement matrix, one red team).
PRESSING = ("Skills\n\n"
            "- Awareness of AI governance frameworks (NIST AI RMF).\n"
            "- Structure every assessment against NIST AI RMF governance frameworks.\n")
UNIEXAMPLE_FIT = {
    "rows": [{"requirement": "PhD in Computer Science or related discipline"},
             {"requirement": "Identify or support research funding"},
             {"requirement": "Research publications in leading venues"},
             {"requirement": "Subject development and teaching"}],
    "met": [{"requirement": "Subject development and teaching",
             "evidence": "Unit Coordinator responsible for Python curriculum"}],
    "red_team": ["The PhD-in-hand wording is the clearest rejection risk, and there"
                 " is no funding development record."],
}


class WhatThePanelPressesMovesUpOneTier(Base):
    """`tier_for` ranked by gap level and by whether the posting states the gap.
    On the 2026-09-24 walk (UniExample) nothing in the rank said how hard the
    posting leans on a gap or whether the red team expects a panel to raise it.
    `emphasis` counts both, and a pressed gap moves up one tier."""

    def test_a_gap_the_posting_comes_back_to_moves_up_one_tier(self):
        pressed = self._track()
        built = K.build(pressed, D.approved(pressed), posting=PRESSING)
        g02 = next(s for s in built["steps"] if s["gap_id"] == "g02")
        self.assertEqual(g02["tier"], "core")
        self.assertEqual(g02["pressed"], ["the posting comes back to it 2 times"])
        stored = {r["gap_id"]: r["tier"] for r in K.steps_of(pressed)}
        self.assertEqual(stored["g02"], "core")
        plain = self._track()
        built = K.build(plain, D.approved(plain))
        self.assertEqual(next(s["tier"] for s in built["steps"]
                              if s["gap_id"] == "g02"), "depth")

    def test_a_weak_row_the_red_team_names_moves_up_on_one_word_of_its_own(self):
        gap = {"label": "Identify or support research funding", "jd_span": "fit:2",
               "level": "shaky"}
        self.assertEqual(K.emphasis(gap, "", UNIEXAMPLE_FIT),
                         (1, ["red team objection 1 names funding"]))
        self.assertEqual(K.tier_for(gap, 1), "core")

    def test_a_word_two_rows_share_is_no_rows_own(self):
        """"research" sits in two rows of the matrix, so an objection naming it
        points at neither of them."""
        fit = dict(UNIEXAMPLE_FIT, red_team=["There is no research record at this level."])
        gap = {"label": "Research publications in leading venues", "jd_span": "fit:3",
               "level": "shaky"}
        self.assertEqual(K.emphasis(gap, "", fit), (0, []))

    def test_a_hyphenated_objection_still_names_its_requirement(self):
        gap = {"label": "PhD in Computer Science or related discipline",
               "jd_span": "fit:1", "level": "shaky"}
        stress, why = K.emphasis(gap, "", UNIEXAMPLE_FIT)
        self.assertEqual(stress, 1)
        self.assertIn("phd", why[0])

    def test_one_shared_word_with_a_claim_is_a_coincidence(self):
        """The claim answers "Subject development and teaching"; the objection
        says "funding development". A claim needs the clause floor."""
        gap = {"label": "Unit Coordinator responsible for Python curriculum",
               "jd_span": None, "level": "shaky"}
        self.assertEqual(K.emphasis(gap, "", UNIEXAMPLE_FIT), (0, []))

    def test_an_objection_is_the_question_the_panel_is_expected_to_ask(self):
        gap = {"label": UNIEXAMPLE_FIT["red_team"][0], "jd_span": None, "level": "shaky"}
        self.assertEqual(K.emphasis(gap, "", UNIEXAMPLE_FIT),
                         (1, ["red team objection 1: the panel is expected to raise it"]))

    def test_logistics_are_never_pressed(self):
        fit = {"rows": [{"requirement": "Submit resume and selection criteria responses"}],
               "red_team": ["The selection criteria responses are thin."]}
        gap = {"label": "Submit resume and selection criteria responses",
               "jd_span": "fit:1", "level": "shaky"}
        self.assertEqual(K.emphasis(gap, "", fit), (0, []))
        # A claim is read as the requirement it answers.
        fit = {"met": [{"requirement": "Valid work rights, no sponsorship",
                        "evidence": "Hobart-based permanent resident"}],
               "red_team": ["Valid work rights without sponsorship are unproven."]}
        self.assertEqual(K.emphasis({"label": "Hobart-based permanent resident",
                                     "jd_span": None}, "", fit), (0, []))

    def test_an_objection_that_mentions_the_paperwork_is_still_the_objection(self):
        """ADR 0008: objections are not screened as logistics. Review of
        f03b5c4: a short objection naming the selection criteria in passing was
        classed as logistics, and the panel's likeliest question was never
        pressed."""
        objection = ("No PhD in hand and no funding record; the selection criteria"
                     " responses do not address either.")
        self.assertEqual(K.emphasis({"label": objection, "jd_span": None}, "",
                                    {"red_team": [objection]}),
                         (1, ["red team objection 1: the panel is expected to raise it"]))

    def test_a_word_that_holds_a_process_word_is_not_logistics(self):
        """Review of f03b5c4: "submit" matched "submitting" and "visa" matched
        "advisable", so real requirements were never pressed."""
        fit = {"rows": [{"requirement": "Track record of submitting first-author"
                                        " papers to CCS or S&P"}],
               "red_team": ["No first-author CCS paper is on the page."]}
        gap = {"label": fit["rows"][0]["requirement"], "jd_span": "fit:1",
               "level": "shaky"}
        stress, _why = K.emphasis(gap, "", fit)
        self.assertEqual(stress, 1)
        self.assertFalse(K.is_process("Knowing when a manual review is advisable"))
        self.assertTrue(K.is_process("Visa sponsorship is not available"))
        from prepwright import rehearse as RH      # one rule for both modules
        self.assertFalse(RH._is_process("Track record of submitting papers"))

    def test_what_the_posting_marks_desirable_is_not_moved_up(self):
        posting = ("Desirable\n\n"
                   "- Awareness of AI governance frameworks (NIST AI RMF).\n"
                   "- NIST AI RMF governance frameworks experience.\n")
        gap = {"label": GAPS[1][1], "jd_span": "30:60", "level": "shaky"}
        stress, why = K.emphasis(gap, posting)
        self.assertEqual(stress, 0)
        self.assertIn("the posting marks it desirable", why)
        self.assertEqual(K.tier_for(gap, stress), "depth")
        # A heading ending in a colon is still a heading (review of f03b5c4).
        for head in ("Desirable:", "Preferred Qualifications:"):
            colon = posting.replace("Desirable", head, 1)
            self.assertEqual(K.emphasis(gap, colon)[0], 0, head)

    def test_it_moves_one_tier_at_most_never_above_core_and_never_down(self):
        for level in ("none", "shaky", "solid"):
            for span in ("1:2", None):
                gap = {"level": level, "jd_span": span}
                base = K.tier_for(gap)
                for stress in (-1, 0, 1, 5):
                    got = K.tier_for(gap, stress)
                    rank = K.TIERS.index
                    self.assertLessEqual(rank(got), rank(base))
                    self.assertLessEqual(rank(base) - rank(got), 1)
                    if stress < 1:
                        self.assertEqual(got, base)


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
