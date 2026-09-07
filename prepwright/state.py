"""library.db and track.db: the authoritative store.

Implements DESIGN-state-corpus.md sections A, B and F. Delta writes only, so
a stale client cannot express a loss. Every cap is enforced inside the same
transaction as the write it bounds.

Three properties this module exists to make structural rather than careful:

1. Nothing shrinks. `turn`, `assessment`, `card_review`, `gap_history` and
   `track_history` raise ABORT on DELETE and on any UPDATE outside one narrow
   receipted case. A stale client cannot express a loss, so the loss is not
   detected by a heuristic, it is unrepresentable.

2. Caps are counted by the write that causes them. `track_meta.bytes_turns`,
   `bytes_cards` and `bytes_corpus` accumulate row-size deltas inside the same
   transaction as the row, so a cap can never be checked against a number that
   was refreshed at startup and has been wrong ever since.

3. One track's bytes cannot reach another track's prompt. `open_track()` is the
   only function in the package that calls sqlite3.connect for a track, and
   `build_pack()` takes a handle rather than a track id, so assembling a prompt
   without a handle is a TypeError at the call site rather than a leak.
"""

import calendar
import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time

from prepwright import config as C


# ---- errors ----------------------------------------------------------------
class StoreError(RuntimeError):
    """Base for everything this module refuses to do."""


class LayoutError(StoreError):
    """The storage root is not the shape this version of the code understands."""


class IsolationError(StoreError):
    """A read or a prompt tried to leave the track that owns it."""


class CapExceeded(StoreError):
    """A write would take a counted byte class past its published cap."""

    def __init__(self, what, would_be, cap):
        super().__init__("%s would reach %d bytes, cap is %d" % (what, would_be, cap))
        self.what, self.would_be, self.cap = what, would_be, cap


class TrackMoved(StoreError):
    """The track changed generation under this handle (archived, restored, closed)."""


class CorruptStore(StoreError):
    """A database file failed quick_check and could not be recovered silently."""


_TRACK_ID = re.compile(C.TRACK_ID_RE)
_DOC_NAME = re.compile(C.DOC_NAME_RE)
_SEC_ID = re.compile(r"^s[0-9]{2}$")

_tmp_counter = [0]
_tmp_lock = threading.Lock()
# One lock per track id, so two threads in this process serialise before they
# reach SQLite. An flock is per file description and does not block a process
# against itself, so it cannot stand in for this.
_track_locks = {}
_track_locks_lock = threading.Lock()


def _lock_for(track_id):
    with _track_locks_lock:
        lock = _track_locks.get(track_id)
        if lock is None:
            lock = _track_locks[track_id] = threading.RLock()
        return lock


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def mono_ns():
    return time.monotonic_ns()


def sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha16(data):
    return sha256_hex(data)[:16]


# ---- filesystem ------------------------------------------------------------
def _no_symlink(path):
    if os.path.islink(path):
        raise LayoutError("%s is a symlink; storage paths must not be" % path)


