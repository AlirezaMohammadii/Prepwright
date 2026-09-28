"""Identity, paths, and every cap in one place.

Nothing else in the package may hardcode a byte budget, a path, or a limit,
with one recorded exception: the CHECK constraints in state.py's DDL carry SQL
literals for INTAKE_MAX_BYTES, DOC_MAX_BYTES, SECTION_MAX_CHARS, TURN_MAX_BYTES
and MARK_MAX_BYTES, because SQLite will not take a parameter in a CHECK. Raising
one of those numbers here without editing the matching CHECK turns a refusal that
names the cap into an IntegrityError that names nothing.

A cap that lives at its use site is a cap nobody can audit.

Every number below comes from DESIGN-state-corpus.md section D, where the
arithmetic behind it is worked out rather than guessed.
"""

import os

APP_ID = "prepwright"

# ---- layout ----------------------------------------------------------------
# Storage lives outside the repository, so `git clean` cannot take a track with
# it and a corpus never lands in a diff. PREPWRIGHT_HOME exists for tests and
# for anyone whose home directory sits on a synced volume. The bridge runs
# under `python3 -I`, which implies -E, and -E ignores PYTHON* variables only,
# so this one still reaches the process.
HOME = os.path.realpath(
    os.environ.get("PREPWRIGHT_HOME") or os.path.expanduser("~/.prepwright"))

LAYOUT_GENERATION = "1"          # contents of HOME/VERSION, read before any open

# Resume Studio files each finished application into a folder under here, and its
# Prep button deep-links one of them (?application=<folder>). A folder named by a
# URL is untrusted input naming a local path, so intake reads only folders whose
# real path sits under this root. PREPWRIGHT_APPLICATIONS moves it, for tests and
# for anyone who keeps the applications elsewhere.
APPLICATIONS_ROOT = os.path.realpath(
    os.environ.get("PREPWRIGHT_APPLICATIONS")
    or os.path.expanduser("~/Desktop/Thesis/Job Applications/applications"))

VERSION_FILE = os.path.join(HOME, "VERSION")
LIBRARY_DB = os.path.join(HOME, "library.db")
LIBRARY_LOCK = os.path.join(HOME, "library.lock")
LIBRARY_BAK = os.path.join(HOME, "library.bak")
TRACKS_ROOT = os.path.join(HOME, "tracks")
ARCHIVE_ROOT = os.path.join(HOME, "archive")
TRASH_ROOT = os.path.join(HOME, "trash")
QUARANTINE_ROOT = os.path.join(HOME, "quarantine")

DIR_MODE = 0o700
FILE_MODE = 0o600

# A track directory name IS its id, and this pattern is the first gate every
# path resolution passes, before anything reaches os.path.join.
#
# \Z, not $. In Python `$` also matches immediately before a trailing
# newline, so `^t-[0-9a-f]{12}$` accepts "t-93c97fdd6d77\n" as a valid
# track id and `DOC_NAME_RE` accepts "D01__x.doc.md\n" as a valid file
# name. Both then reach os.path.join carrying a newline. Verified on 3.9.6
# and 3.14 before this was changed. Same defect this project already fixed
# once in pagestate.STEP_KEY_RE.
TRACK_ID_RE = r"^t-[0-9a-f]{12}\Z"
DOC_NAME_RE = r"^D[0-9]{2}__[a-z0-9][a-z0-9-]{0,60}\.doc\.md\Z"

# ---- schema ----------------------------------------------------------------
LIBRARY_SCHEMA_VERSION = 1
TRACK_SCHEMA_VERSION = 1

# page_size and auto_vacuum take effect only before the first CREATE TABLE, so
# they are applied to a fresh file and merely restated on an existing one.
PRAGMAS_PRE_CREATE = (
    "PRAGMA page_size = 4096",
    "PRAGMA auto_vacuum = INCREMENTAL",
)
PRAGMAS_EVERY_OPEN = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = FULL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA wal_autocheckpoint = 256",   # 256 * 4096 = 1 MiB
)

