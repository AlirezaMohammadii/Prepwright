# PREPWRIGHT PERSISTENCE LAYER, FINAL DESIGN

Five principles carry this design. Classify bytes by whether losing them loses a human decision, and protect only the irreplaceable class. Store the corpus once, already in prompt form, with a contentless full-text index over it. Accept only delta writes, with append-only enforced by triggers rather than by application code. Keep one database file per track, so slice isolation rests on a composite foreign key that cannot cross files. Allocate sequence numbers on the server and derive staleness rather than storing it.

## CONFLICT RESOLUTIONS (one line each)

1. Whole-document `state.json` with a revision hash versus delta-only rows: delta-only rows win, because a revision hash proves the client read the file and proves nothing about what the client is carrying, while an append cannot express the loss at all.
2. Whole-library backups versus per-track backups: per-track wins, because the delta being protected is one hot track per day and a library-wide copy scales its cost along the one axis the candidate asked to bound.
3. Shared content-addressed blob store versus per-track duplication: per-track wins, because dedupe saves single-digit megabytes and buys a refcount table, a collector, and a live pointer from one track's prompt into another track's bytes.
4. Pressure-triggered reclamation versus age-triggered: age-triggered wins, because at this library's real scale a pressure threshold never fires and the library is bounded only by the user's diligence.
5. Store raw fetched sources versus store verbatim source spans: spans win, because raw retention is 10x to 40x the entire per-track budget for PDFs and 560 bytes of quoted span falsify a citation years later just as well.
6. Refuse writes at a cap versus auto-compact at a cap: auto-compact wins for tutor prose in completed steps, because refusing writes freezes the one track that matters on the eve of the interview.

---

## A. ON-DISK LAYOUT

```
~/.prepwright/                                 0700, lstat-checked, never a symlink
├── VERSION                                    "1\n"  layout generation, read before any open
├── config.json                                caps, notice thresholds, cadences (~1.5 KB)
├── library.db                                 registry + due projection + events. page_size 4096
├── library.db-wal
├── library.db-shm
├── library.lock                               fcntl.flock target, cross-process guard
├── library.bak/
│   ├── 000117.library.db                      VACUUM INTO copy, named by monotonic backup_seq
│   └── 000116.library.db                      exactly 2 kept
├── tracks/
│   └── t-9f31c0aa4b17/                        dir name IS track_id, ^t-[0-9a-f]{12}$
│       ├── TRACK_ID                           plain text, three-way checked on open
│       ├── track.db                           AUTHORITATIVE. every mutable byte for this track
│       ├── track.db-wal                       wal_autocheckpoint=256 pages = 1 MiB
│       ├── track.db-shm
│       ├── index.db                           DERIVED: contentless FTS5 postings only
│       ├── backup/
│       │   ├── 000042.track.db                VACUUM INTO copy, monotonic seq
│       │   └── 000041.track.db                2 for the hot track, 1 otherwise, 0 when stale
│       ├── corpus/
│       │   ├── D01__hnsw-recall-tradeoffs.doc.md
│       │   ├── D07__ef-search-latency.doc.md
│       │   └── .tmp/                          <name>.<pid>.<tid>.<n>.part, swept hourly
│       ├── intake/
│       │   ├── source.txt                     verbatim job text or pasted material, immutable
│       │   └── source.meta.json               sha256, kind, external path, captured_at
│       ├── spool/
│       │   └── 000883.raw.txt                 raw model output, written BEFORE any constraint
│       └── quarantine/                        moved here, never silently unlinked
├── archive/
│   ├── t-4c02ab991de3.pwk                     lzma tar: track.db (VACUUMed) + corpus + intake + MANIFEST
│   ├── t-4c02ab991de3.pwk.sha256
│   ├── t-4c02ab991de3.meta.json               OUTSIDE the tar, so the library lists cold tracks
│   └── t-4c02ab991de3.cards.jsonl             OUTSIDE the tar, so cold cards stay reviewable
├── trash/
│   ├── t-1188ffaa0c22.pwk                     user-deleted, 30-day undo window
│   └── t-1188ffaa0c22.purge.json              {deleted_at, purge_after, bytes, summary}
└── quarantine/
    └── 000009-20260907T041109Z-orphan-dir/    a track directory with no registry row
```

Runtime file set, fixed for hash pinning, no migrations directory, no dynamic imports.
This list must stay equal to `ls prepwright/*.py` plus `bridge.py` and `index.html`,
because the manifest is generated from it. The modules not named below
(`__init__.py`, `assess.py`, `curriculum.py`, `diagnose.py`, `intake.py`,
`provider.py`, `security.py`, `teach.py`) exist as documented stubs and belong in
the manifest from the day one of them holds code:

```
prepwright/config.py     every path and every cap, so a cap can be audited in one place
prepwright/state.py      library.db + track.db: schemas, open_track(), TrackHandle, leases
prepwright/track.py      lifecycle: create, archive, restore, trash, purge, reconcile
prepwright/corpus.py     corpus_path(), read_section(), write_doc(), verify_doc()
prepwright/prompt.py     build_pack(), build_prompt(), check_citations()
prepwright/research.py   the ONLY module importing urllib.request
prepwright/keep.py       housekeep(): reconcile, ladder, backups, archive, restore
prepwright/serve.py      HTTP bridge on 127.0.0.1, request routing, lease heartbeat
bridge.py                the running bridge until serve.py is extracted from it
index.html               the whole page: one inline <style>, one inline <script>
```

Three deliberate duplications, each paid for a reason: `intake/source.txt` duplicates the row in track.db so a human recovers the input with `cat`; `TRACK_ID` duplicates the row so a directory copied or restored into the wrong slot is caught before a byte is taught from; the file header hash duplicates `doc.file_sha256` so a single corrupt hash cannot quarantine a good document.

---

## B. SCHEMA

Every connection opens with:

```sql
PRAGMA page_size = 4096;          -- pre-creation only
PRAGMA auto_vacuum = INCREMENTAL; -- pre-creation only; DELETE alone never returns bytes
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
PRAGMA wal_autocheckpoint = 256;  -- 256 * 4096 = 1 MiB
```

`auto_vacuum` and `page_size` must be set before the first CREATE TABLE. Schema version lives in `meta`, and the ladder is a list of DDL strings in code.

### library.db

Registry, projection, coordination. No content column anywhere. Rebuildable from `tracks/*/track.db` and `archive/*.meta.json`, but expensive, so it gets its own 2-copy backup.