def ensure_dir(path):
    _no_symlink(path)
    os.makedirs(path, mode=C.DIR_MODE, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise LayoutError("%s is not a directory" % path)
    os.chmod(path, C.DIR_MODE)
    return path


def atomic_write(path, data, mode=C.FILE_MODE):
    """Write bytes so a reader sees the old file or the new one, never a mix.

    The pid, thread id and counter in the temp name are load-bearing. With one
    shared ".tmp" path two concurrent writers interleave into a single file,
    and the second os.replace raises after the first took the name, so one
    caller is told its save failed while mixed bytes have already landed.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    with _tmp_lock:
        _tmp_counter[0] += 1
        n = _tmp_counter[0]
    directory = os.path.dirname(path) or "."
    tmp = "%s.%d.%d.%d.part" % (path, os.getpid(), threading.get_ident(), n)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return path


def ensure_home():
    """Create the storage root, or refuse a root this code does not understand.

    The layout generation is read before any database is opened, because a
    schema ladder can migrate tables and cannot migrate a directory tree.
    """
    ensure_dir(C.HOME)
    for d in (C.TRACKS_ROOT, C.ARCHIVE_ROOT, C.TRASH_ROOT,
              C.QUARANTINE_ROOT, C.LIBRARY_BAK):
        ensure_dir(d)
    if os.path.exists(C.VERSION_FILE):
        _no_symlink(C.VERSION_FILE)
        with open(C.VERSION_FILE, "r", encoding="utf-8") as fh:
            found = fh.read().strip()
        if found != C.LAYOUT_GENERATION:
            raise LayoutError(
                "storage layout generation %r, this build understands %r"
                % (found, C.LAYOUT_GENERATION))
    else:
        atomic_write(C.VERSION_FILE, C.LAYOUT_GENERATION + "\n")
    return C.HOME


# ---- connections -----------------------------------------------------------
def connect(path, fresh_pragmas=True):
    """One connection with the pragmas of DESIGN section B applied in order."""
    is_new = not os.path.exists(path)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    if is_new:
        os.chmod(path, C.FILE_MODE)
    if fresh_pragmas:
        for p in C.PRAGMAS_PRE_CREATE:
            conn.execute(p)
    for p in C.PRAGMAS_EVERY_OPEN:
        conn.execute(p)
    return conn


def quick_check(conn):
    row = conn.execute("PRAGMA quick_check").fetchone()
    return bool(row) and row[0] == "ok"


class _Txn(object):
    """BEGIN IMMEDIATE ... COMMIT, with the write lock taken up front.

    IMMEDIATE rather than DEFERRED: a deferred transaction takes the write lock
    on its first write, which is after the caps have been read, so two writers
    could both read room and both commit past it.
    """

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            try:
                self.conn.execute("COMMIT")
            except sqlite3.Error:
                # A COMMIT that raises (a full disk, an I/O error, a busy WAL)
                # otherwise leaves the connection inside the transaction holding
                # the write lock, and every later BEGIN IMMEDIATE fails with an
                # error naming none of that. Roll back so the failure stays
                # local to the write that caused it.
                try:
                    self.conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
        else:
            try:
                self.conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        return False


# ---- schema ----------------------------------------------------------------
LIBRARY_DDL = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL) STRICT;

CREATE TABLE IF NOT EXISTS track (
  track_id       TEXT PRIMARY KEY,
  short_code     TEXT NOT NULL UNIQUE,
  title          TEXT NOT NULL,
  employer       TEXT,
  role_title     TEXT,
  kind           TEXT NOT NULL CHECK (kind IN ('job','topic')),
  source_kind    TEXT NOT NULL CHECK (source_kind IN ('pasted','imported','freeform')),
  source_path    TEXT,
  source_sha256  TEXT,
  lifecycle      TEXT NOT NULL CHECK (lifecycle IN
                   ('active','archiving','archived','restoring','trashed','lost')),
  outcome        TEXT NOT NULL DEFAULT 'open' CHECK (outcome IN
                   ('open','interviewing','offer','rejected','withdrawn')),
  pinned         INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0,1)),
  no_auto_compact INTEGER NOT NULL DEFAULT 0 CHECK (no_auto_compact IN (0,1)),
  phase          TEXT NOT NULL CHECK (phase IN
                   ('intake','diagnostic','gaps','research','curriculum','teaching','review')),
  generation     INTEGER NOT NULL DEFAULT 1,
  created_utc    TEXT NOT NULL,
  opened_utc     TEXT NOT NULL,
  touched_utc    TEXT NOT NULL,
  bytes_db       INTEGER NOT NULL DEFAULT 0,
  bytes_wal      INTEGER NOT NULL DEFAULT 0,
  bytes_corpus   INTEGER NOT NULL DEFAULT 0,
  bytes_index    INTEGER NOT NULL DEFAULT 0,
  bytes_backup   INTEGER NOT NULL DEFAULT 0,
  bytes_spool    INTEGER NOT NULL DEFAULT 0,
  bytes_archive  INTEGER NOT NULL DEFAULT 0,
  bytes_total    INTEGER NOT NULL DEFAULT 0,
  n_turns        INTEGER NOT NULL DEFAULT 0,
  n_docs         INTEGER NOT NULL DEFAULT 0,
  n_cards        INTEGER NOT NULL DEFAULT 0,
  accounted_utc  TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS track_by_pressure ON track(lifecycle, pinned, opened_utc);

CREATE TABLE IF NOT EXISTS track_lease (
  track_id      TEXT PRIMARY KEY REFERENCES track(track_id) ON DELETE CASCADE,
  holder_pid    INTEGER NOT NULL,
  holder_boot   TEXT NOT NULL,
  client_label  TEXT NOT NULL,
  generation    INTEGER NOT NULL,
  heartbeat_utc TEXT NOT NULL,
  heartbeat_mono INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS due_card (
  track_id  TEXT NOT NULL,
  card_id   TEXT NOT NULL,
  due_utc   TEXT NOT NULL,
  retired   INTEGER NOT NULL DEFAULT 0,
  cold      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (track_id, card_id)
) STRICT;
CREATE INDEX IF NOT EXISTS due_card_by_when ON due_card(due_utc) WHERE retired = 0;

CREATE TABLE IF NOT EXISTS track_history (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, track_id TEXT NOT NULL,
  kind TEXT NOT NULL, detail TEXT NOT NULL
) STRICT;
CREATE TRIGGER IF NOT EXISTS track_history_no_update BEFORE UPDATE ON track_history
  BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS track_history_no_delete BEFORE DELETE ON track_history
  BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE IF NOT EXISTS event (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, track_id TEXT,
  kind TEXT NOT NULL, bytes_freed INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL
) STRICT;
"""

TRACK_DDL = """
CREATE TABLE IF NOT EXISTS track_meta (
  track_id  TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
  label TEXT NOT NULL, created_utc TEXT NOT NULL,
  next_doc_no INTEGER NOT NULL DEFAULT 1, next_spool_seq INTEGER NOT NULL DEFAULT 1,
  backup_seq INTEGER NOT NULL DEFAULT 0,
  bytes_turns INTEGER NOT NULL DEFAULT 0,
  bytes_cards INTEGER NOT NULL DEFAULT 0,
  bytes_corpus INTEGER NOT NULL DEFAULT 0,
  writes_since_keep INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TRIGGER IF NOT EXISTS track_meta_one_row BEFORE INSERT ON track_meta
  WHEN (SELECT COUNT(*) FROM track_meta) >= 1
  BEGIN SELECT RAISE(ABORT,'track_meta holds exactly one row'); END;

CREATE TABLE IF NOT EXISTS intake (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  kind TEXT NOT NULL CHECK (kind IN ('pasted','imported','freeform')),
  source_path TEXT, source_sha256 TEXT, source_mtime_ns INTEGER,
  captured_utc TEXT NOT NULL,
  body TEXT NOT NULL, body_sha256 TEXT NOT NULL,
  body_bytes INTEGER NOT NULL CHECK (body_bytes <= 65536)
) STRICT;

CREATE TABLE IF NOT EXISTS gap (
  gap_id TEXT PRIMARY KEY, ord INTEGER NOT NULL UNIQUE,
  label TEXT NOT NULL, why TEXT NOT NULL,
  jd_span TEXT,
  level TEXT NOT NULL CHECK (level IN ('none','shaky','solid')),
  status TEXT NOT NULL CHECK (status IN
    ('proposed','approved','edited','declined','covered')),
  weight REAL NOT NULL DEFAULT 1.0,
  rev INTEGER NOT NULL DEFAULT 1,
  proposed_utc TEXT NOT NULL, decided_utc TEXT
) STRICT;
CREATE TABLE IF NOT EXISTS gap_history (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, gap_id TEXT NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, note TEXT
) STRICT;
CREATE TRIGGER IF NOT EXISTS gap_audit AFTER UPDATE OF status ON gap
  BEGIN INSERT INTO gap_history(at_utc,gap_id,from_status,to_status,note)
        VALUES (strftime('%Y-%m-%dT%H:%M:%SZ','now'), NEW.gap_id, OLD.status, NEW.status, NULL); END;
CREATE TRIGGER IF NOT EXISTS gap_history_no_update BEFORE UPDATE ON gap_history
  BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS gap_history_no_delete BEFORE DELETE ON gap_history
  BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE IF NOT EXISTS research_run (
  run_id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, finished_utc TEXT,
  queries TEXT NOT NULL, tool_calls INTEGER NOT NULL DEFAULT 0,
  bytes_fetched INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('running','ok','failed','aborted'))
) STRICT;

CREATE TABLE IF NOT EXISTS doc (
  doc_id        TEXT PRIMARY KEY,
  doc_no        INTEGER NOT NULL UNIQUE,
  run_id        TEXT REFERENCES research_run(run_id),
  status        TEXT NOT NULL CHECK (status IN
                  ('writing','ready','quarantined','origin_drift','unverifiable')),
  file_name     TEXT NOT NULL UNIQUE,
  file_sha256   TEXT,
  file_bytes    INTEGER CHECK (file_bytes <= 12288),
  file_mtime_ns INTEGER,
  title         TEXT NOT NULL,
  origin_url    TEXT NOT NULL,
  final_url     TEXT,
  origin_sha256 TEXT NOT NULL, origin_bytes INTEGER NOT NULL,
  extract_sha256 TEXT NOT NULL,
  fetched_utc   TEXT NOT NULL, verified_utc TEXT,
  publisher TEXT, published_on TEXT,
  vetting TEXT NOT NULL CHECK (vetting IN ('primary','secondary','vendor','community')),
  trust   INTEGER NOT NULL CHECK (trust BETWEEN 1 AND 5),
  n_sections INTEGER NOT NULL DEFAULT 0,
  cite_count INTEGER NOT NULL DEFAULT 0, last_cited_utc TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS section (
  doc_id TEXT NOT NULL REFERENCES doc(doc_id) ON DELETE CASCADE,
  sec_id TEXT NOT NULL,
  ord INTEGER NOT NULL,
  heading TEXT NOT NULL,
  body_chars INTEGER NOT NULL CHECK (body_chars <= 900),
  body_sha16 TEXT NOT NULL,
  byte_off INTEGER NOT NULL, byte_len INTEGER NOT NULL,
  origin_span TEXT NOT NULL,
  src_start INTEGER, src_end INTEGER,
  concept TEXT,
  PRIMARY KEY (doc_id, sec_id)
) STRICT;

CREATE TABLE IF NOT EXISTS step (
  step_id TEXT PRIMARY KEY, ord INTEGER NOT NULL UNIQUE,
  title TEXT NOT NULL, objective TEXT NOT NULL,
  gap_id TEXT REFERENCES gap(gap_id),
  status TEXT NOT NULL CHECK (status IN ('locked','ready','open','done','skipped')),
  evidence_state TEXT NOT NULL DEFAULT 'full'
    CHECK (evidence_state IN ('full','degraded','empty')),
  tier TEXT NOT NULL DEFAULT 'core' CHECK (tier IN ('core','depth','reference')),
  est_minutes INTEGER, score REAL CHECK (score BETWEEN 0 AND 1),
  review TEXT,
  compacted INTEGER NOT NULL DEFAULT 0,
  rev INTEGER NOT NULL DEFAULT 1,
  opened_utc TEXT, completed_utc TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS step_slice (
  step_id TEXT NOT NULL REFERENCES step(step_id) ON DELETE CASCADE,
  doc_id TEXT NOT NULL, sec_id TEXT NOT NULL, ord INTEGER NOT NULL,
  PRIMARY KEY (step_id, doc_id, sec_id),
  FOREIGN KEY (doc_id, sec_id) REFERENCES section(doc_id, sec_id)
) STRICT;

CREATE TABLE IF NOT EXISTS turn (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id TEXT NOT NULL REFERENCES step(step_id),
  at_utc TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user','tutor','system')),
  reply_to_seq INTEGER REFERENCES turn(seq),
  body TEXT NOT NULL,
  body_bytes INTEGER NOT NULL CHECK (body_bytes <= 8192),
  body_sha16 TEXT NOT NULL,
  overflow_bytes INTEGER NOT NULL DEFAULT 0,
  overflow_sha256 TEXT,
  spool_ref TEXT,
  pack_sha16 TEXT,
  citations TEXT,
  ungrounded INTEGER NOT NULL DEFAULT 0,
  client_turn_id TEXT NOT NULL UNIQUE,
  in_tok INTEGER, out_tok INTEGER
) STRICT;
CREATE INDEX IF NOT EXISTS turn_tail ON turn(step_id, seq DESC);

CREATE TABLE IF NOT EXISTS turn_compaction (
  turn_seq INTEGER PRIMARY KEY,
  at_utc TEXT NOT NULL, reason TEXT NOT NULL CHECK (reason IN ('cap','stale','user')),
  orig_bytes INTEGER NOT NULL, orig_sha16 TEXT NOT NULL, step_review_present INTEGER NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS turn_no_delete BEFORE DELETE ON turn
  BEGIN SELECT RAISE(ABORT,'turns are append-only'); END;
CREATE TRIGGER IF NOT EXISTS turn_no_update BEFORE UPDATE ON turn
  WHEN NOT (NEW.body = '' AND OLD.role = 'tutor' AND OLD.body <> ''
            AND (SELECT COUNT(*) FROM turn_compaction WHERE turn_seq = OLD.seq) = 1)
  BEGIN SELECT RAISE(ABORT,'append-only except receipted tutor compaction'); END;

CREATE TABLE IF NOT EXISTS turn_pending (
  step_id TEXT PRIMARY KEY REFERENCES step(step_id) ON DELETE CASCADE,
  client_label TEXT NOT NULL, started_utc TEXT NOT NULL, started_mono INTEGER NOT NULL,
  request_sha16 TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS draft (
  step_id TEXT NOT NULL, client_label TEXT NOT NULL,
  body TEXT NOT NULL, updated_utc TEXT NOT NULL,
  PRIMARY KEY (step_id, client_label)
) STRICT;

CREATE TABLE IF NOT EXISTS assessment (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL,
  step_id TEXT NOT NULL REFERENCES step(step_id),
  score REAL NOT NULL CHECK (score BETWEEN 0 AND 1),
  rubric TEXT NOT NULL, evidence_turn_seq INTEGER, misconception TEXT
) STRICT;
CREATE TRIGGER IF NOT EXISTS assessment_no_update BEFORE UPDATE ON assessment
  BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS assessment_no_delete BEFORE DELETE ON assessment
  BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE IF NOT EXISTS card (
  card_id TEXT PRIMARY KEY, step_id TEXT REFERENCES step(step_id),
  front TEXT NOT NULL, back TEXT NOT NULL,
  cite TEXT NOT NULL,
  cite_snippet TEXT NOT NULL,
  created_utc TEXT NOT NULL,
  due_utc TEXT NOT NULL, interval_days REAL NOT NULL DEFAULT 1,
  ease REAL NOT NULL DEFAULT 2.5, reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0, last_review_utc TEXT,
  retired INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TABLE IF NOT EXISTS card_review (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL REFERENCES card(card_id),
  at_utc TEXT NOT NULL, at_mono INTEGER NOT NULL,
  grade INTEGER NOT NULL CHECK (grade BETWEEN 0 AND 3),
  prev_interval REAL, next_interval REAL,
  clock_suspect INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TRIGGER IF NOT EXISTS card_review_no_update BEFORE UPDATE ON card_review
  BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER IF NOT EXISTS card_review_no_delete BEFORE DELETE ON card_review
  BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE IF NOT EXISTS recovery_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL,
  kind TEXT NOT NULL, detail TEXT NOT NULL
) STRICT;
"""


# ---- library ---------------------------------------------------------------
def open_library(create=True):
    """The registry. Holds no content: no job text, no turns, no card fronts.

    Reading another track's material therefore requires opening a second file,
    which is the first of the eight isolation layers and the cheapest.
    """
    ensure_home()
    if not create and not os.path.exists(C.LIBRARY_DB):
        raise LayoutError("library.db does not exist")
    _no_symlink(C.LIBRARY_DB)
    conn = connect(C.LIBRARY_DB)
    conn.executescript(LIBRARY_DDL)
    conn.execute(
        "INSERT OR IGNORE INTO meta(k,v) VALUES ('schema_version',?)",
        (str(C.LIBRARY_SCHEMA_VERSION),))
    conn.execute(
        "INSERT OR IGNORE INTO meta(k,v) VALUES ('created_at',?)", (utc_now(),))
    return conn


def library_event(conn, kind, detail, track_id=None, bytes_freed=0):
    """A housekeeping receipt. Pruned by count, never silently."""
    conn.execute(
        "INSERT INTO event(at_utc,track_id,kind,bytes_freed,detail) VALUES (?,?,?,?,?)",
        (utc_now(), track_id, kind, int(bytes_freed), detail))


def library_history(conn, track_id, kind, detail):
    """Never pruned. The candidate's record of what they prepared for outlives
    every track it describes, including the ones they delete."""
    conn.execute(
        "INSERT INTO track_history(at_utc,track_id,kind,detail) VALUES (?,?,?,?)",
        (utc_now(), track_id, kind, detail))


# ---- track paths -----------------------------------------------------------
def track_dir(track_id):
    """The one place a track id becomes a path.

    Pattern first, then realpath, then a containment test against the resolved
    tracks root, then an lstat for a symlink at the final component. A fallback
    path here is how the wrong corpus ends up in the right prompt, so there is
    none: every failure raises.
    """
    if not isinstance(track_id, str) or not _TRACK_ID.match(track_id):
        raise IsolationError("not a track id: %r" % (track_id,))
    root = os.path.realpath(C.TRACKS_ROOT)
    full = os.path.realpath(os.path.join(root, track_id))
    if full != root and not full.startswith(root + os.sep):
        raise IsolationError("track path escapes the tracks root")
    if os.path.dirname(full) != root:
        raise IsolationError("track path is not directly under the tracks root")
    return full


class TrackHandle(object):
    """One open track: one connection, one corpus root, one track id.

    Every read and write goes through an instance. There is no module-level
    connection, no connection cache keyed by anything else, and no default
    track, so building a prompt without a handle is a TypeError rather than a
    silent read of whichever track happened to be open last.
    """

    def __init__(self, track_id, conn, directory, generation, holds_lease=False):
        self.track_id = track_id
        self.conn = conn
        self.dir = directory
        self.generation = generation
        self.corpus_root = os.path.realpath(os.path.join(directory, "corpus"))
        self.lock = _lock_for(track_id)
        self.holds_lease = holds_lease
        self.closed = False
        # Its own registry connection, not the caller's. Every write checks its
        # generation through this, and close() gives the lease back through it,
        # so neither depends on a library handle the caller may have closed.
        self._lib = None

    def _library(self):
        if self._lib is None:
            self._lib = open_library()
        return self._lib

    # -- lifecycle ----------------------------------------------------------
    def close(self):
        """Close the connection and, if this handle took the lease, release it.

        Closing the chat has to free the track. Without this, a candidate who
        closes a session cannot archive or delete that track for two minutes and
        is told it is open on a client that has already gone.
        """
        if not self.closed:
            try:
                # At track close, per DESIGN section F. With no caller here, no
                # backup was ever written and recovery from a corrupt track.db
                # had nothing to restore from.
                if self.writes_since_keep() > 0:
                    self.backup()
            except (StoreError, sqlite3.Error, OSError):
                pass
            try:
                if self.holds_lease:
                    release_lease(self._library(), self.track_id)
                    self.holds_lease = False
            except sqlite3.Error:
                pass
            try:
                self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                pass
            self.conn.close()
            if self._lib is not None:
                try:
                    self._lib.close()
                except sqlite3.Error:
                    pass
                self._lib = None
            self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def assert_live(self, lib=None):
        """The generation this handle was opened at still stands.

        Archive, restore and a forced session close all bump it, so a write
        arriving from a handle taken before one of those is refused with the
        client's draft intact on both sides rather than landing in a directory
        that is about to be replaced.
        """
        conn = lib or self._library()
        if self.holds_lease:
            # The lease's liveness test reads this stamp, so a candidate who is
            # writing can never have their open track reclaimed underneath them.
            try:
                heartbeat(conn, self.track_id)
            except sqlite3.Error:
                pass
        row = conn.execute(
            "SELECT generation, lifecycle FROM track WHERE track_id=?",
            (self.track_id,)).fetchone()
        if row is None:
            raise TrackMoved("track %s is no longer registered" % self.track_id)
        if row["generation"] != self.generation:
            raise TrackMoved(
                "track %s moved to generation %d under this handle"
                % (self.track_id, row["generation"]))

    def backup(self, force=False):
        """VACUUM INTO backup/<seq>.track.db, at most once per published interval.

        Named by a monotonic sequence rather than a timestamp, so a clock that
        steps backwards cannot make rotation unlink the newest copy, which is
        the one moment a backup exists for.
        """
        backups = ensure_dir(os.path.join(self.dir, "backup"))
        names = sorted(n for n in os.listdir(backups) if n.endswith(".track.db"))
        if names and not force:
            try:
                newest = os.path.join(backups, names[-1])
                if time.time() - os.path.getmtime(newest) < C.BACKUP_MIN_INTERVAL_SECONDS:
                    return None
            except OSError:
                pass
        if not quick_check(self.conn):
            raise CorruptStore(
                "refusing to back up a track.db that fails quick_check: %s"
                % self.track_id)
        with self.lock:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            seq = int(self._meta()["backup_seq"]) + 1
            with _Txn(self.conn):
                self.conn.execute("UPDATE track_meta SET backup_seq=?", (seq,))
            dest = os.path.join(backups, "%06d.track.db" % seq)
            self.conn.execute("VACUUM INTO ?", (dest,))
            os.chmod(dest, C.FILE_MODE)
        rotate_backups(backups,
                       C.BACKUPS_LEASED if self.holds_lease else C.BACKUPS_ACTIVE)
        return dest

    # -- counters -----------------------------------------------------------
    def _meta(self):
        row = self.conn.execute("SELECT * FROM track_meta").fetchone()
        if row is None:
            raise CorruptStore("track_meta is empty in %s" % self.track_id)
        return row

    def _charge(self, column, delta, cap, label):
        """Add delta to a counted byte class, inside the caller's transaction.

        Read, test, write, all under the same BEGIN IMMEDIATE the caller holds.
        A cap tested against a number read outside the transaction is not a cap,
        it is a suggestion two concurrent writers can both satisfy.
        """
        row = self._meta()
        would_be = int(row[column]) + int(delta)
        if delta > 0 and would_be > cap:
            raise CapExceeded(label, would_be, cap)
        self.conn.execute(
            "UPDATE track_meta SET %s = %s + ?" % (column, column), (int(delta),))
        return would_be

    def _bump_writes(self):
        self.conn.execute(
            "UPDATE track_meta SET writes_since_keep = writes_since_keep + 1")

    def writes_since_keep(self):
        return int(self._meta()["writes_since_keep"])

    def clear_writes_since_keep(self):
        with _Txn(self.conn):
            self.conn.execute("UPDATE track_meta SET writes_since_keep = 0")

    # -- intake -------------------------------------------------------------
    def set_intake(self, kind, body, source_path=None, source_sha256=None,
                   source_mtime_ns=None):
        """The job text, stored once. The external folder is read exactly here.

        `source_path` is provenance and is never re-opened, so a later edit on
        the other side cannot retroactively change what this track was built
        from.
        """
        self.assert_live()
        if kind not in C.SOURCE_KINDS:
            raise ValueError("unknown intake kind %r" % (kind,))
        raw = body.encode("utf-8")
        if len(raw) > C.INTAKE_MAX_BYTES:
            raise CapExceeded("intake", len(raw), C.INTAKE_MAX_BYTES)
        with self.lock, _Txn(self.conn):
            self.conn.execute(
                "INSERT OR REPLACE INTO intake"
                "(id,kind,source_path,source_sha256,source_mtime_ns,captured_utc,"
                " body,body_sha256,body_bytes) VALUES (1,?,?,?,?,?,?,?,?)",
                (kind, source_path, source_sha256, source_mtime_ns, utc_now(),
                 body, sha256_hex(raw), len(raw)))
            self._bump_writes()

    def intake(self):
        return self.conn.execute("SELECT * FROM intake WHERE id=1").fetchone()

    # -- gaps ---------------------------------------------------------------
    def add_gap(self, gap_id, ord_, label, why, level="none", jd_span=None):
        self.assert_live()
        with self.lock, _Txn(self.conn):
            self.conn.execute(
                "INSERT INTO gap(gap_id,ord,label,why,jd_span,level,status,proposed_utc)"
                " VALUES (?,?,?,?,?,?,'proposed',?)",
                (gap_id, int(ord_), label, why, jd_span, level, utc_now()))
            self._bump_writes()

    def set_gap_status(self, gap_id, status, expected_rev):
        """Optimistic locking, narrowly: only genuinely editable objects carry rev.

        Appending a turn bumps no rev, so chatting on one device never produces
        a spurious conflict against a gap list open on another.
        """
        self.assert_live()
        if status not in C.GAP_STATUSES:
            raise ValueError("unknown gap status %r" % (status,))
        with self.lock, _Txn(self.conn):
            cur = self.conn.execute(
                "UPDATE gap SET status=?, rev=rev+1, decided_utc=?"
                " WHERE gap_id=? AND rev=?",
                (status, utc_now(), gap_id, int(expected_rev)))
            if cur.rowcount == 0:
                row = self.conn.execute(
                    "SELECT rev,status FROM gap WHERE gap_id=?", (gap_id,)).fetchone()
                raise TrackMoved(
                    "gap %s is at rev %s (%s), not %s"
                    % (gap_id, row["rev"] if row else "?",
                       row["status"] if row else "missing", expected_rev))
            self._bump_writes()

    # -- curriculum ---------------------------------------------------------
    def add_step(self, step_id, ord_, title, objective, tier="core",
                 gap_id=None, status="ready", est_minutes=None):
        self.assert_live()
        with self.lock, _Txn(self.conn):
            n = self.conn.execute("SELECT COUNT(*) c FROM step").fetchone()["c"]
            if n + 1 > C.MAX_STEPS:
                raise CapExceeded("steps", n + 1, C.MAX_STEPS)
            self.conn.execute(
                "INSERT INTO step(step_id,ord,title,objective,gap_id,status,tier,est_minutes)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (step_id, int(ord_), title, objective, gap_id, status, tier,
                 est_minutes))
            self._bump_writes()

    def pin_slice(self, step_id, doc_id, sec_id, ord_):
        """The only corpus a step may carry or cite.

        The composite foreign key on (doc_id, sec_id) is the isolation teeth:
        foreign keys cannot cross database files, so a step is structurally
        incapable of naming a section that lives in another track.
        """
        self.assert_live()
        with self.lock, _Txn(self.conn):
            self.conn.execute(
                "INSERT OR REPLACE INTO step_slice(step_id,doc_id,sec_id,ord)"
                " VALUES (?,?,?,?)", (step_id, doc_id, sec_id, int(ord_)))
            self._bump_writes()

    # -- corpus -------------------------------------------------------------
    def corpus_path(self, name):
        """Absolute path for a corpus-relative name, or None if it escapes.

        realpath before the containment test, so "../../.ssh/id_rsa" arriving in
        a citation or a pasted message resolves to nothing. A symlink is refused
        rather than followed: a link is the one way a contained path still names
        bytes outside the corpus.
        """
        name = str(name or "").strip().lstrip("/")
        if not name or "\x00" in name or not _DOC_NAME.match(name):
            return None
        root = self.corpus_root
        joined = os.path.join(root, name)
        try:
            if os.path.islink(joined):
                return None
            full = os.path.realpath(joined)
            if full != root and not full.startswith(root + os.sep):
                return None
            if os.path.dirname(full) != root:
                return None
            if not os.path.isfile(full):
                return None
        except OSError:
            return None
        return full

    def write_doc(self, slug, title, sections, origin_url, origin_bytes,
                  origin_sha256, extract_sha256, vetting="primary", trust=5,
                  run_id=None, final_url=None):
        """Two-phase, with both crash windows named.

        txn A registers the row as 'writing' and allocates doc_no, so a doc_no
        is never reused. The file is then written and renamed atomically. txn B
        marks it ready and inserts the section rows with their byte offsets.
        A crash before txn B leaves a 'writing' row and possibly a file, both of
        which housekeep sweeps. There is no window that produces a
        half-registered document.
        """
        self.assert_live()
        if not sections:
            raise ValueError("a document with no sections teaches nothing")
        if len(sections) > C.MAX_SECTIONS_PER_DOC:
            raise CapExceeded("sections in one doc", len(sections),
                              C.MAX_SECTIONS_PER_DOC)
        for s in sections:
            if len(s["body"]) > C.SECTION_MAX_CHARS:
                raise CapExceeded("section %s" % s.get("sec_id"), len(s["body"]),
                                  C.SECTION_MAX_CHARS)
            if "\n" in s["heading"] or "\r" in s["heading"]:
                raise ValueError(
                    "section %s has a heading spanning two lines, which the"
                    " one-line marker cannot carry" % s.get("sec_id"))
            # The writer computes offsets and never parses, so a body carrying
            # the section delimiter at the start of a line writes and reads back
            # perfectly today, then loses every offset after it on the first
            # re-scan, which is what a restore triggers. Refuse it here rather
            # than quarantine a byte-identical document later. The delimiter
            # inside a line is harmless and stays.
            for line in s["body"].split("\n"):
                if line.startswith("§"):
                    raise ValueError(
                        "section %s has a line beginning with the section"
                        " delimiter, which this format cannot round-trip;"
                        " reflow the quote" % s.get("sec_id"))

        with self.lock, _Txn(self.conn):
            n_docs = self.conn.execute("SELECT COUNT(*) c FROM doc").fetchone()["c"]
            if n_docs + 1 > C.MAX_DOCS_PER_TRACK:
                raise CapExceeded("documents", n_docs + 1, C.MAX_DOCS_PER_TRACK)
            doc_no = int(self._meta()["next_doc_no"])
            doc_id = "D%02d" % doc_no
            file_name = "%s__%s.doc.md" % (doc_id, slug)
            if not _DOC_NAME.match(file_name):
                raise ValueError("slug produces an unusable file name: %r" % file_name)
            self.conn.execute("UPDATE track_meta SET next_doc_no = next_doc_no + 1")
            self.conn.execute(
                "INSERT INTO doc(doc_id,doc_no,run_id,status,file_name,title,"
                " origin_url,final_url,origin_sha256,origin_bytes,extract_sha256,"
                " fetched_utc,vetting,trust) "
                "VALUES (?,?,?, 'writing', ?,?,?,?,?,?,?,?,?,?)",
                (doc_id, doc_no, run_id, file_name, title, origin_url, final_url,
                 origin_sha256, int(origin_bytes), extract_sha256, utc_now(),
                 vetting, int(trust)))

        header = {
            "v": 1, "track_id": self.track_id, "doc_id": doc_id, "title": title,
            "n_sections": len(sections), "origin_url": origin_url,
            "final_url": final_url or origin_url, "origin_sha256": origin_sha256,
            "origin_bytes": int(origin_bytes), "extract_sha256": extract_sha256,
            "fetched": utc_now(), "vetting": vetting, "trust": int(trust),
        }
        body_text, offsets = _render_doc(header, sections)
        raw = body_text.encode("utf-8")
        if len(raw) > C.DOC_MAX_BYTES:
            raise CapExceeded("document %s" % doc_id, len(raw), C.DOC_MAX_BYTES)

        ensure_dir(self.corpus_root)
        target = os.path.join(self.corpus_root, file_name)
        atomic_write(target, raw)
        st = os.stat(target)

        with self.lock, _Txn(self.conn):
            self._charge("bytes_corpus", len(raw), C.CORPUS_BYTES_CAP, "corpus")
            self.conn.execute(
                "UPDATE doc SET status='ready', file_sha256=?, file_bytes=?,"
                " file_mtime_ns=?, n_sections=? WHERE doc_id=?",
                (sha256_hex(raw), len(raw), st.st_mtime_ns, len(sections), doc_id))
            for i, s in enumerate(sections):
                off, length = offsets[s["sec_id"]]
                self.conn.execute(
                    "INSERT INTO section(doc_id,sec_id,ord,heading,body_chars,"
                    " body_sha16,byte_off,byte_len,origin_span,concept)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, s["sec_id"], i, s["heading"], len(s["body"]),
                     sha16(s["body"]), off, length, s.get("origin_span", "")[:48],
                     s.get("concept")))
            self._bump_writes()
        return doc_id, file_name

    def rescan_doc(self, doc_id):
        """Re-derive one document's section offsets from the file itself.

        The offset hazard, handled the way DESIGN section E requires. A restore
        from an archive, a backup copy, or any other rewrite gives the file a
        new mtime while its bytes are identical, and refusing to serve it then
        would take a perfectly good corpus offline. So a mismatch triggers a
        re-scan and a delta update rather than a refusal.

        What is NOT tolerated is changed content: every section's body hash is
        checked against the row that was written with it, and a document whose
        text has moved under its own hashes is quarantined rather than taught
        from. A mis-sliced section is a wrong citation, which is the failure
        this whole design exists to prevent.
        """
        row = self.conn.execute(
            "SELECT file_name FROM doc WHERE doc_id=?", (doc_id,)).fetchone()
        if row is None:
            raise IsolationError("%s is not a document of this track" % doc_id)
        path = self.corpus_path(row["file_name"])
        if path is None:
            return False
        with open(path, "rb") as fh:
            raw = fh.read()
        found = _scan_doc_offsets(raw)
        known = self.conn.execute(
            "SELECT sec_id, body_sha16 FROM section WHERE doc_id=?",
            (doc_id,)).fetchall()
        for k in known:
            got = found.get(k["sec_id"])
            if got is None:
                self._quarantine_doc(doc_id, "section %s vanished" % k["sec_id"])
                return False
            body = raw[got[1]:got[1] + got[2]].decode("utf-8", "replace")
            if sha16(body) != k["body_sha16"]:
                self._quarantine_doc(
                    doc_id, "section %s no longer matches its hash" % k["sec_id"])
                return False
        st = os.stat(path)
        with self.lock, _Txn(self.conn):
            for k in known:
                heading, off, length = found[k["sec_id"]]
                self.conn.execute(
                    "UPDATE section SET byte_off=?, byte_len=?, heading=?"
                    " WHERE doc_id=? AND sec_id=?",
                    (off, length, heading, doc_id, k["sec_id"]))
            self.conn.execute(
                "UPDATE doc SET file_bytes=?, file_mtime_ns=?, file_sha256=?"
                " WHERE doc_id=?",
                (len(raw), st.st_mtime_ns, sha256_hex(raw), doc_id))
        return True

    def _quarantine_doc(self, doc_id, why):
        """Move the file aside and mark the row, so the steps that cite it are
        degraded rather than silently thinner than their curriculum assumed."""
        row = self.conn.execute(
            "SELECT file_name FROM doc WHERE doc_id=?", (doc_id,)).fetchone()
        path = self.corpus_path(row["file_name"]) if row else None
        if path:
            dest_dir = ensure_dir(os.path.join(self.dir, "quarantine"))
            os.replace(path, os.path.join(dest_dir, row["file_name"] + ".badhash"))
        with self.lock, _Txn(self.conn):
            self.conn.execute("UPDATE doc SET status='quarantined' WHERE doc_id=?",
                              (doc_id,))
            self.conn.execute(
                "UPDATE step SET evidence_state='degraded' WHERE step_id IN"
                " (SELECT step_id FROM step_slice WHERE doc_id=?)", (doc_id,))
            self.conn.execute(
                "INSERT INTO recovery_log(at_utc,kind,detail) VALUES (?,?,?)",
                (utc_now(), "doc_quarantined", "%s: %s" % (doc_id, why)))

    def verify_offsets(self):
        """Stat every ready document and re-scan any whose file has moved.

        Runs at open_track, before a single slice can be served, because a
        mis-sliced section reaches the candidate as a confident citation of
        something the source does not say.
        """
        rescanned = []
        for row in self.conn.execute(
                "SELECT doc_id, file_name, file_bytes, file_mtime_ns FROM doc"
                " WHERE status='ready'").fetchall():
            path = self.corpus_path(row["file_name"])
            if path is None:
                continue
            st = os.stat(path)
            if st.st_size != row["file_bytes"] or st.st_mtime_ns != row["file_mtime_ns"]:
                if self.rescan_doc(row["doc_id"]):
                    rescanned.append(row["doc_id"])
        return rescanned

    def read_section(self, doc_id, sec_id):
        """Seek and read the exact bytes, with the file checked before the seek.

        A citation token is per-track by construction, so `D01§s01` names this
        handle's own document and can never name another track's. A token this
        track does not have raises rather than resolving to something plausible.
        """
        row = self.conn.execute(
            "SELECT s.byte_off, s.byte_len, s.heading, s.body_sha16, d.file_name,"
            "       d.file_bytes, d.file_mtime_ns, d.status "
            "FROM section s JOIN doc d ON d.doc_id = s.doc_id "
            "WHERE s.doc_id=? AND s.sec_id=?", (doc_id, sec_id)).fetchone()
        if row is None:
            raise IsolationError("%s§%s is not a section of this track" % (doc_id, sec_id))
        if row["status"] != "ready":
            return None
        path = self.corpus_path(row["file_name"])
        if path is None:
            return None
        st = os.stat(path)
        if st.st_size != row["file_bytes"] or st.st_mtime_ns != row["file_mtime_ns"]:
            if not self.rescan_doc(doc_id):
                return None
            row = self.conn.execute(
                "SELECT s.byte_off, s.byte_len, s.heading, s.body_sha16, d.file_name"
                " FROM section s JOIN doc d ON d.doc_id = s.doc_id"
                " WHERE s.doc_id=? AND s.sec_id=?", (doc_id, sec_id)).fetchone()
        with open(path, "rb") as fh:
            fh.seek(row["byte_off"])
            body = fh.read(row["byte_len"]).decode("utf-8", "replace")
        if sha16(body) != row["body_sha16"]:
            raise CorruptStore("%s§%s failed its content hash" % (doc_id, sec_id))
        return {"doc_id": doc_id, "sec_id": sec_id, "heading": row["heading"],
                "body": body, "cite": "%s§%s" % (doc_id, sec_id)}

    def build_pack(self, step_id):
        """The bounded evidence pack for one turn, and the only path to corpus bytes.

        Takes a handle, never a track id. Every section is tagged with this
        handle's track_id at slice time, so a caller that mixes two tracks
        raises IsolationError instead of quietly producing a well-cited answer
        grounded in the wrong job.
        """
        rows = self.conn.execute(
            "SELECT doc_id, sec_id FROM step_slice WHERE step_id=? ORDER BY ord",
            (step_id,)).fetchall()
        out, total = [], 0
        for r in rows:
            sec = self.read_section(r["doc_id"], r["sec_id"])
            if sec is None:
                continue
            sec["track_id"] = self.track_id
            block = "===== %s :: %s =====\n%s" % (sec["cite"], sec["heading"], sec["body"])
            # Bytes, not characters. PACK_MAX_BYTES comes from a token budget,
            # and every block already carries a multi-byte citation delimiter,
            # so counting code points overruns the prompt on any corpus that is
            # not pure ASCII.
            block_bytes = len(block.encode("utf-8"))
            if (total + block_bytes > C.PACK_MAX_BYTES
                    or len(out) >= C.PACK_MAX_SECTIONS):
                break
            out.append(sec)
            total += block_bytes
        for sec in out:
            if sec["track_id"] != self.track_id:
                raise IsolationError("a pack section left its track")
        text = "\n\n".join(
            "===== %s :: %s =====\n%s" % (s["cite"], s["heading"], s["body"])
            for s in out)
        return {"track_id": self.track_id, "step_id": step_id, "sections": out,
                "text": text, "pack_sha16": sha16(text),
                "cites": [s["cite"] for s in out]}

    # -- transcript ---------------------------------------------------------
    def append_turn(self, step_id, role, body, client_turn_id,
                    reply_to_seq=None, pack_sha16=None, citations=None,
                    ungrounded=0, in_tok=None, out_tok=None, spool_ref=None,
                    auto_compact=True):
        """Append one turn. The sequence number is allocated here, by the server.

        A client never sends seq, and transcript order is by seq rather than by
        any clock, so a wrong system clock cannot reorder a lesson. A retried
        POST over a flaky link is a no-op rather than a duplicate, because
        client_turn_id is UNIQUE.

        At the transcript cap this compacts the oldest completed step and tries
        once more, rather than refusing. Freezing the track the candidate is
        studying the night before an interview is a worse failure than losing
        tutor prose from a step they finished three weeks ago and already have a
        review of. Pass auto_compact=False for the opposite trade, which is what
        a track flagged no_auto_compact gets, and the refusal then names the
        remedy.
        """
        try:
            return self._append_turn(
                step_id, role, body, client_turn_id, reply_to_seq, pack_sha16,
                citations, ungrounded, in_tok, out_tok, spool_ref)
        except CapExceeded as refused:
            if not auto_compact or refused.what != "transcript":
                raise
            freed = self.compact_oldest_completed_step()
            if not freed:
                raise
            return self._append_turn(
                step_id, role, body, client_turn_id, reply_to_seq, pack_sha16,
                citations, ungrounded, in_tok, out_tok, spool_ref)

    def compact_oldest_completed_step(self):
        """Free transcript bytes from the oldest step that is safely compactable.

        Safely means: the step is done, it carries the review that holds its
        conclusion, and only the tutor's own prose is emptied. A student turn is
        never touched, by this or by any other path, and each compaction leaves
        a receipt carrying the original length and hash.
        """
        row = self.conn.execute(
            "SELECT step_id FROM step WHERE status='done' AND review IS NOT NULL"
            "  AND review <> '' AND compacted = 0 ORDER BY ord LIMIT 1").fetchone()
        if row is None:
            return 0
        seqs = self.conn.execute(
            "SELECT seq FROM turn WHERE step_id=? AND role='tutor' AND body <> ''",
            (row["step_id"],)).fetchall()
        freed = 0
        for s in seqs:
            try:
                freed += self.compact_turn(int(s["seq"]), reason="cap")
            except StoreError:
                pass
        if freed:
            with self.lock, _Txn(self.conn):
                self.conn.execute("UPDATE step SET compacted=1 WHERE step_id=?",
                                  (row["step_id"],))
                self.conn.execute(
                    "INSERT INTO recovery_log(at_utc,kind,detail) VALUES (?,?,?)",
                    (utc_now(), "compacted_at_cap",
                     "the transcript reached its cap, so the tutor's prose in step"
                     " %s was compacted to make room. Your own words, its score and"
                     " its review are untouched, and every compaction kept a receipt"
                     " with the original length and hash." % row["step_id"]))
        return freed

    def _append_turn(self, step_id, role, body, client_turn_id,
                     reply_to_seq=None, pack_sha16=None, citations=None,
                     ungrounded=0, in_tok=None, out_tok=None, spool_ref=None):
        self.assert_live()
        if role not in C.TURN_ROLES:
            raise ValueError("unknown turn role %r" % (role,))
        raw = body.encode("utf-8")
        original_bytes = len(raw)
        overflow_bytes, overflow_sha = 0, None
        if len(raw) > C.TURN_MAX_BYTES:
            # Room for the marker is reserved BEFORE the cut. Appending it
            # afterwards and re-slicing to the cap chops the marker back off,
            # which stores a silently truncated turn: the candidate reads a
            # sentence that stops mid-thought with nothing saying why.
            overflow_sha = sha256_hex(raw)
            marker = "\n[truncated at the turn cap]"
            room = max(0, C.TURN_MAX_BYTES - len(marker.encode("utf-8")))
            cut, kept = raw[:room], ""
            while cut:                       # never split a UTF-8 code point
                try:
                    kept = cut.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    cut = cut[:-1]
            body = kept + marker
            raw = body.encode("utf-8")
            overflow_bytes = original_bytes - len(raw)

        with self.lock, _Txn(self.conn):
            dup = self.conn.execute(
                "SELECT seq FROM turn WHERE client_turn_id=?",
                (client_turn_id,)).fetchone()
            if dup is not None:
                return int(dup["seq"])
            n = self.conn.execute(
                "SELECT COUNT(*) c FROM turn WHERE step_id=?", (step_id,)).fetchone()["c"]
            if n + 1 > C.MAX_TURNS_PER_STEP:
                raise CapExceeded("turns in step %s" % step_id, n + 1,
                                  C.MAX_TURNS_PER_STEP)
            self._charge("bytes_turns", len(raw) + 150, C.TURNS_BYTES_CAP, "transcript")
            cur = self.conn.execute(
                "INSERT INTO turn(step_id,at_utc,role,reply_to_seq,body,body_bytes,"
                " body_sha16,overflow_bytes,overflow_sha256,spool_ref,pack_sha16,"
                " citations,ungrounded,client_turn_id,in_tok,out_tok)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (step_id, utc_now(), role, reply_to_seq, body, len(raw), sha16(body),
                 overflow_bytes, overflow_sha, spool_ref, pack_sha16,
                 json.dumps(citations or []), int(ungrounded), client_turn_id,
                 in_tok, out_tok))
            self._bump_writes()
            return int(cur.lastrowid)

    def tail(self, step_id, max_bytes=None):
        """Newest-first by seq until the next turn would exceed the budget.

        Capped in bytes rather than in turns, so one long reply cannot blow a
        budget that was derived from an assumed average. At least one turn is
        always included.
        """
        max_bytes = C.HISTORY_MAX_BYTES if max_bytes is None else max_bytes
        rows = self.conn.execute(
            "SELECT seq, role, body, body_bytes FROM turn WHERE step_id=?"
            " ORDER BY seq DESC", (step_id,)).fetchall()
        out, total = [], 0
        for r in rows:
            if out and total + r["body_bytes"] > max_bytes:
                break
            out.append({"seq": r["seq"], "role": r["role"], "body": r["body"]})
            total += r["body_bytes"]
        out.reverse()
        return out

    def compact_turn(self, seq, reason="cap"):
        """The first lossy rung, and it writes its receipt before it acts.

        A student turn cannot be emptied by any path. A tutor turn cannot be
        emptied until turn_compaction records its original length and hash, and
        the trigger enforces that rather than trusting this function.
        """
        with self.lock, _Txn(self.conn):
            row = self.conn.execute(
                "SELECT seq, role, body, body_bytes, body_sha16, step_id FROM turn"
                " WHERE seq=?", (int(seq),)).fetchone()
            if row is None:
                raise ValueError("no turn %s" % seq)
            if row["role"] != "tutor":
                raise StoreError("only a tutor turn may be compacted")
            step = self.conn.execute(
                "SELECT review FROM step WHERE step_id=?", (row["step_id"],)).fetchone()
            has_review = 1 if (step and step["review"]) else 0
            if not has_review:
                raise StoreError(
                    "step %s has no review, so compacting its prose would lose the"
                    " conclusion as well as the words" % row["step_id"])
            self.conn.execute(
                "INSERT INTO turn_compaction(turn_seq,at_utc,reason,orig_bytes,"
                " orig_sha16,step_review_present) VALUES (?,?,?,?,?,?)",
                (row["seq"], utc_now(), reason, row["body_bytes"],
                 row["body_sha16"], has_review))
            # body_bytes and body_sha16 must describe the live body, or tail()
            # keeps charging its budget for text that is gone and the row's own
            # integrity field permanently disagrees with it. The original length
            # and hash are already safe in turn_compaction.
            self.conn.execute(
                "UPDATE turn SET body='', body_bytes=0, body_sha16=? WHERE seq=?",
                (sha16(""), row["seq"]))
            self._charge("bytes_turns", -int(row["body_bytes"]),
                         C.TURNS_BYTES_CAP, "transcript")
            return int(row["body_bytes"])

    # -- assessment and cards ------------------------------------------------
    def add_assessment(self, step_id, score, rubric, evidence_turn_seq=None,
                       misconception=None):
        self.assert_live()
        with self.lock, _Txn(self.conn):
            self.conn.execute(
                "INSERT INTO assessment(at_utc,step_id,score,rubric,"
                " evidence_turn_seq,misconception) VALUES (?,?,?,?,?,?)",
                (utc_now(), step_id, float(score), rubric, evidence_turn_seq,
                 misconception))
            self._bump_writes()

    def add_card(self, card_id, front, back, cite, cite_snippet, step_id=None,
                 due_utc=None):
        """A card cites a section that exists, and freezes the cited text.

        The frozen snippet is what makes a card reviewable after its track is
        archived, without unpacking the archive and without a shared content
        table that would cross the isolation boundary.
        """
        self.assert_live()
        doc_id, _, sec_id = str(cite).partition("§")
        exists = self.conn.execute(
            "SELECT 1 FROM section WHERE doc_id=? AND sec_id=?",
            (doc_id, sec_id)).fetchone()
        if exists is None:
            raise IsolationError("card cites %s, which is not a section here" % cite)
        with self.lock, _Txn(self.conn):
            n = self.conn.execute("SELECT COUNT(*) c FROM card").fetchone()["c"]
            if n + 1 > C.MAX_CARDS:
                raise CapExceeded("cards", n + 1, C.MAX_CARDS)
            size = len(front.encode()) + len(back.encode()) + 120
            self._charge("bytes_cards", size, C.CARDS_BYTES_CAP, "cards")
            self.conn.execute(
                "INSERT INTO card(card_id,step_id,front,back,cite,cite_snippet,"
                " created_utc,due_utc) VALUES (?,?,?,?,?,?,?,?)",
                (card_id, step_id, front, back, cite,
                 cite_snippet[:C.CARD_SNIPPET_MAX], utc_now(),
                 due_utc or utc_now()))
            self._bump_writes()
        self._mirror_due_card(card_id, due_utc or utc_now())

    def review_card(self, card_id, grade, clock_suspect=False):
        self.assert_live()
        with self.lock, _Txn(self.conn):
            row = self.conn.execute(
                "SELECT interval_days, ease, reps, lapses FROM card WHERE card_id=?",
                (card_id,)).fetchone()
            if row is None:
                raise ValueError("no card %s" % card_id)
            prev = float(row["interval_days"])
            nxt = prev if clock_suspect else (
                1.0 if grade <= 1 else max(1.0, prev * float(row["ease"])))
            self.conn.execute(
                "INSERT INTO card_review(card_id,at_utc,at_mono,grade,prev_interval,"
                " next_interval,clock_suspect) VALUES (?,?,?,?,?,?,?)",
                (card_id, utc_now(), mono_ns(), int(grade), prev, nxt,
                 1 if clock_suspect else 0))
            if not clock_suspect:
                # due_utc is the column the whole schedule is keyed on. Moving
                # interval_days without it leaves every card due immediately and
                # forever, which is a pile of flashcards rather than spaced
                # repetition.
                due = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                    time.gmtime(time.time() + nxt * 86400.0))
                self.conn.execute(
                    "UPDATE card SET interval_days=?, due_utc=?, reps=reps+1,"
                    " lapses=lapses+?, last_review_utc=? WHERE card_id=?",
                    (nxt, due, 1 if grade <= 1 else 0, utc_now(), card_id))
                self._mirror_due_card(card_id, due)
            self._bump_writes()
            return nxt

    def _mirror_due_card(self, card_id, due_utc, retired=0):
        """Copy one card's schedule into the registry projection.

        due_card holds a schedule and no content, which is what lets "what is
        due today" be one indexed query across every track without any track's
        text leaving its own file. Nothing wrote it before, so the projection
        stayed empty and an archived track's cards.jsonl came out zero-byte,
        taking the whole point of reviewing a cold track with it.
        """
        try:
            self._library().execute(
                "INSERT OR REPLACE INTO due_card(track_id,card_id,due_utc,retired,cold)"
                " VALUES (?,?,?,?,0)",
                (self.track_id, card_id, due_utc, int(retired)))
        except sqlite3.Error:
            pass        # a stale projection rebuilds; a lost grade does not


