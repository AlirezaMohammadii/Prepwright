"""Curriculum generation, ordering, and the Pareto cut.

Turns an approved gap list plus a corpus into an ordered set of steps. Cuts to
the smallest set carrying most of the value, and marks every step core, depth or
reference so the candidate can see what is load-bearing.

Three rules shape everything here.

**Order by dependency, not by importance.** A concept whose prerequisite is
unlearned is unteachable, so the most important gap on the list is often not the
one to teach first. Importance decides what gets cut; dependency decides the
order of what survives.

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
        for t in set(terms(sec["heading"] + " " + sec["concept"] + " " + sec["body"])):
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
        if total <= 0 or (matched < MIN_TERMS and not labelled):
            continue
        total *= VETTING_WEIGHT.get(sec["vetting"], 0.78)
        total *= 0.9 + 0.05 * max(1, min(5, sec["trust"]))
        scored.append((total, sec))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["doc_id"], pair[1]["sec_id"]))
    return scored


def relevant(scored, limit=None):
    """The scored sections a step should actually teach from.

    Everything below `RELATIVE_FLOOR` of the best score is dropped. `limit`
    defaults to the pack's own section cap, because pinning more than the pack
    can carry is not harmless: `build_pack` walks `step_slice` by `ord` and stops
    at the cap, so everything past it is invisible while still looking pinned.
    """
    if not scored:
        return []
    limit = C.PACK_MAX_SECTIONS if limit is None else max(1, int(limit))
    best = scored[0][0]
    return [sec for score, sec in scored[:limit] if score >= best * RELATIVE_FLOOR]


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


def tier_for(gap):
    """Level first, then where the gap came from.

    An unlearned requirement the posting states loses the interview at the
    screen. An undefended claim on the candidate's own resume loses the room
    once they are already in it, which is later and therefore second.
    """
    level = (gap.get("level") or "none").lower()
    from_posting = bool(gap.get("jd_span"))
    if level == "none":
        return "core" if from_posting else "depth"
    if level == "shaky":
        return "depth" if from_posting else "reference"
    return "reference"


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


def plan(gaps, index, edges=(), max_steps=None, est_minutes=25):
    """The ordered, tiered, sliced plan, plus everything it could not build.

    Deferral is never deletion. A gap with no usable corpus keeps its row and is
    returned in `deferred` with the reason, which is also the research list: the
    curriculum is the thing that knows what is missing.
    """
    if not gaps:
        raise CurriculumRefused(
            "there are no approved gaps, so there is nothing to plan")
    max_steps = C.MAX_STEPS if max_steps is None else max(1, int(max_steps))
    ordered, edge_report = order_gaps(gaps, edges)
    df = _document_frequency(index) if index else {}

    steps, deferred = [], []
    for gap in ordered:
        title = (gap.get("label") or "").strip()[:160] or gap["gap_id"]
        objective = (gap.get("why") or "").strip()[:400]
        scored = score_sections(index, "%s %s" % (title, objective), df=df) if index else []
        slices = relevant(scored)
        if not slices:
            deferred.append({"gap_id": gap["gap_id"], "label": title,
                             "reason": "no corpus in this track teaches it yet"})
            continue
        steps.append({
            "gap_id": gap["gap_id"],
            "title": title,
            "objective": objective or "Be able to answer this in the room.",
            "tier": tier_for(gap),
            "est_minutes": int(est_minutes),
            "slices": slices,
        })

    # The cut. Order is already dependency-correct, so cutting from the tail
    # cannot orphan a step whose prerequisite was kept.
    if len(steps) > max_steps:
        for step in steps[max_steps:]:
            deferred.append({"gap_id": step["gap_id"], "label": step["title"],
                             "reason": "beyond the %d-step plan" % max_steps})
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


def build(handle, gaps, edges=(), max_steps=None):
    """Approved gaps plus this track's corpus into a written plan."""
    check_gaps(handle, gaps)
    index = corpus_index(handle)
    built = plan(gaps, index, edges=edges, max_steps=max_steps)
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
