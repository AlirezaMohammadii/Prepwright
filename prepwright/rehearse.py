"""Rehearsal: the interview asked and graded, after the teaching (ADR 0008).

Teaching makes a concept explainable. The room asks something else: defend this
line of your resume, answer this objection, tell us about a time you did what
we need. The 2026-09-24 walk showed the cost of leaving that to the tutor. It
never saw the application, so it coached the candidate to say "ASVspoof wasn't
run at all" about a resume that claims ASVspoof evaluations, and the session
review carried the sentence into the recap bank.

A rehearsal step is a topic step with an R<nn> id, planned deterministically
from what the diagnostic already extracts: red-team objections, weak fit rows,
resume claims, and behavioural prompts from the posting's responsibilities. The
candidate's own application is stored as corpus documents (vetting `primary`)
and pinned to each step with the study corpus, so a strong answer is built from
his evidence and cited through the same `build_pack` and `check_citations` as
every teaching turn. Grounding is not relaxed anywhere.

The question needs no model. The answer is graded by the grader role against
RUBRIC; `concise` is counted here rather than judged. The step is delivered by
its best grade at C.REHEARSAL_PASS or above (state.sync_step_lifecycle), never
by a tick. Standard library only.
"""

import hashlib
import json
import os
import re

from prepwright import config as C
from prepwright import corpus as CO
from prepwright import curriculum as CU

STEP_RE = re.compile(C.REHEARSAL_STEP_RE)
PASS = C.REHEARSAL_PASS
# A criterion at 0 caps the grade below the pass. The store sees one number,
# so the rule that the page shows ("no criterion at 0") has to live in it.
CAP_WITH_A_ZERO = 0.625
SPOKEN_WORDS = C.REHEARSAL_SPOKEN_WORDS
FIT_EVIDENCE_TITLE = "Your application: fit report"
RESUME_EVIDENCE_TITLE = "Your application: resume"

RUBRIC = (
    ("grounded", "Correct, and every fact in it is in your application or the corpus."),
    ("specific", "Your own numbers, datasets, venues and outcomes, not the category of thing."),
    ("structured", "One shape a listener can follow: situation, task, action, result for a"
                   " story; claim, evidence, limit for a defence."),
    ("concise", "About %d words or fewer spoken, which is about two minutes." % SPOKEN_WORDS),
)

# Application logistics and eligibility are not interview questions
# (config.PROCESS_PHRASES says why; _is_process reads it through curriculum).
_BEHAVIOUR = ("support", "mentor", "collaborate", "lead", "manage", "communicate", "explain",
              "present", "supervise", "coordinate", "work with", "engage", "influence",
              "build relationships", "teach", "negotiate", "resolve")
_CREDENTIAL = ("phd", "degree", "doctorate", "qualification", "certification", "licence",
               "license", "clearance")
_WORD = re.compile(r"[a-z0-9]+")


def is_rehearsal(step_id):
    return bool(STEP_RE.match(str(step_id or "")))


def _flat(text):
    return " ".join(_WORD.findall(str(text or "").lower()))


def _is_process(text):
    return CU.is_process(text)     # whole words, one rule for both modules


def _is_behavioural(requirement):
    low = _flat(requirement)
    return any(low.startswith(_flat(v)) for v in _BEHAVIOUR)


def _clip(text, n):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def persona(employer, role):
    """Who is asking. Drawn from the posting's own employer and role only."""
    role = (role or "").strip() or "this role"
    employer = (employer or "").strip()
    return ("The selection panel for %s at %s" % (role, employer)) if employer \
        else ("The selection panel for %s" % role)


