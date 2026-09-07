"""Housekeeping: recounts, caps, the eviction ladder, backups.

Implements DESIGN-state-corpus.md section D. Evicts in a published order and
tells the candidate what went and what it cost them.

Two properties make this safe to run automatically. The ladder is age-driven
rather than pressure-driven, so every rung actually fires at this library's
real scale instead of being untested code that first runs in year nine. And
every rung above the last is representation-only: it changes how bytes are
stored, never whether a decision survives.

There is no rung 10. When the ladder runs out, this module posts a notice
naming the largest tracks and waits for a human.
"""

import calendar
import json
import os
import shutil
import sqlite3
import tempfile
import time

from prepwright import config as C
from prepwright import state as S
from prepwright import track as T


# ---- clock -----------------------------------------------------------------
def clock_suspect(lib):
    """One gate for everything that depends on time.

    Staleness, pressure archiving, trash purging and card interval advancement
    all read this. A clock that has gone backwards, or jumped a week forward
    while the monotonic clock says minutes passed, suspends all four rather
    than letting each one guess separately.
    """
    row = lib.execute("SELECT v FROM meta WHERE k='last_seen_utc'").fetchone()
    anchor = lib.execute("SELECT v FROM meta WHERE k='last_seen_mono'").fetchone()
    now, now_mono = time.time(), S.mono_ns()
    suspect = False
    if row is not None:
        try:
            then = calendar.timegm(time.strptime(row["v"], "%Y-%m-%dT%H:%M:%SZ"))
            if now < then - 1:
                suspect = True                    # the clock went backwards
            elif now - then > C.CLOCK_JUMP_SUSPECT_DAYS * 86400:
                # A big wall-clock gap is only suspicious if the machine did not
                # actually spend that time running. Without this second half,
                # not opening the app for eight days over a holiday suspended
                # staleness, pressure archiving, trash purging and card
                # scheduling on the very run that should have caught up.
                elapsed_mono = None
                if anchor is not None:
                    try:
                        elapsed_mono = (now_mono - int(anchor["v"])) / 1e9
                    except (ValueError, TypeError):
                        elapsed_mono = None
                if elapsed_mono is not None and elapsed_mono >= 0:
                    suspect = (now - then) - elapsed_mono > C.CLOCK_JUMP_SUSPECT_DAYS * 86400
        except (ValueError, TypeError):
            suspect = True
    lib.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('last_seen_utc',?)",
                (S.utc_now(),))
    lib.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('last_seen_mono',?)",
                (str(now_mono),))
    lib.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('clock_suspect',?)",
                ("1" if suspect else "0",))
    return suspect


# ---- accounting ------------------------------------------------------------
def _dir_bytes(path):
    total = 0
    for base, _dirs, names in os.walk(path):
        for n in names:
            p = os.path.join(base, n)
            try:
                if not os.path.islink(p):
                    total += os.path.getsize(p)
            except OSError:
                pass
    return total


