"""Progress scoring, end-of-session review, and recap cards.

Advisory, never authoritative: the candidate ticks a step off, not the grader.
A fabricated recap card is worse than no card, because it gets drilled as true.

Both calls here are schema-constrained, which costs two CLI turns rather than
one: the model answers, then it emits the structured output. Until ADR 0005 the
bound was 1 and every one of these paths had never completed on any machine.

Extracted from bridge.py in ADR 0007. `_persist_assessment` takes its
current-track opener as an argument, because grading a transcript and deciding
which track the grade lands in are two decisions and only the second belongs to
the server. That argument is also what lets this module sit below the request
boundary in the import order instead of reaching back up to it.

`run_cli`, `_role_choice` and `_usage` are bound here as module-level names
rather than reached for through the provider module, so a test that fakes a
model reply patches the name in the module whose code reads it.
tests/test_extraction_seams.py fails the suite if one is patched elsewhere.

Import direction is one-way. Nothing in this package imports bridge.
"""

import json
import sqlite3
import sys

from prepwright import provider as PPROV
from prepwright import security as PSEC
from prepwright import state as PSTATE

PROVIDER_MODELS = PPROV.PROVIDER_MODELS
_role_choice = PPROV._role_choice
_usage = PPROV._usage
run_cli = PPROV.run_cli


# ---- progress assessment ---------------------------------------------------
# Graded on Haiku on purpose: this is a mechanical read of a transcript, not
# teaching, and it runs over many steps at once. The page says which model
# graded, and the verdict is advisory — the candidate still presses the button
# that marks a step delivered.
ASSESS_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "mastery": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["key", "mastery", "reason"],
            },
        }
    },
    "required": ["steps"],
})


ASSESS_SYSTEM = (
    "You grade a candidate's tutoring transcripts from a track that prepares them for one "
    "specific technical interview.\n"
    "Each step has a defined task. Score ONLY how much of THAT task the student has "
    "delivered. This is the whole point of the grade: the candidate asks many side questions "
    "in whichever step box is open — about other steps, other topics, general background — "
    "and those questions are NOT progress on this step's task, however long or thoughtful "
    "the exchange is. A step where the student asked twenty good questions about something "
    "else has delivered nothing and scores 0.0.\n"
    "mastery: 1.0 = answered the step's task correctly in their own words; 0.6 = engaged with "
    "the task and mostly right, one gap; 0.3 = touched the task but has not answered it; "
    "0.0 = the task was not addressed, including when the conversation went elsewhere.\n"
    "reason: at most 12 words, concrete, and say so plainly when the chat was off-task. "
    "Do not be generous — an unearned pass costs them a real interview. Reply only with JSON."
)


MAX_ASSESS_STEPS = PSEC.MAX_ASSESS_STEPS


ASSESS_EXCERPT = PSEC.ASSESS_EXCERPT


# Graded in small batches rather than one big call. The model reasons per step
# before emitting JSON, so one large request produces far more output than the
# rows need, and a single over-long reply fails the whole run and loses every
# grade in it. Batches keep each reply short and make a failure partial.
ASSESS_BATCH = 5


def _digest(it):
    """The task first, then what the student actually said about it.

    Several of the student's own messages go over, not just the last one: the
    last message is often a tangent, and judging task delivery from a tangent
    scores the wrong thing. The tutor's replies are represented by one excerpt
    because they are the expensive half and only needed for tone.
    """
    said = [str(x)[:ASSESS_EXCERPT] for x in (it.get("said") or [])][-3:]
    return (
        "key: %s\nstep: %s\nTHE TASK: %s\nturns in this step: %s\n"
        "what the student said (oldest first):\n%s\nlast tutor reply: %s"
    ) % (
        str(it.get("key", ""))[:80],
        str(it.get("title", ""))[:120],
        str(it.get("task", ""))[:600] or "(none given)",
        int(it.get("turns", 0) or 0),
        "\n".join("  - " + s for s in said) or "  (nothing)",
        str(it.get("tutor", ""))[:ASSESS_EXCERPT] or "(none)",
    )