def questions(fit, requirements=(), limit=C.REHEARSAL_MAX):
    """The questions a panel is likeliest to ask, most dangerous first.

    Objections first, because an objection the application invites is the one
    most likely to be put to the candidate and the one that ends interviews.
    Then what the posting asks for and the application could not show, the
    resume's own claims, and behavioural prompts built from what the role says
    the person will do. Each kind gets a share of `limit` before any kind gets
    more, so a long claim list cannot crowd out the objection.
    """
    fit = fit or {}
    seen = set()
    kinds = {"objection": [], "gap": [], "claim": [], "behavioural": []}

    def add(kind, about, question, ref, requirement=""):
        # Objections are not screened: a red team that also mentions the
        # paperwork is still the objection the panel will raise.
        key = _flat(about)[:80]
        if not key or key in seen or (kind != "objection" and (
                _is_process(about) or _is_process(requirement))):
            return
        seen.add(key)
        kinds[kind].append({"kind": kind, "about": _clip(about, 300),
                            "question": question, "ref": ref,
                            "requirement": _clip(requirement, 300)})

    def story(req, ref):
        add("behavioural", req, "The role involves “%s”. Tell us about a time you did that:"
            " the situation, what you did, and what came of it." % _clip(req, 200), ref, req)

    for i, risk in enumerate(fit.get("red_team") or (), start=1):
        add("objection", risk, "One of the panel has read your application and says: “%s”"
            " How do you answer that?" % _clip(risk, 420), "red:%d" % i)
    order = {"MISSING": 0, "UNVERIFIED": 1, "PARTIAL": 2}
    for row in sorted(fit.get("weak") or (), key=lambda r: (order.get(r["verdict"], 9), r["n"])):
        req = row.get("requirement") or ""
        if _is_behavioural(req):
            story(req, "fit:%d" % row["n"])
        else:
            add("gap", req, "The role asks for “%s”, and your application does not show it"
                " yet. What do you say when we ask about it?" % _clip(req, 200),
                "fit:%d" % row["n"], req)
    for row in fit.get("met") or ():
        req, evidence = row.get("requirement") or "", row.get("evidence") or ""
        if _is_behavioural(req):
            story(req, "fit:%d" % row["n"])
        elif len(evidence) >= 24:
            add("claim", evidence, "On “%s” your application says “%s”. Take us through it:"
                " what exactly did you do, what did it show, and where does it stop?"
                % (_clip(req, 120), _clip(evidence, 260)), "fit:%d" % row["n"], req)
    for req in requirements or ():
        if req.get("kind") == "responsibility":
            story(req["text"], req["req_id"])

    limit = max(1, int(limit))
    share = {"objection": 2, "gap": 2, "claim": 2, "behavioural": 2}
    out = []
    for kind in ("objection", "gap", "claim", "behavioural"):
        out.extend(kinds[kind][:share[kind]])
    for kind in ("objection", "gap", "claim", "behavioural"):
        out.extend(kinds[kind][share[kind]:])
    out = out[:limit]
    rank = {"objection": 0, "gap": 1, "claim": 2, "behavioural": 3}
    out.sort(key=lambda q: rank[q["kind"]])
    for i, q in enumerate(out, start=1):
        q["qid"] = "R%02d" % i
    return out


# ---- the candidate's own evidence, as corpus -------------------------------
def fit_evidence_sections(fit):
    """The fit report as (heading, body) pairs a pack can cite."""
    fit = fit or {}
    out = []
    if (fit.get("steelman") or "").strip():
        out.append(("Steelman: the strongest case for you", fit["steelman"].strip()))
    for i, risk in enumerate(fit.get("red_team") or (), start=1):
        out.append(("Red team objection %d" % i, risk))
    chunk, first = [], None
    for row in fit.get("rows") or ():
        line = "- [%s] %s: %s" % (row["verdict"], row["requirement"], row["evidence"])
        if chunk and len("\n".join(chunk + [line])) > C.SECTION_MAX_CHARS - 60:
            out.append(("Requirement matrix, rows %d-%d" % (first, last), "\n".join(chunk)))
            chunk, first = [], None
        chunk.append(line)
        first = first or row["n"]
        last = row["n"]
    if chunk:
        out.append(("Requirement matrix, rows %d-%d" % (first, last), "\n".join(chunk)))
    return out


def _as_documents(title, pairs):
    """Markdown documents of at most MAX_SECTIONS_PER_DOC sections each."""
    per = C.MAX_SECTIONS_PER_DOC
    parts = [pairs[i:i + per] for i in range(0, len(pairs), per)] or []
    out = []
    for n, part in enumerate(parts, start=1):
        name = title if len(parts) == 1 else "%s (%d of %d)" % (title, n, len(parts))
        out.append((name, "# %s\n\n" % name + "\n\n".join(
            "## %s\n\n%s" % (h, b) for h, b in part) + "\n"))
    return out


