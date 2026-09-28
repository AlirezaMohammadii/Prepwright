"""Curriculum generation, ordering, and the Pareto cut.

Turns an approved gap list plus a corpus into an ordered set of steps. Cuts to
the smallest set carrying most of the value, and marks every step core, depth or
reference so the candidate can see what is load-bearing.

Three rules shape everything here.

**Order by dependency, not by importance.** A concept whose prerequisite is
unlearned is unteachable, so the most important gap on the list is often not the
one to teach first. Importance decides what gets cut; dependency decides the
order of what survives. What the posting and its red team press moves a step's
tier by one at most (`emphasis`), and never whether it is planned, deferred or
cut.

**A step pins the sections it teaches from, and no others.** `corpus.pin_all` was
the placeholder: every section of every ready document, pinned to every step.
`build_pack` then takes the first ten by `ord` and stops, so under a placeholder
the pack is whichever sections happened to be written first, not the ones the
step is about. Choosing here is what makes the `ord` mean something.

**A step with nothing to teach from is not a step.** It is deferred and named as
needing research, rather than created with an empty slice and left to refuse
itself at the moment the candidate opens it.

Nothing here calls a model or reaches the network. Prerequisites can be declared
by one (the schema and the prompt are below) or left out entirely, in which case
the ordering falls back to the gap list's own order, which the candidate
approved.
"""

import re

from . import config as C
from . import diagnose as D


class CurriculumRefused(ValueError):
    """The plan cannot be built. The message says what is missing."""


# ---- terms ------------------------------------------------------------------
_WORD = re.compile(r"[a-z0-9][a-z0-9+.#-]{1,}")
# Deliberately short. A long stop list starts deleting the words that make a
# security posting specific ("control", "risk", "assurance" are content here).
_STOP = frozenset("""
a an and are as at be been but by can could do does for from had has have how
if in into is it its may might must of on or should so than that the their them
then there these they this those to was were what when which who will with
would you your able about across also any been being both each into more most
not only other over same some such take than through under upon use used using
very want ways well within work works
""".split())


def terms(text):
    """The scoring vocabulary of one piece of text. Order-free, duplicates kept.

    Duplicates are kept because a section that says "calibration" four times is
    more about calibration than one that says it once, and dropping the repeats
    throws that away before it can be counted.
    """
    return [w for w in _WORD.findall(str(text or "").lower())
            if w not in _STOP and len(w) > 2]


def _counts(tokens):
    out = {}
    for t in tokens:
        out[t] = out.get(t, 0) + 1
    return out


# ---- reading the corpus -----------------------------------------------------
def corpus_index(handle):
    """Every ready section of this track, with the text used to score it.

    Reads through the handle it was given and nothing else, so a section from
    another track cannot enter a plan. `read_section` raises IsolationError on a
    token this handle does not own, and a section that will not read is skipped
    rather than pinned: pinning it produces a step whose pack is short by one
    block with nothing saying why.
    """
    rows = handle.conn.execute(
        "SELECT s.doc_id, s.sec_id, s.heading, s.concept, s.ord,"
        "       d.title, d.trust, d.vetting"
        "  FROM section s JOIN doc d ON d.doc_id = s.doc_id"
        " WHERE d.status = 'ready'"
        " ORDER BY s.doc_id, s.sec_id").fetchall()
    out = []
    for r in rows:
        try:
            sec = handle.read_section(r["doc_id"], r["sec_id"])
        except Exception:                                    # noqa: BLE001
            sec = None
        if sec is None:
            continue
        out.append({
            "doc_id": r["doc_id"], "sec_id": r["sec_id"],
            "cite": sec.get("cite") or ("%s§%s" % (r["doc_id"], r["sec_id"])),
            "heading": r["heading"] or "", "concept": r["concept"] or "",
            "doc_title": r["title"] or "", "trust": int(r["trust"] or 1),
            "vetting": r["vetting"] or "community",
            "body": sec.get("body") or "",
        })
    return out


# A section's own weight, before any query touches it. The numbers are the ones
# DESIGN-state-corpus.md:682 already uses for retrieval, so a slice chosen here
# and a slice retrieved there rank the same sources the same way.
VETTING_WEIGHT = {"primary": 1.00, "secondary": 0.92, "vendor": 0.85,
                  "community": 0.78}