# ---- per-object caps (hard refuse at the write) ----------------------------
DOC_MAX_BYTES = 12_288
SECTION_MAX_CHARS = 900
MAX_SECTIONS_PER_DOC = 10
MAX_DOCS_PER_TRACK = 48
TURN_MAX_BYTES = 8_192
MARK_MAX_BYTES = 4_096
TURN_META_MAX_BYTES = 4_096
MAX_TURNS_PER_STEP = 150
MAX_STEPS = 40
MAX_CARDS = 300
INTAKE_MAX_BYTES = 65_536
CARD_SNIPPET_MAX = 240
STEP_REVIEW_MAX = 900
SPOOL_CAP = 1_048_576
EVENT_ROWS = 5_000

# ---- per-track byte caps ---------------------------------------------------
CORPUS_BYTES_CAP = 655_360        # 640 KiB, 64 KiB above the worst legal case
TURNS_BYTES_CAP = 3_145_728       # 3 MiB, about 3x a typical worked track
CARDS_BYTES_CAP = 327_680         # 320 KiB
# Page marks: ticked topics, practice statuses, banked questions, check results.
# Measured, not guessed: a ticked topic charges 144 bytes, so 256 KiB is about
# 1,800 of those, or about 800 changes carrying a banked question. A track works
# through a few hundred. Summed with the three caps above this leaves 320 KiB
# spare inside TRACK_DB_CAP. There is no compaction rung for marks yet, so this
# cap refuses rather than reclaims: see ADR 0003.
MARKS_BYTES_CAP = 262_144         # 256 KiB
TRACK_DB_CAP = 4_718_592          # 4.5 MiB
BACKUP_BYTES_CAP = 5_242_880      # per track, at most 2 copies

# ---- library caps ----------------------------------------------------------
LIBRARY_SOFT = 201_326_592        # 192 MiB: notices and pressure archiving
LIBRARY_HARD = 402_653_184        # 384 MiB: research refused, teaching still works
LIBRARY_HYSTERESIS = 16_777_216   # stop pressure archiving this far below SOFT
TRASH_CAP = 33_554_432
QUARANTINE_CAP = 16_777_216
QUARANTINE_AGE_DAYS = 90

# ---- prompt budget ---------------------------------------------------------
PACK_MAX_BYTES = 12_000
PACK_MAX_SECTIONS = 10
# What one pack block costs beyond its heading and body: the citation token, the
# two delimiter lines, the publisher and the date, and the blank line joining it
# to the next. Measured against `state._pack_block` at 128 bytes with a short
# publisher; 192 leaves room for a long one. It exists so `curriculum.relevant`
# can stop pinning at the same budget `build_pack` stops reading at, instead of
# pinning ten sections on the assumption that ten times SECTION_MAX_CHARS fits
# -- which is true for ASCII and false for any source with curly quotes.
PACK_BLOCK_OVERHEAD = 192
HISTORY_MAX_BYTES = 12_000

# ---- ages that drive the ladder --------------------------------------------
# Published, because an automatic behaviour nobody was told about is
# indistinguishable from data loss on the day it fires.
STALE_AFTER_DAYS = 21
ARCHIVE_PROPOSAL_DAYS = 60
PRESSURE_ARCHIVE_DAYS = 120
TRASH_UNDO_DAYS = 30
SPOOL_AGE_DAYS = 7
PART_FILE_AGE_SECONDS = 3600
LEASE_STALE_SECONDS = 120
LEASE_HEARTBEAT_SECONDS = 30
BACKUP_MIN_INTERVAL_SECONDS = 1200        # at most one per 20 minutes while dirty
BACKUPS_LEASED = 2
BACKUPS_ACTIVE = 1
BACKUPS_STALE = 0
HOUSEKEEP_EVERY_WRITES = 200

# A clock that has gone backwards, or jumped this far forward while the
# monotonic clock says minutes passed, suspends every age-driven behaviour.
CLOCK_JUMP_SUSPECT_DAYS = 7

# ---- where the bridge listens ---------------------------------------------
# Here rather than in bridge.py because `prepwright/security.py` computes its
# origin and host allowlists from the port at import time, and a module in the
# package cannot import them back out of the script that starts it. This was
# the stated blocker on splitting the request boundary out of bridge.py.
HOST = "127.0.0.1"
PORT = int(os.environ.get("PREPWRIGHT_PORT", "8010"))