def _intake_file(intake_dir, suffix):
    try:
        names = sorted(n for n in os.listdir(intake_dir) if n.endswith(suffix))
    except OSError:
        return None, ""
    for name in names:
        path = os.path.join(intake_dir, name)
        if os.path.isfile(path) and not os.path.islink(path):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return path, fh.read(C.DOC_MAX_BYTES * 4)
    return None, ""


def store_evidence(handle, fit):
    """Write the candidate's application into the corpus once. Returns doc ids.

    Idempotent by title, as the seed is. The origin is the copy in this track's
    own intake directory, which is where the application was read once and
    hashed; the folder itself is never reopened.
    """
    intake_dir = os.path.join(handle.dir, "intake")
    have = {r["title"]: r["doc_id"] for r in handle.conn.execute(
        "SELECT doc_id, title FROM doc WHERE status='ready'").fetchall()}
    fit_path, _ = _intake_file(intake_dir, "_FitReport.md")
    resume_path, resume_md = _intake_file(intake_dir, C.RESUME_EVIDENCE_SUFFIX)
    sources = []
    if fit_path:
        sources.append((fit_path, _as_documents(FIT_EVIDENCE_TITLE, fit_evidence_sections(fit))))
    if resume_path:
        pairs = CO.parse_loose(resume_md)[1]
        sources.append((resume_path, _as_documents(RESUME_EVIDENCE_TITLE, pairs)))
    ids = []
    for path, docs in sources:
        with open(path, "rb") as fh:
            sha = hashlib.sha256(fh.read()).hexdigest()
        for title, text in docs:
            doc_id = have.get(title)
            if doc_id is None:
                doc_id = CO.ingest_text(
                    handle, text, origin_url="file://" + path, title=title,
                    slug=CO.slug_for(title), vetting="primary", trust=5,
                    source_sha256=sha)
            if doc_id:
                ids.append(doc_id)
    return ids


def _evidence_index(handle, doc_ids):
    """(doc_id, sec_id, heading, body) for every section of the evidence docs."""
    out = []
    for doc_id in doc_ids:
        for r in handle.conn.execute(
                "SELECT sec_id FROM section WHERE doc_id=? ORDER BY ord", (doc_id,)).fetchall():
            sec = handle.read_section(doc_id, r["sec_id"])
            if sec is not None:
                out.append((doc_id, sec["sec_id"], sec["heading"], sec["body"]))
    return out


def pins_for(question, evidence, study=(), limit=C.PACK_MAX_SECTIONS):
    """The sections one rehearsal step may cite: its own evidence, then the study corpus.

    Scored by shared vocabulary with what the question is about, the way the
    curriculum scores a slice. The objection and the steelman ride along on
    every objection, because an answer to an objection is built from the case
    for the candidate. What room is left goes to the rest of his record, resume
    first: on the first live grade the pack held no resume section on his unit
    coordination, and the grader marked his true "data science unit" as a claim
    the record did not confirm.
    """
    want = CU._counts(CU.terms(question["about"] + " " + question.get("requirement", "")))

    def score(heading, body):
        have = CU._counts(CU.terms(heading + " " + body))
        return sum(min(n, have.get(t, 0)) for t, n in want.items())

    ranked = sorted(evidence, key=lambda e: (-score(e[2], e[3]), e[0], e[1]))
    picked = [e for e in ranked if score(e[2], e[3]) > 0][:max(1, limit - len(study) - 1)]
    if question["kind"] == "objection":
        picked += [e for e in evidence if e[2].startswith(("Red team", "Steelman"))
                   and e not in picked]
    if not picked:
        picked = ranked[:3]
    out = [(e[0], e[1]) for e in picked]
    for pair in study:
        if pair not in out:
            out.append(pair)
    resume_first = sorted(evidence, key=lambda e: (not e[2].startswith(
        ("Summary", "Experience", "Publications", "Education")), e[0], e[1]))
    for e in resume_first:
        if len(out) >= limit:
            break
        if (e[0], e[1]) not in out:
            out.append((e[0], e[1]))
    return out[:limit]


