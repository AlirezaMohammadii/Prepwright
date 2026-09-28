"""Source discovery: the bounds, and what a nomination is not allowed to claim.

The app now goes looking for its own study sources, which puts a model's output
at the front of the pipeline that fills the corpus. Everything here exists
because of that one change:

  * every loop is driven by a model's answer, so every loop needs a bound a
    model cannot lengthen;
  * a nomination is a URL and nothing else, so the labels that decide how much a
    source is trusted have to be decided by where it actually lives;
  * a date the tutor will repeat has to come off the page, never out of memory.

No network. `fetch_fn` is injected in every test, which is also the seam the
route uses, so nothing here is testing a different code path from production.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prepwright import research as R          # noqa: E402
from prepwright import corpus as CO          # noqa: E402


GAPS = [{"gap_id": "g%02d" % i,
         "label": "AI governance frameworks ISO 42001 and NIST AI RMF",
         "why": "the role asks for awareness of AI governance frameworks"}
        for i in range(1, 6)]

TEACHING_PAGE = (
    "# Governance\n\n## What the framework asks for\n\n"
    + "ISO 42001 governance frameworks awareness. The role asks for AI "
      "governance and risk management. " * 10)


def page(url, body=TEACHING_PAGE, ctype="text/markdown"):
    return {"asked_url": url, "final_url": url, "status": 200,
            "content_type": ctype, "bytes": len(body), "sha256": "0" * 64,
            "fetched_utc": "2026-01-01T00:00:00Z", "redirects": 0,
            "body": body.encode("utf-8")}


def candidates(*urls, **kw):
    vet = kw.get("vetting", "primary")
    return json.dumps({"candidates": [
        {"url": u, "title": "t", "publisher": "p", "vetting": vet,
         "trust": 5, "why": "w"} for u in urls]})


class ANominationCannotInflateItsOwnRank(unittest.TestCase):
    """The nominator proposes a label. The host it lives on decides.

    Without this the ranking is whatever the model says it is, and the whole
    ledger becomes decoration: any blog can call itself the standard.
    """

    def test_primary_survives_only_on_a_standards_body_or_a_regulator(self):
        for host in ("www.nist.gov", "csrc.nist.gov", "oaic.gov.au", "iso.org",
                     "mit.edu", "www.oecd.org", "cyber.gov.au"):
            self.assertEqual(R.vetting_for(host, "primary"), "primary", host)

    def test_primary_is_refused_to_everyone_else_however_it_is_claimed(self):
        for host in ("someblog.com", "medium.com", "example.io",
                     "nist.gov.attacker.com", "notnist.gov.co"):
            self.assertEqual(R.vetting_for(host, "primary"), "secondary", host)

    def test_a_gov_suffix_inside_the_host_is_not_a_gov_host(self):
        """`evil.gov.attacker.com` ends in .com. The suffix test reads the END."""
        self.assertFalse(R.institutional("evil.gov.attacker.com"))
        self.assertTrue(R.institutional("evil.gov"))

    def test_an_admitted_community_source_is_not_promoted(self):
        self.assertEqual(R.vetting_for("www.nist.gov", "community"), "community")

    def test_trust_is_capped_by_the_label_the_host_allowed(self):
        self.assertEqual(R.trust_for("primary", 5), 5)
        self.assertEqual(R.trust_for("secondary", 5), 4)
        self.assertEqual(R.trust_for("community", 5), 2)
        self.assertEqual(R.trust_for("community", 1), 1)
        self.assertEqual(R.trust_for("primary", "not a number"), 3)


class ADateComesOffThePageOrNotAtAll(unittest.TestCase):
    """None is a real answer, and the tutor is told to say so.

    A remembered version number is the most confident-sounding wrong thing a
    tutor can say, and the candidate repeats it in the room.
    """

    def test_a_labelled_date_is_read(self):
        self.assertEqual(R.published_on_from("Last updated: 2026-03-11 x"),
                         "2026-03-11")
        self.assertEqual(R.published_on_from("Published 14 March 2026 by us"),
                         "2026-03-14")
        self.assertEqual(R.published_on_from("Updated March 2026."), "2026-03")

    def test_a_copyright_year_is_not_a_publication_date(self):
        self.assertIsNone(R.published_on_from("Copyright 2026 Example Corp."))
        self.assertIsNone(R.published_on_from("In 2019 the standard changed."))
        self.assertIsNone(R.published_on_from("A page that never says."))


class CoverageDecidesWhetherThePageAnswered(unittest.TestCase):
    def test_an_off_topic_page_shares_no_vocabulary_with_the_gap(self):
        want = R.gap_terms(GAPS[0])
        hits, ratio, _ = R.coverage(
            "Our cafe serves excellent coffee every morning in Melbourne.", want)
        self.assertEqual(hits, 0)
        self.assertEqual(ratio, 0.0)

    def test_an_on_topic_page_clears_both_floors(self):
        want = R.gap_terms(GAPS[0])
        hits, ratio, _ = R.coverage(TEACHING_PAGE, want)
        self.assertGreaterEqual(hits, R.COVERAGE_MIN_TERMS)
        self.assertGreaterEqual(ratio, R.COVERAGE_MIN_RATIO)

    def test_a_page_that_answered_but_is_off_topic_is_discarded_with_a_reason(self):
        def nominate(_s, _p, _sch):
            return candidates("https://www.nist.gov/cafe")
        out = R.discover(
            GAPS[:1], nominate, lambda f, g: "D01",
            fetch_fn=lambda u: page(u, "# Cafe\n\n## Coffee\n\n" + "espresso " * 200))
        self.assertEqual(out["stored"], 0)
        self.assertTrue(out["discarded"])
        self.assertEqual(out["discarded"][0]["stage"], "coverage")
        self.assertIn("does not cover this", out["discarded"][0]["why"])


class EveryLoopTerminatesUnderAHostileProvider(unittest.TestCase):
    """Each loop below is driven by a model's answer. None may be unbounded.

    These are not hypothetical: the provider is a subprocess whose output is
    parsed, and a provider that has been prompt-injected by a fetched page is
    exactly a provider that returns the same URL forever.
    """

    def _failing_fetch(self, counter):
        def fetch(url):
            counter["n"] += 1
            raise R.FetchFailed("%s answered 500" % url)
        return fetch

    def test_a_provider_repeating_one_url_forever_fetches_it_once(self):
        calls, fetches = {"n": 0}, {"n": 0}

        def nominate(_s, _p, _sch):
            calls["n"] += 1
            return candidates("https://www.nist.gov/same")

        out = R.discover(GAPS, nominate, lambda f, g: None,
                         fetch_fn=self._failing_fetch(fetches))
        self.assertEqual(fetches["n"], 1, "the same URL was fetched more than once")
        self.assertLessEqual(calls["n"], len(GAPS) * R.DISCOVER_ROUNDS)
        self.assertEqual(out["stored"], 0)

    def test_a_provider_flooding_candidates_is_cut_to_the_per_gap_limit(self):
        fetches = {"n": 0}
        run = {"n": 0}

        def nominate(_s, _p, _sch):
            run["n"] += 1
            return candidates(*["https://www.nist.gov/p%d-%d" % (run["n"], i)
                                for i in range(500)])

        R.discover(GAPS, nominate, lambda f, g: None, per_gap=4,
                   fetch_fn=self._failing_fetch(fetches))
        self.assertLessEqual(fetches["n"], len(GAPS) * R.DISCOVER_ROUNDS * 4)

    def test_a_provider_returning_garbage_stops_rather_than_looping(self):
        out = R.discover(GAPS, lambda _s, _p, _sch: "not json at all",
                         lambda f, g: None,
                         fetch_fn=self._failing_fetch({"n": 0}))
        self.assertEqual(out["stored"], 0)
        self.assertEqual(out["gaps"][0]["stopped"],
                         "nothing new was nominated for this gap")

    def test_a_provider_that_cannot_be_reached_stops_that_gap(self):
        def nominate(_s, _p, _sch):
            raise RuntimeError("the CLI is not logged in")
        out = R.discover(GAPS[:1], nominate, lambda f, g: None,
                         fetch_fn=self._failing_fetch({"n": 0}))
        self.assertIn("provider could not be asked", out["gaps"][0]["stopped"])

    def test_max_docs_is_a_ceiling_on_the_whole_run(self):
        seq = {"n": 0}

        def nominate(_s, _p, _sch):
            seq["n"] += 1
            return candidates("https://www.nist.gov/ok%d" % seq["n"])

        stored = {"n": 0}

        def ingest(_found, _gap):
            stored["n"] += 1
            return "D%02d" % stored["n"]

        out = R.discover(GAPS, nominate, ingest, max_docs=2, fetch_fn=page)
        self.assertEqual(out["stored"], 2)
        self.assertEqual(stored["n"], 2, "ingest ran past the ceiling")

    def test_breadth_first_by_default_one_source_per_gap(self):
        """48 documents, twenty gaps. A generous early gap starves a later one."""
        seq = {"n": 0}

        def nominate(_s, _p, _sch):
            seq["n"] += 1
            return candidates(*["https://www.nist.gov/a%d" % seq["n"],
                                "https://www.nist.gov/b%d" % seq["n"],
                                "https://www.nist.gov/c%d" % seq["n"]])

        out = R.discover(GAPS, nominate, lambda f, g: "D01", fetch_fn=page)
        for rec in out["gaps"]:
            self.assertLessEqual(len(rec["stored"]), 1, rec["gap_id"])


class AStoreRefusalIsRecordedRatherThanRaised(unittest.TestCase):
    def test_an_ingest_that_raises_becomes_a_discard_with_its_reason(self):
        def nominate(_s, _p, _sch):
            return candidates("https://www.nist.gov/ok")

        def ingest(_found, _gap):
            raise RuntimeError("documents 49 exceeds the cap of 48")

        out = R.discover(GAPS[:1], nominate, ingest, fetch_fn=page)
        self.assertEqual(out["stored"], 0)
        self.assertEqual(out["discarded"][0]["stage"], "store")
        self.assertIn("exceeds the cap", out["discarded"][0]["why"])

    def test_a_page_with_no_citable_section_is_named_as_such(self):
        def nominate(_s, _p, _sch):
            return candidates("https://www.nist.gov/ok")

        out = R.discover(GAPS[:1], nominate, lambda f, g: None, fetch_fn=page)
        self.assertEqual(out["discarded"][0]["stage"], "sections")
        self.assertIn("no headings", out["discarded"][0]["why"])


class AnUntriedUrlIsNotABlacklistedUrl(unittest.TestCase):
    """`seen` means "this has been tried". The per-gap cap does not try them all.

    Marking every nomination seen meant a good source the cap merely had not got
    to in round one could not be re-offered in round two, and the model was told
    to avoid it as though it had failed.
    """

    def test_candidates_cut_by_the_limit_can_be_offered_again(self):
        seen = set()
        raw = [{"url": "https://www.nist.gov/%d" % i, "title": "t",
                "publisher": "p", "vetting": "primary", "trust": 5, "why": "w"}
               for i in range(6)]
        kept = R._clean_candidates(raw, seen, 2)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(seen), 2, "an unfetched candidate was marked tried")
        again = R._clean_candidates(raw, seen, 2)
        self.assertEqual(len(again), 2, "the untried candidates were blacklisted")
        self.assertNotIn(again[0]["url"], [k["url"] for k in kept])


class OnlyHttpsUrlsSurviveCleaning(unittest.TestCase):
    def test_a_non_https_or_hostless_nomination_never_reaches_a_fetch(self):
        fetched = []

        def nominate(_s, _p, _sch):
            return json.dumps({"candidates": [
                {"url": "http://www.nist.gov/plain", "title": "t",
                 "publisher": "p", "vetting": "primary", "trust": 5, "why": "w"},
                {"url": "file:///etc/passwd", "title": "t", "publisher": "p",
                 "vetting": "primary", "trust": 5, "why": "w"},
                {"url": "not a url", "title": "t", "publisher": "p",
                 "vetting": "primary", "trust": 5, "why": "w"},
            ]})

        def fetch(url):
            fetched.append(url)
            raise R.FetchFailed("should never be reached")

        out = R.discover(GAPS[:1], nominate, lambda f, g: None, fetch_fn=fetch)
        self.assertEqual(fetched, [], "a non-https nomination reached the fetcher")
        self.assertEqual(out["stored"], 0)


# An arXiv /abs/ page as the fetch sees it: the paper's own block (title h1,
# authors, the abstract in a <blockquote>, the metadata table) sits above the
# first h2, and every h2 and h3 below it is page chrome.
ARXIV_GAP = {"gap_id": "g01",
             "label": "Audio deepfake detection and spoofing countermeasures",
             "why": "the role asks for self-supervised speech front ends and"
                    " equal error rate evaluation"}
ARXIV_ABS = (
    '<!DOCTYPE html><html lang="en"><head>'
    '<title>[2403.01234] Detecting Audio Deepfakes with Self-Supervised Features</title>'
    '<meta name="citation_title" content="Detecting Audio Deepfakes with Self-Supervised Features"/>'
    '<meta name="citation_abstract" content="Audio deepfake detection models ..."/>'
    '</head><body><header><a href="#content">Skip to main content</a>'
    '<form action="https://arxiv.org/search"><input name="query"></form></header>'
    '<main><div id="abs-outer"><div class="leftcolumn">'
    '<div class="subheader"><h1>Computer Science &gt; Sound</h1></div>'
    '<div id="abs"><div class="dateline">[Submitted on 2 Mar 2024]</div>'
    '<h1 class="title mathjax"><span class="descriptor">Title:</span>'
    'Detecting Audio Deepfakes with Self-Supervised Features</h1>'
    '<div class="authors"><span class="descriptor">Authors:</span>'
    '<a href="/a/doe_j_1">Jane Doe</a>, <a href="/a/roe_r_1">Richard Roe</a></div>'
    '<blockquote class="abstract mathjax"><span class="descriptor">Abstract:</span>'
    'Audio deepfake detection models trained on one spoofing corpus generalise'
    ' poorly to unseen attacks. We evaluate self-supervised speech'
    ' representations as front ends for spoofing countermeasures. A frozen front'
    ' end reduces the equal error rate on In-the-Wild from 37.8% to 7.4%.'
    '</blockquote>'
    '<div class="metatable"><table><tr><td>Subjects:</td>'
    '<td>Sound (cs.SD); Cryptography and Security (cs.CR)</td></tr></table></div>'
    '</div><div class="submission-history"><h2>Submission history</h2>'
    'From: Jane Doe [view email]<br/>[v1] Sat, 2 Mar 2024 10:11:12 UTC (812 KB)'
    '</div></div><div class="extra-services"><div class="full-text">'
    '<h2>Access Paper:</h2><ul><li><a href="/pdf/2403.01234">View PDF</a></li>'
    '<li><a href="/src/2403.01234">TeX Source</a></li></ul></div>'
    '<div class="extra-ref-cite"><h3>References &amp; Citations</h3><ul>'
    '<li>NASA ADS</li><li>Google Scholar</li><li>Semantic Scholar</li></ul></div>'
    '<div class="bib-modal" hidden="true"><h2>BibTeX formatted citation</h2>'
    'loading... Data provided by:</div>'
    '<div class="bookmarks"><div><h3>Bookmark</h3></div>'
    '<a href="https://www.bibsonomy.org/"><img alt="BibSonomy logo"/></a></div>'
    '</div></div><div id="labstabs"><h1>arXivLabs: experimental projects with'
    ' community collaborators</h1><p>arXivLabs is a framework that allows'
    ' collaborators to develop and share new arXiv features directly on our'
    ' website.</p></div></main><footer><a href="/about">About</a></footer>'
    '</body></html>')


def _one_arxiv_run(body, gap=None):
    stored_texts = []

    def nominate(_s, _p, _sch):
        return candidates("https://arxiv.org/abs/2403.01234", vetting="primary")

    def ingest(found, _gap):
        stored_texts.append(found["text"])
        return "D01" if CO.parse_loose(found["text"])[1] else None

    out = R.discover([gap or ARXIV_GAP], nominate, ingest,
                     fetch_fn=lambda u: page(u, body, ctype="text/html"))
    return out, stored_texts


class AnAbstractAboveTheFirstHeadingIsKeptNotDropped(unittest.TestCase):
    """The 2026-09-24 walk stored an arXiv /abs/ page as seven sections of chrome
    ("Submission history", "Access Paper:", "BibTeX", "Bookmark") and dropped the
    abstract: it sits under the page's h1, above the first h2, and
    `corpus.parse_loose` drops prose before the first `## `."""

    def test_an_arxiv_abs_page_is_stored_with_its_abstract_first(self):
        out, texts = _one_arxiv_run(ARXIV_ABS)
        self.assertEqual(out["stored"], 1, out["discarded"])
        title, pairs = CO.parse_loose(texts[0])
        self.assertEqual(title, "[2403.01234] Detecting Audio Deepfakes with"
                                " Self-Supervised Features")
        self.assertEqual(pairs[0][0], R.LEAD_HEADING)
        self.assertIn("equal error rate", pairs[0][1])
        # The host rule still decides the label: arxiv.org is not institutional.
        landed = out["gaps"][0]["stored"][0]
        self.assertEqual((landed["vetting"], landed["trust"]), ("secondary", 4))

    def test_a_blog_s_opening_definition_is_kept(self):
        text = R.text_of(page("https://example.com/dp", (
            "<html><head><title>Differential privacy explained</title></head>"
            "<body><article><h1>Differential privacy explained</h1>"
            "<p>Differential privacy bounds what one record can change.</p>"
            "<h2>Related posts</h2><ul><li>Our newsletter</li></ul>"
            "</article></body></html>"), ctype="text/html"))
        pairs = CO.parse_loose(text)[1]
        self.assertEqual(pairs[0][0], R.LEAD_HEADING)
        self.assertIn("bounds what one record can change", pairs[0][1])

    def test_the_title_tag_is_not_read_as_page_text(self):
        text = R.text_of(page("https://arxiv.org/abs/2403.01234", ARXIV_ABS,
                              ctype="text/html"))
        pairs = CO.parse_loose(text)[1]
        self.assertEqual(pairs[0][0], R.LEAD_HEADING)
        self.assertNotIn("[2403.01234]", pairs[0][1])

    def test_a_page_with_no_second_level_heading_is_still_refused(self):
        text = R.text_of(page("https://example.com/flat", (
            "<html><head><title>T</title></head><body><h1>Epsilon</h1><p>"
            + "Differential privacy bounds the privacy loss. " * 20
            + "</p></body></html>"), ctype="text/html"))
        self.assertEqual(CO.parse_loose(text)[1], [])


# The same page with a realistic abstract: over the 900-character section cap
# once the dateline, the title and the authors ride in the lead, with the result
# in its last sentence.
LONG_ABSTRACT = (
    "Audio deepfake detection models trained on one spoofing corpus generalise"
    " poorly to unseen attacks, codecs and recording channels. " * 6
    + "We evaluate self-supervised speech representations as front ends for"
    " spoofing countermeasures across four corpora and three back ends. "
    + "A frozen front end reduces the equal error rate on In-the-Wild from 37.8%"
    " to 7.4%.")
_QUOTE = ARXIV_ABS.index("Abstract:</span>") + len("Abstract:</span>")
ARXIV_LONG = (ARXIV_ABS[:_QUOTE] + LONG_ABSTRACT
              + ARXIV_ABS[ARXIV_ABS.index("</blockquote>", _QUOTE):])


class AReviewOfTheLeadFixFoundThreeMore(unittest.TestCase):
    """An independent review of cf2a983 (2026-09-28) confirmed three defects in
    keep_lead and the floor: the lead was one section cut at 900 characters, so
    a real abstract lost its result sentence; the floor counted words Prepwright
    writes itself ("Opening" and the truncation marker); and a bare "#" or a
    byte-order mark counted as prose, spending a section slot on nothing."""

    def test_a_long_abstract_keeps_its_result_sentence(self):
        self.assertGreater(len(LONG_ABSTRACT), 900)
        out, texts = _one_arxiv_run(ARXIV_LONG)
        self.assertEqual(out["stored"], 1, out["discarded"])
        kept = CO.fit_sections(CO.parse_loose(texts[0])[1])
        opening = [k for k in kept if k["heading"].startswith(R.LEAD_HEADING)]
        self.assertGreater(len(opening), 1)
        self.assertEqual(opening[1]["heading"], R.LEAD_HEADING + " (cont. 2)")
        lead = "\n".join(k["body"] for k in opening)
        self.assertIn("from 37.8% to 7.4%", lead)
        self.assertNotIn("TRUNCATED", lead)

    def test_the_floor_counts_only_the_page_s_own_words(self):
        gap = {"gap_id": "g01", "label": "Section 13G civil penalty cap under the Privacy Act",
               "why": "the posting names the opening of a privacy compliance program"}
        body = ("<html><head><title>Log rotation notes</title></head><body>"
                "<h1>Log rotation notes</h1><p>We rotate logs weekly; the Privacy Act is"
                " not discussed here.</p><h2>Rotation</h2><p>"
                + "Logs rotate at midnight and compress with gzip before shipping. " * 20
                + "</p></body></html>")
        out, _texts = _one_arxiv_run(body, gap=gap)
        self.assertEqual(out["stored"], 0)
        self.assertEqual(out["discarded"][0]["stage"], "coverage", out["discarded"])

    def test_an_image_only_h1_is_not_a_lead(self):
        sections = "".join("<h2>Topic %d</h2><p>%s</p>" % (i, "Differential privacy bounds"
                           " the privacy loss of one record. " * 3) for i in range(10))
        text = R.text_of(page("https://example.com/dp", (
            "<html><head><title>DP guide</title></head><body><h1 class=logo>"
            "<a href='/'><img alt='Acme'></a></h1>" + sections + "</body></html>"),
            ctype="text/html"))
        pairs = CO.parse_loose(text)[1]
        self.assertEqual([h for h, _b in pairs], ["Topic %d" % i for i in range(10)])

    def test_a_byte_order_mark_is_not_a_lead(self):
        text = R.text_of(page("https://example.com/notes.md",
                              "\ufeff# Differential privacy notes\n\n## Definition\n"
                              "Differential privacy bounds what one record can change.\n",
                              ctype="text/markdown"))
        title, pairs = CO.parse_loose(text)
        self.assertEqual(title, "Differential privacy notes")
        self.assertEqual([h for h, _b in pairs], ["Definition"])


class TheCoverageFloorMeasuresWhatTheStoreKeeps(unittest.TestCase):
    """The same page passed the coverage floor on its whole text, abstract
    included, while the store kept none of the text that matched."""

    def test_a_page_whose_gap_words_are_only_in_its_title_is_discarded(self):
        chrome = ("<h2>Access Paper:</h2><ul><li>View PDF</li><li>TeX Source</li>"
                  "</ul><h3>Bookmark</h3><p>Share this on BibSonomy, Reddit and"
                  " Mendeley, or export a citation.</p>") * 6
        body = ("<html><head><title>Audio deepfake detection and spoofing"
                " countermeasures with self-supervised speech front ends</title>"
                "</head><body>" + chrome + "</body></html>")
        out, _texts = _one_arxiv_run(body)
        self.assertEqual(out["stored"], 0)
        self.assertEqual(out["discarded"][0]["stage"], "coverage")


if __name__ == "__main__":
    unittest.main()