_MARKER = "§".encode("utf-8")           # the section marker, as bytes


def _scan_doc_offsets(raw):
    """Byte offsets of every section body, read back out of the file itself.

    Works on bytes rather than on decoded text, because an offset into a
    decoded string is not an offset into the file, and the read path seeks.
    """
    out, lines, pos = {}, [], 0
    while pos < len(raw):
        nl = raw.find(b"\n", pos)
        if nl == -1:
            lines.append((pos, len(raw)))
            break
        lines.append((pos, nl + 1))
        pos = nl + 1
    marks = []
    for start, end in lines:
        line = raw[start:end]
        if not line.startswith(_MARKER):
            continue
        text = line[len(_MARKER):].rstrip(b"\n").decode("utf-8", "replace")
        if text == "spans":
            marks.append(("spans", start, end))
            break
        sec_id, _, heading = text.partition("|")
        if _SEC_ID.match(sec_id):
            marks.append((sec_id, start, end, heading))
    for i, m in enumerate(marks):
        if m[0] == "spans":
            continue
        sec_id, _start, body_start, heading = m
        next_start = marks[i + 1][1] if i + 1 < len(marks) else len(raw)
        length = max(0, next_start - body_start - 2)   # the "\n\n" separator
        out[sec_id] = (heading, body_start, length)
    return out