def plan(handle, fit, requirements=(), employer="", role=""):
    """Add the rehearsal steps to a track with a written plan, once.

    Returns (added, questions). A second call adds nothing: the steps are the
    denominator of `prepared`, and planning them twice would move it.
    """
    existing = [r["step_id"] for r in handle.conn.execute("SELECT step_id FROM step")]
    if any(is_rehearsal(s) for s in existing):
        return 0, []
    qs = questions(fit, requirements, limit=min(C.REHEARSAL_MAX, C.MAX_STEPS - len(existing)))
    if not qs:
        return 0, []
    doc_ids = store_evidence(handle, fit)
    evidence = _evidence_index(handle, doc_ids)
    by_gap = {}
    for r in handle.conn.execute(
            "SELECT s.gap_id, g.label, sl.doc_id, sl.sec_id FROM step s"
            " JOIN gap g ON g.gap_id = s.gap_id"
            " JOIN step_slice sl ON sl.step_id = s.step_id ORDER BY sl.ord"):
        by_gap.setdefault(_flat(r["label"])[:80], []).append((r["doc_id"], r["sec_id"]))
    first = int(handle.conn.execute("SELECT COALESCE(MAX(ord),0) m FROM step").fetchone()["m"]) + 1
    who = persona(employer, role)
    added = 0
    for i, q in enumerate(qs):
        ord_ = first + i
        step_id = "%d:topic:%s" % (min(99, ord_), q["qid"])
        study = by_gap.get(_flat(q["about"])[:80], [])[:3]
        handle.add_step(step_id, ord_, "Rehearse: " + _clip(q["about"], 90),
                        "%s asks: %s" % (who, q["question"]), tier="core",
                        est_minutes=10)
        for n, (doc_id, sec_id) in enumerate(pins_for(q, evidence, study)):
            handle.pin_slice(step_id, doc_id, sec_id, n)
        q["stepKey"] = step_id
        added += 1
    return added, qs


# ---- grading one answer -----------------------------------------------------
GRADE_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "grounded": {"type": "integer", "minimum": 0, "maximum": 2},
        "specific": {"type": "integer", "minimum": 0, "maximum": 2},
        "structured": {"type": "integer", "minimum": 0, "maximum": 2},
        "sinks": {"type": "string"},
        "strongAnswer": {"type": "string"},
        "followUp": {"type": "string"},
    },
    "required": ["grounded", "specific", "structured", "sinks", "strongAnswer", "followUp"],
    "additionalProperties": False,
})

GRADE_SYSTEM = (
    "You grade one answer from a mock interview. The question came from the selection panel"
    " named in the prompt, and the candidate answered it out loud.\n"
    "Score three criteria, each 0, 1 or 2.\n"
    "grounded: 2 = correct, and every fact about the candidate or the field is supported by the"
    " evidence pack; 1 = one unsupported or vague claim; 0 = a wrong or unsupported claim an"
    " interviewer would catch. A claim the pack contradicts is a 0.\n"
    "specific: 2 = the candidate's own numbers, datasets, venues and outcomes; 1 = some;"
    " 0 = generic.\n"
    "structured: 2 = answers the question asked, in one shape a listener can follow"
    " (situation, task, action, result for a story; claim, evidence, limit for a defence);"
    " 1 = followable but it wanders or buries the answer; 0 = no shape, or it answers a"
    " different question.\n"
    "sinks: at most 40 words. The one thing in this answer that would cost the candidate in the"
    " room, or 'nothing'.\n"
    "strongAnswer: at most 180 words, first person, as the candidate would say it. Build it ONLY"
    " from the evidence pack: the sections titled 'Your application' are the candidate's own"
    " record, the rest is the study corpus. Put the citation token for each factual sentence in"
    " square brackets, like [D03§s02]. Never state a fact about the candidate that the pack"
    " does not contain, and never deny one it does contain. Where the pack cannot support a"
    " point the question needs, say so the way an honest candidate would.\n"
    "followUp: the one question this panel would ask next.\n"
    "Do not be generous: an unearned pass costs the candidate a real interview. Reply only with"
    " JSON."
)


def retry_on_max_turns(call):
    """Run one schema-bound grading call, and once more if the CLI ran out of turns.

    A schema reply the CLI has to re-validate needs a third turn, which
    provider.CLI_SCHEMA_MAX_TURNS does not give it; assess and review retry
    the whole call once for that reason (provider.py). The second live
    rehearsal grade on 2026-09-24 failed exactly so, "CLI reported
    error_max_turns", and the candidate's answer came back as an error.
    Anything else is raised at once.
    """
    try:
        return call()
    except RuntimeError as exc:
        if "error_max_turns" not in str(exc):
            raise
    return call()


def words_in(text):
    return len(str(text or "").split())