def _document_frequency(index):
    """How many sections each term appears in. The basis of the rarity weight.

    Without it, "security" scores every section of a security corpus equally and
    the slice is decided by section length. With it, the terms that separate one
    section from another are the ones that count.
    """
    df = {}
    for sec in index:
        # The SAME four fields `score_sections` scores against, doc_title
        # included. It was counted over heading, concept and body only while
        # scoring weighted a doc_title hit at 2.0, so a term living in document
        # titles and nowhere else never entered df at all. `df.get(term, 1)`
        # then returned the sentinel meant for "appears in exactly one section",
        # and the rarity weight handed 1 + sqrt(n) -- the largest value this
        # corpus can produce -- to the word shared by the most documents.
        #
        # The consequence was not noise, it was eviction: on a corpus of one
        # multi-section standard, every section of it cleared RELATIVE_FLOOR
        # together and filled PACK_MAX_SECTIONS, pushing the genuinely matching
        # sections out of the step's slice. Counting titles here is what makes
        # the comment below true rather than aspirational.
        for t in set(terms(sec["heading"] + " " + sec["concept"] + " "
                           + sec["doc_title"] + " " + sec["body"])):
            df[t] = df.get(t, 0) + 1
    return df


# A section has to be about the step, not merely share a word with it. Two
# thresholds, because one is not enough.
#
# MIN_TERMS: a single common word in a body is a coincidence. The exception is a
# hit in the heading, the concept or the document title, where the author is
# saying what the text is for and one word is meaningful.
#
# RELATIVE_FLOOR: a section scoring a twentieth of the best one is not the same
# kind of match, and pinning it fills the pack with near-misses that push the
# real sections past PACK_MAX_SECTIONS. Relative and not absolute, because these
# scores are not normalised and an absolute cut would be a different rule on
# every corpus.
MIN_TERMS = 2
RELATIVE_FLOOR = 0.25


def term_floor(query):
    """How many of the goal's words a section must share to count.

    MIN_TERMS, except when the goal has fewer words than that. A flat floor of 2
    is UNSATISFIABLE for a one-word goal: no section can match two words of a
    one-word query, so every section is dropped, `select` refuses the whole file
    and tells the candidate the resource "shares no vocabulary with the goal".
    That claim is false, and worse it is unactionable, because rewording cannot
    add a second word to a goal that is legitimately one word. "Kubernetes" is a
    reasonable thing to prepare for.

    Scaling it keeps the original rule everywhere it was doing work -- a single
    common word in a body is still a coincidence when the goal has several -- and
    stops it from being a gate nothing can pass.
    """
    return min(MIN_TERMS, len(_counts(terms(query))) or MIN_TERMS)


def document_frequency(index):
    """How many sections each term appears in. Public because callers batch.

    `score_sections` computes this itself when it is not given one, which is the
    right default for a single query. A caller scoring twenty gaps against the
    same corpus would then rebuild it twenty times, so it is exposed rather than
    reached for through the private name.
    """
    return _document_frequency(index)


