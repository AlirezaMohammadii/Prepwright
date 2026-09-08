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


if __name__ == "__main__":
    unittest.main()
