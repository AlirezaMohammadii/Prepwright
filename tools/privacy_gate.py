#!/usr/bin/env python3
"""Refuse a commit or a push that would put a real application into git.

The owner's applications (the postings applied to, the employers' names, the
resumes, letters, fit reports and evaluations generated for them) and the owner's
own contact details stay on this machine. On 2026-10-02 both this repo's history
and Prepwright's public history were rewritten to take such material down; this
gate is what keeps it from coming back.

    python3 tools/privacy_gate.py --tree     every tracked file (the test suite runs this)
    python3 tools/privacy_gate.py --staged   the staged changes (the pre-commit hook)
    python3 tools/privacy_gate.py --push     the commits a push would send (pre-push hook,
                                             reading git's ref lines on stdin)

Four checks, every one reported as path:line plus the rule, never the matched text:

1. paths: application folders, generated documents and private files never enter git;
2. contact details: email addresses outside reserved domains, Australian phone numbers,
   LinkedIn profile handles, home-directory paths, and the owner's identity values;
3. employer names: every company a local application record names, plus a seeded list;
4. copied text: a 10-word run shared with a real posting or with the owner's resumes.

Checks 2 to 4 read local data that never enters git, located by
~/.config/claude-apps/privacy-gate.json (override with PRIVACY_GATE_CONFIG):

    {"corpus": "<the folder that holds applications/ and the resume sources>",
     "identity_json": "<resume-studio's skills/identity.json>",
     "deny": ["<regex>", ...],         seeded employer and owner-fact patterns
     "allow": ["Anthropic", ...]}      names that are tools or job boards, never flagged

With no config file (a clone on another machine) only checks 1 and 2 run, and the
gate says so. Standard library only, Python 3.9+.
"""

import glob
import hashlib
import json
import os
import re
import subprocess
import sys

CONFIG = os.environ.get("PRIVACY_GATE_CONFIG") or os.path.expanduser(
    "~/.config/claude-apps/privacy-gate.json")
REPO = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                      text=True).stdout.strip() or os.getcwd()
K = 10                     # words in a copied-text run
MAX_BYTES = 2_000_000      # larger blobs are binary or generated, and refused by path
ZERO = "0" * 40

FORBIDDEN_PATHS = [
    (r"(^|/)(applications|sessions|runs)/", "an application or session folder"),
    (r"(^|/)skills/identity\.json$", "the owner's identity file"),
    (r"_(FitReport|PortalAnswers|JobDescription|Evaluation_R\d+)[^/]*\.(md|json)$",
     "a generated application document"),
    (r"_CoverLetter[^/]*\.(tex|pdf)$", "a generated cover letter"),
    (r"(^|/)eval-inputs\.json$", "a judge's input bundle"),
    (r"-text\.txt$", "a resume's text layer"),
    (r"\.(docx?|pages|rtf)$", "a word-processor document"),
    (r"\.pdf$", "a PDF"),
]
# Synthetic fixtures whose names follow a generated-document pattern. Their
# content still answers to checks 2 to 4.
ALLOWED_PATHS = {"tests/fixtures/sample_resume.pdf", "tests/fixtures/governance_r1/resume-text.txt"}
RESERVED_MAIL = re.compile(
    r"@(example\.(com|org|net|test|invalid)|[a-z0-9.-]+\.(test|invalid|example)|"
    r"anthropic\.com|users\.noreply\.github\.com)$", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(\+61[ -]?4|04)\d{2}[ -]?\d{3}[ -]?\d{3}(?!\d)")
PLACEHOLDER_PHONE = re.compile(r"^(\+61[ -]?4|04)00[ -]?000[ -]?000$")
LINKEDIN = re.compile(r"linkedin\.com/in/([A-Za-z0-9-]{3,})", re.I)
HOME = re.compile(r"/(Users|home)/(?!<|\$|(?:USER|you|me|name|runner|testuser|user|someone|example|alice|bob)/)[a-z][a-z0-9_-]+/")
PREAMBLE = re.compile(r"\\(usepackage|setlist|newcommand|documentclass|geometry|definecolor|"
                      r"hypersetup|renewcommand|titleformat|pagestyle)")
TOOL_SOURCES = re.compile(r"^(bridge\.py|index\.html|skills/|context/|tools/|sync_skills\.py|"
                          r"prepwright/|context-manifest\.json)")


def _git(*args, text=True):
    return subprocess.run(["git", "-C", REPO] + list(args), capture_output=True,
                          text=text).stdout


def _config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else None
    except (OSError, ValueError):
        return None


# ---- local data: employers, identity values, real postings and resumes ------------
def _name_rx(name):
    body = r"[ _]+".join(re.escape(w) for w in name.split())
    return re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % body)