def rotate_backups(backups, keep):
    """At most `keep` copies, and never fewer than one while any exist."""
    names = sorted(n for n in os.listdir(backups) if n.endswith(".track.db"))
    total = sum(os.path.getsize(os.path.join(backups, n)) for n in names)
    while len(names) > max(1, keep) or (len(names) > 1 and total > C.BACKUP_BYTES_CAP):
        oldest = names.pop(0)
        p = os.path.join(backups, oldest)
        total -= os.path.getsize(p)
        os.remove(p)


def _render_doc(header, sections):
    """One header line, then §<sec_id>|<key> blocks, then the spans block.

    Returns the text and, for each section, the byte offset and length of its
    body, so a read is a seek and a read with no parse and no decode of
    anything the reader does not need.
    """
    head = json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "\n"
    parts = [head]
    cursor = len(head.encode("utf-8"))
    offsets = {}
    for s in sections:
        if not _SEC_ID.match(s["sec_id"]):
            raise ValueError("section id %r is not sNN" % (s["sec_id"],))
        marker = "§%s|%s\n" % (s["sec_id"], s["heading"])
        parts.append(marker)
        cursor += len(marker.encode("utf-8"))
        body = s["body"]
        blen = len(body.encode("utf-8"))
        offsets[s["sec_id"]] = (cursor, blen)
        parts.append(body + "\n\n")
        cursor += len(((body + "\n\n")).encode("utf-8"))
    parts.append("§spans\n")
    for s in sections:
        parts.append("%s|%s\n" % (s["sec_id"], s.get("origin_span", "")[:48]))
    return "".join(parts), offsets


