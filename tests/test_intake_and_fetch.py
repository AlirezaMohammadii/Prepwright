"""The properties of the fetch guard, the posting parser, and track intake.

No test here touches the network. `research.fetch` is exercised through its
refusal path, which runs entirely before a socket is opened, and the parser runs
against saved markup. A test that needs the internet to pass is a test that
fails on a train.

Run:  cd ~/Desktop/Prepwright && python3 -m unittest discover -s tests -v
"""

import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from prepwright import config as C           # noqa: E402
from prepwright import intake as I           # noqa: E402
from prepwright import research as R         # noqa: E402
from prepwright import state as S            # noqa: E402


POSTING = """About the Role

You will assess AI systems and run governance forums.

Key Accountabilities

- Identify and manage AI-related security risks.
- Support compliance with ISO 42001, SOC2 and emerging AI regulations.
- Awareness of AI governance frameworks (NIST AI RMF).
"""

# A page in the shape the parser was written against: the employer and role in
# the title, double-escaped exactly as LinkedIn serves them, the body in a div
# the reader looks for by name.
PAGE = """<html><head>
<title>AI Security &amp;amp; Governance Analyst at Wingtip Australia &amp;amp; New Zealand — Brisbane, Queensland, Australia | LinkedIn Jobs</title>
<meta property="og:title" content="AI Security &amp;amp; Governance Analyst at Wingtip Australia &amp;amp; New Zealand — Brisbane, Queensland, Australia | LinkedIn Jobs"/>
</head><body>
<div class="description__text show-more-less-html__markup relative">
<strong>About the Role</strong><br>You will assess AI systems and run governance forums.
<ul><li>Identify and manage AI-related security risks.</li>
<li>Support compliance with ISO 42001, SOC2 and emerging AI regulations.</li>
<li>Awareness of AI governance frameworks (NIST AI RMF).</li></ul>
</div></body></html>"""

# The variant LinkedIn served during a real track creation: no "Role at
# Employer" title shape, identity only in the header elements.
VARIANT_PAGE = ('<html><head><title>Sign in | LinkedIn</title></head><body>'
                '<h1 class="topcard__title">AI Security &amp;amp; Governance'
                ' Analyst</h1>'
                '<a class="topcard__org-name-link">Wingtip Australia &amp;amp; New'
                ' Zealand</a>'
                '<span class="topcard__flavor--bullet">Brisbane, Queensland,'
                ' Australia</span>'
                '<div class="show-more-less-html__markup"><ul>'
                + "".join("<li>Requirement number %d the employer states"
                          " plainly right here.</li>" % i for i in range(6))
                + '</ul></div></body></html>')

LD_PAGE = """<html><head><title>irrelevant</title>
<script type="application/ld+json">%s</script>
</head><body></body></html>""" % json.dumps({
    "@type": "JobPosting",
    "title": "Staff Security Engineer",
    "hiringOrganization": {"name": "Example Corp"},
    "jobLocation": {"address": {"addressLocality": "Melbourne",
                                "addressRegion": "VIC"}},
    "description": "<p>Own the threat model.</p><ul><li>Run red teams.</li></ul>"
    + "x" * 300,
})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prepwright-intake-")
        # Registered first so it runs last: unittest runs every addCleanup after
        # tearDown, so a later cleanup must not be able to reach the real store.
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