```sql
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL) STRICT;
-- schema_version, created_at, backup_seq, quarantine_seq, clock_suspect, last_seen_utc

CREATE TABLE track (
  track_id       TEXT PRIMARY KEY,            -- 't-' || 12 hex, immutable, IS the directory name
  short_code     TEXT NOT NULL UNIQUE,        -- 4 chars, typed to confirm deletion
  title          TEXT NOT NULL,
  employer       TEXT,
  role_title     TEXT,
  kind           TEXT NOT NULL CHECK (kind IN ('job','topic')),
  source_kind    TEXT NOT NULL CHECK (source_kind IN ('pasted','imported','freeform')),
  source_path    TEXT,                        -- external folder, recorded, never re-opened
  source_sha256  TEXT,
  lifecycle      TEXT NOT NULL CHECK (lifecycle IN
                   ('active','archiving','archived','restoring','trashed','lost')),
  outcome        TEXT NOT NULL DEFAULT 'open' CHECK (outcome IN
                   ('open','interviewing','offer','rejected','withdrawn')),
  pinned         INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0,1)),
  no_auto_compact INTEGER NOT NULL DEFAULT 0 CHECK (no_auto_compact IN (0,1)),
  phase          TEXT NOT NULL CHECK (phase IN
                   ('intake','diagnostic','gaps','research','curriculum','teaching','review')),
  generation     INTEGER NOT NULL DEFAULT 1,  -- bumped by archive/restore/force-close
  created_utc    TEXT NOT NULL,
  opened_utc     TEXT NOT NULL,               -- last render. drives derived staleness
  touched_utc    TEXT NOT NULL,               -- last successful write
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
CREATE INDEX track_by_pressure ON track(lifecycle, pinned, opened_utc);

-- Coordination. A lease is what makes "delete while open" impossible.
CREATE TABLE track_lease (
  track_id      TEXT PRIMARY KEY REFERENCES track(track_id) ON DELETE CASCADE,
  holder_pid    INTEGER NOT NULL,
  holder_boot   TEXT NOT NULL,                -- boot id, so a recycled pid is not mistaken for live
  client_label  TEXT NOT NULL,                -- 'laptop' | 'phone'
  generation    INTEGER NOT NULL,             -- copy of track.generation at acquisition
  heartbeat_utc TEXT NOT NULL,
  heartbeat_mono INTEGER NOT NULL             -- time.monotonic_ns, clock-skew proof
) STRICT;
-- A lease whose heartbeat_mono is older than 120 s of this process's monotonic clock,
-- or whose holder_boot differs from the current boot id, is reclaimable.

CREATE TABLE due_card (                       -- DERIVED projection. NO CONTENT. rebuildable.
  track_id  TEXT NOT NULL,
  card_id   TEXT NOT NULL,
  due_utc   TEXT NOT NULL,
  retired   INTEGER NOT NULL DEFAULT 0,
  cold      INTEGER NOT NULL DEFAULT 0,       -- 1 = read from archive/<id>.cards.jsonl
  PRIMARY KEY (track_id, card_id)
) STRICT;
CREATE INDEX due_card_by_when ON due_card(due_utc) WHERE retired = 0;

CREATE TABLE track_history (                  -- never pruned. ~20 rows per track, 200 B each
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, track_id TEXT NOT NULL,
  kind TEXT NOT NULL,        -- created|archived|restored|trashed|purged|recovered|lost
  detail TEXT NOT NULL
) STRICT;
CREATE TRIGGER track_history_no_update BEFORE UPDATE ON track_history
  BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER track_history_no_delete BEFORE DELETE ON track_history
  BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE event (                          -- housekeeping receipts. pruned to 5000 rows
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, track_id TEXT,
  kind TEXT NOT NULL, bytes_freed INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL
) STRICT;
```

There is no `is_hot` column and no partial unique index on it. Exactly one live teaching lease is enforced by `track_lease` plus an application check, because a lease expires and a pin does not, and a crashed process must not wedge the library.

### tracks/<id>/track.db

Authoritative for one track. Nothing here is derivable from anywhere else.