def score_sections(index, query, df=None):
    """Rank every section against one step's title and objective.

    Three weights. A section heading is the author saying what the section is
    for, so it counts most. The DOCUMENT title counts next: it cannot separate
    two sections of the same document, but separating documents is most of the
    work, and leaving it out is how a step about "data protection and privacy
    principles" pinned ISO 42001's Annex A controls while an Australian Privacy
    Principles document sat in the same corpus unread. Its section headings say
    "APP 11 security of personal information", which shares no word with the
    query; its title says "Australian Privacy Principles", which shares two.

    Ties break on (doc_id, sec_id) ascending, the rule the design already fixes
    for retrieval, so the same step always produces the same slice.
    """
    if df is None:
        df = _document_frequency(index)
    n = max(1, len(index))
    want = _counts(terms(query))
    if not want:
        return []
    floor = min(MIN_TERMS, len(want))
    scored = []
    for sec in index:
        head = _counts(terms(sec["heading"] + " " + sec["concept"]))
        title = _counts(terms(sec["doc_title"]))
        body = _counts(terms(sec["body"]))
        total, matched, labelled = 0.0, 0, False
        for term, times in want.items():
            hits = (3.0 * head.get(term, 0) + 2.0 * title.get(term, 0)
                    + 1.0 * body.get(term, 0))
            if not hits:
                continue
            matched += 1
            if head.get(term) or title.get(term):
                labelled = True
            # Rarity, bounded. A term in every section carries no information;
            # a term in one carries the most this corpus can offer.
            rarity = 1.0 + (n / float(df.get(term, 1))) ** 0.5
            total += times * hits * rarity
        if total <= 0 or (matched < floor and not labelled):
            continue
        total *= VETTING_WEIGHT.get(sec["vetting"], 0.78)
        total *= 0.9 + 0.05 * max(1, min(5, sec["trust"]))
        scored.append((total, sec))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["doc_id"], pair[1]["sec_id"]))
    return scored


def relevant(scored, limit=None, byte_budget=None):
    """The scored sections a step should actually teach from.

    Everything below `RELATIVE_FLOOR` of the best score is dropped. `limit`
    defaults to the pack's own section cap, because pinning more than the pack
    can carry is not harmless: `build_pack` walks `step_slice` by `ord` and stops
    at the cap, so everything past it is invisible while still looking pinned.
    """
    if not scored:
        return []
    limit = C.PACK_MAX_SECTIONS if limit is None else max(1, int(limit))
    budget = C.PACK_MAX_BYTES if byte_budget is None else max(1, int(byte_budget))
    best = scored[0][0]
    out, used = [], 0
    for score, sec in scored[:limit]:
        if score < best * RELATIVE_FLOOR:
            break
        # Bytes, because `build_pack` counts bytes. Ten sections of
        # SECTION_MAX_CHARS fit PACK_MAX_BYTES in ASCII and do not in anything
        # else, so on a source with curly quotes or an accented word the last
        # pinned sections were invisible: `build_pack` walks `step_slice` by
        # `ord` and stops at the cap, so they still LOOKED pinned. The first
        # section is always kept, otherwise a single large one would pin nothing.
        cost = (len((sec.get("heading") or "").encode("utf-8"))
                + len((sec.get("body") or "").encode("utf-8"))
                + C.PACK_BLOCK_OVERHEAD)
        if out and used + cost > budget:
            break
        out.append(sec)
        used += cost
    return out


def choose_slices(index, title, objective, limit=None):
    """The sections one step teaches from, best first."""
    return relevant(score_sections(index, "%s %s" % (title or "", objective or "")),
                    limit=limit)


# ---- prerequisites ----------------------------------------------------------
PREREQ_SCHEMA = {
    "type": "object",
    "properties": {
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "gap_id": {"type": "string"},
                    "needs_first": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["gap_id", "needs_first"],
            },
        },
    },
    "required": ["edges"],
}

PREREQ_SYSTEM = (
    "You are ordering a study plan. For each gap, say which OTHER gap on the"
    " same list must be understood first, if any.\n"
    "Declare an edge only for a real prerequisite: the candidate cannot"
    " understand the first thing without the second. Do NOT declare an edge"
    " because one topic is more important, more urgent, more general, or"
    " usually taught earlier. Importance decides what gets cut. Dependency"
    " decides the order of what is left, and an edge that is really about"
    " importance produces a plan that teaches the wrong thing first and is"
    " impossible to argue with afterwards.\n"
    "Most gaps have no prerequisite. An empty list is the common answer."
)


def prereq_prompt(gaps):
    lines = ["%s: %s" % (g["gap_id"], (g["label"] or "")[:200]) for g in gaps]
    return "%s\n\nThe gaps:\n%s" % (PREREQ_SYSTEM, "\n".join(lines))


