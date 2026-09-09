"""The page's document and the track store, mapped in both directions.

The page renders one JSON object. The store holds append-only rows in one
database per track. This module is the only place that knows both shapes, so a
change to either has exactly one file to follow.

Two rules decide the whole mapping.

**A transcript is a turn; everything else is a mark.** Chat is the one thing
whose loss loses a lesson, and `turn` already refuses DELETE and refuses UPDATE
outside one receipted case. Every other thing the page records (a topic ticked
off, a practice status, a banked question, a check result, a preference) is a
`mark`: one append-only row per change, keyed by kind and key, and the current
value of a key is the newest row for it. The older rows are its undo history.

**A client sends the change, never the document.** `/api/state` used to take a
whole `state.json` and a revision hash, and DESIGN-state-corpus.md rejects that
in its first conflict resolution: a revision proves the client read the file and
proves nothing about what the client is carrying. With deltas the loss is not
detected heuristically, it is unrepresentable. A stale tab that never saw a turn
cannot emit an op that removes it, because there is no op that removes anything.

That is also why no monotonicity rule is enforced here. The page diffs against
the document the server last acknowledged, so a value that goes backwards went
backwards because the candidate did it, and the superseded row is still on disk.
Refusing it would break un-ticking a topic, which is a thing the UI offers.

`FIELDS` below is served to the page inside the GET reply, so the page builds
its diff from this table rather than from a second copy of it. Adding a field
here is the whole change; the page picks it up on the next load.
"""

import calendar
import json
import re
import time

from . import config as C
from . import state as S


# The step-key contract, minted in index.html and validated here. Both sides
# change together or neither does: a key the page mints and this rejects is a
# turn the candidate watches disappear.
# \Z and not $: in Python `$` also matches immediately before a trailing
# newline, so "1:topic:T1\n" passed the old inline copy of this pattern and was
# then stored WITH the newline. A validated string and a stored string that
# differ is not a validation.
# Bounded, and not only shaped. The trailing run used to be unbounded, so a
# turn could carry a step key of any size: the turn branch charges `text` and
# `meta_json` and never the key, so the key rode into storage free and the delta
# budget could be evaded up to the HTTP request cap.
STEP_KEY_RE = re.compile(r"^\d{1,2}:(?:topic|practice|check):[A-Za-z0-9]{1,64}\Z")

# Ids are client-minted and land in a UNIQUE column, so they are checked here
# rather than trusted. Bounded length, and no characters that would make a log
# line or an error message ambiguous.
OP_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}\Z")
MARK_KEY_RE = re.compile(r"^[^\x00-\x1f]{1,200}\Z")

# The page's own role word for a chat entry, and the canonical role stored.
# "session" is an end-of-session review: it is not the tutor teaching, so it is
# stored as `system` and is therefore never picked up by
# compact_oldest_completed_step(), which only empties role='tutor'.
PAGE_ROLE_TO_TURN = {
    "me": "user",
    "assistant": "tutor",
    "sys": "system",
    "session": "system",
}
TURN_ROLE_TO_PAGE = {"user": "me", "tutor": "assistant", "system": "sys"}
# Two page roles share the canonical role `system`, so one of them cannot be
# recovered from the stored role alone and has to be carried in client_meta.
LOSSY_PAGE_ROLES = frozenset(
    r for r in PAGE_ROLE_TO_TURN
    if TURN_ROLE_TO_PAGE.get(PAGE_ROLE_TO_TURN[r]) != r)