```sql
CREATE TABLE track_meta (
  track_id  TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
  label TEXT NOT NULL, created_utc TEXT NOT NULL,
  next_doc_no INTEGER NOT NULL DEFAULT 1, next_spool_seq INTEGER NOT NULL DEFAULT 1,
  backup_seq INTEGER NOT NULL DEFAULT 0,
  bytes_turns INTEGER NOT NULL DEFAULT 0,     -- maintained INSIDE each write txn
  bytes_cards INTEGER NOT NULL DEFAULT 0,
  bytes_corpus INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TRIGGER track_meta_one_row BEFORE INSERT ON track_meta
  WHEN (SELECT COUNT(*) FROM track_meta) >= 1
  BEGIN SELECT RAISE(ABORT,'track_meta holds exactly one row'); END;

CREATE TABLE intake (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  kind TEXT NOT NULL CHECK (kind IN ('pasted','imported','freeform')),
  source_path TEXT, source_sha256 TEXT, source_mtime_ns INTEGER,
  captured_utc TEXT NOT NULL,
  body TEXT NOT NULL, body_sha256 TEXT NOT NULL,
  body_bytes INTEGER NOT NULL CHECK (body_bytes <= 65536)
) STRICT;

CREATE TABLE gap (
  gap_id TEXT PRIMARY KEY,                    -- 'g03'
  ord INTEGER NOT NULL UNIQUE,
  label TEXT NOT NULL, why TEXT NOT NULL,
  jd_span TEXT,                               -- verbatim span quoted out of intake.body
  level TEXT NOT NULL CHECK (level IN ('none','shaky','solid')),
  status TEXT NOT NULL CHECK (status IN ('proposed','approved','edited','declined','covered')),
  weight REAL NOT NULL DEFAULT 1.0,
  rev INTEGER NOT NULL DEFAULT 1,             -- per-row optimistic lock, editable objects ONLY
  proposed_utc TEXT NOT NULL, decided_utc TEXT
) STRICT;
CREATE TABLE gap_history (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL, gap_id TEXT NOT NULL,
  from_status TEXT, to_status TEXT NOT NULL, note TEXT
) STRICT;
CREATE TRIGGER gap_audit AFTER UPDATE OF status ON gap
  BEGIN INSERT INTO gap_history(at_utc,gap_id,from_status,to_status,note)
        VALUES (strftime('%Y-%m-%dT%H:%M:%SZ','now'), NEW.gap_id, OLD.status, NEW.status, NULL); END;

CREATE TABLE research_run (
  run_id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, finished_utc TEXT,
  queries TEXT NOT NULL, tool_calls INTEGER NOT NULL DEFAULT 0,
  bytes_fetched INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('running','ok','failed','aborted'))
) STRICT;

CREATE TABLE doc (
  doc_id        TEXT PRIMARY KEY,             -- 'D07'. per-track, 6-char citation token
  doc_no        INTEGER NOT NULL UNIQUE,
  run_id        TEXT REFERENCES research_run(run_id),
  status        TEXT NOT NULL CHECK (status IN
                  ('writing','ready','quarantined','origin_drift','unverifiable')),
  file_name     TEXT NOT NULL UNIQUE,
  file_sha256   TEXT,                          -- ALSO written into the file's own header
  file_bytes    INTEGER CHECK (file_bytes <= 12288),
  file_mtime_ns INTEGER,                       -- with file_bytes, guards the section offsets
  title         TEXT NOT NULL,
  origin_url    TEXT NOT NULL,
  final_url     TEXT,
  origin_sha256 TEXT NOT NULL, origin_bytes INTEGER NOT NULL,
  extract_sha256 TEXT NOT NULL,                -- hash of the plain text the distiller read
  fetched_utc   TEXT NOT NULL, verified_utc TEXT,
  publisher TEXT, published_on TEXT,
  vetting TEXT NOT NULL CHECK (vetting IN ('primary','secondary','vendor','community')),
  trust   INTEGER NOT NULL CHECK (trust BETWEEN 1 AND 5),
  n_sections INTEGER NOT NULL DEFAULT 0,
  cite_count INTEGER NOT NULL DEFAULT 0, last_cited_utc TEXT
) STRICT;

CREATE TABLE section (
  doc_id TEXT NOT NULL REFERENCES doc(doc_id) ON DELETE CASCADE,
  sec_id TEXT NOT NULL,                        -- 's03'
  ord INTEGER NOT NULL,
  heading TEXT NOT NULL,
  body_chars INTEGER NOT NULL CHECK (body_chars <= 900),
  body_sha16 TEXT NOT NULL,                    -- 16 hex, bitrot detector for our own bytes
  byte_off INTEGER NOT NULL, byte_len INTEGER NOT NULL,   -- seek+read, no parse
  origin_span TEXT NOT NULL,                   -- 32-48 chars of the source span, verbatim
  src_start INTEGER, src_end INTEGER,          -- char span into the extracted source text
  concept TEXT,
  PRIMARY KEY (doc_id, sec_id)
) STRICT;

CREATE TABLE step (
  step_id TEXT PRIMARY KEY, ord INTEGER NOT NULL UNIQUE,
  title TEXT NOT NULL, objective TEXT NOT NULL,
  gap_id TEXT REFERENCES gap(gap_id),
  status TEXT NOT NULL CHECK (status IN ('locked','ready','open','done','skipped')),
  evidence_state TEXT NOT NULL DEFAULT 'full'
    CHECK (evidence_state IN ('full','degraded','empty')),
  est_minutes INTEGER, score REAL CHECK (score BETWEEN 0 AND 1),
  review TEXT,                                 -- <= 900 chars, written AT completion, in context
  compacted INTEGER NOT NULL DEFAULT 0,
  rev INTEGER NOT NULL DEFAULT 1,
  opened_utc TEXT, completed_utc TEXT
) STRICT;

CREATE TABLE step_slice (                      -- the ONLY corpus a step may carry or cite
  step_id TEXT NOT NULL REFERENCES step(step_id) ON DELETE CASCADE,
  doc_id TEXT NOT NULL, sec_id TEXT NOT NULL, ord INTEGER NOT NULL,
  PRIMARY KEY (step_id, doc_id, sec_id),
  FOREIGN KEY (doc_id, sec_id) REFERENCES section(doc_id, sec_id)
) STRICT;
-- The composite foreign key is the isolation teeth. Foreign keys cannot cross database
-- files, so a step is structurally incapable of naming another track's evidence.

CREATE TABLE turn (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,       -- SERVER allocated inside the write txn
  step_id TEXT NOT NULL REFERENCES step(step_id),
  at_utc TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user','tutor','system')),
  reply_to_seq INTEGER REFERENCES turn(seq),   -- pairs a reply to its question
  body TEXT NOT NULL,
  body_bytes INTEGER NOT NULL CHECK (body_bytes <= 8192),
  body_sha16 TEXT NOT NULL,
  overflow_bytes INTEGER NOT NULL DEFAULT 0,   -- non-zero when the reply was truncated
  overflow_sha256 TEXT,
  spool_ref TEXT,                              -- 'spool/000883.raw.txt' while it survives
  pack_sha16 TEXT,                             -- hash of the exact pack sent
  citations TEXT,                              -- JSON array of 'D07§s03'
  ungrounded INTEGER NOT NULL DEFAULT 0,
  client_turn_id TEXT NOT NULL UNIQUE,         -- client-generated, makes a retried POST a no-op
  in_tok INTEGER, out_tok INTEGER
) STRICT;
CREATE INDEX turn_tail ON turn(step_id, seq DESC);

CREATE TABLE turn_compaction (                 -- the receipt. must exist BEFORE a body is emptied
  turn_seq INTEGER PRIMARY KEY,
  at_utc TEXT NOT NULL, reason TEXT NOT NULL CHECK (reason IN ('cap','stale','user')),
  orig_bytes INTEGER NOT NULL, orig_sha16 TEXT NOT NULL, step_review_present INTEGER NOT NULL
) STRICT;

CREATE TRIGGER turn_no_delete BEFORE DELETE ON turn
  BEGIN SELECT RAISE(ABORT,'turns are append-only'); END;
CREATE TRIGGER turn_no_update BEFORE UPDATE ON turn
  WHEN NOT (NEW.body = '' AND OLD.role = 'tutor' AND OLD.body <> ''
            AND (SELECT COUNT(*) FROM turn_compaction WHERE turn_seq = OLD.seq) = 1)
  BEGIN SELECT RAISE(ABORT,'append-only except receipted tutor compaction'); END;
-- A student turn cannot be emptied by any path. A tutor turn cannot be emptied until a
-- receipt row records its original hash and length. Policy becomes a property of the file.

CREATE TABLE turn_pending (                    -- serializes the model call per step
  step_id TEXT PRIMARY KEY REFERENCES step(step_id) ON DELETE CASCADE,
  client_label TEXT NOT NULL, started_utc TEXT NOT NULL, started_mono INTEGER NOT NULL,
  request_sha16 TEXT NOT NULL
) STRICT;

CREATE TABLE draft (                           -- what the user typed, saved before anything else
  step_id TEXT NOT NULL, client_label TEXT NOT NULL,
  body TEXT NOT NULL, updated_utc TEXT NOT NULL,
  PRIMARY KEY (step_id, client_label)
) STRICT;

CREATE TABLE assessment (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL,
  step_id TEXT NOT NULL REFERENCES step(step_id),
  score REAL NOT NULL CHECK (score BETWEEN 0 AND 1),
  rubric TEXT NOT NULL, evidence_turn_seq INTEGER, misconception TEXT
) STRICT;
CREATE TRIGGER assessment_no_update BEFORE UPDATE ON assessment
  BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER assessment_no_delete BEFORE DELETE ON assessment
  BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE card (
  card_id TEXT PRIMARY KEY, step_id TEXT REFERENCES step(step_id),
  front TEXT NOT NULL, back TEXT NOT NULL,
  cite TEXT NOT NULL,                          -- 'D07§s03', validated against section on insert
  cite_snippet TEXT NOT NULL,                  -- <= 240 chars, FROZEN copy of the cited text
  created_utc TEXT NOT NULL,
  due_utc TEXT NOT NULL, interval_days REAL NOT NULL DEFAULT 1,
  ease REAL NOT NULL DEFAULT 2.5, reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0, last_review_utc TEXT,
  retired INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TABLE card_review (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL REFERENCES card(card_id),
  at_utc TEXT NOT NULL, at_mono INTEGER NOT NULL,
  grade INTEGER NOT NULL CHECK (grade BETWEEN 0 AND 3),
  prev_interval REAL, next_interval REAL,
  clock_suspect INTEGER NOT NULL DEFAULT 0     -- 1 = graded but interval NOT advanced
) STRICT;
CREATE TRIGGER card_review_no_update BEFORE UPDATE ON card_review
  BEGIN SELECT RAISE(ABORT,'append-only'); END;

CREATE TABLE recovery_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at_utc TEXT NOT NULL,
  kind TEXT NOT NULL, detail TEXT NOT NULL
) STRICT;
```

### tracks/<id>/index.db

Wholly derived. Delete and rebuild is always legal, and it holds nothing that a rebuild cannot recompute from `corpus/*.md` and `track.db`.

```sql
PRAGMA synchronous = NORMAL;                  -- rebuildable; durability is not worth the fsync
CREATE VIRTUAL TABLE sec_fts USING fts5(
  heading, body, content='', contentless_delete=1, tokenize='porter unicode61');
CREATE TABLE fts_map (rowid INTEGER PRIMARY KEY, doc_id TEXT NOT NULL, sec_id TEXT NOT NULL);
CREATE TABLE index_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);  -- built_utc, docs_digest
```

Contentless FTS5 stores postings and no copy of the text, so the corpus exists on disk exactly once. Rebuild for 48 docs and 480 sections is expected to take well under a second, and the figure below should be confirmed once an index exists. If a future runtime lacks `contentless_delete`, the fallback is a `posting(term, rowid, tf)` plus `df(term, n)` pair with BM25 scored in about 30 lines of Python, same query surface, roughly 3x slower, still under 10 ms at this size.

---

## C. TRACK LIFECYCLE

Six persisted lifecycle values plus one derived state. Staleness is derived from `opened_utc`, never stored, so there is no transition to get wrong and no timer that mutates a row.

The governing rule: an automatic transition may change how bytes are represented. It may never change whether a decision survives. Nothing in the automatic set removes a gap, a curriculum step, a score, a citation, a card, a corpus document, or a single student turn.

