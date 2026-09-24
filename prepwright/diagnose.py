"""The conversational knowledge probe and the gap list it produces.

Asks rather than assumes. A requirement the candidate can explain unprompted is
not a gap, and a resume claim they cannot defend IS a gap even when the posting
never mentions it. The second half is the part that is easy to skip and the part
that makes this worth running: an interviewer reads the resume and asks about
what is on it, not about what the advertisement asked for.

Nothing here calls a model and nothing here reaches the network. It extracts
requirements from the posting, claims from the resume pipeline's fit report,
builds the probe plan, and turns a judgement into proposed gaps. The provider
call belongs to the route, which is how `assess` already works: this module owns
the schema and the prompt, the route owns the socket.

Nothing downstream may run on a proposed gap. `approved()` is the gate, and the
candidate is the only thing that opens it.
"""

import hashlib
import json
import re

from . import config as C
from . import state as S


class DiagnoseRefused(ValueError):
    """The input cannot produce a gap list. The message says what is missing."""


# ---- requirements out of the posting ---------------------------------------
# Deterministic on purpose. Every gap has to trace back to a span of the posting
# the candidate can read, or to a claim on their own resume. A gap a model
# invented and nobody can point at is the thing this design exists to refuse.

_BULLET = re.compile(r"^\s*(?:[-•*•●]|\d+[.)])\s+(?P<text>\S.*)$")
_SENTENCE_END = re.compile(r"[.!?:]\s*$")

# Heading text decides what a bullet under it means. Matched on a lowercase,
# punctuation-stripped form so "Skills, Experience & Role Fit" and
# "Skills Experience and Role Fit" are the same heading.
_NEED_WORDS = ("skill", "experience", "requirement", "qualification", "about you",
               "who you are", "what you bring", "you will have", "essential",
               "role fit", "selection criteria", "eligibility")
_NICE_WORDS = ("desirable", "highly regarded", "preferred", "nice to have",
               "bonus", "advantageous", "well regarded", "a plus")
_DO_WORDS = ("accountabilit", "responsibilit", "what you", "the role",
             "day to day", "duties", "key deliverable")
# Two classes, not one list of phrases seen once. A sentence about the ACT of
# applying is not a requirement however it is worded, and neither is boilerplate
# about the employer. Extracted from the Wingtip posting's own closing line, "If you
# are passionate ... we'd love to hear from you", which sits under the skills
# heading and was read as a requirement until this existed.
_NOISE = (
    # the act of applying
    "apply now", "how to apply", "hear from you", "we d love", "we would love",
    "get in touch", "reach out", "join us", "join our team", "send your",
    "submit your", "applications close", "click apply", "your application",
    "we encourage you to apply", "if you are passionate", "if this sounds like",
    # employer boilerplate
    "equal opportunity", "we celebrate diversity", "inclusive environment",
    "about us", "about the company", "privacy policy", "sign in",
    "recruitment agencies", "no agencies",
)

_PUNCT = re.compile(r"[^a-z0-9 ]+")


def _flat(text):
    return _PUNCT.sub(" ", str(text or "").lower()).strip()


def _heading_kind(heading):
    """(kind, explicit). `explicit` is False when nothing matched and the kind
    is the default, which is what decides whether a non-bullet line under this
    heading may be read as a requirement. "About Wingtip" defaults to requirement so
    its bullets are still probed, but its marketing paragraph must not be."""
    h = _flat(heading)
    if not h:
        return "requirement", False
    if any(w in h for w in _NICE_WORDS):
        return "desirable", True
    if any(w in h for w in _NEED_WORDS):
        return "requirement", True
    if any(w in h for w in _DO_WORDS):
        return "responsibility", True
    return "requirement", False


def _looks_like_a_heading(line):
    """A short line that is not a sentence and is not a bullet."""
    s = line.strip()
    return bool(s) and len(s) <= 90 and not _SENTENCE_END.search(s) and \
        not _BULLET.match(line) and len(s.split()) <= 12