def recount(lib, track_id):
    """The authoritative recount, against the filesystem rather than a counter.

    The in-transaction counters are what caps are enforced against; this is what
    catches them drifting, which is a different job and belongs on a different
    cadence.
    """
    directory = os.path.join(C.TRACKS_ROOT, track_id)
    if not os.path.isdir(directory):
        return None
    def size(*parts):
        p = os.path.join(directory, *parts)
        try:
            return os.path.getsize(p) if os.path.isfile(p) else _dir_bytes(p) \
                if os.path.isdir(p) else 0
        except OSError:
            return 0
    fields = {
        "bytes_db": size("track.db"),
        "bytes_wal": size("track.db-wal"),
        "bytes_corpus": size("corpus"),
        "bytes_index": size("index.db"),
        "bytes_backup": size("backup"),
        "bytes_spool": size("spool"),
    }
    fields["bytes_total"] = sum(fields.values())
    n_turns = n_docs = n_cards = 0
    db = os.path.join(directory, "track.db")
    try:
        if not os.path.isfile(db):
            raise S.StoreError("no track.db to count")   # sqlite would create one
        conn = S.connect(db)
        try:
            n_turns = conn.execute("SELECT COUNT(*) c FROM turn").fetchone()["c"]
            n_docs = conn.execute("SELECT COUNT(*) c FROM doc").fetchone()["c"]
            n_cards = conn.execute("SELECT COUNT(*) c FROM card").fetchone()["c"]
        finally:
            conn.close()
    except (sqlite3.Error, S.StoreError):
        pass
    lib.execute(
        "UPDATE track SET bytes_db=?,bytes_wal=?,bytes_corpus=?,bytes_index=?,"
        " bytes_backup=?,bytes_spool=?,bytes_total=?,n_turns=?,n_docs=?,n_cards=?,"
        " accounted_utc=? WHERE track_id=?",
        (fields["bytes_db"], fields["bytes_wal"], fields["bytes_corpus"],
         fields["bytes_index"], fields["bytes_backup"], fields["bytes_spool"],
         fields["bytes_total"], n_turns, n_docs, n_cards, S.utc_now(), track_id))
    return fields


def library_bytes(lib):
    row = lib.execute("SELECT COALESCE(SUM(bytes_total),0) t FROM track").fetchone()
    return int(row["t"]) + _dir_bytes(C.LIBRARY_BAK) + \
        (os.path.getsize(C.LIBRARY_DB) if os.path.exists(C.LIBRARY_DB) else 0)


# ---- backups ---------------------------------------------------------------
def backup_track(track_id, lib=None, force=True):
    """VACUUM INTO backup/<seq>.track.db, named by a monotonic sequence.

    Never by a timestamp. A clock that steps backwards would otherwise make
    rotation unlink the newest copy, which is the one moment a backup exists
    for.
    """
    own = lib is None
    library = lib or S.open_library()
    try:
        directory = S.track_dir(track_id)
        if not os.path.isfile(os.path.join(directory, "track.db")):
            return None
        handle = S.open_track(track_id, lib=library, take_lease=False, touch=False)
        try:
            return handle.backup(force=force)
        finally:
            handle.close()
    finally:
        if own:
            library.close()


def all_backups(track_id):
    """Every backup for a track, newest first.

    Recovery walks this list. Taking only the newest made one bad newest backup
    enough to lose a track that had an intact older one sitting beside it.
    """
    backups = os.path.join(C.TRACKS_ROOT, track_id, "backup")
    try:
        names = sorted(n for n in os.listdir(backups) if n.endswith(".track.db"))
    except OSError:
        return []
    return [os.path.join(backups, n) for n in reversed(names)]


def newest_backup(track_id):
    candidates = all_backups(track_id)
    return candidates[0] if candidates else None


def _backup_is_sound(path):
    """Open a COPY of a backup and prove it reads before anything is overwritten.

    On a copy, not the original: an open can create -wal and -shm beside the
    file, and a backup directory is not a place to leave sidecars. Returns the
    turn count on success and None when the candidate cannot be trusted.
    """
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".verify-", suffix=".track.db",
                                   dir=os.path.dirname(path))
        os.close(fd)
        shutil.copy2(path, tmp)
        conn = S.connect(tmp)
        try:
            if not S.quick_check(conn):
                return None
            return int(conn.execute(
                "SELECT COUNT(*) c FROM turn").fetchone()["c"])
        finally:
            conn.close()
    except (sqlite3.Error, S.StoreError, OSError):
        return None
    finally:
        if tmp:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(tmp + suffix)
                except OSError:
                    pass