**created → active.** Trigger: the user pastes a job description, imports one from the external per-job folder, or pastes bare material with no job behind it. Effect: `mkdir tracks/<id>` at 0700, write `TRACK_ID`, create `track.db` through the schema ladder, copy the external file to `intake/source.txt` and hash it. That folder is read exactly once, here. `source_path` is provenance and is never re-opened, so a later change on the other side cannot retroactively alter what this track was built from. Cost at creation: about 40 KB.

**active.** Every write bumps `touched_utc`; every render bumps `opened_utc`. Full function: per-document random access, live FTS index, 2 backups for the leased track and 1 for the others.

**active → stale.** DERIVED, not stored. Condition: `now - opened_utc >= 21 days`, and `pinned = 0`, and `outcome NOT IN ('interviewing','offer')`, and no live lease. What the user sees: the row moves to a collapsed Dormant group reading "idle 34 days, 2.2 MB, archiving reclaims 1.6 MB". What housekeep does on first observing it, all representation only: TRUNCATE-checkpoint the WAL, delete `index.db`, delete `backup/*`, run `PRAGMA incremental_vacuum`. Saves about 2.4 MiB, the gap between the ACTIVE and STALE totals below, and always fires, because it is keyed on age rather than on library pressure. Opening the track restores everything: the index rebuilds in under 100 ms and `opened_utc` resets.

**stale → archived, path 1 (user).** One click, from the per-track row or from the batch proposal housekeep raises at 60 days idle. The proposal lists each track with byte counts and last-open dates, per-track checkboxes, and a default of Not now.

**stale → archived, path 2 (pressure).** AUTOMATIC, and only when every one of these holds: library bytes above LIBRARY_SOFT, idle at least 120 days, `pinned = 0`, `outcome NOT IN ('interviewing','offer')`, no live lease. Oldest `opened_utc` first, stopping at LIBRARY_SOFT minus 16 MiB of hysteresis so the collector does not run on every save. This is a representation change and it is fully restorable from the `.pwk`, so no duplicate copy is kept in `trash/` and no undo window is needed. Every pressure archive writes an `event` row and a named line in the notice feed.

Archiving is two-phase and verifies before it deletes:

```
1. txn: UPDATE track SET lifecycle='archiving', generation=generation+1  (COMMIT FIRST)
2. refuse if a live lease exists; offer "close the session on <client>" which bumps generation
3. wal_checkpoint(TRUNCATE); VACUUM INTO archive/<id>.pwk.db.tmp
4. build archive/<id>.pwk.tmp  (lzma tar: the VACUUMed db, corpus/*.md, intake/*, MANIFEST.json
   of per-member path/size/sha256). No already-compressed member goes in, so nothing is
   compressed twice.
5. fsync, os.replace to <id>.pwk, fsync the archive dir, write <id>.pwk.sha256
6. REOPEN the tar, re-read every member, compare each hash to MANIFEST. Any mismatch aborts
   and leaves the live directory untouched.
7. write archive/<id>.meta.json and archive/<id>.cards.jsonl OUTSIDE the tar
8. shutil.rmtree(tracks/<id>)
9. txn: UPDATE track SET lifecycle='archived', bytes_archive=?, ...
```

A crash anywhere leaves `lifecycle='archiving'`, which the startup reconcile resolves by checking which side exists on disk. The meta file outside the tar is what lets the library list, search, and size cold tracks with nothing unpacked. The cards file outside the tar is what keeps a cold track's flashcards reviewable, which is the whole point of spaced repetition across a job hunt.

> **Dormant as of 2026-09-09.** This layer is designed but not wired. The `card` and `card_review` tables hold a complete SM-2 scheduler that nothing in the running app can write: `review_card` has no caller and `add_card` has one, in a test. All three live tracks carry zero rows in both. What the Recap drill actually runs is a shuffled bank stored as `mark` rows of kind `card`, which is a different mechanism sharing the word, and the page no longer claims otherwise. The owner ruled on 2026-09-09 that the tables stay dormant rather than being dropped: ADR 0006 records the ruling, ADR 0005 the 37-point surface behind it. Read this paragraph as a design that is available, not one that is running.

**archived → active (restore).** `lifecycle='restoring'`, extract, verify every member against MANIFEST, `quick_check` the database, three-way check `TRACK_ID`, rebuild `index.db`, then delete the `.pwk`. Any mismatch aborts and keeps the `.pwk`.

**→ trashed. NEVER AUTOMATIC. NOT UNDER ANY PRESSURE.** Requires typing the 4-character `short_code` after a dialog stating the exact loss: title, employer, turn count, document count, card count, first and last activity, bytes. The `.pwk`, its meta and its cards move to `trash/` with `purge_after = now + 30 days`. `track_history` rows survive forever, so the user's record of what they prepared for is not part of what gets deleted.

**trashed → gone.** Semi-automatic and only for something a human already deleted: unlink once `now > purge_after`, the clock is not suspect, and the app has been opened inside the window. An app unused for a year reaps nothing. If `trash/` exceeds TRASH_CAP the oldest entries beyond the cap purge early with a notice.

**lost.** Reconcile found a registry row with no directory and no `.pwk`. The row is kept, the state is shown, and a restore from `backup/` is offered if one survived.

Never automatic, in one list: deleting a track, deleting a card, deleting a corpus document, dropping a gap, a step, a score, a citation, or a student turn, and any transition at all on a pinned track or one whose outcome is `interviewing` or `offer`.

---

## D. STORAGE ACCOUNTING AND CAPS

Every cap is enforced at the write, against counters maintained inside the same transaction as the write (`track_meta.bytes_turns`, `bytes_cards`, `bytes_corpus` accumulate row-size deltas), plus an authoritative recount at each housekeep. Caps are never checked against numbers refreshed only at startup.

### Unit costs, stated so they can be checked

```
section body                  <= 900 chars
section heading line          ~28 B          -> section unit 940 B
doc header line (JSON)        ~700 B
doc spans block               10 x 56 B = 560 B
typical doc (7 sections)      7*940 + 700 + 400  = 7,680 B, call it 8,000 B
worst legal doc               10*940 + 700 + 560 = 10,660 B
turn: user 350 B, tutor 1,800 B, alternating average 1,075 B + 150 B row overhead = 1,225 B
card row: 200 front + 300 back + 16 cite + 240 snippet + 60 sched + 120 overhead = 936 B
```

### Prompt budget, which is what sets the corpus caps

Target 9,500 input tokens per teaching turn. Technical prose with code and symbols runs about 3.3 bytes per token, not 4.

```
system + step objective + grounding rules   1,800 B
corpus pack                                12,000 B
history tail                               12,000 B
user message                                4,000 B
                                    total  29,800 B  /  3.3  =  9,030 tokens
```

Both the pack and the tail are capped in BYTES, not in item counts. The tail is filled newest-first with whole turns until the next would exceed 12,000 B, with at least one turn always included. A single 8,192-byte turn therefore cannot blow a budget derived from an assumed average.

Pack: 12,000 / (940 + 45 header) = 12 sections; PACK_MAX_SECTIONS = 10, so 9,850 B typical.

### Per-object caps (hard refuse at the write)

```
DOC_MAX_BYTES          12,288    a source that will not fit is split into two documents
SECTION_MAX_CHARS         900
MAX_SECTIONS_PER_DOC       10
MAX_DOCS_PER_TRACK         48
TURN_MAX_BYTES          8,192    truncate with marker; overflow hash + length + spool retained
MAX_TURNS_PER_STEP        150
MAX_STEPS                  40
MAX_CARDS                 300
INTAKE_MAX_BYTES       65,536
CARD_SNIPPET_MAX          240
STEP_REVIEW_MAX           900
SPOOL_CAP           1,048,576    per track
EVENT_ROWS              5,000
```