def requirements_from_posting(text):
    """Every bullet the posting asks for, with the span it came from.

    Bullets, because that is how postings are written and because a bullet is
    the smallest unit a candidate can be asked about. Prose paragraphs are left
    alone: a paragraph split into sentences produces requirements nobody wrote,
    and a gap derived from one cannot be shown to the candidate as something the
    employer actually asked for.
    """
    body = str(text or "")
    out, heading, offset, n = [], "", 0, 0
    for line in body.split("\n"):
        start = offset
        offset += len(line) + 1
        m = _BULLET.match(line)
        if not m:
            if _looks_like_a_heading(line):
                heading = line.strip()
                continue
            # A standalone line under a heading that EXPLICITLY names skills or
            # desirables. The Wingtip posting states "Relevant certifications
            # (desirable): CISSP, CISM, or AI governance certifications" as prose
            # rather than a bullet, and dropping it loses a real, actionable gap.
            # Narrow on purpose: the heading must have matched a word rather than
            # defaulted, or "About Wingtip" would turn its marketing paragraph into a
            # requirement the employer never asked for.
            kind, explicit = _heading_kind(heading)
            item = line.strip()
            if not (explicit and kind in ("requirement", "desirable")):
                continue
            if not (24 <= len(item) <= 300) or _flat(item) in ("", heading.lower()):
                continue
            if any(w in _flat(item) for w in _NOISE):
                continue
            span_start = start + line.index(item)
            n += 1
            out.append({
                "req_id": "r%02d" % n,
                "text": item[:400],
                "section": heading[:120],
                "kind": ("desirable"
                         if any(w in _flat(item) for w in _NICE_WORDS) else kind),
                "span": "%d:%d" % (span_start, span_start + len(item)),
            })
            continue
        item = m.group("text").strip().rstrip(".").strip()
        if not item or len(item) < 12:
            continue
        low = _flat(item)
        if any(w in low for w in _NOISE):
            continue
        kind, _explicit = _heading_kind(heading)
        if any(w in low for w in _NICE_WORDS):
            kind = "desirable"
        n += 1
        span_start = start + line.index(m.group("text"))
        out.append({
            "req_id": "r%02d" % n,
            "text": item[:400],
            "section": heading[:120],
            "kind": kind,
            "span": "%d:%d" % (span_start, span_start + len(m.group("text"))),
        })
    return out


# ---- claims out of the resume pipeline's fit report -------------------------
# `<base>_FitReport.md` carries a requirement matrix with a verdict and an
# evidence sentence per row, a steelman, and a red team. The verdicts are a gap
# list somebody already did the work for; the evidence column is a list of
# claims the candidate will be asked to defend in an interview.

_TABLE_ROW = re.compile(r"^\|(?P<rest>.*)\|\s*$")
_RULE_ROW = re.compile(r"^[\s|:-]+$")
_VERDICT = re.compile(r"\b(MISSING|UNVERIFIED|PARTIAL|MET|PROCESS REQUIREMENT)\b")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_LISTED = re.compile(r"^(?:\d+[.)]|[-*\u2022])\s+")

# Weakest first. A row reading "MET (security) / PARTIAL (governance)" carries
# two verdicts, and taking the first one loses the half the candidate is short
# of. Pessimism is the rule everywhere else in this module and it is the rule
# here: the cost of an extra probe is one question, the cost of a missed gap is
# the interview.
VERDICT_ORDER = ("MISSING", "UNVERIFIED", "PARTIAL", "MET", "PROCESS REQUIREMENT")
WEAK_VERDICTS = ("MISSING", "UNVERIFIED", "PARTIAL")


def _section_of(markdown, name):
    """The body of one `## name` section, up to the next `## `."""
    pattern = re.compile(r"(?ims)^##\s+%s\s*$(?P<body>.*?)(?=^##\s|\Z)"
                         % re.escape(name))
    m = pattern.search(str(markdown or ""))
    return (m.group("body").strip() if m else "")