def _persist_assessment(graded, provider, model, opener):
    """Write the grades to the assessment table. Returns how many landed.

    `opener` is the current-track opener, passed in rather than imported. This
    module grades a transcript; deciding WHICH track the grade is written to is
    the caller's business, and taking it as an argument is what lets this file
    sit below the server in the import order instead of reaching back up to it.

    Best effort, deliberately. By the time this runs the model call is paid
    for, so a store failure must not turn a grade the candidate has bought into
    a 502. It is reported to the terminal and the grade still reaches the page,
    where it is durable as a page mark either way.

    Until 2026-09-09 this route opened no handle at all, so the assessment
    table had exactly two writers and both were tests. The grade lived only as
    page state: one superseded value, no history, and nothing to rebuild the
    panel from after a reload.
    """
    try:
        handle = opener(take_lease=False)
    except (PSTATE.StoreError, sqlite3.Error, OSError) as exc:
        sys.stderr.write("tutor: grades not written to the store: %s\n" % exc)
        return 0
    try:
        known = set()
        for row in handle.conn.execute("SELECT step_id FROM step"):
            known.add(row["step_id"])
        rubric = "%s/%s" % (provider, model)
        landed = 0
        for row in graded:
            key = str(row.get("key") or "")
            # assessment.step_id is a foreign key and PRAGMA foreign_keys is on
            # every open, so a grade naming a step this track does not have
            # would abort the statement. Filtered here so one stale key cannot
            # cost the grades that ARE valid.
            if key not in known:
                continue
            try:
                handle.add_assessment(
                    key, float(row.get("mastery") or 0.0), rubric,
                    misconception=(str(row.get("reason") or "")[:400] or None))
                landed += 1
            except (PSTATE.StoreError, sqlite3.Error) as exc:
                sys.stderr.write("tutor: grade for %s not stored: %s\n" % (key, exc))
        return landed
    finally:
        handle.close()


def _clean_rows(rows):
    """Grader rows, bounded to what a grade can actually mean.

    ASSESS_SCHEMA asks for 0..1, but a schema is a request and not a guarantee:
    the first real grading call this project ever made returned mastery 45 for
    what the reason text described as a partial answer. The page clamps into
    state.assess and does NOT clamp state.assessList, so an unbounded number
    reaches the panel as "4500%" and arms the Mark done button, which is gated
    on mastery >= 0.85.

    The three cases, and why each resolves the way it does. A value in 1..100
    is a percent written where a fraction was asked for, so it is divided; 45
    becomes 0.45. Clamping it to 1.0 instead would turn a mediocre grade into a
    perfect one and invite the candidate to tick a step they have not
    delivered, which the grader's own system prompt calls the expensive
    mistake. Anything else -- negative, above 100, NaN, unparseable -- is a
    grader that is confused, and a confused grader gets no vote: the row is
    dropped and the step reads as ungraded rather than as a score nobody meant.
    """
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            m = float(r.get("mastery"))
        except (TypeError, ValueError):
            continue
        if m != m:                      # NaN compares unequal to itself
            continue
        if 1.0 < m <= 100.0:
            m = m / 100.0
        if not 0.0 <= m <= 1.0:
            continue
        key = str(r.get("key", ""))[:80]
        if not key:
            continue
        out.append({"key": key, "mastery": m,
                    "reason": str(r.get("reason", ""))[:200]})
    return out


def _assess_batch(provider, items):
    prompt = "Grade each step. Return one object per step, nothing else.\n\n" + \
        "\n\n---\n\n".join(_digest(it) for it in items)
    model, effort = _role_choice(provider, "assess")
    data = run_cli(provider, model, ASSESS_SYSTEM, prompt,
                   effort=effort, schema=ASSESS_SCHEMA)
    try:
        parsed = json.loads(data.get("result") or "{}")
    except ValueError:
        parsed = {}
    # A top-level array parses fine and then makes .get raise AttributeError,
    # which escapes the ValueError-only guard above and costs both attempts.
    if not isinstance(parsed, dict):
        parsed = {}
    return _clean_rows(parsed.get("steps")), _usage(data)


def assess_via_cli(provider, items):
    """Grade steps from a compact digest, in batches.

    A digest, not the transcripts: the last exchange plus a turn count is what a
    grade turns on, and shipping full logs for every step would cost more than
    the chat turns that produced them.

    One retry per batch, then that batch is skipped. A partial grade is worth
    more to the candidate than an error where a number should be, and the steps
    that did come back still move the percentage.
    """
    if provider not in PROVIDER_MODELS:
        raise ValueError("Unknown tutor provider.")
    items = items[:MAX_ASSESS_STEPS]
    graded, total = [], {"in": 0, "cached": 0, "out": 0,
                         "cost": 0.0, "costKnown": True}
    failed = 0
    for i in range(0, len(items), ASSESS_BATCH):
        chunk = items[i:i + ASSESS_BATCH]
        for attempt in (1, 2):
            try:
                rows, usage = _assess_batch(provider, chunk)
                # Usage is charged before the row check on purpose: the call
                # was made and billed whether or not it came back usable, and
                # the old order discarded the cost of every failed attempt.
                for k in ("in", "cached", "out"):
                    total[k] += usage[k]
                if usage.get("costKnown") and usage.get("cost") is not None:
                    total["cost"] += usage["cost"]
                else:
                    total["cost"] = None
                    total["costKnown"] = False
                if not rows:
                    # A reply that parsed but carried no usable row is a
                    # failure, not an empty success. Treating it as success
                    # skipped the `failed` counter below, so `capped` stayed 0
                    # and the toast read "13 steps graded" with no suffix while
                    # five steps silently sat at 0%, indistinguishable from
                    # steps that were never in scope.
                    raise RuntimeError("grader returned no usable rows")
                graded.extend(rows)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    failed += len(chunk)
                    sys.stderr.write("tutor: assess batch failed (%s)\n" % str(e)[:160])
    if total["cost"] is not None:
        total["cost"] = round(total["cost"], 5)
    return graded, total, failed