### Per-track caps, with the arithmetic

```
corpus:   typical  48 * 8,000  =   384,000 B  (375 KiB)
          worst    48 * 12,288 =   589,824 B  (576 KiB)
          CORPUS_BYTES_CAP     =   655,360 B  (640 KiB), 65,536 B above the worst legal case

turns:    typical  20 steps * 44 turns * 1,225 = 1,078,000 B  (1.03 MiB)
          TURNS_BYTES_CAP      = 3,145,728 B  (3 MiB), 2.92x the typical worked track

cards:    300 * 936            =   280,800 B  (274 KiB)
          CARDS_BYTES_CAP      =   327,680 B  (320 KiB)

steps/gaps/diagnostic/assessments:
          40 steps  * 1,340    =    53,600
          20 gaps   *   280    =     5,600
          40 diag   *   750    =    30,000   (needs a diagnostic table;
                                              section B does not define one yet)
          120 assess*   240    =    28,800
                                   118,000 B  (115 KiB)

doc + section rows:
          48 docs   *   640    =    30,720
          480 secs  *   200    =    96,000
                                   126,720 B  (124 KiB)

track.db typical: 1,078,000 + 280,800 + 118,000 + 126,720 = 1,603,520; x1.15 = 1,844,048 (1.76 MiB)
track.db worst:   3,145,728 + 327,680 + 118,000 + 126,720 = 3,718,128; x1.15 = 4,275,847 (4.08 MiB)
          TRACK_DB_CAP         = 4,718,592 B  (4.5 MiB), 442,745 B (432 KiB) above the worst legal case

WAL_BUDGET   = 1,048,576 (wal_autocheckpoint 256 pages x 4096)
INDEX_BUDGET =   327,680 (contentless postings ~0.7x corpus text: 0.7 * 384,000 = 268,800)
BACKUP_BYTES_CAP = 5,242,880 per track, at most 2 copies; drop to 1 when the pair exceeds it
```

Per-track totals, with every byte class counted including WAL, index, spool and backups:

```
HOT ceiling      4.5 db + 1.0 wal + 0.03 shm + 0.64 corpus + 0.32 index + 0.06 intake
                 + 1.0 spool + 5.0 backup                             = 12.55 MiB -> cap 13 MiB
HOT typical      1.76 + 1.0 + 0.375 + 0.263 + 0.02 + 0.1 + 3.2        =  6.72 MiB
ACTIVE typical   1.76 + 0.5 + 0.375 + 0.263 + 0.02 + 1.60             =  4.52 MiB
STALE typical    1.76 + 0.375 + 0.02                                  =  2.16 MiB
ARCHIVED typical db text 1.03/3.6 = 0.286, other pages 0.57/3.0 = 0.190,
                 corpus 0.375/3.2 = 0.117, intake 0.007, cards.jsonl 0.11, meta 0.004
                                                                      =  0.71 MiB
                 ARCHIVE_CAP = 2.5 MiB
```

### Library projection, three years at 25 tracks per year

```
 1 hot       x 6.72 =   6.72
 3 active    x 4.52 =  13.56
 8 stale     x 2.16 =  17.28
63 archived  x 0.71 =  44.73
library.db: 75 rows x 700 = 52,500; due projection 22,500 x 56 = 1,260,000;
            event 5,000 x 220 = 1,100,000; history 1,500 x 200 = 300,000
            = 2,712,500 x 1.2 = 3,255,000                        =   3.10
library.bak 2 x 3.10                                             =   6.20
                                                       live total =  91.59 MiB
trash cap 32 MiB + quarantine cap 16 MiB, worst case              = 139.59 MiB

LIBRARY_SOFT = 201,326,592 (192 MiB)  notice + pressure archiving, stops at 176 MiB
LIBRARY_HARD = 402,653,184 (384 MiB)  research refused; teaching, assessment, review,
                                      restore and delete keep working
TRASH_CAP      = 33,554,432 (32 MiB)
QUARANTINE_CAP = 16,777,216 (16 MiB), age-purge at 90 days, oldest first, with a notice
```

Honest statement of what the caps do and do not bound: 40 live tracks all sitting at the HOT ceiling is 520 MiB, above LIBRARY_HARD. The per-track ceiling alone does not bound the library. What bounds it is the age-driven staleness compaction, which always fires and takes each cold track to 2.16 MiB, plus the hard cap that stops the only network-fed growth path. The expected trajectory reaches 92 MiB in year three, which is 48 percent of the soft cap, so the soft cap is a tripwire for pathology, not a constraint the candidate will feel.

### What the user sees

Every track row carries its own byte bar broken into db / corpus / sessions / backup, plus turn count, doc count, card count and last-opened date, so the user sees which lever is which before pulling one. One library bar against LIBRARY_SOFT. At 80 percent of a per-track cap: a banner naming the remedy. At 80 percent of LIBRARY_SOFT: the archive proposal becomes persistent and names the ten largest tracks with sizes and last-open dates.

### Eviction ladder, in order, with the loss class of each rung

```
0. Reconcile registry against the filesystem, both directions.           no loss
1. Sweep spool > 7 days, corpus/.tmp/*.part > 1 h, orphan tmp files.     no loss
2. wal_checkpoint(TRUNCATE) on every track with no live lease.           no loss
3. Delete index.db of every track that is not leased. Rebuild is
   projected under 100 ms and is not yet measured.                       no loss
4. Delete backups of stale tracks. The .pwk path is available and a
   track untouched for 21 days has no 20-minute rollback worth keeping.  no loss of decisions
5. PRAGMA incremental_vacuum on stale tracks. Returns freed pages to
   the filesystem, which DELETE alone never does.                        no loss
6. Compact tutor prose of steps with status='done' in stale tracks.
   Requires step.review to exist, writes a turn_compaction receipt with
   the original hash and length, and never touches a student turn.       FIRST lossy rung
7. Pressure-archive per section C path 2.                                representation only
8. Purge trash past purge_after or beyond TRASH_CAP, clock-sane only.    user already deleted
9. STOP. There is no rung 10. Post a notice naming the ten largest
   tracks with bytes and last-open dates, and wait for a human.
```

Rung 6 is the correct first lossy step: the tutor's prose is regenerable from the same cited sections, the student's words carry where the learning actually stands, and `step.review` already holds the conclusion. The review is written at step completion while the session is still in context, never by a background pass that has no context to summarize.

Never dropped automatically at any pressure: `track.db` and its backups for a leased track, `intake/`, corpus documents, `doc` provenance, cards and their schedules, student turns, assessments, `track_history`, and anything belonging to a pinned track or one whose outcome is `interviewing` or `offer`.

### What happens at the turns cap

At 80 percent of TURNS_BYTES_CAP the user gets a banner: "1.4 of 3.0 MB. Compact 6 completed steps to reclaim 0.9 MB, or raise the cap." At 100 percent, writes are not refused: the app compacts the oldest completed step automatically, logs it, and continues. Freezing the one track the user is studying the night before an interview is a worse failure than losing tutor prose from a step finished three weeks ago. A user who sets `no_auto_compact = 1` gets the opposite trade and is told so: writes at the cap are then refused with a named remedy.

---

## E. CORPUS DOCUMENT FORMAT

One distilled source is one immutable UTF-8 file, `corpus/D<nn>__<slug>.doc.md`. The file is the prompt fragment store: it is optimized for `seek`, `read` and paste, not for editing. A correction produces a new `doc_id` and never edits an existing file.