class TheFetchGuardRefusesBeforeAnyByte(Base):
    """A local app that fetches URLs is an SSRF surface, so the guard is the
    feature. Every case below must be refused with no socket opened, which is
    why they raise FetchRefused and never FetchFailed: FetchFailed means the
    attempt was made."""

    def test_only_https_is_fetched(self):
        for url in ("http://example.com/", "file:///etc/passwd",
                    "ftp://example.com/", "gopher://example.com/",
                    "data:text/html,x", "example.com/no-scheme"):
            with self.assertRaises(R.FetchRefused, msg=url):
                R.fetch(url)

    def test_private_and_reserved_space_is_refused(self):
        for url in ("https://127.0.0.1/", "https://localhost/",
                    "https://10.0.0.1/", "https://192.168.1.1/",
                    "https://172.16.0.1/", "https://169.254.169.254/latest/",
                    "https://[::1]/", "https://[fd00::1]/"):
            with self.assertRaises(R.FetchRefused, msg=url):
                R.fetch(url)

    def _resolving_to(self, addr):
        """Point every lookup at one address, so the guard is exercised through
        fetch() rather than re-implemented in the assertion."""
        import socket
        real = socket.getaddrinfo
        family = socket.AF_INET6 if ":" in addr else socket.AF_INET
        sockaddr = (addr, 443, 0, 0) if family == socket.AF_INET6 else (addr, 443)

        def one(host, port, *a, **k):
            return [(family, socket.SOCK_STREAM, 6, "", sockaddr)]

        socket.getaddrinfo = one
        self.addCleanup(setattr, socket, "getaddrinfo", real)

    def test_the_positive_rule_catches_what_a_negative_list_misses(self):
        """100.64.0.0/10 is RFC 6598 carrier-grade NAT and reaches a home
        router's WAN side on many networks. On CPython 3.9 and 3.14 alike it
        answers False to is_private, is_reserved, is_loopback AND is_link_local,
        so the obvious negative list waves it through. The rule this module uses
        is positive (is_global and not multicast), and this drives it through
        fetch() so removing the rule fails the test rather than the assertion
        agreeing with a copy of itself."""
        for addr in ("100.64.0.1", "0.0.0.0", "192.0.0.1", "198.18.0.1",
                     "224.0.0.1", "::", "::ffff:10.0.0.1", "::ffff:169.254.169.254"):
            self._resolving_to(addr)
            with self.assertRaises(R.FetchRefused, msg=addr):
                R.fetch("https://looks-fine.example/")

    def test_the_guard_is_capable_of_passing(self):
        """A control. Without it, every refusal above proves only that the
        fetcher is broken. It drives the real guard function rather than the
        whole fetch, so it needs no socket and no network.

        192.0.2.1 is NOT usable here: TEST-NET-1 answers False to is_global, as
        this test found the first time it was written against it."""
        self._resolving_to("8.8.8.8")
        got = R._addresses_for("looks-fine.example", 443)
        self.assertEqual([str(ip) for _f, ip in got], ["8.8.8.8"])
        self._resolving_to("2606:4700:4700::1111")
        self.assertEqual(len(R._addresses_for("looks-fine.example", 443)), 1)

    def test_every_resolved_address_is_checked_not_just_the_first(self):
        """A host that resolves to one public and one private address must be
        refused: which one gets connected to is the resolver's choice."""
        import socket
        real = socket.getaddrinfo

        def both(host, port, *a, **k):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        socket.getaddrinfo = both
        try:
            with self.assertRaises(R.FetchRefused):
                R.fetch("https://split-horizon.example/")
        finally:
            socket.getaddrinfo = real

    def test_credentials_in_a_url_are_refused_not_redacted(self):
        with self.assertRaises(R.FetchRefused) as cm:
            R.fetch("https://user:hunter2@example.com/")
        self.assertNotIn("hunter2", str(cm.exception),
                         "the refusal echoed the password back")

    def test_a_host_that_does_not_resolve_is_refused_by_name(self):
        with self.assertRaises(R.FetchRefused):
            R.fetch("https://this-host-does-not-exist.invalid/")