# doc field  ->  (mark kind, container)
# container "map"    : doc[field][key] = value
# container "list"   : doc[field] is a list of value objects, key is the item id
# container "scalar" : doc[field] = value, key is the field name itself
FIELDS = {
    "topics":     ("topic", "map"),
    "practice":   ("practice", "map"),
    "recap":      ("recap", "map"),
    "checks":     ("check", "map"),
    "questions":  ("question", "map"),
    "assess":     ("assess", "map"),
    "qa":         ("qa", "list"),
    "sessions":   ("session", "list"),
    "recapBank":  ("card", "list"),
    "theme":      ("pref", "scalar"),
    "lastView":   ("pref", "scalar"),
    "provider":   ("pref", "scalar"),
    "model":      ("pref", "scalar"),
    "models":     ("pref", "scalar"),
    "effort":     ("pref", "scalar"),
    "assessAt":   ("pref", "scalar"),
    "assessCost": ("pref", "scalar"),
}
# Three names in the page document that this table deliberately leaves out.
#   assessList  the rows of the last re-check run, kept only to redraw one
#               panel. The old note here said its size grows with the
#               curriculum. It does not: MAX_ASSESS_STEPS is 18 and is enforced
#               on both sides, so one run returns at most 18 rows whatever the
#               plan looks like, and 18 typical rows measure 1,693 bytes against
#               the 4 KiB per-mark cap. The real objection is the tail. Rows
#               carrying 200-character reasons reach 4,753 bytes, a mark that
#               size is refused, and validate_ops rejects the WHOLE delta, so
#               one verbose grading run would wedge every later save until the
#               tab was reloaded. Excluded until that tail is bounded rather
#               than because it is unbounded. The visible cost: after a reload
#               the "Last checked" hint survives, built from the two `pref`
#               scalars below, while the graded rows under it do not.
#   _seq        a per-page id counter. makeId() already mixes in Date.now(),
#               so ids do not collide across reloads without it.
#   savedAt     derived here from the newest row, never sent by the page.
# Every non-scalar kind names exactly one field, so the reverse map is a
# straight lookup. `pref` is deliberately absent: its key IS the field name, so
# it has no single field to point at and materialise() handles it before it
# reaches here.
KIND_TO_FIELD = {kind: field for field, (kind, container) in FIELDS.items()
                 if container != "scalar"}
MARK_KINDS = frozenset(k for k, _ in FIELDS.values())
PREF_FIELDS = frozenset(f for f, (_, c) in FIELDS.items() if c == "scalar")

# A delta is small by construction. The one large write is a first push, which
# carries the seeded document, and that is four topics and five practice items.
MAX_OPS_PER_WRITE = 2_000
MAX_DELTA_BYTES = 2 * 1024 * 1024


class OpRejected(ValueError):
    """A malformed op. The whole write is refused, never partly applied.

    Dropping one bad op and applying the rest would be a silent loss on the one
    path whose entire purpose is that loss cannot happen silently.
    """


def _require(cond, message):
    if not cond:
        raise OpRejected(message)


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


# ---- reading ---------------------------------------------------------------
def materialise(handle):
    """The page document this track currently holds.

    Only fields with rows are present. Every absent field is left out rather
    than filled with an empty value, so the page's own hydrate() supplies its
    seed defaults instead of being handed an emptier document than it started
    with.
    """
    doc = {}
    newest_utc = None

    for kind, key, _seq, raw in handle.marks():
        if kind not in MARK_KINDS:
            continue                       # a kind this build no longer serves
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if kind == "pref":
            if key in PREF_FIELDS:
                doc[key] = value
            continue
        field = KIND_TO_FIELD.get(kind)
        if field is None:
            continue
        container = FIELDS[field][1]
        if container == "map":
            doc.setdefault(field, {})[key] = value
        else:
            doc.setdefault(field, []).append(value)

    rows = handle.conn.execute(
        "SELECT step_id, at_utc, role, body, client_meta FROM turn ORDER BY seq")
    for row in rows:
        entry = {"role": TURN_ROLE_TO_PAGE.get(row["role"], "sys"),
                 "text": row["body"]}
        meta = row["client_meta"]
        if meta:
            try:
                extra = json.loads(meta)
            except ValueError:
                extra = None
            if isinstance(extra, dict):
                # The store owns these three. client_meta is client-supplied
                # display data, and a plain update() let it overwrite `text`,
                # so the document the page rendered could differ from the
                # append-only body the triggers exist to protect. Everything
                # else it carries (provider, model, effort, tokens, cost, the
                # page's own role word) is display-only and passes through.
                for k, v in extra.items():
                    if k not in _STORE_OWNED_TURN_FIELDS:
                        entry[k] = v
        doc.setdefault("stepLog", {}).setdefault(row["step_id"], []).append(entry)
        newest_utc = row["at_utc"] if newest_utc is None else max(
            newest_utc, row["at_utc"])

    mark_utc = handle.conn.execute(
        "SELECT MAX(at_utc) m FROM mark").fetchone()["m"]
    if mark_utc:
        newest_utc = mark_utc if newest_utc is None else max(newest_utc, mark_utc)
    if newest_utc:
        doc["savedAt"] = _utc_to_ms(newest_utc)
    return doc