def _verdict_of(cell):
    """The weakest verdict named in one cell, or UNKNOWN."""
    found = set(_VERDICT.findall(_BOLD.sub(r"\1", cell).upper()))
    for name in VERDICT_ORDER:
        if name in found:
            return name
    return "UNKNOWN"


def _matrix_columns(cells):
    """Which column holds the requirement, the verdict and the evidence.

    Read from the header rather than assumed by position. Two real fit reports
    on disk disagree about the shape: one leads with a `#` column and one does
    not, and a parser pinned to `| <n> | requirement | ...` reads the second as
    zero rows and produces a gap list with nothing on it.
    """
    lower = [c.strip().lower() for c in cells]
    want = {"requirement": ("requirement", "criterion", "criteria", "what they ask"),
            "verdict": ("verdict", "status", "assessment", "result"),
            "evidence": ("evidence", "why", "notes", "detail")}
    found = {}
    for field, names in want.items():
        for i, name in enumerate(lower):
            if any(n in name for n in names):
                found[field] = i
                break
    if "requirement" not in found or "verdict" not in found:
        return None
    found.setdefault("evidence", min(found["verdict"] + 1, len(cells) - 1))
    return found


def claims_from_fit_report(markdown):
    """The requirement matrix, the steelman and the red team, as data.

    Returns rows even when a verdict cannot be read, marked `UNKNOWN`, because
    dropping a row silently would shorten the gap list without saying so.
    """
    md = str(markdown or "")
    lines = [ln.strip() for ln in _section_of(md, "Requirement matrix").split("\n")]
    rows, columns, n = [], None, 0
    for i, line in enumerate(lines):
        m = _TABLE_ROW.match(line)
        if not m:
            continue
        raw = m.group("rest")
        if _RULE_ROW.match(raw):
            continue
        # A row followed by a rule row is the header, by markdown's own rule.
        # Deciding that from the column NAMES instead reads "| # | R | V | E |"
        # as a requirement called "R", because no name matched. The separator is
        # a fact about the table; the names are a guess about the author.
        nxt = next((lines[j] for j in range(i + 1, len(lines)) if lines[j]), "")
        is_header = bool(_TABLE_ROW.match(nxt)
                         and _RULE_ROW.match(_TABLE_ROW.match(nxt).group("rest")))
        cells = [c.strip() for c in raw.split("|")]
        if is_header:
            # Named columns when the author named them, otherwise the last three,
            # which is the shape every fit report seen so far ends in.
            columns = _matrix_columns(cells) or {
                "requirement": max(0, len(cells) - 3),
                "verdict": max(0, len(cells) - 2),
                "evidence": len(cells) - 1}
            continue
        if columns is None:
            # A table with no header at all. Read it rather than return nothing.
            columns = {"requirement": max(0, len(cells) - 3),
                       "verdict": max(0, len(cells) - 2),
                       "evidence": len(cells) - 1}
        if len(cells) <= columns["verdict"]:
            continue
        n += 1
        rows.append({
            "n": n,
            "requirement": _BOLD.sub(r"\1", cells[columns["requirement"]])[:400],
            "verdict": _verdict_of(cells[columns["verdict"]]),
            "evidence": _BOLD.sub(r"\1", cells[columns["evidence"]]
                                  if columns["evidence"] < len(cells) else "")[:600],
        })

    # Numbered or bulleted objections when the section is written as a list, and
    # the whole block as ONE objection when it is written as prose. Splitting
    # prose into sentences would manufacture three objections out of one, which
    # is the same invention the posting parser refuses.
    block = _section_of(md, "Red team")
    listed = [ln.strip() for ln in block.split("\n")
              if ln.strip() and _LISTED.match(ln.strip())]
    if listed:
        red = [_LISTED.sub("", item)[:600] for item in listed]
    else:
        joined = " ".join(block.split())
        red = [joined[:600]] if len(joined) > 40 else []

    return {
        "rows": rows,
        "steelman": _section_of(md, "Steelman")[:2000],
        "red_team": red,
        "weak": [r for r in rows if r["verdict"] in WEAK_VERDICTS],
        "met": [r for r in rows if r["verdict"] == "MET"],
    }