class ThePostingParserNeverGuesses(Base):
    def test_the_title_shape_survives_double_escaping(self):
        """LinkedIn serves `&amp;amp;` in og:title. One unescape pass leaves
        `&amp;` sitting inside the employer name."""
        p = I.parse_posting_page(PAGE)
        self.assertEqual(p["employer"], "Wingtip Australia & New Zealand")
        self.assertEqual(p["role_title"], "AI Security & Governance Analyst")
        self.assertEqual(p["location"], "Brisbane, Queensland, Australia")

    def test_the_body_comes_from_the_body_not_the_page(self):
        p = I.parse_posting_page(PAGE)
        self.assertTrue(p["confident"])
        self.assertIn("ISO 42001", p["text"])
        self.assertNotIn("LinkedIn Jobs", p["text"],
                         "page furniture was ingested as a requirement")

    def test_structured_data_wins_when_the_page_publishes_it(self):
        p = I.parse_posting_page(LD_PAGE)
        self.assertEqual(p["employer"], "Example Corp")
        self.assertEqual(p["role_title"], "Staff Security Engineer")
        self.assertEqual(p["location"], "Melbourne, VIC")
        self.assertIn("Run red teams.", p["text"])

    def test_a_page_with_no_body_asks_for_a_paste_rather_than_inventing_one(self):
        """The failure that matters. A posting silently replaced by page
        furniture is a track built on the wrong requirements, and every gap
        derived from it is wrong in a way nothing downstream can detect."""
        p = I.parse_posting_page(
            "<html><head><title>Sign in | LinkedIn</title></head>"
            "<body><div id=nav>Sign in</div></body></html>", url="https://x.test/j/1")
        self.assertFalse(p["confident"])
        self.assertEqual(p["text"], "")
        self.assertIn("paste", p["note"].lower())

    def test_the_page_header_supplies_an_identity_the_title_withheld(self):
        """A real track was built "Untitled posting" with no employer and no
        role: LinkedIn served a variant whose title carried no "Role at
        Employer" shape. The body parsed, so the track had the right
        requirements under the wrong name, which is the wrong half to lose."""
        variant = VARIANT_PAGE
        self.assertIsNone(I._from_title(variant),
                          "the fixture no longer exercises a missing title")
        p = I.parse_posting_page(variant)
        self.assertEqual(p["employer"], "Wingtip Australia & New Zealand")
        self.assertEqual(p["role_title"], "AI Security & Governance Analyst")
        self.assertEqual(p["location"], "Brisbane, Queensland, Australia")
        self.assertTrue(p["confident"])

    def test_the_readers_chain_rather_than_the_first_one_winning(self):
        """One reader supplying half the identity must not stop the next from
        supplying the other half. Structured data here names the role and omits
        the employer; only the page header has it."""
        split = ('<html><head><title>Careers | LinkedIn</title>'
                 '<script type="application/ld+json">%s</script></head><body>'
                 '<a class="topcard__org-name-link">Example Corp</a>'
                 '<div class="show-more-less-html__markup"><ul>%s</ul></div>'
                 '</body></html>'
                 % (json.dumps({"@type": "JobPosting",
                                "title": "Staff Security Analyst",
                                "description": "<p>Own the estate.</p>" + "y" * 300}),
                    "".join("<li>Requirement %d stated plainly here.</li>" % i
                            for i in range(6))))
        self.assertIsNone(I._from_title(split),
                          "the fixture no longer exercises a missing title")
        first = I._from_ld_json(split)
        self.assertEqual(first["role_title"], "Staff Security Analyst")
        self.assertFalse(first["employer"], "the fixture must omit the employer")
        p = I.parse_posting_page(split)
        self.assertEqual(p["role_title"], "Staff Security Analyst")
        self.assertEqual(p["employer"], "Example Corp",
                         "the second reader never ran, so half the identity was lost")

    def test_the_header_reader_refuses_a_value_that_is_not_a_name(self):
        """An h1 exists on almost every page. Taking whatever is inside it puts
        a paragraph, or a nest of markup, in the employer field of a track."""
        junk = ('<html><head><title>Careers</title></head><body>'
                '<h1 class="topcard__title">%s</h1>'
                '<a class="topcard__org-name-link"><span>Example</span></a>'
                '</body></html>' % ("A very long sentence about the company. " * 12))
        p = I.parse_posting_page(junk)
        self.assertIsNone(p["role_title"],
                          "a paragraph was accepted as a role title")
        self.assertIsNone(p["employer"],
                          "a fragment of markup was accepted as an employer")

    def test_a_header_with_no_identity_at_all_is_still_not_invented(self):
        p = I.parse_posting_page(
            "<html><head><title>Careers</title></head><body>"
            "<h1></h1><div>nothing here</div></body></html>")
        self.assertIsNone(p["employer"])
        self.assertIsNone(p["role_title"])
        self.assertFalse(p["confident"])

    def test_a_credential_in_a_posting_never_survives_the_parse(self):
        page = PAGE.replace("Run governance forums.",
                            'api_key: "AKIAIOSFODNN7EXAMPLE1234"')
        page = page.replace("You will assess AI systems and run governance forums.",
                            'api_key: "AKIAIOSFODNN7EXAMPLE1234"')
        p = I.parse_posting_page(page)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE1234", p["text"])
        self.assertIn("REDACTED", p["text"])