# ---- end-of-session review -------------------------------------------------
# The recap bank could be filled by hand: write a session entry with three
# questions in it after every session. That does not survive contact with a
# tired student at the end of a long session, and a spaced-repetition deck
# nobody fills is a drill nobody opens. So the review writes itself.
#
# So the server writes it. Same reasoning as the grader above: this is a
# mechanical read of a transcript, not teaching, so it runs on the cheap model
# under a schema. The candidate still presses the button, and still edits or
# deletes what comes back — the page treats the result as a draft.
REVIEW_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "covered": {"type": "string"},
        "gotRight": {"type": "string"},
        "slipped": {"type": "string"},
        "next": {"type": "string"},
        "recap": {
            "type": "array",
            "minItems": 2,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "a": {"type": "string"},
                },
                "required": ["q", "a"],
            },
        },
    },
    "required": ["covered", "gotRight", "slipped", "next", "recap"],
})


REVIEW_SYSTEM = (
    "You close out one tutoring session for a candidate preparing for one specific technical "
    "interview. You are given the step's task and the whole transcript of that step.\n"
    "covered: what was actually taught, 40 words at most, concrete, naming topics or concepts. "
    "Not what the step intended to cover — what the transcript shows.\n"
    "gotRight: what the student demonstrably explained in their own words, 25 words at most. "
    "If they explained nothing back, say so plainly. An unearned pass costs them a real "
    "interview.\n"
    "slipped: the specific errors, gaps or unanswered questions, 25 words at most. Name them. "
    "If nothing slipped, say nothing slipped.\n"
    "next: the single most useful thing to do next, 20 words at most.\n"
    "recap: two or three questions for spaced repetition, drawn ONLY from what this transcript "
    "actually covered. Each q is one sentence a person could be asked in an interview; each a is "
    "the correct answer in at most 30 words. No question whose answer is not in the transcript.\n"
    "Never invent sources, numbers or evidence. Reply only with JSON."
)


REVIEW_MAX_TURNS = 40


REVIEW_TURN_CHARS = 1_400


def review_via_cli(provider, step, messages):
    """One schema-bound session review. Returns (review_dict, usage).

    Cheap model on purpose (same rationale as assess_via_cli): reading a
    transcript back is mechanical. One retry, then the caller reports failure
    rather than inventing a review, because a fabricated recap card is worse
    than no card at all.
    """
    if provider not in PROVIDER_MODELS:
        raise ValueError("Unknown tutor provider.")
    step = step if isinstance(step, dict) else {}
    turns = messages[-REVIEW_MAX_TURNS:]
    lines = []
    for m in turns:
        who = "Student" if m.get("role") == "user" else "Tutor"
        lines.append("%s: %s" % (who, m.get("content", "")[:REVIEW_TURN_CHARS]))
    prompt = (
        "STEP: %s\nTITLE: %s\nTHE TASK: %s\n\nTRANSCRIPT (oldest first):\n%s"
    ) % (
        " ".join(str(step.get("kind") or "step").split())[:80],
        " ".join(str(step.get("title") or "Untitled").split())[:160],
        " ".join(str(step.get("prompt") or "").split())[:800] or "(none given)",
        "\n".join(lines),
    )
    model, effort = _role_choice(provider, "review")
    last = None
    for attempt in (1, 2):
        try:
            data = run_cli(provider, model, REVIEW_SYSTEM, prompt,
                           effort=effort, schema=REVIEW_SCHEMA)
            parsed = json.loads(data.get("result") or "{}")
            recap = [r for r in (parsed.get("recap") or [])
                     if isinstance(r, dict) and r.get("q") and r.get("a")]
            if not recap:
                raise ValueError("review returned no recap questions")
            return {
                "covered": str(parsed.get("covered") or "")[:600],
                "gotRight": str(parsed.get("gotRight") or "")[:400],
                "slipped": str(parsed.get("slipped") or "")[:400],
                "next": str(parsed.get("next") or "")[:300],
                "recap": [{"q": str(r["q"])[:300], "a": str(r["a"])[:400]}
                          for r in recap[:3]],
                "model": model,
            }, _usage(data)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt == 2:
                sys.stderr.write("tutor: session review failed (%s)\n" % str(e)[:160])
    raise RuntimeError("The reviewer returned nothing usable.") from last