```
{"v":1,"track_id":"t-9f31c0aa4b17","doc_id":"D07","title":"HNSW recall and ef_search",
"body_sha256":"3f9a1c4e...d2","body_bytes":606,"n_sections":3,
"origin_url":"https://example.org/specs/hnsw","final_url":"https://example.org/specs/hnsw",
"origin_sha256":"c1d0...9f","origin_bytes":412880,"extract_sha256":"9c40be...",
"fetched":"2026-09-05T11:13:20Z","vetting":"primary","trust":5,"license":"cc-by-4.0",
"extract":"3 of 9 sections, ~18% of source"}
§s01|what-ef-search-controls
ef_search sets the size of the candidate list the graph search keeps while descending.
It is the only knob on the query latency path, and the only one adjustable after the
index is built.

§s02|why-recall-falls-off-below-ef-k
Below ef_search = k the search cannot hold k candidates, so recall collapses rather than
degrading smoothly. Raising it past about 200 buys tenths of a point at linear cost.

§s03|where-the-latency-goes
Query cost is dominated by distance computations, which scale with ef_search and not with
dataset size. Doubling ef_search roughly doubles p99.

§spans
s01|ef_search sets the size of the candidate
s02|Below ef_search = k the search cannot hold
s03|Query cost is dominated by distance comput
```

Writer rules enforced at ingest: the header is one line and parses with `json.loads` on the first line; each section opens with `§<sec_id>|<kebab-key>` at line start; section bodies are at most 900 characters with no nested headings, tables, HTML, images or base64, prose and short fences only; `§spans` is the last block, one line per section carrying 32 to 48 verbatim characters of the source span the section was distilled from. Section keys are stable across re-distillations of the same origin, so a citation survives a refresh whenever the section survives.

**(i) Cheap in a prompt.** The tutor is handed sections, never whole documents. `step_slice` names the exact `(doc_id, sec_id)` pairs. Loading is `f.seek(byte_off); f.read(byte_len)`, no parse and no decode. A ten-section pack is 9,850 bytes and about 2,985 tokens. Handing over the same three documents whole would be 24,000 bytes, so section-level slicing cuts per-turn corpus cost by roughly 2.4x on a three-document step and by 9x on the pathological one.

**(ii) Citable to a section.** `D07§s03` is 7 characters and is already printed in the pack the tutor reads, so citing costs nothing and traceability is a substring match rather than a lookup table shipped in the prompt. The reply validator extracts every `D\d{2}§s\d{2}` token and checks set membership against the exact pack that was sent, not against "a valid id in this track". An id from an earlier turn of another track cannot pass. A failing reply is regenerated once with the offending token quoted back, then stored with `ungrounded = 1` and shown flagged rather than presented as taught fact. A claim with no citation is permitted only inside an explicit "this is not in your sources" sentence, which the system prompt requires and the validator checks for.

**(iii) Small on disk.** 606 bytes of body in the worked example, and a typical
seven-section document about 7,400, plus roughly 1,260 bytes of header and spans. Provenance and verification together are 14.5 percent of the file. The raw fetched page was 412,880 bytes, so retention is 2.1 percent. Storing origins would cost 48 x 412,880 = 18.9 MiB per track, which is 30x the entire corpus budget, so origins are not stored.

**(iv) Verifiable against origin later.** Four hashes with four jobs, and one manual command, `verify D07`, which is the only moment other than research that touches the network.

```
origin_sha256   over the exact fetched bytes. Equal on refetch means the distillation
                still stands on the byte string it was made from.
extract_sha256  over the plain text the distiller read. Separates "the page changed"
                from "our extractor changed".
body_sha256     over everything after the header line. Written in TWO places, the file
                header and doc.file_sha256.
section spans   32-48 verbatim source characters per section. 560 B per document that
                falsify every citation years after the raw source is gone.
```

Verify outcomes, and note that none of them is automatic:

```
origin hash equal                       -> verified_utc updated, nothing else changes
hash differs, all spans still found     -> source edited elsewhere, cited spans survive,
                                           status stays 'ready', fetched_utc refreshed
hash differs, k spans missing           -> status='origin_drift'. The document stays in
                                           prompts, and every citation from it renders
                                           "source changed since 5 Sep". Exclusion needs
                                           a click.
fetch looks wrong (non-2xx, content-type
not text/html or pdf, body under 2 KB,
or zero spans found)                    -> status='unverifiable'. Nothing changes about
                                           teaching. A soft-404, paywall or interstitial
                                           must not silently amputate a corpus.
origin 404                              -> status='unverifiable', flagged "origin gone"
```

The two-place hash rule closes a real failure: on read, compute `h` over the body. If `h` equals the header hash but differs from `doc.file_sha256`, the ROW is corrupt, so repair the row and post a notice. Only when `h` differs from the header hash is the FILE corrupt, in which case it moves to `quarantine/<name>.badhash` and `status='quarantined'`. A single flipped bit in a stored hash cannot quarantine a perfectly good document.

**Degraded evidence policy.** When a document is quarantined, `step_slice` rows naming it are excluded from the pack and the affected steps are set `evidence_state='degraded'`. The step still runs, its system prompt names the missing sections, and the UI flags it. If every section of a step is gone, `evidence_state='empty'` and the step refuses to run, offering refetch or re-slice. A step silently teaching from a thinner evidence base than its curriculum assumed is exactly the failure the grounding rule exists to prevent.

**The offset hazard.** `doc.file_bytes` and `doc.file_mtime_ns` are recorded at write time and checked on `open_track()`. Any mismatch triggers a re-scan of that file's `§` markers and a delta update of its `section` rows before a single slice is served. A mis-sliced section is a wrong citation, so the check is neither optional nor deferred.

**Retrieval.** A normal teaching turn does zero searching: the step's `step_slice` was pinned at curriculum generation. BM25 over `sec_fts` runs only when the student asks sideways, scored as `bm25 x vetting_weight (primary 1.00, secondary 0.92, vendor 0.85, community 0.78) x 1.5 when section.concept matches the step`, filled to 10 sections or 12,000 bytes, ties broken by `(doc_id, sec_id)` ascending so the same question yields the same pack. The step's pinned sections are always included first, at any score.

---

## F. CONCURRENCY AND CRASH SAFETY

Two clients, laptop and phone over the tailnet, reach one bridge process, so the common case is two threads. A second bridge launch is possible, so every guard is doubled.

**Mutual exclusion.** A module-level `threading.Lock` per `track_id` (an `flock` on a file the same process already holds does not block), then `fcntl.flock(LOCK_EX | LOCK_NB)` on `library.lock` in a 5-second poll loop. On timeout the request returns 409 naming the holder, never a silent wait. Long housekeeping work (VACUUM INTO, tar building, index rebuilds) runs outside every write transaction and drops the flock between units. A model call is never inside a transaction.

**Every write is a delta.** There is no endpoint anywhere that accepts a client's copy of the track and makes it the truth. Append a turn. Set one gap's status. Insert one card review. The append-only triggers back this up where application code cannot reach: `turn`, `assessment`, `card_review`, `gap_history` and `track_history` raise ABORT on DELETE and on any UPDATE outside the one narrow permitted case. The class of bug where a stale tab replaces a rich transcript with an old poor one is not detected by a heuristic, it is unrepresentable. And because nothing shrinks, backups stop being an undo mechanism and become disaster recovery only, which is what justifies 2 copies of one track instead of a snapshot history of everything.

**Sequence numbers are server-allocated.** A client never sends `seq`. Inside `BEGIN IMMEDIATE`, `turn.seq` comes from AUTOINCREMENT and ordering is by rowid, not by any clock. Both concurrent appends land, interleaved in arrival order. `client_turn_id UNIQUE` makes a retried POST over a flaky phone link a 200 no-op instead of a duplicate.