# ---- corruption ------------------------------------------------------------
def recover_track(track_id, lib=None):
    """A corrupt track.db is moved aside, never deleted, then restored.

    The candidate is told which backup was restored, how many turns it holds,
    and which newer backups were rejected as unreadable. Not the gap: the live
    file is corrupt, which is why recovery is running, so counting the turns it
    held is exactly the query that cannot be trusted. The quarantined copy is
    kept so the gap can be established by hand later. Saying "the exact gap"
    here promised an accounting this function has no sound way to produce.

    A silent continuation from a backup is how a person discovers three weeks
    later that an evening is missing, so the record names what was lost track of
    rather than implying nothing was.
    """
    own = lib is None
    library = lib or S.open_library()
    try:
        directory = S.track_dir(track_id)
        db = os.path.join(directory, "track.db")
        backup = newest_backup(track_id)
        quarantine = S.ensure_dir(os.path.join(directory, "quarantine"))

        # Prove there is somewhere to land BEFORE moving the live files. Moving
        # first and discovering there is no backup empties the directory of the
        # only copy of the data, and leaves a track marked lost whose database
        # was fine except for one bad page.
        if backup is None:
            library.execute("UPDATE track SET lifecycle='lost' WHERE track_id=?",
                            (track_id,))
            S.library_event(library, "corrupt_no_backup",
                            "%s is corrupt and has no backup" % track_id,
                            track_id=track_id)
            raise S.CorruptStore(
                "track.db for %s is corrupt and no backup survived. The file was"
                " left exactly where it is, in %s, and nothing was deleted."
                % (track_id, directory))

        # Choose a backup that PROVES it reads, before the live file is touched.
        # The old order copied the newest over track.db and only then ran
        # quick_check, so a corrupt newest backup destroyed the live path and
        # every later open re-entered recovery and quarantined one more copy.
        chosen, n_turns, rejected = None, 0, []
        for candidate in all_backups(track_id):
            count = _backup_is_sound(candidate)
            if count is None:
                rejected.append(os.path.basename(candidate))
                continue
            chosen, n_turns = candidate, count
            break
        if chosen is None:
            library.execute("UPDATE track SET lifecycle='lost' WHERE track_id=?",
                            (track_id,))
            S.library_event(library, "corrupt_no_backup",
                            "%s is corrupt and no backup verified (%d tried)"
                            % (track_id, len(rejected)), track_id=track_id)
            raise S.CorruptStore(
                "track.db for %s is corrupt and none of its %d backup(s) could be"
                " read either. The file was left exactly where it is, in %s, and"
                " nothing was deleted or moved."
                % (track_id, len(rejected), directory))
        backup = chosen

        # One directory per recovery, created exclusively. A second-granularity
        # name plus shutil.move silently REPLACED an earlier quarantine, so two
        # recoveries in the same second destroyed the only copy of the damaged
        # original. os.makedirs without exist_ok is the refusal.
        for attempt in range(1, 1000):
            slot = os.path.join(quarantine, "db-%04d-%s" % (
                attempt, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())))
            try:
                os.makedirs(slot, C.DIR_MODE)
                break
            except FileExistsError:
                continue
        else:
            raise S.CorruptStore(
                "%s has too many quarantined copies to add another" % track_id)

        moved = []
        # The sidecars are named off the DATABASE's destination, so SQLite still
        # pairs them: it only ever looks for "<db path>-wal". Naming the main
        # file db.db and its log db-wal orphaned the log that held exactly the
        # turns recovery exists to account for.
        dest_db = os.path.join(slot, "track.db")
        for suffix in ("", "-wal", "-shm"):
            src = db + suffix
            if os.path.exists(src):
                shutil.move(src, dest_db + suffix)
                moved.append(dest_db + suffix)

        shutil.copy2(backup, db)
        os.chmod(db, C.FILE_MODE)
        conn = S.connect(db)
        try:
            conn.execute(
                "INSERT INTO recovery_log(at_utc,kind,detail) VALUES (?,?,?)",
                (S.utc_now(), "restored_from_backup",
                 "restored %s; %d turns present; %d newer backup(s) rejected as"
                 " unreadable: %s"
                 % (os.path.basename(backup), n_turns, len(rejected),
                    ", ".join(rejected) or "none")))
        finally:
            conn.close()
        S.library_history(library, track_id, "recovered",
                          "restored from %s" % os.path.basename(backup))
        S.library_event(library, "corrupt_recovered",
                        "restored %s from %s" % (track_id, os.path.basename(backup)),
                        track_id=track_id)
        return {"restored_from": backup, "quarantined": moved, "turns": n_turns}
    finally:
        if own:
            library.close()