def concise_score(words):
    if words <= SPOKEN_WORDS:
        return 2
    return 1 if words <= int(SPOKEN_WORDS * 1.3) else 0


def grade_prompt(objective, answer, pack_text):
    return ("%s\n\nTHE CANDIDATE'S ANSWER (%d words):\n%s\n\nEVIDENCE PACK:\n%s"
            % (objective, words_in(answer), str(answer)[:4000], pack_text))


def grade_result(parsed, answer, pack):
    """The rubric scores, the verdict and the feedback, from the grader's JSON.

    `concise` is counted, not judged. A strong answer that names a citation the
    pack did not carry is reported through `citations.invented`, exactly as a
    teaching turn's is; that is the grader's fault, not the candidate's, so it
    never moves the score.
    """
    parsed = parsed if isinstance(parsed, dict) else {}
    scores = {}
    for key in ("grounded", "specific", "structured"):
        v = parsed.get(key)
        scores[key] = v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 2 else 0
    words = words_in(answer)
    scores["concise"] = concise_score(words)
    points = sum(scores.values())
    capped = min(scores.values()) == 0
    total = min(points / 8.0, CAP_WITH_A_ZERO) if capped else points / 8.0
    strong = str(parsed.get("strongAnswer") or "").strip()[:2000]
    return {
        "scores": scores,
        "points": points,
        "capped": capped,
        "words": words,
        "total": round(total, 3),
        "ready": total >= PASS,
        "sinks": str(parsed.get("sinks") or "").strip()[:400],
        "strongAnswer": strong,
        "followUp": str(parsed.get("followUp") or "").strip()[:400],
        "citations": CO.check_citations(strong, pack),
        "grounded": bool(pack.get("grounded")),
        "cites": pack.get("cites") or [],
        "packSha16": pack.get("pack_sha16") or "",
    }


STRONG_MARK = "A strong answer from your own evidence:"
NEXT_MARK = "Their next question:"


def feedback_text(result):
    """The graded reply as the transcript keeps it. The brief reads it back."""
    s = result["scores"]
    verdict = ("Ready for the room." if result["ready"]
               else "Not yet: this needs %d of 8 with no criterion at 0." % int(PASS * 8))
    capped = (" A criterion at 0 caps it at %d." % int(CAP_WITH_A_ZERO * 8)
              if result.get("capped") else "")
    return "\n".join([
        "Score %d/8.%s %s" % (sum(s.values()), capped, verdict),
        "Grounded %d · Specific %d · Structured %d · Concise %d (%d words)"
        % (s["grounded"], s["specific"], s["structured"], s["concise"], result["words"]),
        "What would cost you: %s" % (result["sinks"] or "nothing named"),
        "",
        STRONG_MARK,
        result["strongAnswer"] or "(the grader returned none)",
        "",
        "%s %s" % (NEXT_MARK, result["followUp"] or "(none)"),
    ])


# ---- the day before ---------------------------------------------------------
def _strong_answer_of(body):
    body = str(body or "")
    if STRONG_MARK not in body:
        return ""
    return body.split(STRONG_MARK, 1)[1].split(NEXT_MARK, 1)[0].strip()


def ask_them(posting, fit):
    """Up to three questions for the candidate to ask, each quoting what it rests on."""
    out = []
    lines = [" ".join(l.split()) for l in str(posting or "").split("\n")]
    cues = ("focus", "you will", "lead", "responsib", "develop", "deliver", "priorit")
    for line in lines:
        low = line.lower()
        if 30 <= len(line) and any(c in low for c in cues) and not _is_process(line):
            quote = _clip(line, 160)
            if not out:
                out.append("The posting says “%s” What would you want this role to have"
                           " delivered on that twelve months in? (posting)" % quote)
            else:
                out.append("The posting says “%s” How is that work shared across the"
                           " group, and who would I work with most closely? (posting)" % quote)
        if len(out) == 2:
            break
    for row in (fit or {}).get("weak") or ():
        # A credential is his to hold, not theirs to build: asking the panel how
        # it would help him get a PhD is the wrong question to ask them.
        if set(_flat(row["requirement"]).split()) & set(_CREDENTIAL):
            continue
        if row["verdict"] == "MISSING" and not _is_process(row["requirement"]):
            out.append("The role asks for \u201c%s\u201d. How does the group help a new starter"
                       " build that? (fit report row %d)" % (_clip(row["requirement"], 160), row["n"]))
            break
    return out[:3]