**Optimistic locking, narrowly.** Only genuinely editable objects carry `rev`: `gap` and `step`. `UPDATE gap SET ... , rev = rev + 1 WHERE gap_id = ? AND rev = ?`, and `rowcount == 0` returns 409 with the current row so the UI shows a diff. Appending a turn does not bump any `rev`, so chatting on the phone never produces a spurious conflict on a curriculum edit open on the laptop.

**Model call serialization and paid work.** The sequence for a teaching turn:

```
1. txn: UPSERT draft(step_id, client_label, body)          the user's typed text is durable first
2. txn: INSERT turn_pending(step_id, client_label, started_mono, request_sha16)
        A second client on the same step gets 409 busy naming the holder, with the option to
        take over after 180 s of monotonic time. Its draft is already saved.
3. txn: INSERT the user turn (seq allocated here). COMMIT. Release the write lock.
4. model call. No lock held, no transaction open.
5. write the raw response to spool/<n>.raw.txt, fsync, os.replace, fsync the dir
6. txn: INSERT the tutor turn with reply_to_seq = the user turn's seq, body truncated to
        8,192 with a marker and overflow_sha256 / overflow_bytes / spool_ref recorded;
        DELETE turn_pending; DELETE draft. COMMIT.
```

A cap refusal, a constraint violation or a crash after step 4 never destroys a completion that was already paid for: it is on disk in `spool/` and the turn row points at it. Spool files are swept at 7 days or beyond SPOOL_CAP.

**Atomic replace, for every file the app writes.**

```
tmp = "<target>.<pid>.<tid>.<counter>.part"    in the SAME directory
fd = os.open(tmp, O_WRONLY|O_CREAT|O_EXCL, 0o600); write; flush; os.fsync(fd); close
os.replace(tmp, target)
dfd = os.open(dirname, O_DIRECTORY); os.fsync(dfd); os.close(dfd)
```

The pid, thread id and counter in the temp name are load-bearing. With one shared `.tmp` path two concurrent writers interleave into one file, and the second `os.replace` raises after the first took the name, so one client is told its save failed while mixed bytes have landed. The directory fsync is what makes the rename itself durable rather than only the bytes.

**Corpus writes are two-phase, with both crash windows named.**

```
txn A: INSERT doc(..., status='writing', file_name=?)  COMMIT
       write corpus/.tmp/<name>.<pid>.<tid>.<n>.part, fsync, os.replace, fsync dir
txn B: UPDATE doc SET status='ready', file_sha256=?, file_bytes=?, file_mtime_ns=?
       + INSERT the section rows with byte_off/byte_len/origin_span     COMMIT
```

Crash before txn B leaves a `'writing'` row and possibly a file: housekeep deletes both and logs `orphan_swept`. Crash before the rename leaves a `.part`: swept at one hour. There is no window producing a half-registered document, and `doc_no` is never reused because it is allocated from `track_meta.next_doc_no` in txn A.

**kill -9 mid-write.** Inside a transaction, WAL replay discards it on next open. `synchronous=FULL` means an acknowledged turn survives a power cut, not merely a process kill, at the cost of one fsync per sub-kilobyte write, which is noise next to the model call that produced it. There is no torn-document failure mode anywhere, because there is no document being rewritten.

**Delete or archive while a chat is open.** The lease is what closes this. `open_track()` takes or refreshes a `track_lease` row and the bridge heartbeats every 30 seconds using `time.monotonic_ns`, so a wrong wall clock cannot make a live lease look dead or a dead one look live. Archive and delete refuse while a lease is live; the user is offered "close the session on phone", which bumps `track.generation`. Every write asserts its handle's generation against the row inside the same transaction and returns 409 `track_moved` on mismatch, with the client's draft preserved on both sides. No operation ever unlinks a directory whose database has an open descriptor, so the silent write-into-an-unlinked-inode failure cannot occur.

**Reconcile, at every startup and every housekeep.** Compare `set(os.listdir(tracks/))`, `set(archive/*.pwk)` and the registry rows.

```
directory with no row                -> move to quarantine/, notice, never delete
row 'active' with no directory       -> lifecycle='lost', offer restore from backup/
row 'archiving', both sides present  -> resume the archive from step 3
row 'archiving', only a verified .pwk-> finish: rmtree if needed, mark 'archived'
row 'restoring', both sides present  -> re-verify, finish
.pwk with no row                     -> read its meta.json, re-register as 'archived'
```

Reconcile is incremental: it stats directories and compares against `accounted_utc`, and it does not open a single track database. Boot cost is one `quick_check` on `library.db` plus a directory listing, so startup time does not grow with the library.

**Corrupt file on read.**

```
library.db  quick_check at boot. On failure: rename to quarantine/, restore the newest
            library.bak copy, then run a full reconcile, which reconstructs registry
            drift from the tracks themselves. The tracks are authoritative.
track.db    quick_check on open (single-digit ms at 1.8 MiB); full integrity_check before
            each backup rotation. On failure: MOVE db, -wal and -shm to
            quarantine/db-<seq>-<utc>/ (move, never delete), restore the newest
            backup/<seq>.track.db, verify, then show a blocking notice naming the backup's
            timestamp and the exact gap. Never a silent continuation.
row payload integrity_check validates b-tree structure, not payload bytes. turn.body_sha16
            and section.body_sha16 are written at insert and verified whenever the row
            enters a prompt or an export, except for a compacted tutor turn,
            whose row is rewritten to describe its now-empty body while
            turn_compaction keeps the original length and hash. 16 hex chars x 880 turns = 14 KB per track to
            detect the bitrot that integrity_check misses.
corpus      the two-place hash rule of section E.
index.db    any DatabaseError, integrity failure, or a docs_digest that disagrees with
            track.db deletes index.db* and rebuilds. It is derived, so this costs nothing.
```

**Backups.** `VACUUM INTO backup/<seq>.track.db` at track close, at every phase change, and at most once per 20 minutes while dirty. Named by a monotonic `track_meta.backup_seq`, never by a timestamp, and rotated by sequence, so a clock that steps backwards cannot cause rotation to unlink the newest copy. 2 copies for the leased track, 1 for other active tracks, 0 for stale and archived tracks (for an archived track the `.pwk` is a better artifact and it was verified member by member before the live directory was removed). Worst-case loss from disk-level corruption is 20 minutes of one track, and that number is shown in the storage panel rather than implied.

**Clock skew, handled once for everything that depends on time.** `meta.last_seen_utc` and a monotonic anchor are updated at every housekeep. If `now < last_seen_utc`, or `now` jumps more than 7 days while the monotonic clock says minutes passed, set `meta.clock_suspect = 1`, post one banner, and suspend every age-driven automatic transition: staleness compaction, pressure archiving, trash purging and card interval advancement. A card review submitted with `at_utc` earlier than the card's `last_review_utc` is recorded with `clock_suspect = 1` and does not advance `interval_days` or `ease`. Ordering inside a track is by `seq`, never by a timestamp, so a bad clock cannot reorder a transcript. This single mechanism covers the purge, the backup rotation, the staleness gate and the review schedule together.

---

## G. CROSS-TRACK ISOLATION

Eight layers, structural first. The first four make a leak require a code change rather than a mistake.

**1. Physical separation.** One database file and one corpus directory per track. No table in the system holds rows from two tracks, and `library.db` holds no content: no job text, no gaps, no turns, no document bodies, no card fronts. Reading track B's material while holding track A requires opening a second file. No shared corpus pool, no deduplication, no symlinks between tracks. A source distilled for three tracks is stored three times at 8,000 bytes each, costing 16,000 bytes of duplication. That waste is bought deliberately: a refcounted shared pool saves single-digit megabytes and introduces a collector, a refcount table, and a live pointer from one track's prompt into another track's bytes.