def open_track_or_recover(track_id, lib=None, client_label="laptop"):
    """Open a track, and repair it in the open path rather than at some later
    sweep, because the moment a corrupt file is discovered is the moment the
    candidate is waiting on it."""
    try:
        return S.open_track(track_id, lib=lib, client_label=client_label)
    except S.CorruptStore:
        recover_track(track_id, lib=lib)
        return S.open_track(track_id, lib=lib, client_label=client_label)


# ---- the ladder ------------------------------------------------------------
def housekeep(lib=None, now=None):
    """Run the published ladder in order and return what each rung freed.

    Rungs 0 to 5 lose nothing. Rung 6 is the first lossy step and it is the
    right one: the tutor's prose is regenerable from the same cited sections,
    step.review already holds the conclusion, and a student turn is never
    touched. Rung 9 stops and asks for a human.
    """
    own = lib is None
    library = lib or S.open_library()
    report = {"rungs": [], "freed": 0, "notices": [], "clock_suspect": False}
    try:
        suspect = clock_suspect(library)
        report["clock_suspect"] = suspect
        if suspect:
            report["notices"].append(
                "The system clock looks wrong, so every age-driven step is"
                " suspended: staleness compaction, pressure archiving, trash"
                " purging and card scheduling.")

        # rung 0 -- reconcile, both directions, no loss
        notes = T.reconcile(lib=library)
        report["rungs"].append({"rung": 0, "what": "reconcile", "detail": notes})

        # Recount FIRST. Every pressure decision below reads library_bytes(),
        # which sums track.bytes_total, and that column is written only by
        # recount. Refreshing it at the end meant a library could be far past
        # its soft cap while rungs 6, 7 and 9 all read zero and did nothing.
        for row in library.execute("SELECT track_id FROM track").fetchall():
            recount(library, row["track_id"])
        rows = list(library.execute("SELECT * FROM track"))
        leased = {r["track_id"] for r in library.execute("SELECT track_id FROM track_lease")
                  if S.live_lease(library, r["track_id"])}

        # rung 1 -- sweep spool, part files and half-registered documents, no loss
        freed, orphans = 0, []
        for row in rows:
            d = os.path.join(C.TRACKS_ROOT, row["track_id"])
            freed += _sweep(os.path.join(d, "spool"), C.SPOOL_AGE_DAYS * 86400)
            # atomic_write names its temp file beside the target, inside
            # corpus/ itself, so an interrupted document write leaves a .part
            # there and never in a .tmp subdirectory that nothing creates.
            freed += _sweep(os.path.join(d, "corpus"),
                            C.PART_FILE_AGE_SECONDS, suffix=".part")
            if row["track_id"] not in leased and row["lifecycle"] == "active":
                gone, bytes_gone = _sweep_writing_docs(library, row["track_id"])
                freed += bytes_gone
                orphans.extend(gone)
        report["rungs"].append({"rung": 1, "what": "sweep spool, .part and orphans",
                                "freed": freed, "orphans": orphans})
        report["freed"] += freed

        # rung 2 -- checkpoint the WAL of every unleased track, no loss
        n = 0
        for row in rows:
            if row["track_id"] in leased or row["lifecycle"] != "active":
                continue
            db = os.path.join(C.TRACKS_ROOT, row["track_id"], "track.db")
            if not os.path.isfile(db):
                continue
            try:
                conn = S.connect(db)
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    n += 1
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
        report["rungs"].append({"rung": 2, "what": "wal checkpoint", "tracks": n})

        # rung 3 -- drop the derived index of unleased tracks, rebuildable
        freed = 0
        for row in rows:
            if row["track_id"] in leased:
                continue
            idx = os.path.join(C.TRACKS_ROOT, row["track_id"], "index.db")
            if os.path.isfile(idx):
                freed += os.path.getsize(idx)
                os.remove(idx)
        report["rungs"].append({"rung": 3, "what": "drop derived index", "freed": freed})
        report["freed"] += freed

        # rungs 4 and 5 -- stale tracks only, and only if the clock is sane
        freed = 0
        vacuumed = 0
        if not suspect:
            for row in rows:
                if not T.is_stale(row) or row["track_id"] in leased:
                    continue
                backups = os.path.join(C.TRACKS_ROOT, row["track_id"], "backup")
                if os.path.isdir(backups):
                    for name in os.listdir(backups):
                        p = os.path.join(backups, name)
                        if os.path.isfile(p):
                            freed += os.path.getsize(p)
                            os.remove(p)
                db = os.path.join(C.TRACKS_ROOT, row["track_id"], "track.db")
                if os.path.isfile(db):
                    try:
                        conn = S.connect(db)
                        try:
                            conn.execute("PRAGMA incremental_vacuum")
                            vacuumed += 1
                        finally:
                            conn.close()
                    except sqlite3.Error:
                        pass
        report["rungs"].append({"rung": 4, "what": "drop stale backups", "freed": freed})
        report["rungs"].append({"rung": 5, "what": "incremental vacuum",
                                "tracks": vacuumed})
        report["freed"] += freed

        # rung 6 -- FIRST LOSSY RUNG, and only under real pressure
        compacted = []
        if not suspect and library_bytes(library) > C.LIBRARY_SOFT:
            for row in rows:
                if (not T.is_stale(row) or row["no_auto_compact"]
                        or row["track_id"] in leased):
                    continue
                compacted.extend(_compact_done_steps(library, row["track_id"]))
        report["rungs"].append({"rung": 6, "what": "compact tutor prose in done steps",
                                "turns": compacted})
        if compacted:
            report["notices"].append(
                "Compacted the tutor's prose in %d completed step(s) of tracks left"
                " idle for %d days. Your own words, every score, every card and"
                " every citation are untouched, and each compaction wrote a receipt"
                " with the original length and hash."
                % (len(compacted), C.STALE_AFTER_DAYS))

        # rung 7 -- pressure archive, representation only
        archived = []
        if not suspect and library_bytes(library) > C.LIBRARY_SOFT:
            for row in sorted(rows, key=lambda r: r["opened_utc"]):
                if library_bytes(library) <= C.LIBRARY_SOFT - C.LIBRARY_HYSTERESIS:
                    break
                if row["lifecycle"] != "active" or T.protected(row):
                    continue
                if row["track_id"] in leased:
                    continue
                if T._age_days(row["opened_utc"]) < C.PRESSURE_ARCHIVE_DAYS:
                    continue
                try:
                    T.archive_track(row["track_id"], lib=library)
                    archived.append(row["track_id"])
                except (T.LifecycleError, S.StoreError):
                    pass
        report["rungs"].append({"rung": 7, "what": "pressure archive",
                                "tracks": archived})

        # rung 8 -- purge trash the candidate already deleted
        purged, early = [], []
        if not suspect:
            purged, early = _purge_trash()
        report["rungs"].append({"rung": 8, "what": "purge trash",
                                "files": purged, "early": early})
        if early:
            report["notices"].append(
                "The trash was over its cap, so %d deleted track(s) were purged "
                "before their %d-day undo window was up: %s. Nothing still inside "
                "its window was touched beyond what it took to get back under the "
                "cap." % (len(early), C.TRASH_UNDO_DAYS,
                          "; ".join("%s (%.1f days left)"
                                    % (e["summary"] or e["track_id"], e["days_left"])
                                    for e in early)))

        # rung 9 -- stop
        if library_bytes(library) > C.LIBRARY_SOFT:
            biggest = library.execute(
                "SELECT track_id, title, bytes_total, opened_utc FROM track"
                " ORDER BY bytes_total DESC LIMIT 10").fetchall()
            report["notices"].append(
                "The library is past its soft cap and the ladder has run out. The"
                " largest tracks are: "
                + "; ".join("%s (%s, %d bytes, last opened %s)"
                            % (r["title"], r["track_id"], r["bytes_total"],
                               r["opened_utc"]) for r in biggest))
        report["rungs"].append({"rung": 9, "what": "stop and ask"})

        for row in rows:
            recount(library, row["track_id"])
        _prune_events(library)
        for note in report["notices"]:
            S.library_event(library, "notice", note)
        return report
    finally:
        if own:
            library.close()


