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
TRACK_ID_RE = r"^t-[0-9a-f]{12}$"
DOC_NAME_RE = r"^D[0-9]{2}__[a-z0-9][a-z0-9-]{0,60}\.doc\.md$"

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

LIFECYCLES = ("active", "archiving", "archived", "restoring", "trashed", "lost")
OUTCOMES = ("open", "interviewing", "offer", "rejected", "withdrawn")
PHASES = ("intake", "diagnostic", "gaps", "research", "curriculum",
          "teaching", "review")
SOURCE_KINDS = ("pasted", "imported", "freeform")
TRACK_KINDS = ("job", "topic")
TURN_ROLES = ("user", "tutor", "system")
STEP_STATUSES = ("locked", "ready", "open", "done", "skipped")
GAP_STATUSES = ("proposed", "approved", "edited", "declined", "covered")
DOC_STATUSES = ("writing", "ready", "quarantined", "origin_drift", "unverifiable")

# A track in one of these outcomes is never touched by an automatic transition,
# whatever the pressure. Neither is a pinned one.
PROTECTED_OUTCOMES = ("interviewing", "offer")