class IntakeBuildsATrackThatRecordsWhatItWasBuiltFrom(Base):
    def test_a_pasted_posting_becomes_a_track(self):
        track_id, rep = I.intake_from_text(
            POSTING, employer="Wingtip", role_title="Analyst")
        self.assertTrue(track_id.startswith("t-"))
        self.assertEqual(rep["employer"], "Wingtip")
        self.assertEqual(rep["role_title"], "Analyst")
        self.assertEqual(rep["title"], "Analyst at Wingtip")
        self.assertFalse(rep["truncated"])

    def test_the_source_is_on_disk_so_the_archive_can_carry_it(self):
        """DESIGN-state-corpus.md:407 requires the source copied in and hashed.
        archive_track tars `intake/*`, so a provenance path with nothing behind
        it archives to nothing."""
        track_id, _ = I.intake_from_text(POSTING, employer="Wingtip", role_title="A")
        intake_dir = os.path.join(S.track_dir(track_id), "intake")
        self.assertTrue(os.path.isfile(os.path.join(intake_dir, "source.txt")))
        self.assertTrue(os.path.isfile(os.path.join(intake_dir, "provenance.json")))
        with open(os.path.join(intake_dir, "source.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), POSTING.strip())

    def test_the_intake_row_hashes_the_bytes_it_stored(self):
        track_id, _ = I.intake_from_text(POSTING, employer="Wingtip", role_title="A")
        handle = S.open_track(track_id, client_label="test")
        try:
            row = handle.intake()
            self.assertEqual(
                row["body_sha256"],
                hashlib.sha256(row["body"].encode("utf-8")).hexdigest())
            self.assertEqual(row["body_bytes"], len(row["body"].encode("utf-8")))
        finally:
            handle.close()

    def test_an_oversized_posting_is_marked_not_silently_cut(self):
        """A silently shortened posting reads as a complete requirement list
        that happens to stop early."""
        huge = "requirement line\n" * 8000
        self.assertGreater(len(huge.encode("utf-8")), C.INTAKE_MAX_BYTES)
        track_id, rep = I.intake_from_text(huge, employer="E", role_title="R")
        self.assertTrue(rep["truncated"])
        self.assertLessEqual(rep["bytes"], C.INTAKE_MAX_BYTES)
        # The marker must reach STORAGE, not just the report. A reader of the
        # posting is the one who needs to know it stops early, and they read the
        # intake row, not the return value of the call that wrote it.
        handle = S.open_track(track_id, client_label="test")
        try:
            self.assertIn("TRUNCATED", handle.intake()["body"])
        finally:
            handle.close()
        with open(os.path.join(S.track_dir(track_id), "intake", "source.txt"),
                  encoding="utf-8") as fh:
            self.assertIn("TRUNCATED", fh.read())

    def test_an_empty_paste_is_refused(self):
        for bad in ("", "   \n\t "):
            with self.assertRaises(I.IntakeRefused):
                I.intake_from_text(bad)

    def test_an_unknown_source_kind_is_refused_by_name(self):
        with self.assertRaises(I.IntakeRefused):
            I.intake_from_text(POSTING, source_kind="whatever")


class TheApplicationFolderIsReadOnceAndHashed(Base):
    def _folder(self, base="Analyst_Wingtip", with_posting=True):
        root = os.path.join(self.tmp, "applications", "2026-09-08__" + base)
        os.makedirs(root)
        with open(os.path.join(root, base + "_FitReport.md"), "w") as fh:
            fh.write("# Fit report\n\n## Requirement matrix\n\n| # | R | V | E |\n")
        with open(os.path.join(root, base + "_A_Mohammadi.tex"), "w") as fh:
            fh.write("\\documentclass{article}")
        if with_posting:
            with open(os.path.join(root, base + "_JobDescription.md"), "w") as fh:
                fh.write("# Job description for %s\n\n## Provenance\n\n"
                         "```json\n%s\n```\n\n## Posting\n\n%s\n"
                         % (base, json.dumps({"source_url": "https://x.test/j/1",
                                              "captured_utc": "2026-09-08T00:00:00Z"}),
                            POSTING))
        return root

    def test_it_finds_every_deliverable_and_hashes_each(self):
        app = I.read_application_folder(self._folder())
        self.assertEqual(app["base"], "Analyst_Wingtip")
        names = {m["name"] for m in app["manifest"]}
        self.assertIn("Analyst_Wingtip_FitReport.md", names)
        self.assertIn("Analyst_Wingtip_JobDescription.md", names)
        for m in app["manifest"]:
            self.assertEqual(len(m["sha256"]), 64)

    def test_the_folder_hash_changes_when_the_folder_does(self):
        """The whole point of hashing at track creation: a later edit on the
        resume side is detectable without ever reopening the folder."""
        root = self._folder()
        before = I.read_application_folder(root)["sha256"]
        with open(os.path.join(root, "Analyst_Wingtip_FitReport.md"), "a") as fh:
            fh.write("\n## Red team\nOne more line.\n")
        self.assertNotEqual(before, I.read_application_folder(root)["sha256"])

    def test_a_symlink_is_refused_rather_than_followed(self):
        root = self._folder()
        target = os.path.join(self.tmp, "secret.txt")
        with open(target, "w") as fh:
            fh.write("not yours")
        link = os.path.join(root, "Analyst_Wingtip_A_Mohammadi.pdf")
        os.symlink(target, link)
        app = I.read_application_folder(root)
        self.assertNotIn("resume_pdf", app["files"],
                         "a symlink was followed out of the application folder")

    def test_importing_a_folder_carries_its_posting_and_its_hash(self):
        root = self._folder()
        track_id, rep = I.intake_from_application(root)
        self.assertEqual(rep["source_kind"], "imported")
        self.assertEqual(rep["application"], os.path.realpath(root))
        handle = S.open_track(track_id, client_label="test")
        try:
            row = handle.intake()
            self.assertEqual(row["kind"], "imported")
            self.assertEqual(row["source_path"], os.path.realpath(root))
            self.assertIn("ISO 42001", row["body"])
        finally:
            handle.close()
        kept = os.path.join(S.track_dir(track_id), "intake", "application.json")
        self.assertTrue(os.path.isfile(kept))
        with open(kept, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["sha256"],
                             I.read_application_folder(root)["sha256"])

    def test_a_folder_with_no_posting_is_refused_not_filled_in_from_the_resume(self):
        """A track built from the answers instead of the questions produces a
        gap list that agrees with itself."""
        root = self._folder(with_posting=False)
        with self.assertRaises(I.IntakeRefused) as cm:
            I.intake_from_application(root)
        self.assertIn("no job description", str(cm.exception))

    def test_a_directory_that_is_not_an_application_folder_is_refused(self):
        empty = os.path.join(self.tmp, "applications", "2026-09-08__Nothing")
        os.makedirs(empty)
        with self.assertRaises(I.IntakeRefused):
            I.read_application_folder(empty)
        with self.assertRaises(I.IntakeRefused):
            I.read_application_folder(os.path.join(self.tmp, "no-such-folder"))


if __name__ == "__main__":
    unittest.main()