**2. One gate.** Exactly one function calls `sqlite3.connect` for a track: `open_track(track_id) -> TrackHandle`. It matches `^t-[0-9a-f]{12}$`, resolves with `os.path.realpath` and asserts `os.path.commonpath([TRACKS_ROOT, resolved]) == TRACKS_ROOT`, rejects a symlink at any component with `os.lstat`, compares directory name against the `TRACK_ID` file against `track_meta.track_id` three ways, takes the lease, records the generation, and returns a handle owning one connection, one corpus root fd and one `track_id`. There is no module-level connection, no connection cache keyed by anything else, no default track, and no fallback path. A fallback is how the wrong corpus ends up in the right prompt.

**3. Schema-level impossibility.** `step_slice` carries `FOREIGN KEY (doc_id, sec_id) REFERENCES section(doc_id, sec_id)` with `foreign_keys = ON`. Foreign keys cannot cross database files, so a curriculum step is structurally incapable of naming a section outside its own track, whatever the application code intends.

**4. Prompt assembly is handle-scoped and asserted.** `build_pack(handle, step_id)` and `build_prompt(handle, step_id)` are the only paths to corpus bytes and to transcript rows; there is deliberately no `search_all_corpora()`, no `load_section(doc_id, sec_id)` free function and no default scope, so building a prompt without a handle is a TypeError at the call site. Every retrieved section is tagged with the handle's `track_id` at slice time and `send(prompt, handle)` asserts `prompt.track_id == handle.track_id` and raises `IsolationError` rather than degrading. The pack is hashed and `pack_sha16` plus the citation list are written into the turn row, so "what was this answer grounded in" is answerable from the log alone.

**5. Output-side validation.** Every `D\d{2}§s\d{2}` token in a reply is checked against the exact pack that was sent. This is the same line of code that catches invention, so isolation cannot be removed without the grounding check disappearing with it.

**6. Documents self-identify.** Every corpus file header carries `track_id`, checked against the handle on every read. A file copied into the wrong directory by hand, or restored from the wrong archive, is refused rather than taught from. This is the layer that catches human error and restore bugs, which path validation cannot.

**7. The narrow world of a model call.** Teaching and assessment turns run with an empty tool set, no network, and `cwd` set to the track directory. `research.py` is the only module importing `urllib.request`, is never called from the teaching handler, and writes only into its own track's `corpus/`, `doc` rows and `index.db`. The only conversation history in a prompt is read from this handle's own `turn` table.

**8. Card review, the one cross-track surface, kept content-free.** Spaced repetition across a whole library is the point of the feature, so `due_card` in `library.db` is a derived projection carrying `(track_id, card_id, due_utc, retired, cold)` and no text at all. "What is due today" is one indexed query. Rendering a card opens exactly one track through `open_track()` (or reads exactly one `archive/<id>.cards.jsonl` through the same scoped accessor when `cold = 1`), and the header shows that track's title and employer, so the switch is visible. Grading is a delta write into that track's database plus a projection update; if the two disagree, the track wins and the projection rebuilds. If the user asks "why is that the answer", the UI switches into that track's scope and opens a single-track teaching turn. There is no path where two tracks' documents sit in one context window. The frozen `cite_snippet` on each card is what makes a cold card reviewable without unpacking an archive and without a shared content table.

**Intake boundary.** The external per-job folder is opened exactly once, at creation, copied into `intake/source.txt` and hashed. Prompt building never reads that folder, so the job folders beside it are never in scope, and a later change on its side cannot alter what this track was built from. A hash mismatch on a later manual re-import is surfaced as a question, never applied silently.

---

## WHAT I REJECTED, AND WHY

1. **A whole-document `state.json` per track, with a revision hash and a monotonic shrink-detector.** Revision agreement proves the client read the file and proves nothing about what it is carrying. The counter guard covers appends and leaves every edit (gap labels, curriculum order, session recaps, card fronts) open to a stale tab that increments one counter and rolls back an evening of work. Delta-only rows make the loss unrepresentable instead of heuristically detected.

2. **Rotating snapshot directories, per-track or global.** Snapshots existed to undo a whole-document clobber. With append-only rows plus `gap_history`, `turn_compaction`, `card_review` and `track_history`, undo is already served, so backups collapse to two disaster-recovery copies of one track. If append-only is ever relaxed, this decision must be revisited first.

3. **Whole-library backups on a timer.** Cost is O(library), the delta being protected is O(one hot track). At 30 tracks it is 63 percent of all bytes and roughly 44 GB of lifetime writes to protect a few hundred megabytes of work.

4. **A shared content-addressed blob store with refcounts.** Saves single-digit megabytes at this scale and buys a garbage collector, a reference table, a filesystem-versus-database reconciliation problem, and a live pointer from one track's prompt into another track's bytes.

5. **Storing raw fetched sources.** 48 documents at 412 KB each is 18.9 MiB per track, and primary-vetted sources are often PDFs that gzip by 2 to 5 percent. Quoted source spans cost 560 bytes per document and falsify every citation years later.

6. **A single library-wide FTS table with a `track_id` column.** That column becomes a WHERE clause, and a WHERE clause is a thing a future refactor can forget. Per-track `index.db` costs more files and cannot leak.

7. **Storing derived values only in the derived index.** Everything durable (`cite_count`, `last_cited_utc`, `section.concept`, byte offsets) lives in `track.db`. A rebuild of `index.db` must be able to cost nothing, and it cannot cost nothing if it silently zeroes the input to the eviction policy.

8. **Pressure-only reclamation.** Any threshold above the projected steady state never fires, which means every reclamation path is untested code that will first run in year nine. Age drives the ladder; pressure is a backstop.

9. **A background daemon or cron sweeper.** `housekeep()` runs at four named points: 2 seconds after the UI is served at startup, on track close, after every 200 writes to a track, and on an explicit click. A cadence that is not stated is a cap that is not enforced.

10. **Refusing writes at the turns cap by default.** It freezes the one track that matters at the worst possible moment. Automatic compaction of tutor prose in completed steps, with a receipt, is the bounded alternative. The refusal behaviour remains available behind `no_auto_compact`.

11. **Auto-demoting a document on a failed origin refetch.** Byte-identical refetch of a live page is essentially never true, and soft-404s, paywalls and interstitials return 200 with none of the quoted spans present. Automatic exclusion would let a network condition silently amputate a corpus. Verification produces an advisory flag; exclusion needs a click.

12. **Deleting quarantined files, corrupt database copies, or trashed archives without a cap.** Any never-deleted class is unbounded by definition. Each has a byte cap, an age purge, a line in the accounting, and a rung on the ladder.

13. **Keeping detached cards after a track is deleted.** Delete means delete, and the confirmation states the card count. The user who wants to keep reviewing is offered Archive, which keeps everything and leaves the cards reviewable through `archive/<id>.cards.jsonl`.

14. **Sorting anything durable by wall-clock time.** Transcript order is by `seq`, backup rotation is by `backup_seq`, quarantine directories carry a sequence prefix, lease liveness is monotonic. Wall clock is display metadata and a gate that suspends itself when it looks wrong.

15. **A migrations directory.** The launcher is meant to pin SHA-256 hashes of the runtime file set above; today it declares only `BRIDGE_SHA256` and `INDEX_SHA256`, both still empty, and says so at every start. The schema ladder is a list of DDL strings in `state.py` (`LIBRARY_DDL` and `TRACK_DDL`) with the version in `meta`.

16. **Cross-track full-text search.** It is the one feature whose implementation cuts directly across the isolation boundary, and nobody asked for it. Searching one track is an indexed query on that track's own index.