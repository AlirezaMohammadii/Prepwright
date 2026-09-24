# ADR 0008 — Rehearsal steps, delivered by a grade

Date: 2026-09-24
Status: accepted

## Context

Prepwright taught concepts and stopped. It had no mock interview, no behavioural
practice and no objection handling. The `prepared` stage was reached by ticking
steps, which proves the candidate clicked, not that he can answer in the room.

The 2026-09-24 live walk on the University of Example application showed what
that costs. The tutor never sees the application, so on the evaluation-protocol
step it coached the candidate to say "ASVspoof wasn't run at all", although his
resume claims ASVspoof evaluations. The session review then wrote the same
sentence into his recap bank. Every teaching turn was `grounded` with `invented`
empty: the grounding check audits citation tokens, and nothing in it can see a
false statement about the candidate that cites nothing.

## Decision

A rehearsal step is a topic step whose id is `R<nn>`
(`config.REHEARSAL_STEP_RE`). `rehearse.plan` writes up to eight of them after
the study steps, once per track, from what the diagnostic already extracts:

1. red-team objections, first, because they end interviews;
2. weak fit rows, as "the role asks for X, and your application does not show it";
3. resume claims (MET rows), as "take us through it, and where does it stop";
4. behavioural prompts, when a requirement starts with a behaviour
   (support, mentor, collaborate, lead…) or the posting lists responsibilities.

Each kind gets two slots before any kind gets more. Application logistics and
eligibility ("submit resume…", "work rights") are not asked. The question is the
step's objective, with a panel persona drawn from the posting's employer and role,
so asking costs nothing.

The candidate's own record becomes corpus. `intake.resume_evidence` keeps the
resume's sections and bullets from the first `\section` on, which leaves out the
header and its contact block, and removes contact-shaped strings anywhere else. It
is written to `intake/<base>_ResumeEvidence.md` at intake, because the folder is
never reopened. `rehearse.store_evidence` ingests it and the fit report as
documents titled "Your application: …", vetting `primary`. Each rehearsal step is
pinned to the evidence closest to its question, then to the study step's slices
for the same gap, then to the rest of the record up to `PACK_MAX_SECTIONS`.
`build_pack` and `check_citations` are unchanged.

An answer is graded by the `rehearse` role (`POST /api/rehearse`, action
`answer`) against a stated rubric of four criteria, each 0 to 2:

- grounded: correct, and supported by his application or the corpus;
- specific: his own numbers;
- structured: situation, task, action, result, or claim, evidence, limit;
- concise: about 250 words spoken. This one is counted, not judged.

The reply carries what would cost him in the room, a strong answer of at most 180
words built only from the pack, with a citation per factual sentence, and the
panel's next question. A citation the strong answer invents is reported as it is
for a teaching turn, and it never moves the score.

The grade is written to `assessment` with rubric `rehearsal:<provider>/<model>`.
`state.sync_step_lifecycle` delivers a rehearsal step when its best rehearsal
grade reaches `REHEARSAL_PASS` (0.75, 6 of 8). A criterion at 0 caps the grade at
5 of 8 (`CAP_WITH_A_ZERO`), so the store and the page's "ready" agree on one
number. A tick, or a teaching grade, never delivers it. The steps
sit in the step table, so "finished means the written plan" still holds and
`prepared` now needs demonstrated answers.

`GET /api/brief` builds the day-before page from the store alone:
- the top study concepts, with a citation each;
- five stories, taken from experience lines that carry a number and mapped to the
  requirement each one answers;
- the three likeliest objections, each with the strong answer from its graded
  rehearsal, if one exists;
- up to three questions for him to ask them, each quoting the posting line or
  fit-report row it rests on.

## Consequences

- A sixth model role, `rehearse`, with its own call site. `test_model_roles` pins
  one call site per role. It defaults to `claude-sonnet-5` at low effort rather
  than the grader's haiku, because it also writes the answer the candidate will
  say. The two live grades on 2026-09-24 cost $0.051 and $0.031.
- A schema call that runs out of turns is retried once (`retry_on_max_turns`),
  for the reason `provider.CLI_SCHEMA_MAX_TURNS` records. The second live grade
  failed exactly that way before the retry existed.
- Tracks planned before this change get the steps from "Add interview
  rehearsal". Their intake has no resume evidence, so their packs hold the fit
  report only.
- No schema change. A rehearsal step is `tier='core'` in the table, and the page
  gives it its own block from the bridge's `rehearse` flag.