# What the store, not the client, decides about a rendered turn.
_STORE_OWNED_TURN_FIELDS = frozenset({"text", "seq", "at"})


def _utc_to_ms(stamp):
    """"2026-09-07T04:05:06Z" -> epoch milliseconds, or None.

    calendar.timegm, not time.mktime: the stamp is UTC and mktime reads local
    time, which is how an earlier age check came out by twice the local offset.
    """
    try:
        return int(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))) * 1000
    except (ValueError, TypeError):
        return None


# ---- writing ---------------------------------------------------------------
def validate_ops(raw_ops):
    """Type-check and bound every op before a single one is applied.

    Client input, so nothing here is trusted: the step key must match the
    contract, the mark kind must be one this build serves, and every id must fit
    the column it lands in. A malformed op refuses the whole write.
    """
    _require(isinstance(raw_ops, list), "ops must be a list")
    _require(len(raw_ops) <= MAX_OPS_PER_WRITE,
             "at most %d changes per write" % MAX_OPS_PER_WRITE)
    total, out = 0, []
    for i, op in enumerate(raw_ops):
        _require(isinstance(op, dict), "op %d is not an object" % i)
        kind = op.get("op")
        op_id = op.get("id")
        _require(isinstance(op_id, str) and OP_ID_RE.match(op_id),
                 "op %d has no usable id" % i)
        if kind == "turn":
            step = op.get("step")
            _require(isinstance(step, str) and STEP_KEY_RE.match(step),
                     "op %d names a step key that is not in the contract" % i)
            page_role = op.get("pageRole")
            # `in` on a dict hashes the key, and an unhashable one (a list, a
            # dict) raises TypeError straight out of this function, past the
            # OpRejected contract, so the request dies with no named refusal.
            _require(isinstance(page_role, str) and page_role in PAGE_ROLE_TO_TURN,
                     "op %d carries an unknown chat role" % i)
            text = op.get("text")
            _require(isinstance(text, str), "op %d has no text" % i)
            # turn.body_bytes carries CHECK (body_bytes <= 8192). Without this
            # the oversized body reaches that CHECK from inside apply_ops, which
            # has no enclosing transaction, so every op before it in the batch is
            # already committed and this function's promise to "refuse the whole
            # write" is broken by the one case it does not check.
            text_bytes = len(text.encode("utf-8"))
            _require(text_bytes <= C.TURN_MAX_BYTES,
                     "op %d is larger than one turn holds" % i)
            title = op.get("title")
            _require(title is None or isinstance(title, str),
                     "op %d has a non-text step title" % i)
            meta = op.get("meta")
            _require(meta is None or isinstance(meta, dict),
                     "op %d has non-object metadata" % i)
            meta_json = None
            if meta or page_role in LOSSY_PAGE_ROLES:
                carry = dict(meta or {})
                carry["role"] = page_role
                meta_json = _json_dump(carry)
                _require(len(meta_json.encode("utf-8")) <= C.TURN_META_MAX_BYTES,
                         "op %d carries more display metadata than a turn holds" % i)
            # Bytes on every term. MAX_DELTA_BYTES is a byte cap, and meta_json
            # is a str: charging its character count undercounts a non-ASCII
            # value by up to four times.
            total += text_bytes + len(step.encode("utf-8"))
            if meta_json:
                total += len(meta_json.encode("utf-8"))
            out.append({"op": "turn", "id": op_id, "step": step,
                        "title": (title or step)[:160],
                        "role": PAGE_ROLE_TO_TURN[page_role],
                        "text": text, "meta_json": meta_json})
        elif kind == "mark":
            mark_kind = op.get("kind")
            _require(isinstance(mark_kind, str) and mark_kind in MARK_KINDS,
                     "op %d names an unknown mark kind" % i)
            key = op.get("key")
            _require(isinstance(key, str) and MARK_KEY_RE.match(key),
                     "op %d has no usable key" % i)
            if mark_kind == "pref":
                _require(key in PREF_FIELDS,
                         "op %d sets a preference this build does not serve" % i)
            _require("value" in op, "op %d has no value" % i)
            try:
                value_json = _json_dump(op["value"])
            except (TypeError, ValueError):
                raise OpRejected("op %d has a value that is not JSON" % i)
            _require(len(value_json.encode("utf-8")) <= C.MARK_MAX_BYTES,
                     "op %d is larger than one mark holds" % i)
            total += len(value_json.encode("utf-8")) + len(key.encode("utf-8"))
            out.append({"op": "mark", "id": op_id, "kind": mark_kind,
                        "key": key, "value_json": value_json})
        else:
            raise OpRejected("op %d is neither a turn nor a mark" % i)
        _require(total <= MAX_DELTA_BYTES, "this write is too large")
    return out