def order_gaps(gaps, edges=()):
    """A dependency order, deterministic, with cycles broken in the open.

    Kahn's algorithm with a stable tie-break, so the same gaps and the same
    edges always produce the same plan. An edge naming a gap that is not on the
    list is dropped: it is a model naming something it invented, and honouring
    it would reorder the plan around a prerequisite that does not exist.

    A cycle is not an error. It is what a model produces when it declares
    importance as dependency. The edge that closes it is dropped by a fixed rule
    and reported, because a plan that silently reorders itself around a
    contradiction is worse than one that says which contradiction it found.
    """
    order = {g["gap_id"]: i for i, g in enumerate(gaps)}
    by_id = {g["gap_id"]: g for g in gaps}
    kept, dropped = [], []
    seen = set()
    for e in edges or ():
        if not isinstance(e, dict):
            dropped.append({"edge": repr(e)[:80], "why": "not an edge"})
            continue
        a, b = str(e.get("gap_id") or ""), str(e.get("needs_first") or "")
        if a not in by_id or b not in by_id:
            dropped.append({"edge": "%s<-%s" % (a, b), "why": "names a gap that is not on the list"})
            continue
        if a == b:
            dropped.append({"edge": "%s<-%s" % (a, b), "why": "depends on itself"})
            continue
        if (a, b) in seen:
            continue
        seen.add((a, b))
        kept.append((a, b))

    # Kahn, ties broken by the candidate's own approved order.
    incoming = {gid: set() for gid in by_id}
    outgoing = {gid: set() for gid in by_id}
    for a, b in kept:
        incoming[a].add(b)
        outgoing[b].add(a)

    ready = sorted((g for g in by_id if not incoming[g]), key=lambda g: order[g])
    out, broken = [], []
    while ready:
        gid = ready.pop(0)
        out.append(by_id[gid])
        for nxt in sorted(outgoing[gid], key=lambda g: order[g]):
            incoming[nxt].discard(gid)
            if not incoming[nxt]:
                ready.append(nxt)
                ready.sort(key=lambda g: order[g])
    if len(out) < len(gaps):
        stuck = sorted((g for g in by_id if g not in {x["gap_id"] for x in out}),
                       key=lambda g: order[g])
        for gid in stuck:
            for dep in sorted(incoming[gid]):
                broken.append({"edge": "%s<-%s" % (gid, dep),
                               "why": "part of a cycle; dropped to make the plan buildable"})
            out.append(by_id[gid])
    return out, {"kept": len(kept), "dropped": dropped, "cycles_broken": broken}


# ---- tiers and the cut ------------------------------------------------------
# core      the interview is lost without it
# depth     it is shaky and the room will find the edge
# reference worth having, not worth a lesson before the others
TIERS = ("core", "depth", "reference")


def tier_for(gap, stress=0):
    """Level first, then where the gap came from, then what the panel presses.

    An unlearned requirement the posting states loses the interview at the
    screen. An undefended claim on the candidate's own resume loses the room
    once they are already in it, which is later and therefore second. A gap the
    posting keeps coming back to, or the red team expects a panel to raise,
    moves up one tier (`emphasis`): never above core, never down.
    """
    level = (gap.get("level") or "none").lower()
    from_posting = bool(gap.get("jd_span"))
    if level == "none":
        tier = "core" if from_posting else "depth"
    elif level == "shaky":
        tier = "depth" if from_posting else "reference"
    else:
        tier = "reference"
    return _UP[tier] if stress >= 1 else tier


# ---- what the panel presses -------------------------------------------------
# Level and source say how much a gap would cost. They said nothing about how
# hard this posting leans on it, or whether the red team expects a panel to
# probe it, and the 2026-09-24 walk on the University of Example role showed
# that half of the rank missing. Both are counted here, never judged.
_UP = {"reference": "depth", "depth": "core", "core": "core"}
_CLAUSE = re.compile(r";|\n|(?<=[.!?])\s+")
_PLAIN = re.compile(r"[a-z0-9]+")


def _marks(text):
    """terms() for matching one line against another. A trailing '.' goes, and
    a hyphenated word also counts as its parts, or the red team's "PhD-in-hand"
    never meets the posting's "PhD" and "funding." never meets "funding"."""
    out = set()
    for w in terms(text):
        w = w.strip(".-")
        out.update(t for t in [w] + w.split("-") if len(t) > 2 and t not in _STOP)
    return out