def _employers(cfg):
    """(label, regex) for every employer the local records name, and the seeded list."""
    corpus = cfg.get("corpus") or ""
    allow = {a.lower() for a in cfg.get("allow", [])}
    names = set()
    for p in glob.glob(os.path.join(corpus, "applications", "*", "*_JobDescription.md")):
        try:
            m = re.search(r"```json\s*(\{.*?\})\s*```", open(p, encoding="utf-8").read(), re.S)
            names.add(str(json.loads(m.group(1)).get("company") or "") if m else "")
        except (OSError, ValueError):
            pass
    for p in (glob.glob(os.path.join(corpus, "applications", "*", "job-context.json"))
              + glob.glob(os.path.join(corpus, "resume-studio", "sessions", "*", "job-context.json"))):
        try:
            c = json.load(open(p, encoding="utf-8")).get("company")
            names.add(c if isinstance(c, str) else "")
        except (OSError, ValueError):
            pass
    out = []
    for n in sorted({n.strip() for n in names if n and len(n.strip()) >= 3}):
        if n.lower() not in allow and not any(w.lower() in allow for w in n.split()):
            out.append(("an employer from a local application record", _name_rx(n)))
    for pattern in cfg.get("deny", []):
        out.append(("a name or fact on the local deny list", re.compile(pattern)))
    return out


def _identity_values(cfg):
    """The owner's contact values: emails, phone digits and profile handles in the
    identity file's contact block, plus every other identity value of 6+ characters."""
    try:
        ident = json.load(open(cfg.get("identity_json") or "", encoding="utf-8"))
    except (OSError, ValueError):
        return []
    vals = []
    for k, v in ident.items():
        if not isinstance(v, str):
            continue
        if k == "CONTACT_BLOCK":
            vals += EMAIL.findall(v) + [m.group(0) for m in PHONE.finditer(v)]
            vals += ["linked" "in.com/in/" + h for h in LINKEDIN.findall(v)]
        elif k != "WORKSPACE" and len(v) >= 6:
            vals.append(v)
    return [v for v in vals if v]


def _words(text):
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}", " email ", text)
    text = re.sub(r"https?://\S+", " url ", text)
    return re.findall(r"[a-z0-9]+", re.sub(r"\\[A-Za-z]+", " ", text).lower())


def _shingles(text, drop_preamble=False):
    if drop_preamble:
        # A resume's preamble and its % comments are template and tool syntax
        # (RS-KEEP, RS-SAME), shared with every fixture shaped like it; the content
        # is everything else.
        text = "\n".join(l for l in text.split("\n")
                          if not PREAMBLE.search(l) and not l.lstrip().startswith("%"))
    w = _words(text)
    return {hashlib.blake2b(" ".join(w[i:i + K]).encode(), digest_size=8).digest()
            for i in range(len(w) - K + 1)}


def _body(text):
    """A LaTeX source from \\begin{document} on: the preamble is template, shared with
    every fixture built in the same shape, and only the body is the owner's content."""
    i = text.find("\\begin{document}")
    return text[i:] if i >= 0 else text


def _corpus(cfg):
    """10-word runs of real postings (with how many applications share each) and of
    the owner's resumes. Tool text that an application merely quotes is removed later."""
    root = cfg.get("corpus") or ""
    postings, resumes = {}, set()
    for p in glob.glob(os.path.join(root, "applications", "**", "*"), recursive=True):
        if not os.path.isfile(p) or os.path.getsize(p) > MAX_BYTES:
            continue
        name, folder = os.path.basename(p), os.path.relpath(p, root).split(os.sep)[1]
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if re.search(r"JobDescription|posting|job-context", name) and name.endswith((".md", ".txt", ".json")):
            for h in _shingles(text):
                postings.setdefault(h, set()).add(folder)
        elif name.endswith(".tex") or name.endswith("-text.txt"):
            resumes |= _shingles(_body(text), drop_preamble=True)
    for p in (glob.glob(os.path.join(root, "masters", "*.tex")) + glob.glob(os.path.join(root, "*.tex"))
              + glob.glob(os.path.join(root, "reference", "**", "*.*"), recursive=True)):
        if os.path.isfile(p) and p.endswith((".tex", ".md", ".txt")):
            resumes |= _shingles(_body(open(p, encoding="utf-8", errors="replace").read()),
                                 drop_preamble=True)
    return postings, resumes


