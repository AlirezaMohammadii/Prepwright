# Prepwright: rules for every agent working in this repo

## Real applications never enter git (2026-10-02)

This repo is public, and it is the tool. The owner's job applications, interview tracks
and resumes are not part of it. None of the following is ever committed or pushed:

- A track, a corpus or a transcript from the study store (`~/.prepwright`), anything from
  Resume Studio's `applications/` folder, and any generated document: a
  resume or cover-letter `.tex` or `.pdf`, a FitReport, PortalAnswers, JobDescription or
  Evaluation file, or a `-text.txt` text layer.
- The text of a real posting, of a page the writer filed, or of the owner's resumes and
  masters.
- An employer's or institution's name from a real application, in code, comments, tests,
  docs or commit messages. Name a run by its date, role and job id instead: "the
  2026-09-29 research-engineer run (job 749ab930dc8a)".
- The owner's contact details or identity values.

Test fixtures are synthetic: an invented employer and page in the shape of the real one.

`tools/privacy_gate.py` enforces this as the pre-commit and pre-push hook
(`git config core.hooksPath tools/git-hooks`, set on the owner's machine, and
`tests/test_privacy_gate.py` fails there if it is not). Never bypass it with
`--no-verify`, and never weaken the gate or its local config
(`~/.config/claude-apps/privacy-gate.json`) to let a change through: rewrite the change.

On 2026-10-02 this repo's history was rewritten to take such material down and
force-pushed. A backup bundle of the old history exists outside the repo. Never restore
or push from it.