def _clauses(posting):
    """(heading, clause) for every clause of the posting, with the heading it
    sits under, so a line under "Desirable" can be told from one under
    "Essential"."""
    heading, out = "", []
    for line in str(posting or "").split("\n"):
        if D._looks_like_a_heading(line):
            heading = line.strip()
            continue
        out += [(heading, " ".join(c.split())) for c in _CLAUSE.split(line)
                if len(c.strip()) >= 12]
    return out


def is_process(text):
    """Application logistics or eligibility (config.PROCESS_PHRASES)."""
    low = " ".join(_PLAIN.findall(str(text or "").lower()))
    return any(" ".join(_PLAIN.findall(p)) in low for p in C.PROCESS_PHRASES)


def emphasis(gap, posting="", fit=None):
    """(stress, reasons): how hard the posting and the red team press a gap.

    - A red-team objection is the question the panel is expected to ask: +1.
    - Two or more posting clauses carrying enough of the gap's words: +1.
    - A red-team objection naming it: +1. For a weak fit row one word of its
      own is enough, a word no other row of the matrix uses; for a claim it
      takes the same floor a clause does, because a claim shares ordinary
      words with any objection.
    - Every clause that matched marks it desirable: -1.

    A claim is read as the requirement it answers, from the fit report's
    matrix. Logistics are never pressed. `tier_for` reads only whether the sum
    reaches 1; the reasons are for the candidate.
    """
    fit = fit or {}
    label = (gap.get("label") or "").strip()
    if not label or is_process(label):
        return 0, []
    red = [str(r or "").strip() for r in fit.get("red_team") or ()]
    for i, risk in enumerate(red, start=1):
        if risk and risk[:200] == label[:200]:
            return 1, ["red team objection %d: the panel is expected to raise it" % i]
    about = next((str(r.get("requirement") or "") for r in fit.get("met") or ()
                  if str(r.get("evidence") or "").strip()[:200] == label[:200]), label)
    want = _marks(about)
    if not want or is_process(about):
        return 0, []
    floor = min(MIN_TERMS, len(want))
    stress, why = 0, []
    hits = [(h, c) for h, c in _clauses(posting) if len(want & _marks(c)) >= floor]
    if len(hits) >= 2:
        stress += 1
        why.append("the posting comes back to it %d times" % len(hits))
    one_word = str(gap.get("jd_span") or "").startswith("fit:")
    rows = [_marks(r.get("requirement")) for r in fit.get("rows") or ()]
    own = {t for t in want if sum(t in r for r in rows) <= 1}
    for i, risk in enumerate(red, start=1):
        shared = want & _marks(risk)
        if len(shared) >= floor or (one_word and shared & own):
            stress += 1
            why.append("red team objection %d names %s"
                       % (i, ", ".join(sorted((shared & own) or shared)[:3])))
            break
    if hits and all(any(w in D._flat(h + " " + c) for w in D._NICE_WORDS)
                    for h, c in hits):
        stress -= 1
        why.append("the posting marks it desirable")
    return stress, why


# A step id is a STEP KEY, and the two sides have to agree or the plan cannot be
# taught. `pagestate.STEP_KEY_RE` is the contract the page mints against and the
# bridge validates: `<stage>:<kind>:<id>`. The first version of this module
# minted "s01", which every route that teaches rejects before it reads the body,
# so a plan built here was a plan nothing could open. Stage numbering is one
# stage per step for now, because the store has no stage object and inventing
# one here would be a second source of truth for something the curriculum does
# not yet decide.
STEP_KIND = "topic"


def step_key(ordinal):
    return "%d:%s:S%02d" % (min(99, max(1, int(ordinal))), STEP_KIND, int(ordinal))