def _tool_text(rev=None):
    """Runs of this repo's own prompt and code sources (in the working tree, or at
    `rev`): application folders quote them back, and a quote of the tool is not a
    leak of the application. A commit or push is measured against the sources as
    they stood before it, so text it adds to a prompt cannot vouch for itself."""
    seen = set()
    paths = (_git("ls-tree", "-r", "--name-only", rev) if rev else _git("ls-files")).split("\n")
    for path in paths:
        if not path or not TOOL_SOURCES.match(path):
            continue
        try:
            if rev:
                data = _blob("%s:%s" % (rev, path)).decode("utf-8")
            else:
                data = open(os.path.join(REPO, path), encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        seen |= _shingles(data)
    return seen


# ---- the checks ---------------------------------------------------------------------
class Gate:
    def __init__(self, baseline=None):
        self.cfg = _config()
        self.baseline = baseline
        self.problems = []
        self.employers = _employers(self.cfg) if self.cfg else []
        self.identity = _identity_values(self.cfg) if self.cfg else []
        self._corpus = None

    def corpus(self):
        if self._corpus is None:
            postings, resumes = _corpus(self.cfg) if self.cfg else ({}, set())
            self._corpus = (postings, resumes, _tool_text(self.baseline) if self.cfg else set())
        return self._corpus

    def flag(self, where, rule):
        self.problems.append("%s: %s" % (where, rule))

    def check_path(self, path):
        if path in ALLOWED_PATHS:
            return
        for rx, rule in FORBIDDEN_PATHS:
            if re.search(rx, path):
                self.flag(path, rule)
                return

    def check_text(self, where, text, own_tool_text=False):
        lines = text.split("\n")
        for i, line in enumerate(lines, 1):
            for m in EMAIL.finditer(line):
                if not RESERVED_MAIL.search(m.group(0)):
                    self.flag("%s:%d" % (where, i), "an email address outside the reserved domains")
            for m in PHONE.finditer(line):
                if not PLACEHOLDER_PHONE.match(m.group(0)):
                    self.flag("%s:%d" % (where, i), "an Australian phone number")
            for h in LINKEDIN.findall(line):
                if not re.search(r"example|placeholder|candidate|your|handle", h, re.I):
                    self.flag("%s:%d" % (where, i), "a LinkedIn profile handle")
            if HOME.search(line):
                self.flag("%s:%d" % (where, i), "a home-directory path")
            for v in self.identity:
                if v in line:
                    self.flag("%s:%d" % (where, i), "one of the owner's identity values")
            for rule, rx in self.employers:
                if rx.search(line):
                    self.flag("%s:%d" % (where, i), rule)
        if not self.cfg or own_tool_text:
            return
        postings, resumes, tool = self.corpus()
        for i in range(len(lines)):
            w = _words(" ".join(lines[max(0, i - 1):i + 2]))
            for j in range(len(w) - K + 1):
                h = hashlib.blake2b(" ".join(w[j:j + K]).encode(), digest_size=8).digest()
                if h in tool:
                    continue
                folders = postings.get(h)
                if folders and len(folders) <= 4:
                    self.flag("%s:%d" % (where, i + 1), "text copied from a real posting")
                    break
                if h in resumes:
                    self.flag("%s:%d" % (where, i + 1), "text copied from the owner's resumes")
                    break

    def check_blob(self, path, data, label=None, whole_tree=False):
        self.check_path(path)
        if len(data) > MAX_BYTES or b"\0" in data[:8000]:
            return
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return
        # Over the whole tree the prompt sources ARE the tool text the copied-text
        # check subtracts, so there they answer to checks 1 to 3 only.
        self.check_text(label or path, text,
                        own_tool_text=whole_tree and bool(TOOL_SOURCES.match(path)))

    def report(self, what):
        if not self.cfg:
            print("privacy gate: no %s, so only the path and contact checks ran" % CONFIG)
        if self.problems:
            seen, uniq = set(), []
            for p in self.problems:
                if p not in seen:
                    seen.add(p)
                    uniq.append(p)
            print("privacy gate: %s refused, %d finding(s):" % (what, len(uniq)))
            for p in uniq[:60]:
                print("  " + p)
            if len(uniq) > 60:
                print("  ... and %d more" % (len(uniq) - 60))
            print("Describe a run without its employer, posting or page, and keep real "
                  "documents in applications/. Nothing was committed or pushed.")
            return 1
        print("privacy gate: %s clean" % what)
        return 0


def _blob(spec):
    return subprocess.run(["git", "-C", REPO, "cat-file", "-p", spec], capture_output=True).stdout


def main(argv):
    if "--tree" in argv:
        gate = Gate()
        for path in _git("ls-files").split("\n"):
            if path:
                full = os.path.join(REPO, path)
                gate.check_blob(path, open(full, "rb").read() if os.path.isfile(full) else b"",
                                whole_tree=True)
        return gate.report("the tracked tree")
    if "--staged" in argv:
        head = _git("rev-parse", "--verify", "-q", "HEAD").strip() or None
        gate = Gate(baseline=head)
        for line in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").split("\n"):
            if line:
                gate.check_blob(line, _blob(":" + line))
        return gate.report("this commit")
    if "--push" in argv:
        refs = [l.split() for l in sys.stdin.read().splitlines() if len(l.split()) == 4]
        status = 0
        for _lref, local, _rref, remote in refs:
            if local == ZERO:
                continue                      # a branch deletion sends no content
            if remote == ZERO:
                base = _git("merge-base", local, "--octopus", *_git(
                    "for-each-ref", "--format=%(objectname)", "refs/remotes").split()).strip()
                span = [local, "--not", "--remotes"]
            else:
                base, span = remote, ["%s..%s" % (remote, local)]
            gate = Gate(baseline=base or None)
            for commit in _git("rev-list", *span).split():
                gate.check_text("commit %s message" % commit[:7], _git("log", "-1", "--format=%B", commit))
                for row in _git("diff-tree", "-r", "--no-commit-id", "--root", "--diff-filter=ACMR",
                                "--name-only", commit).split("\n"):
                    if row:
                        gate.check_blob(row, _blob("%s:%s" % (commit, row)),
                                        label="%s:%s" % (commit[:7], row))
            status |= gate.report("this push")
        return status
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