def apply_ops(handle, ops):
    """Apply validated ops in order. Returns what happened, per class.

    Idempotent twice over. A mark whose value already matches is skipped without
    a row, so a client that re-sends its whole document costs nothing, and both
    row types carry a client-minted id in a UNIQUE column, so a POST retried
    over a dropped connection returns the existing row instead of a duplicate.
    """
    report = {"turns": 0, "marks": 0, "unchanged": 0, "steps": 0}
    for op in ops:
        if op["op"] == "turn":
            if handle.ensure_step(op["step"], op["title"]):
                report["steps"] += 1
            handle.append_turn(op["step"], op["role"], op["text"], op["id"],
                               client_meta=op["meta_json"])
            report["turns"] += 1
        else:
            current = handle.current_mark(op["kind"], op["key"])
            if current is not None and current[1] == op["value_json"]:
                report["unchanged"] += 1
                continue
            handle.append_mark(op["kind"], op["key"], op["value_json"], op["id"])
            report["marks"] += 1
    return report


# ---- migration -------------------------------------------------------------
def ops_from_document(doc, op_prefix):
    """Every field of a whole page document, as ops. Used by the import path.

    The page never calls this: it sends changes. It exists for the one-time
    import of a `progress/state.json` written before the store existed, where
    the whole document genuinely is the delta.
    """
    ops = []
    if not isinstance(doc, dict):
        return ops
    for field, (kind, container) in FIELDS.items():
        value = doc.get(field)
        if value is None:
            continue
        if container == "scalar":
            ops.append({"op": "mark", "kind": "pref", "key": field,
                        "value": value,
                        "id": "%s-pref-%s" % (op_prefix, field)})
        elif container == "map":
            if not isinstance(value, dict):
                continue
            for key in sorted(value):
                ops.append({"op": "mark", "kind": kind, "key": str(key)[:200],
                            "value": value[key],
                            "id": "%s-%s-%s" % (op_prefix, kind,
                                                S.sha16(str(key)))})
        else:
            if not isinstance(value, list):
                continue
            for n, item in enumerate(value):
                if not isinstance(item, dict):
                    continue
                key = str(item.get("id") or S.sha16(_json_dump(item)))[:200]
                ops.append({"op": "mark", "kind": kind, "key": key,
                            "value": item,
                            "id": "%s-%s-%d" % (op_prefix, kind, n)})

    log = doc.get("stepLog")
    if isinstance(log, dict):
        for step in sorted(log):
            entries = log.get(step)
            if not isinstance(entries, list):
                continue
            for n, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                role = entry.get("role")
                if role not in PAGE_ROLE_TO_TURN:
                    continue
                meta = {k: v for k, v in entry.items()
                        if k not in ("role", "text")}
                ops.append({"op": "turn", "step": step, "pageRole": role,
                            "text": str(entry.get("text") or ""),
                            "meta": meta or None,
                            "id": "%s-t-%s-%d" % (op_prefix,
                                                  S.sha16(step)[:12], n)})
    return ops