def open_track(track_id, lib=None, client_label="laptop", take_lease=True,
               touch=True):
    """The one gate. Nothing else in this package calls sqlite3.connect for a track.

    It matches the id pattern, resolves the directory with realpath and asserts
    containment, three-way checks the directory name against the TRACK_ID file
    against track_meta.track_id, records the generation, and returns a handle
    owning exactly one connection and one corpus root. There is no fallback
    path, because a fallback is how the wrong corpus reaches the right prompt.
    """
    directory = track_dir(track_id)
    if not os.path.isdir(directory):
        raise IsolationError("track %s has no directory" % track_id)

    marker = os.path.join(directory, "TRACK_ID")
    _no_symlink(marker)
    with open(marker, "r", encoding="utf-8") as fh:
        stamped = fh.read().strip()
    if stamped != track_id:
        raise IsolationError(
            "directory %s carries TRACK_ID %r" % (track_id, stamped))

    db = os.path.join(directory, "track.db")
    _no_symlink(db)
    conn = connect(db)
    if not quick_check(conn):
        conn.close()
        raise CorruptStore("track.db for %s failed quick_check" % track_id)
    conn.executescript(TRACK_DDL)
    row = conn.execute("SELECT track_id FROM track_meta").fetchone()
    if row is None or row["track_id"] != track_id:
        conn.close()
        raise IsolationError(
            "track.db in %s says it belongs to %r"
            % (track_id, row["track_id"] if row else None))

    own_lib = lib is None
    library = lib or open_library()
    try:
        trow = library.execute(
            "SELECT generation, lifecycle FROM track WHERE track_id=?",
            (track_id,)).fetchone()
        if trow is None:
            conn.close()
            raise IsolationError("track %s is not in the registry" % track_id)
        generation = int(trow["generation"])
        handle = TrackHandle(track_id, conn, directory, generation,
                             holds_lease=bool(take_lease))
        try:
            # Before a single slice can be served: any document whose file has
            # moved under its recorded offsets is re-scanned, and one whose text
            # no longer matches its hashes is quarantined.
            handle.verify_offsets()
            if take_lease:
                _take_lease(library, track_id, generation, client_label)
        except BaseException:
            # Refusing a lease another client holds is an ordinary outcome, not
            # a bug, and a half-built handle must not survive it. A probe showed
            # CPython already collects these connections; this is explicit now
            # that the handle owns two of them.
            handle.holds_lease = False
            handle.close()
            raise
        if touch:
            # opened_utc is the only input to staleness, so a housekeeping sweep
            # opens with touch=False. Stamping it here made every track look
            # freshly opened after any housekeep, and the age-driven half of the
            # ladder could never fire again.
            library.execute("UPDATE track SET opened_utc=? WHERE track_id=?",
                            (utc_now(), track_id))
    finally:
        if own_lib:
            library.close()
    return handle