def resume_claims(fit):
    """The claims the candidate will be asked to defend, from the MET rows.

    A MET row is the fit report asserting the candidate has something. That is
    exactly the sentence an interviewer reads off the resume and pushes on, and
    it is where a gap the posting never mentions comes from.
    """
    out = []
    for row in fit.get("met") or ():
        evidence = (row.get("evidence") or "").strip()
        if len(evidence) < 24:
            continue
        out.append({
            "claim_id": "c%02d" % row["n"],
            "claim": evidence[:600],
            "about": (row.get("requirement") or "")[:200],
        })
    return out


# ---- the probe plan ---------------------------------------------------------
PROBE_SOURCES = ("posting", "resume")


def probe_plan(requirements=(), fit=None, limit=24):
    """The ordered questions to ask, from both sources, with both guaranteed room.

    The limit is a conversation length, and it is allocated across the two
    sources rather than filled first-come. Filled first-come it is not a length
    at all: a posting with twenty requirements consumes every slot, no resume
    probe is ever asked, and the tool becomes a re-reading of the advertisement.
    A gap the posting never mentions is the thing worth paying for, so the resume
    side gets a reserved share and only gives it back when it has nothing to say.

    Ordered hardest-first inside each side. What does not fit is counted and
    returned per source, never dropped in silence.
    """
    fit = fit or {}
    seen = set()

    def make(source, about, prompt, ref, span=None, kind=""):
        key = _flat(about)[:80]
        if not key or key in seen:
            return None
        seen.add(key)
        return {"source": source, "about": about[:300], "prompt": prompt,
                "ref": ref, "span": span, "kind": kind}

    posting_side, resume_side = [], []

    # What the fit report already calls weak, hardest first. These are posting
    # requirements the application could not evidence, so they lead.
    order = {"MISSING": 0, "UNVERIFIED": 1, "PARTIAL": 2, "UNKNOWN": 3}
    for row in sorted(fit.get("weak") or (),
                      key=lambda r: (order.get(r["verdict"], 9), r["n"])):
        posting_side.append(make(
            "posting", row["requirement"],
            "The posting asks for this and your application could not evidence it."
            " Tell me what you have actually done here, in specifics. If the answer"
            " is nothing, say nothing.",
            "fit:%d" % row["n"], kind=row["verdict"].lower()))

    # Every stated requirement, so a posting with no fit report still probes.
    for req in requirements:
        if req["kind"] == "responsibility":
            continue
        posting_side.append(make(
            "posting", req["text"],
            "Explain how you would do this, and name the closest thing you have"
            " actually done.",
            req["req_id"], span=req["span"], kind=req["kind"]))

    # The claims on the candidate's own resume. An interviewer reads these off
    # the page and pushes; the posting has nothing to say about them.
    for claim in resume_claims(fit):
        resume_side.append(make(
            "resume", claim["claim"],
            "Your application claims this. An interviewer will read it off the page"
            " and push. Defend it: what did you build, what broke, what would you"
            " do differently.",
            claim["claim_id"], kind="claim"))

    for i, risk in enumerate(fit.get("red_team") or (), start=1):
        resume_side.append(make(
            "resume", risk,
            "This is the objection your application invites. Answer it as you would"
            " in the room.",
            "red:%d" % i, kind="objection"))

    posting_side = [p for p in posting_side if p]
    resume_side = [p for p in resume_side if p]

    limit = max(1, int(limit))
    reserved = min(len(resume_side), max(2, limit // 3))
    take_posting = min(len(posting_side), limit - reserved)
    # Slack in one direction only: a side with nothing to say hands its room back.
    take_resume = min(len(resume_side), limit - take_posting)

    plan = posting_side[:take_posting] + resume_side[:take_resume]
    for i, probe in enumerate(plan, start=1):
        probe["probe_id"] = "p%02d" % i
    return plan, {
        "posting": len(posting_side) - take_posting,
        "resume": len(resume_side) - take_resume,
        "total": (len(posting_side) + len(resume_side)) - len(plan),
    }


# ---- what the judge is asked, and what it must return -----------------------
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "probe_id": {"type": "string"},
                    "level": {"type": "string", "enum": ["none", "shaky", "solid"]},
                    "why": {"type": "string"},
                },
                "required": ["probe_id", "level", "why"],
            },
        },
    },
    "required": ["verdicts"],
}