def _sweep(directory, older_than_seconds, suffix=None):
    freed, now = 0, time.time()
    if not os.path.isdir(directory):
        return 0
    for name in os.listdir(directory):
        if suffix and not name.endswith(suffix):
            continue
        p = os.path.join(directory, name)
        try:
            if os.path.isfile(p) and now - os.path.getmtime(p) > older_than_seconds:
                freed += os.path.getsize(p)
                os.remove(p)
        except OSError:
            pass
    return freed


def _sweep_writing_docs(lib, track_id):
    """Delete documents that never finished registering, and say so.

    A doc row sits in 'writing' for the milliseconds between the two
    transactions of write_doc, so this is keyed on age rather than on status
    alone: another process may be inside that window right now. One hour is the
    threshold the design gives the .part files, for the same reason.

    doc_no is not reclaimed. It comes from track_meta.next_doc_no and was
    already consumed in txn A, so a citation token can never be reused for a
    different document, which is worth more than the two digits.
    """
    swept, freed = [], 0
    db = os.path.join(C.TRACKS_ROOT, track_id, "track.db")
    if not os.path.isfile(db):
        return swept, freed
    try:
        handle = S.open_track(track_id, lib=lib, take_lease=False, touch=False)
    except (S.StoreError, sqlite3.Error):
        return swept, freed
    try:
        cutoff = time.time() - C.PART_FILE_AGE_SECONDS
        rows = handle.conn.execute(
            "SELECT doc_id, file_name, fetched_utc FROM doc WHERE status='writing'"
        ).fetchall()
        for row in rows:
            try:
                # calendar.timegm, not mktime: mktime reads the struct as
                # LOCAL time and applies the CURRENT DST offset, while
                # time.timezone is the non-DST offset, so the pair is an hour
                # early through every summer. An hour early here means a
                # document still being written looks old enough to sweep.
                # track.py's own docstring says exactly this; this was the one
                # site in the package that did not follow it.
                born = calendar.timegm(time.strptime(
                    row["fetched_utc"], "%Y-%m-%dT%H:%M:%SZ"))
            except (ValueError, TypeError):
                born = 0
            if born > cutoff:
                continue                      # still inside the write window
            path = handle.corpus_path(row["file_name"])
            if path:
                try:
                    freed += os.path.getsize(path)
                    os.remove(path)
                except OSError:
                    pass
            with handle.lock, S._Txn(handle.conn):
                handle.conn.execute("DELETE FROM doc WHERE doc_id=?", (row["doc_id"],))
                handle.conn.execute(
                    "INSERT INTO recovery_log(at_utc,kind,detail) VALUES (?,?,?)",
                    (S.utc_now(), "orphan_swept",
                     "%s never finished registering; its file and row are gone,"
                     " and its number is not reused" % row["doc_id"]))
            swept.append("%s/%s" % (track_id, row["doc_id"]))
            S.library_event(lib, "orphan_swept",
                            "swept half-registered %s" % row["doc_id"],
                            track_id=track_id, bytes_freed=freed)
    finally:
        handle.close()
    return swept, freed