def brief(handle, fit, posting, employer="", role=""):
    """One page for the day before, from the store and nothing else. Markdown."""
    steps = [dict(r) for r in handle.conn.execute(
        "SELECT step_id, title, tier, status FROM step ORDER BY ord").fetchall()]
    study = [s for s in steps if not is_rehearsal(s["step_id"]) and s["title"]]
    tiers = {"core": 0, "depth": 1, "reference": 2}
    study.sort(key=lambda s: tiers.get(s["tier"], 3))
    lines = ["# The day before: %s" % (persona(employer, role).replace(
        "The selection panel for ", "")), ""]

    lines += ["## The concepts that carry this interview", ""]
    for s in study[:5]:
        cite = handle.conn.execute(
            "SELECT doc_id, sec_id FROM step_slice WHERE step_id=? ORDER BY ord LIMIT 1",
            (s["step_id"],)).fetchone()
        lines.append("- %s%s" % (_clip(s["title"], 140),
                                 " [%s§%s]" % (cite["doc_id"], cite["sec_id"]) if cite else ""))
    if not study:
        lines.append("- No study step has been planned yet.")

    lines += ["", "## Your five strongest stories", ""]
    resume = [r for r in handle.conn.execute(
        "SELECT d.doc_id, s.sec_id, s.heading FROM doc d JOIN section s ON s.doc_id = d.doc_id"
        " WHERE d.title LIKE ? AND d.status='ready' ORDER BY d.doc_no, s.ord",
        (RESUME_EVIDENCE_TITLE + "%",)).fetchall()]
    met = (fit or {}).get("met") or []
    stories = []
    for r in resume:
        # A story is something he did: an experience line with a number in it.
        # The summary and the publication list are claims about him, not stories.
        if not r["heading"].startswith("Experience"):
            continue
        sec = handle.read_section(r["doc_id"], r["sec_id"])
        for line in (sec["body"] if sec else "").split("\n"):
            line = line.lstrip("- ").strip()
            if len(line) > 40 and re.search(r"\d", line):
                want = CU._counts(CU.terms(line))
                best, best_n = None, 0
                for row in met:
                    have = CU._counts(CU.terms(row["requirement"] + " " + row["evidence"]))
                    n = sum(min(v, have.get(t, 0)) for t, v in want.items())
                    if n > best_n:
                        best, best_n = row, n
                stories.append((best_n, line, best, "%s§%s" % (r["doc_id"], r["sec_id"])))
    stories.sort(key=lambda x: -x[0])
    for _n, line, row, cite in stories[:5]:
        lines.append("- %s [%s]%s" % (_clip(line, 220), cite,
                                      (" → answers “%s”" % _clip(row["requirement"], 90))
                                      if row else ""))
    if not stories:
        lines.append("- No resume evidence was imported with this application.")

    lines += ["", "## The three objections likeliest to come up", ""]
    rehearsed = {}
    for s in steps:
        if not is_rehearsal(s["step_id"]):
            continue
        row = handle.conn.execute(
            "SELECT body FROM turn WHERE step_id=? AND role='tutor' ORDER BY seq DESC LIMIT 1",
            (s["step_id"],)).fetchone()
        rehearsed[_flat(s["title"].replace("Rehearse: ", ""))[:60]] = (
            s, _strong_answer_of(row["body"]) if row else "")
    objections = list((fit or {}).get("red_team") or [])
    objections += [r["requirement"] for r in (fit or {}).get("weak") or ()
                   if r["verdict"] == "MISSING" and not _is_process(r["requirement"])]
    for obj in objections[:3]:
        lines.append("- %s" % _clip(obj, 300))
        match = next((v for k, v in rehearsed.items() if k and _flat(obj).startswith(k[:40])), None)
        if match and match[1]:
            lines.append("  Your answer: %s" % _clip(match[1], 700))
        elif match:
            lines.append("  Not rehearsed yet: step %s." % match[0]["step_id"])
        else:
            lines.append("  Not rehearsed: no step asks this one.")

    lines += ["", "## Questions to ask them", ""]
    asks = ask_them(posting, fit)
    lines += ["- " + q for q in asks] or ["- The posting gave nothing specific to ask about."]
    return "\n".join(lines) + "\n"