# ---- leases ----------------------------------------------------------------
def _boot_id():
    """Something that changes when the machine reboots, so a recycled pid is not
    mistaken for a live holder. Boot time is enough and needs no privileges."""
    try:
        import subprocess
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"],
                             capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return "unknown-boot"


def lease_is_live(row, now_mono=None):
    """Is some process still holding this track?

    Three tests, and all three earn their place.

    The boot id stops a pid recycled across a reboot from impersonating the
    holder. The process probe stops a lease outliving the bridge that took it.
    The recorded heartbeat stops a pid recycled inside one boot from holding a
    track forever: without it, killing the bridge and letting the OS hand its
    pid to any same-user process leaves that track permanently unopenable,
    unarchivable and undeletable, with reconcile refusing to reclaim it.

    An earlier version expired a lease LEASE_STALE_SECONDS after a monotonic
    stamp nothing refreshed, so a candidate working for ten minutes silently
    lost the lease on their own open track and archive and delete stopped
    refusing. A monotonic stamp is also not comparable across processes, so it
    could never have answered this for the case that matters. The wall-clock
    heartbeat is refreshed by every write through assert_live, so it cannot
    expire under someone who is working. It does expire on a track left open
    and idle, because no timed heartbeat is sent yet: that belongs to the
    serving loop, and heartbeat() below is the call it will make.

    ProcessLookupError means the pid is gone. PermissionError means a process
    with that pid exists under another user, so it is not one of this
    candidate's bridges. Both mean "not a live holder", and reclaiming is safe
    because it reclaims the lease and not the data: a handle taken under the
    old generation is refused by assert_live on its next write.
    """
    if row is None:
        return False
    if row["holder_boot"] != _boot_id():
        return False
    if row["holder_pid"] != os.getpid():
        try:
            os.kill(int(row["holder_pid"]), 0)
        except OSError:
            return False
    try:
        beat = calendar.timegm(
            time.strptime(row["heartbeat_utc"], "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return False
    return (time.time() - beat) < C.LEASE_STALE_SECONDS


def _take_lease(library, track_id, generation, client_label):
    # Check and claim together. Apart they autocommit separately, and two
    # bridge processes launched at once each run the SELECT, each see no live
    # holder, each run the INSERT, and both believe they hold the track. The
    # per-track threading.Lock does not help: it serialises threads inside one
    # process, which is not the case that fails.
    with _Txn(library):
        row = library.execute("SELECT * FROM track_lease WHERE track_id=?",
                              (track_id,)).fetchone()
        if row is not None and lease_is_live(row) and row["holder_pid"] != os.getpid():
            raise TrackMoved(
                "track %s is open on %s (pid %s)"
                % (track_id, row["client_label"], row["holder_pid"]))
        library.execute(
            "INSERT OR REPLACE INTO track_lease(track_id,holder_pid,holder_boot,"
            " client_label,generation,heartbeat_utc,heartbeat_mono)"
            " VALUES (?,?,?,?,?,?,?)",
            (track_id, os.getpid(), _boot_id(), client_label, generation,
             utc_now(), mono_ns()))


def heartbeat(library, track_id):
    library.execute(
        "UPDATE track_lease SET heartbeat_utc=?, heartbeat_mono=? WHERE track_id=?",
        (utc_now(), mono_ns(), track_id))


def release_lease(library, track_id):
    library.execute("DELETE FROM track_lease WHERE track_id=?", (track_id,))


def live_lease(library, track_id):
    row = library.execute("SELECT * FROM track_lease WHERE track_id=?",
                          (track_id,)).fetchone()
    return row if lease_is_live(row) else None