JUDGE_SYSTEM = (
    "You are grading how well a candidate can defend one thing, from their own"
    " words. Grade only what they said. Do not credit them for what the question"
    " implied, for what a reasonable person in their field would know, or for"
    " what you can infer from their job title.\n"
    "solid: they gave specifics that could only come from having done it.\n"
    "shaky: they know the shape of it but the specifics are thin, borrowed, or"
    " wrong.\n"
    "none: they did not answer, said they do not know, or answered a different"
    " question.\n"
    "Not answering IS an answer, and it is `none`. Grading it as shaky to be kind"
    " puts an unlearned thing on the studied list and takes it off the plan. Be"
    " harsh where the evidence is thin; the candidate is paying for the truth."
)


def judge_prompt(plan, answers):
    """One prompt carrying every probe and what the candidate said about it.

    `answers` maps probe_id to the candidate's words. A probe with no answer is
    still sent, marked, so the judge grades a silence as a silence rather than
    the probe vanishing from the list.
    """
    lines = []
    for probe in plan:
        said = str((answers or {}).get(probe["probe_id"]) or "").strip()
        lines.append(json.dumps({
            "probe_id": probe["probe_id"],
            "about": probe["about"],
            "candidate_said": said or "(no answer given)",
        }, ensure_ascii=False))
    return "%s\n\nGrade each of these %d probes.\n%s" % (
        JUDGE_SYSTEM, len(plan), "\n".join(lines))


def verdicts_without_a_model(plan, answers):
    """The fallback grade when no judge ran. Deliberately pessimistic.

    An unanswered probe is `none`. An answered one is `shaky`, never `solid`:
    this function cannot read, and awarding `solid` from a length check would
    delete a real gap from the plan on the strength of a long sentence.
    """
    out = []
    for probe in plan:
        said = str((answers or {}).get(probe["probe_id"]) or "").strip()
        out.append({
            "probe_id": probe["probe_id"],
            "level": "shaky" if len(said) >= 40 else "none",
            "why": "graded without a model: length only, so never better than shaky",
        })
    return out


# ---- proposing the gaps -----------------------------------------------------
LEVELS = ("none", "shaky", "solid")