# One provider reply. Long because a schema-constrained call spends two turns
# and the second one is the structured emit.
REQUEST_TIMEOUT = 180
# What one HTTP request body may carry, and what one page delta may carry. The
# second is a delta of appended ops, never a document; see pagestate.py.
MAX_REQUEST_BYTES = 256 * 1024
MAX_STATE_BYTES = 4 * 1024 * 1024

# ---- the installation, as opposed to the store -----------------------------
# The directory bridge.py sits in. Derived from this file's own location rather
# than from the entry point, so a module can find the page, the seed corpus and
# the legacy state file without importing the script that starts the server.
# bridge.py computes the same path a second time, before any import can happen,
# because it needs it to put this package on sys.path at all; the two are
# asserted equal in tests/test_extraction_seams.py rather than assumed equal.
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The development seed corpus. NOT the runtime store: a track's corpus lives
# inside that track, under HOME/tracks/<id>/corpus, and every teaching byte is
# read through one TrackHandle. This directory is ingested into a track once, by
# corpus.seed_from_directory, so a track created before the research pipeline
# exists still has something real to teach from.
SEED_CORPUS_DIR = os.path.join(SCRIPT_DIR, "corpus")
# Read once, imported, and renamed. There were two stores here until ADR 0002:
# progress/state.json, a whole document written on every keystroke behind a
# revision hash and a heuristic shrink-detector. Both stores describing the same
# thing was the single biggest defect in that tree.
LEGACY_STATE_DIR = os.path.join(SCRIPT_DIR, "progress")
LEGACY_STATE_FILE = os.path.join(LEGACY_STATE_DIR, "state.json")

LIFECYCLES = ("active", "archiving", "archived", "restoring", "trashed", "lost")
OUTCOMES = ("open", "interviewing", "offer", "rejected", "withdrawn")
SOURCE_KINDS = ("pasted", "imported", "freeform")
TRACK_KINDS = ("job", "topic")
TURN_ROLES = ("user", "tutor", "system")
STEP_STATUSES = ("locked", "ready", "open", "done", "skipped")
GAP_STATUSES = ("proposed", "approved", "edited", "declined", "covered")
DOC_STATUSES = ("writing", "ready", "quarantined", "origin_drift", "unverifiable")

# ---- rehearsal (ADR 0008) ---------------------------------------------------
# Teaching makes a concept explainable; the room asks the candidate to defend a
# line of the resume, answer an objection, or tell a story. A rehearsal step is a
# topic step whose id is R<nn>, planned after the study steps, asked by the
# panel and graded against a stated rubric. It is delivered by a graded answer
# at REHEARSAL_PASS or above, never by a tick, so `prepared` needs performance.
REHEARSAL_STEP_RE = r"^\d{1,2}:topic:R\d{2}\Z"
REHEARSAL_RUBRIC = "rehearsal:"      # the assessment.rubric prefix of a rehearsal grade
REHEARSAL_PASS = 0.75                # 6 of 8 rubric points
REHEARSAL_MAX = 8                    # steps planned per track
REHEARSAL_SPOKEN_WORDS = 250         # about two minutes out loud
# Application logistics and eligibility are not interview questions, and a
# posting that repeats them is not stressing a skill. The fit report for the
# University of Example role carries "Submit resume, cover letter, and
# selection-criteria responses" as a requirement row, and the walk asked the
# candidate to defend his work rights with "what did you build". Read by
# rehearse (no question) and curriculum.emphasis (no promotion).
PROCESS_PHRASES = ("submit", "upload", "cover letter", "selection criteria",
                   "selection-criteria", "work rights", "sponsorship", "visa",
                   "how to apply", "application form", "right to work", "police check",
                   "working with children")
RESUME_EVIDENCE_SUFFIX = "_ResumeEvidence.md"

# A track in one of these outcomes is never touched by an automatic transition,
# whatever the pressure. Neither is a pinned one.
PROTECTED_OUTCOMES = ("interviewing", "offer")