def _compact_done_steps(lib, track_id):
    out = []
    db = os.path.join(C.TRACKS_ROOT, track_id, "track.db")
    if not os.path.isfile(db):
        return out
    handle = S.open_track(track_id, lib=lib, take_lease=False, touch=False)
    try:
        rows = handle.conn.execute(
            "SELECT t.seq FROM turn t JOIN step s ON s.step_id = t.step_id"
            " WHERE s.status='done' AND s.review IS NOT NULL AND s.review <> ''"
            "   AND t.role='tutor' AND t.body <> ''").fetchall()
        for r in rows:
            try:
                handle.compact_turn(int(r["seq"]), reason="stale")
                out.append(int(r["seq"]))
            except S.StoreError:
                pass
    finally:
        handle.close()
    return out


TRASH_SUFFIXES = (".pwk", ".pwk.sha256", ".cards.jsonl", ".meta.json",
                  ".purge.json")


def _purge_trash():
    """Unlink what the candidate already deleted, once its undo window is up.

    Two rules, and the second is the one that has teeth. Past the window, an
    entry goes. Inside the window it goes only under real pressure, oldest
    first, and only until the trash is back under its cap: an early purge takes
    back an undo the candidate was promised, so it stops the moment it has
    made room rather than emptying the directory.

    The cap is measured against the trash entries themselves, never against the
    directory. A stray file dropped in here would otherwise count toward the
    cap and force real deletions to pay for it.

    Returns (purged, early), where early is what went before its window was up
    and is what housekeep tells the candidate about.
    """
    purged, early, now = [], [], time.time()
    if not os.path.isdir(C.TRASH_ROOT):
        return purged, early

    def entry_bytes(track_id):
        total = 0
        for suffix in TRASH_SUFFIXES:
            f = os.path.join(C.TRASH_ROOT, track_id + suffix)
            try:
                if os.path.isfile(f):
                    total += os.path.getsize(f)
            except OSError:
                pass
        return total

    entries = []
    for name in sorted(os.listdir(C.TRASH_ROOT)):
        if not name.endswith(".purge.json"):
            continue
        try:
            with open(os.path.join(C.TRASH_ROOT, name), "r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        track_id = name[:-len(".purge.json")]
        entries.append((meta.get("purge_after", 0), track_id, meta,
                        entry_bytes(track_id)))
    entries.sort()
    total = sum(e[3] for e in entries)

    def unlink(track_id):
        for suffix in TRASH_SUFFIXES:
            f = os.path.join(C.TRASH_ROOT, track_id + suffix)
            if os.path.isfile(f):
                os.remove(f)

    remaining = []
    for purge_after, track_id, meta, size in entries:
        if now > purge_after:
            unlink(track_id)
            purged.append(track_id)
            total -= size
        else:
            remaining.append((purge_after, track_id, meta, size))
    for purge_after, track_id, meta, size in remaining:   # oldest first already
        if total <= C.TRASH_CAP:
            break
        unlink(track_id)
        purged.append(track_id)
        early.append({"track_id": track_id, "bytes": size,
                      "summary": meta.get("summary", ""),
                      "days_left": max(0.0, (purge_after - now) / 86400.0)})
        total -= size
    return purged, early


def _prune_events(lib):
    row = lib.execute("SELECT COUNT(*) c FROM event").fetchone()
    if int(row["c"]) <= C.EVENT_ROWS:
        return 0
    cut = lib.execute(
        "SELECT seq FROM event ORDER BY seq DESC LIMIT 1 OFFSET ?",
        (C.EVENT_ROWS,)).fetchone()
    if cut is None:
        return 0
    lib.execute("DELETE FROM event WHERE seq <= ?", (cut["seq"],))
    return 1