def plan(gaps, index, edges=(), max_steps=None, est_minutes=25, posting="", fit=None):
    """The ordered, tiered, sliced plan, plus everything it could not build.

    Deferral is never deletion. A gap with no usable corpus keeps its row and is
    returned in `deferred` with the reason, which is also the research list: the
    curriculum is the thing that knows what is missing.

    `posting` and `fit` (the parsed fit report) feed `emphasis`, which moves a
    tier and nothing else. Each step and each deferral carries its `pressed`
    reasons, so the research list says which missing source the panel presses.
    """
    if not gaps:
        raise CurriculumRefused(
            "there are no approved gaps, so there is nothing to plan")
    # The store enforces MAX_STEPS inside `add_step`, one step at a time, with
    # no transaction across the batch. A caller asking for more than that used
    # to commit the first forty steps and their slices, then raise, and `build`
    # never returned the report naming what was cut: the gaps past the cap had
    # neither a step nor a deferral, which is the one outcome the owner ruled
    # out. So the ceiling is applied HERE, where the cut is reported.
    requested = C.MAX_STEPS if max_steps is None else max(1, int(max_steps))
    max_steps = min(requested, C.MAX_STEPS)
    ordered, edge_report = order_gaps(gaps, edges)
    df = _document_frequency(index) if index else {}

    steps, deferred = [], []
    for gap in ordered:
        title = (gap.get("label") or "").strip()[:160] or gap["gap_id"]
        why = (gap.get("why") or "").strip()[:400]
        # `why` is the DIAGNOSTIC's reason this is a gap, not a learning
        # objective, and the two were the same field until it started showing.
        # For an ungraded probe it reads "No answer given."; for the fallback
        # grader it reads "graded without a model: length only". Both were
        # written into the step objective, where the tutor read them out as the
        # task, and both were concatenated into the query that chooses which
        # sections the step teaches from, where their words scored against the
        # corpus as if they described the topic.
        #
        # So it is used for neither unless it carries something. A why with real
        # content -- a judge explaining what was thin about an answer -- is
        # genuinely about the topic and helps both jobs, and is kept.
        informative = len(terms(why)) >= 3 and not why.lower().startswith(
            ("no answer", "graded without a model", "not graded"))
        query = ("%s %s" % (title, why)) if informative else title
        scored = score_sections(index, query, df=df) if index else []
        slices = relevant(scored)
        stress, pressed = emphasis(gap, posting, fit)
        if not slices:
            deferred.append({"gap_id": gap["gap_id"], "label": title,
                             "reason": "no corpus in this track teaches it yet",
                             "pressed": pressed})
            continue
        steps.append({
            "gap_id": gap["gap_id"],
            "title": title,
            # The specificity lives in the title, which is the thing the posting
            # actually asks for. The objective says what DELIVERED means, which
            # is the same sentence for every step and is the honest answer:
            # explaining it back is the bar, and it is the bar for all of them.
            "objective": ("Explain this in your own words and answer one"
                          " follow-up on it."
                          + ((" What is thin about it now: " + why)
                             if informative else "")),
            "tier": tier_for(gap, stress),
            "pressed": pressed,
            "est_minutes": int(est_minutes),
            "slices": slices,
        })

    # The cut. Order is already dependency-correct, so cutting from the tail
    # cannot orphan a step whose prerequisite was kept.
    if len(steps) > max_steps:
        reason = ("beyond the %d-step plan" % max_steps if requested <= max_steps
                  else "beyond the %d steps one track can hold, and %d were asked"
                       " for" % (C.MAX_STEPS, requested))
        for step in steps[max_steps:]:
            deferred.append({"gap_id": step["gap_id"], "label": step["title"],
                             "reason": reason, "pressed": step["pressed"]})
        steps = steps[:max_steps]

    for i, step in enumerate(steps, start=1):
        step["ord"] = i
        step["step_id"] = step_key(i)
    return {
        "steps": steps,
        "deferred": deferred,
        "edges": edge_report,
        "tiers": {t: sum(1 for s in steps if s["tier"] == t) for t in TIERS},
        "minutes": sum(s["est_minutes"] for s in steps),
    }


def write(handle, built):
    """Write the plan: one step per entry, and its slices in relevance order.

    The `ord` on a slice is not decoration. `build_pack` walks `step_slice` by
    it and stops at the pack cap, so it decides which sections a teaching turn
    actually sees.
    """
    written = []
    for step in built["steps"]:
        handle.add_step(step["step_id"], step["ord"], step["title"],
                        step["objective"], tier=step["tier"],
                        gap_id=step["gap_id"], status="ready",
                        est_minutes=step["est_minutes"])
        for rank, sec in enumerate(step["slices"]):
            handle.pin_slice(step["step_id"], sec["doc_id"], sec["sec_id"], rank)
        written.append(step["step_id"])
    return written


