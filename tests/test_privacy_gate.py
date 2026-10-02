"""tools/privacy_gate.py keeps real applications out of git.

On 2026-10-02 this repo's public history and Resume Studio's history were rewritten to
take down real postings, employer names, resume text and a page the writer filed.
These tests prove the gate refuses each of those again, on an invented employer and
an invented corpus, and that the tracked tree passes it.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(REPO, "tools", "privacy_gate.py")
LIVE_CONFIG = os.path.expanduser("~/.config/claude-apps/privacy-gate.json")

POSTING = ("Zentrafix Labs is hiring a Telemetry Analyst to own the vibration archive and "
           "report every anomaly to the plant board within two working days of capture.")
RESUME_LINE = ("Rebuilt the turbine vibration archive so that every anomaly report now "
               "reaches the plant board within two working days of capture.")


def _git(cwd, *args, stdin=None):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True,
                          input=stdin)


class TheGateRefusesARealApplication(unittest.TestCase):
    """A throwaway repo and a throwaway corpus: one application folder whose posting
    names its employer in the provenance block, and one master resume."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rs-gate-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        corpus = os.path.join(self.tmp, "corpus")
        app = os.path.join(corpus, "applications", "2026-01-05__Telemetry_Analyst_Zentrafix")
        os.makedirs(app)
        with open(os.path.join(app, "Telemetry_Analyst_Zentrafix_JobDescription.md"), "w") as f:
            f.write("# Job description\n\n## Provenance\n\n```json\n%s\n```\n\n## Posting\n\n%s\n"
                    % (json.dumps({"company": "Zentrafix Labs"}), POSTING))
        os.makedirs(os.path.join(corpus, "masters"))
        with open(os.path.join(corpus, "masters", "Master_Ops.tex"), "w") as f:
            f.write("\\documentclass{article}\n\\usepackage{enumitem}\n\\begin{document}\n"
                    "\\item %s\n\\end{document}\n" % RESUME_LINE)
        self.config = os.path.join(self.tmp, "gate.json")
        with open(self.config, "w") as f:
            json.dump({"corpus": corpus, "deny": ["(?<![A-Za-z0-9])Quorvane(?![A-Za-z0-9])"],
                       "allow": ["Anthropic"]}, f)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "tools"))
        shutil.copy(GATE, os.path.join(self.repo, "tools", "privacy_gate.py"))
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "gate@example.invalid")
        _git(self.repo, "config", "user.name", "Gate Test")
        self._write("README.md", "A clean note about the bridge.\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "start")

    def _write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def _gate(self, mode, stdin=None, config=True):
        env = dict(os.environ, PRIVACY_GATE_CONFIG=self.config if config else
                   os.path.join(self.tmp, "absent.json"))
        return subprocess.run([sys.executable, os.path.join("tools", "privacy_gate.py"), mode],
                              cwd=self.repo, capture_output=True, text=True, env=env,
                              input=stdin)

    def _staged(self, rel, text, config=True):
        self._write(rel, text)
        _git(self.repo, "add", "-A")
        return self._gate("--staged", config=config)

    def test_a_clean_change_passes(self):
        r = self._staged("notes.md", "The judge round on 2026-01-05 retried once.\n")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("clean", r.stdout)

    def test_an_employer_from_a_local_record_is_refused(self):
        r = self._staged("notes.md", "The Zentrafix Labs run stopped at 94%.\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("notes.md:1: an employer from a local application record", r.stdout)
        self.assertNotIn("Zentrafix", r.stdout, "the gate echoed the name it refused")

    def test_a_folder_name_spelling_is_refused_too(self):
        r = self._staged("tests/t.py", 'STEM = "Telemetry_Analyst_Zentrafix_Labs_A_Owner"\n')
        self.assertEqual(r.returncode, 1, r.stdout)

    def test_the_seeded_deny_list_is_read(self):
        r = self._staged("notes.md", "Quorvane rejected the page.\n")
        self.assertIn("a name or fact on the local deny list", r.stdout)

    def test_posting_text_is_refused_without_any_name(self):
        r = self._staged("tests/fixture.txt", POSTING.replace("Zentrafix Labs", "The company") + "\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("text copied from a real posting", r.stdout)

    def test_resume_text_is_refused(self):
        r = self._staged("docs/note.md", "The writer kept: %s\n" % RESUME_LINE)
        self.assertIn("text copied from the owner's resumes", r.stdout)

    def test_application_paths_are_refused(self):
        for rel in ("applications/x/Role_Co_A_Owner.tex", "sessions/abc/job.json",
                    "out/Role_Co_FitReport.md", "out/Role_Co_CoverLetter_A_Owner.tex",
                    "out/page.pdf", "skills/identity.json"):
            r = self._staged(rel, "x\n")
            self.assertEqual(r.returncode, 1, rel)
            _git(self.repo, "rm", "-q", "--cached", rel)

    def test_contact_details_are_refused_even_with_no_config(self):
        # Assembled at run time, so this file never holds a contact value itself.
        email, phone = "jane.doe" + "@" + "gmail.com", " ".join(["+61", "412", "345", "678"])
        handle, home = "linked" "in.com/in/" + "jane-doe-1234", "/Users/" + "janedoe/files"
        r = self._staged("notes.md", "Reach me at %s or %s, %s, %s.\n" % (email, phone, handle, home),
                         config=False)
        self.assertEqual(r.returncode, 1)
        for rule in ("an email address", "an Australian phone number",
                     "a LinkedIn profile handle", "a home-directory path"):
            self.assertIn(rule, r.stdout)
        self.assertIn("only the path and contact checks ran", r.stdout)

    def test_reserved_and_placeholder_values_pass(self):
        phone = " ".join(["+61", "400", "000", "000"])
        r = self._staged("notes.md", "candidate" + "@example.com, " + phone + ", linked" "in.com/in/"
                                     + "candidate-example, /Users/" + "testuser/x\n")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_a_push_checks_every_commit_and_its_message(self):
        self._write("notes.md", "fine\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "the Zentrafix Labs round, fixed")
        sha = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        r = self._gate("--push", stdin="refs/heads/main %s refs/heads/main %s\n" % (sha, "0" * 40))
        self.assertEqual(r.returncode, 1)
        self.assertIn("message", r.stdout)
        self.assertIn("an employer from a local application record", r.stdout)

    def test_a_deletion_push_sends_nothing(self):
        r = self._gate("--push", stdin="(delete) %s refs/heads/old %s\n" % ("0" * 40, "1" * 40))
        self.assertEqual(r.returncode, 0, r.stdout)


class TheTrackedTreePassesTheGate(unittest.TestCase):
    def test_the_tree_is_clean(self):
        r = subprocess.run([sys.executable, GATE, "--tree"], cwd=REPO, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_the_hooks_are_enabled_where_the_gate_is_configured(self):
        """On the owner's machine (the gate's config exists) the hooks must be live,
        so a session that commits or pushes runs the gate whether or not it knows to."""
        if not os.path.exists(LIVE_CONFIG):
            self.skipTest("no privacy-gate config on this machine")
        path = subprocess.run(["git", "-C", REPO, "config", "--get", "core.hooksPath"],
                              capture_output=True, text=True).stdout.strip()
        self.assertEqual(path, "tools/git-hooks",
                         "run: git config core.hooksPath tools/git-hooks")
        for hook in ("pre-commit", "pre-push"):
            self.assertTrue(os.access(os.path.join(REPO, "tools", "git-hooks", hook), os.X_OK), hook)


if __name__ == "__main__":
    unittest.main()