def proposals_from(plan, verdicts):
    """The gap rows a judgement implies, in the order they should be worked.

    A `solid` probe produces no gap: the candidate explained it unprompted, and
    the whole point of asking first was to not teach it. `none` before `shaky`,
    and a posting requirement before a resume claim at the same level, because
    an unmet requirement loses the interview and an undefended claim loses the
    room once you are already in it.
    """
    by_id = {p["probe_id"]: p for p in plan}
    graded = {}
    for v in verdicts or ():
        # A verdict comes back from a model. `(v or {}).get` assumes a dict and
        # raises AttributeError on a bare string or a number, which takes the
        # whole grading down and loses every gap on the track rather than the one
        # malformed row. An entry that is not an object is not a verdict.
        if not isinstance(v, dict):
            continue
        pid = str(v.get("probe_id") or "")
        level = str(v.get("level") or "").lower()
        if pid in by_id and level in LEVELS:
            graded[pid] = (level, str(v.get("why") or "")[:600])

    rank = {"none": 0, "shaky": 1}
    rows = []
    for pid, probe in by_id.items():
        level, why = graded.get(pid, ("none", "not graded, so treated as unlearned"))
        if level == "solid":
            continue
        rows.append({
            "probe_id": pid,
            "label": probe["about"][:200],
            "why": why or "the candidate could not evidence this",
            "level": level,
            "source": probe["source"],
            # A requirement the fit report says the posting states, and the
            # application could not evidence, has no character span of its own:
            # the fit report paraphrases it. Its row is where it was read from.
            # Without this every such gap was labelled "your application claims"
            # and tiered as one (curriculum.tier_for reads jd_span as "the
            # posting states it"): on the 2026-09-24 walk the PhD and CCS/S&P
            # requirements of the UniExample posting were both labelled so.
            "jd_span": probe.get("span") or (
                probe.get("ref") if probe["source"] == "posting" else None),
            "ref": probe.get("ref"),
            "kind": probe.get("kind") or "",
        })
    rows.sort(key=lambda r: (rank.get(r["level"], 9),
                             0 if r["source"] == "posting" else 1,
                             r["probe_id"]))
    for i, row in enumerate(rows, start=1):
        row["ord"] = i
        row["gap_id"] = "g%02d" % i
    return rows


def propose(handle, rows):
    """Write the proposed gap list. Proposed, and nothing else.

    `add_gap` hard-codes status 'proposed', which is the whole safety property
    here: no path through this module can produce an approved gap, so nothing
    downstream can act on a list the candidate has not seen.
    """
    written = []
    for row in rows:
        handle.add_gap(row["gap_id"], row["ord"], row["label"], row["why"],
                       level=row["level"], jd_span=row.get("jd_span"))
        written.append(row["gap_id"])
    return written


def gap_list(handle):
    """Every gap on this track, newest decision first within its order."""
    return [dict(r) for r in handle.conn.execute(
        "SELECT gap_id, ord, label, why, jd_span, level, status, weight, rev,"
        " proposed_utc, decided_utc FROM gap ORDER BY ord").fetchall()]


def approve(handle, gap_id, expected_rev=None):
    """The candidate approving one gap. The only way a gap becomes workable."""
    return _decide(handle, gap_id, "approved", expected_rev)


def decline(handle, gap_id, expected_rev=None):
    """The candidate saying they already have this. It stays on the record."""
    return _decide(handle, gap_id, "declined", expected_rev)


def _decide(handle, gap_id, status, expected_rev):
    if expected_rev is None:
        row = handle.conn.execute(
            "SELECT rev FROM gap WHERE gap_id=?", (gap_id,)).fetchone()
        if row is None:
            raise DiagnoseRefused("no gap %s on this track" % gap_id)
        expected_rev = row["rev"]
    handle.set_gap_status(gap_id, status, expected_rev)
    return status


def approved(handle):
    """The gaps the candidate approved. The curriculum's only legitimate input."""
    return [g for g in gap_list(handle) if g["status"] in ("approved", "edited")]


def is_approved(handle):
    """True once the candidate has decided every proposed gap.

    The gate the curriculum checks. A list with one undecided row is a list the
    candidate is still reading, and building a plan from it changes the thing
    they are being asked to approve while they are approving it.
    """
    rows = gap_list(handle)
    if not rows:
        return False
    return not any(g["status"] == "proposed" for g in rows)


def summary(handle):
    """What to show on the approval screen, counted rather than described."""
    rows = gap_list(handle)
    counts = {}
    for g in rows:
        counts[g["status"]] = counts.get(g["status"], 0) + 1
    return {
        "total": len(rows),
        "by_status": counts,
        "by_level": {lv: sum(1 for g in rows if g["level"] == lv) for lv in LEVELS},
        "decided": all(g["status"] != "proposed" for g in rows) if rows else False,
        "approved": len([g for g in rows if g["status"] in ("approved", "edited")]),
    }