WORKABLE = ("approved", "edited")


def check_gaps(handle, gaps):
    """Refuse by name before the first write, not by IntegrityError halfway in.

    `step.gap_id` carries a foreign key to `gap`, and foreign keys are on, so a
    gap that is not in this track's table fails inside `add_step` with
    "FOREIGN KEY constraint failed" and nothing else. `add_step` has no
    enclosing transaction across the batch either, so by the time it fires the
    earlier steps of the plan are already committed and the track is left
    holding half a curriculum.

    Status is checked here for the same reason it exists: a plan built on a
    proposed gap is a plan built on something the candidate has not agreed to
    study, which is the one thing the approval screen is for.
    """
    have = {r["gap_id"]: r["status"] for r in handle.conn.execute(
        "SELECT gap_id, status FROM gap").fetchall()}
    missing = [g["gap_id"] for g in gaps if g.get("gap_id") not in have]
    if missing:
        raise CurriculumRefused(
            "these gaps are not on this track: %s" % ", ".join(sorted(missing)))
    unapproved = sorted(g["gap_id"] for g in gaps
                        if have[g["gap_id"]] not in WORKABLE)
    if unapproved:
        raise CurriculumRefused(
            "the candidate has not approved %s, so there is nothing to plan from"
            % ", ".join(unapproved))


def build(handle, gaps, edges=(), max_steps=None, posting="", fit=None):
    """Approved gaps plus this track's corpus into a written plan."""
    check_gaps(handle, gaps)
    index = corpus_index(handle)
    built = plan(gaps, index, edges=edges, max_steps=max_steps,
                 posting=posting, fit=fit)
    built["written"] = write(handle, built)
    return built


def steps_of(handle):
    return [dict(r) for r in handle.conn.execute(
        "SELECT step_id, ord, title, objective, gap_id, status, tier,"
        " evidence_state, est_minutes FROM step ORDER BY ord").fetchall()]


def slices_of(handle, step_id):
    return [dict(r) for r in handle.conn.execute(
        "SELECT doc_id, sec_id, ord FROM step_slice WHERE step_id=? ORDER BY ord",
        (step_id,)).fetchall()]


def slices_by_step(handle):
    """Every pinned slice in this track, grouped by step, in pin order.

    One query, not one per step. The page asks for the whole plan in a single
    GET, and forty steps through `slices_of` is forty round trips for something
    SQLite groups in one scan.

    The document title and the section heading come along because the page has
    to name what a step teaches from, and the alternative is the page holding a
    second copy of the corpus index to look them up. The join is LEFT on
    `section` on purpose: a slice whose section row is missing is a defect worth
    seeing as a blank heading rather than a step that silently loses a source.
    """
    grouped = {}
    rows = handle.conn.execute(
        "SELECT sl.step_id AS step_id, sl.doc_id AS doc_id, sl.sec_id AS sec_id,"
        "       sl.ord AS ord, d.title AS doc_title, d.vetting AS vetting,"
        "       d.trust AS trust, d.origin_url AS origin_url,"
        "       d.publisher AS publisher, d.published_on AS published_on,"
        "       sc.heading AS heading"
        "  FROM step_slice sl"
        "  JOIN doc d ON d.doc_id = sl.doc_id"
        "  LEFT JOIN section sc"
        "    ON sc.doc_id = sl.doc_id AND sc.sec_id = sl.sec_id"
        " ORDER BY sl.step_id, sl.ord").fetchall()
    for r in rows:
        grouped.setdefault(r["step_id"], []).append({
            "doc_id": r["doc_id"], "sec_id": r["sec_id"], "ord": r["ord"],
            "doc_title": r["doc_title"], "heading": r["heading"] or "",
            "vetting": r["vetting"], "trust": r["trust"],
            "origin_url": r["origin_url"], "publisher": r["publisher"],
            "published_on": r["published_on"],
        })
    return grouped
