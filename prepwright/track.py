"""Track lifecycle: create, archive, restore, trash, purge.

Implements DESIGN-state-corpus.md section C. Every transition is either the
candidate's explicit choice or an age rule they were told about in advance.
Nothing is ever deleted as a side effect of something else.

One rule governs every automatic transition in this module: it may change how
bytes are represented. It may never change whether a decision survives. Nothing
in the automatic set removes a gap, a curriculum step, a score, a citation, a
card, a corpus document or a single student turn.
"""

import calendar
import io
import json
import os
import secrets
import shutil
import tarfile
import time

from prepwright import config as C
from prepwright import state as S


class LifecycleError(S.StoreError):
    """A transition that would lose a decision, or that the candidate must make."""


class LeaseHeld(LifecycleError):
    """The track is open somewhere. Archive and delete both refuse."""


def new_track_id():
    return "t-" + secrets.token_hex(6)


def _short_code(lib):
    """Four characters the candidate types to confirm a delete.

    Deliberately not the track id: a code short enough to retype is a code long
    enough to be typed by accident, so it is checked against a dialog that has
    already named what is about to be lost.
    """
    while True:
        code = secrets.token_hex(2)
        if lib.execute("SELECT 1 FROM track WHERE short_code=?",
                       (code,)).fetchone() is None:
            return code


def create_track(title, kind="job", source_kind="pasted", employer=None,
                 role_title=None, source_path=None, source_sha256=None,
                 lib=None):
    """mkdir, TRACK_ID, track.db through the schema ladder, registry row.

    The directory is written before the registry row, and reconcile treats a
    directory with no row as something to quarantine rather than delete, so a
    crash between the two costs a notice and never a track.
    """
    if kind not in C.TRACK_KINDS:
        raise ValueError("unknown track kind %r" % (kind,))
    if source_kind not in C.SOURCE_KINDS:
        raise ValueError("unknown source kind %r" % (source_kind,))
    S.ensure_home()
    own = lib is None
    library = lib or S.open_library()
    try:
        track_id = new_track_id()
        directory = S.track_dir(track_id)
        S.ensure_dir(directory)
        S.ensure_dir(os.path.join(directory, "corpus"))
        S.ensure_dir(os.path.join(directory, "intake"))
        S.ensure_dir(os.path.join(directory, "spool"))
        S.ensure_dir(os.path.join(directory, "backup"))
        S.ensure_dir(os.path.join(directory, "quarantine"))
        S.atomic_write(os.path.join(directory, "TRACK_ID"), track_id + "\n")

        conn = S.connect(os.path.join(directory, "track.db"))
        try:
            conn.executescript(S.TRACK_DDL)
            conn.execute(
                "INSERT INTO track_meta(track_id,schema_version,label,created_utc)"
                " VALUES (?,?,?,?)",
                (track_id, C.TRACK_SCHEMA_VERSION, title, S.utc_now()))
        finally:
            conn.close()

        now = S.utc_now()
        library.execute(
            # No `phase`. It was written here once and never read or
            # updated, so it said 'intake' for the life of every track. The
            # stage comes from `bridge.flow_state`, which derives it from the
            # data, and one source of truth is the whole point.
            "INSERT INTO track(track_id,short_code,title,employer,role_title,kind,"
            " source_kind,source_path,source_sha256,lifecycle,created_utc,"
            " opened_utc,touched_utc,accounted_utc)"
            " VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,?,?)",
            (track_id, _short_code(library), title, employer, role_title, kind,
             source_kind, source_path, source_sha256, now, now, now, now))
        S.library_history(library, track_id, "created", title)
        return track_id
    finally:
        if own:
            library.close()


def get(lib, track_id):
    row = lib.execute("SELECT * FROM track WHERE track_id=?", (track_id,)).fetchone()
    if row is None:
        raise LifecycleError("no track %s" % track_id)
    return row


def _age_days(utc_string):
    """Days since a UTC stamp, measured in UTC.

    calendar.timegm is the exact inverse of the time.gmtime that utc_now writes.
    time.mktime reads the same struct as LOCAL time, so using it puts every age
    out by the UTC offset, and correcting it on the wrong side doubles the
    error: in Sydney a track went stale nearly a day before the published
    twenty-one, and west of Greenwich nearly a day after it. This function is
    the clock behind is_stale and behind the pressure-archive age, so both were
    firing on a rule the candidate was never told.
    """
    try:
        then = calendar.timegm(time.strptime(utc_string, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return 0.0
    return max(0.0, (time.time() - then) / 86400.0)


def is_stale(row):
    """Derived, never stored, so there is no transition to get wrong.

    A pinned track, and one whose outcome says an interview is live, never goes
    stale however long it sits.
    """
    if row["lifecycle"] != "active" or row["pinned"]:
        return False
    if row["outcome"] in C.PROTECTED_OUTCOMES:
        return False
    return _age_days(row["opened_utc"]) >= C.STALE_AFTER_DAYS


def protected(row):
    return bool(row["pinned"]) or row["outcome"] in C.PROTECTED_OUTCOMES


def bump_generation(lib, track_id, reason):
    """Invalidate every open handle on this track, and say why.

    Every write asserts its handle's generation inside the same transaction, so
    a handle taken before an archive or a forced close is refused rather than
    writing into a directory that is being replaced underneath it.
    """
    lib.execute("UPDATE track SET generation = generation + 1 WHERE track_id=?",
                (track_id,))
    S.library_history(lib, track_id, "generation", reason)
    return int(get(lib, track_id)["generation"])


def close_session(lib, track_id, reason="closed by the candidate"):
    """Take the lease back. The only way to free a track another client holds."""
    S.release_lease(lib, track_id)
    return bump_generation(lib, track_id, reason)


# ---- archive ---------------------------------------------------------------
def archive_track(track_id, lib=None, force=False):
    """Two-phase, and it verifies the archive member by member before deleting.

    Step 6 re-opens the tar and re-reads every member against the manifest. Any
    mismatch aborts with the live directory untouched, because an archive that
    was never verified is a deletion with extra steps.
    """
    own = lib is None
    library = lib or S.open_library()
    try:
        row = get(library, track_id)
        if row["lifecycle"] not in ("active", "archiving"):
            raise LifecycleError("track %s is %s" % (track_id, row["lifecycle"]))
        if S.live_lease(library, track_id) is not None and not force:
            raise LeaseHeld(
                "track %s is open; close the session on that client first" % track_id)
        if protected(row) and not force:
            raise LifecycleError(
                "track %s is pinned or has a live outcome; archiving it is a"
                " decision only the candidate makes" % track_id)

        library.execute(
            "UPDATE track SET lifecycle='archiving', generation=generation+1"
            " WHERE track_id=?", (track_id,))
        directory = S.track_dir(track_id)
        S.ensure_dir(C.ARCHIVE_ROOT)

        conn = S.connect(os.path.join(directory, "track.db"))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            vac = os.path.join(directory, "archive-copy.db")
            if os.path.exists(vac):
                os.remove(vac)
            conn.execute("VACUUM INTO ?", (vac,))
        finally:
            conn.close()

        members, manifest = [], []
        members.append((vac, "track.db"))
        # quarantine travels with the track. Two other paths promise never to
        # delete from it, and the rmtree below would otherwise break both the
        # moment a stale track is pressure-archived.
        for sub in ("corpus", "intake", "quarantine"):
            base = os.path.join(directory, sub)
            for name in sorted(os.listdir(base)) if os.path.isdir(base) else []:
                full = os.path.join(base, name)
                if os.path.isfile(full) and not os.path.islink(full):
                    members.append((full, "%s/%s" % (sub, name)))
        for full, arc in members:
            with open(full, "rb") as fh:
                data = fh.read()
            manifest.append({"path": arc, "size": len(data),
                             "sha256": S.sha256_hex(data)})

        pwk = os.path.join(C.ARCHIVE_ROOT, "%s.pwk" % track_id)
        tmp = pwk + ".tmp"
        with tarfile.open(tmp, "w:xz") as tar:
            for full, arc in members:
                tar.add(full, arcname=arc)
            man = json.dumps({"track_id": track_id, "members": manifest},
                             indent=1).encode("utf-8")
            info = tarfile.TarInfo("MANIFEST.json")
            info.size = len(man)
            tar.addfile(info, io.BytesIO(man))
        os.replace(tmp, pwk)
        os.chmod(pwk, C.FILE_MODE)
        with open(pwk, "rb") as fh:
            pwk_hash = S.sha256_hex(fh.read())
        S.atomic_write(pwk + ".sha256", pwk_hash + "\n")

        # verify before deleting anything
        with tarfile.open(pwk, "r:xz") as tar:
            found = json.loads(tar.extractfile("MANIFEST.json").read().decode())
            by_path = {m["path"]: m for m in found["members"]}
            for m in manifest:
                got = tar.extractfile(m["path"])
                if got is None:
                    raise LifecycleError("archive is missing %s" % m["path"])
                if S.sha256_hex(got.read()) != by_path[m["path"]]["sha256"]:
                    raise LifecycleError("archive member %s does not verify" % m["path"])

        cards = library.execute(
            "SELECT card_id, due_utc, retired FROM due_card WHERE track_id=?",
            (track_id,)).fetchall()
        S.atomic_write(
            os.path.join(C.ARCHIVE_ROOT, "%s.cards.jsonl" % track_id),
            "".join(json.dumps(dict(c)) + "\n" for c in cards))
        S.atomic_write(
            os.path.join(C.ARCHIVE_ROOT, "%s.meta.json" % track_id),
            json.dumps({k: row[k] for k in row.keys()}, indent=1))

        shutil.rmtree(directory)
        size = os.path.getsize(pwk)
        library.execute(
            "UPDATE track SET lifecycle='archived', bytes_archive=?, bytes_db=0,"
            " bytes_wal=0, bytes_corpus=0, bytes_index=0, bytes_backup=0,"
            " bytes_spool=0, bytes_total=?, accounted_utc=? WHERE track_id=?",
            (size, size, S.utc_now(), track_id))
        S.library_history(library, track_id, "archived", "%d bytes" % size)
        S.library_event(library, "archive", "archived %s" % track_id,
                        track_id=track_id)
        return pwk
    finally:
        if own:
            library.close()


def restore_track(track_id, lib=None):
    """Extract, verify every member, quick_check, three-way check the id."""
    own = lib is None
    library = lib or S.open_library()
    try:
        row = get(library, track_id)
        if row["lifecycle"] != "archived":
            raise LifecycleError("track %s is %s" % (track_id, row["lifecycle"]))
        pwk = os.path.join(C.ARCHIVE_ROOT, "%s.pwk" % track_id)
        if not os.path.isfile(pwk):
            library.execute("UPDATE track SET lifecycle='lost' WHERE track_id=?",
                            (track_id,))
            raise LifecycleError("archive for %s is missing" % track_id)
        library.execute("UPDATE track SET lifecycle='restoring' WHERE track_id=?",
                        (track_id,))
        directory = S.track_dir(track_id)
        S.ensure_dir(directory)
        with tarfile.open(pwk, "r:xz") as tar:
            manifest = json.loads(tar.extractfile("MANIFEST.json").read().decode())
            by_path = {m["path"]: m for m in manifest["members"]}
            for m in manifest["members"]:
                arc = m["path"]
                if arc.startswith("/") or ".." in arc.split("/"):
                    raise LifecycleError("archive member escapes: %s" % arc)
                data = tar.extractfile(arc).read()
                if S.sha256_hex(data) != by_path[arc]["sha256"]:
                    raise LifecycleError("member %s does not verify" % arc)
                dest = os.path.join(directory, arc)
                S.ensure_dir(os.path.dirname(dest))
                S.atomic_write(dest, data)
        S.atomic_write(os.path.join(directory, "TRACK_ID"), track_id + "\n")
        for sub in ("corpus", "intake", "spool", "backup", "quarantine"):
            S.ensure_dir(os.path.join(directory, sub))

        conn = S.connect(os.path.join(directory, "track.db"))
        try:
            if not S.quick_check(conn):
                raise S.CorruptStore("restored track.db for %s fails quick_check"
                                     % track_id)
            stamped = conn.execute("SELECT track_id FROM track_meta").fetchone()
            if stamped is None or stamped["track_id"] != track_id:
                raise S.IsolationError("restored track.db claims another track id")
        finally:
            conn.close()

        os.remove(pwk)
        for extra in (pwk + ".sha256",):
            if os.path.exists(extra):
                os.remove(extra)
        library.execute(
            "UPDATE track SET lifecycle='active', generation=generation+1,"
            " opened_utc=?, bytes_archive=0 WHERE track_id=?",
            (S.utc_now(), track_id))
        S.library_history(library, track_id, "restored", "from archive")
        return directory
    finally:
        if own:
            library.close()


# ---- delete ----------------------------------------------------------------
def trash_track(track_id, confirm_code, lib=None):
    """NEVER automatic, and never under any pressure.

    The caller must present the track's four-character short_code, which the UI
    shows only after a dialog stating the exact loss: title, employer, turn
    count, document count, card count, first and last activity, bytes.
    track_history rows survive forever, so the candidate's record of what they
    prepared for is not part of what gets deleted.
    """
    own = lib is None
    library = lib or S.open_library()
    try:
        row = get(library, track_id)
        if str(confirm_code).lower() != row["short_code"]:
            raise LifecycleError(
                "confirmation code does not match; nothing was deleted")
        if S.live_lease(library, track_id) is not None:
            raise LeaseHeld(
                "track %s is open; close the session on that client first" % track_id)

        S.ensure_dir(C.TRASH_ROOT)
        pwk = os.path.join(C.ARCHIVE_ROOT, "%s.pwk" % track_id)
        if row["lifecycle"] == "active":
            pwk = archive_track(track_id, lib=library, force=True)
        elif row["lifecycle"] != "archived" or not os.path.isfile(pwk):
            # Only 'active' and 'archived' have something to move. A 'lost'
            # track has no archive by definition, and an interrupted one may
            # never have written it, so the delete would raise from os.replace
            # with the row still saying the track exists.
            raise LifecycleError(
                "track %s is %s and has no archive to move to the trash; run"
                " housekeeping to reconcile it first" % (track_id, row["lifecycle"]))
        dest = os.path.join(C.TRASH_ROOT, os.path.basename(pwk))
        os.replace(pwk, dest)
        for suffix in (".sha256",):
            src = pwk + suffix
            if os.path.exists(src):
                os.replace(src, dest + suffix)
        for suffix in (".cards.jsonl", ".meta.json"):
            src = os.path.join(C.ARCHIVE_ROOT, track_id + suffix)
            if os.path.exists(src):
                os.replace(src, os.path.join(C.TRASH_ROOT, track_id + suffix))
        S.atomic_write(
            os.path.join(C.TRASH_ROOT, "%s.purge.json" % track_id),
            json.dumps({"deleted_at": S.utc_now(),
                        "purge_after": time.time() + C.TRASH_UNDO_DAYS * 86400,
                        "bytes": os.path.getsize(dest),
                        "summary": row["title"]}, indent=1))
        library.execute("UPDATE track SET lifecycle='trashed' WHERE track_id=?",
                        (track_id,))
        S.release_lease(library, track_id)
        S.library_history(library, track_id, "trashed", row["title"])
        return dest
    finally:
        if own:
            library.close()


def reconcile(lib=None):
    """Compare the filesystem against the registry, both directions.

    A directory with no row is moved to quarantine and never deleted, because
    the one thing worse than an orphan is an orphan that used to be a track.
    """
    own = lib is None
    library = lib or S.open_library()
    notes = []
    try:
        S.ensure_home()
        rows = {r["track_id"]: r for r in library.execute("SELECT * FROM track")}
        try:
            on_disk = set(os.listdir(C.TRACKS_ROOT))
        except OSError:
            on_disk = set()
        for name in sorted(on_disk):
            if name in rows:
                continue
            src = os.path.join(C.TRACKS_ROOT, name)
            if not os.path.isdir(src):
                continue
            S.ensure_dir(C.QUARANTINE_ROOT)
            dest = os.path.join(
                C.QUARANTINE_ROOT,
                "%s-%s-orphan-dir" % (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), name))
            shutil.move(src, dest)
            notes.append("quarantined orphan directory %s" % name)
            S.library_event(library, "orphan_dir", dest)
        for track_id, row in sorted(rows.items()):
            directory = os.path.join(C.TRACKS_ROOT, track_id)
            pwk = os.path.join(C.ARCHIVE_ROOT, "%s.pwk" % track_id)
            if row["lifecycle"] == "active" and not os.path.isdir(directory):
                library.execute(
                    "UPDATE track SET lifecycle='lost' WHERE track_id=?", (track_id,))
                notes.append("track %s has no directory; marked lost" % track_id)
            elif row["lifecycle"] == "archiving":
                if os.path.isdir(directory) and os.path.isfile(pwk):
                    notes.append("track %s was mid-archive; both sides present"
                                 % track_id)
                elif os.path.isfile(pwk):
                    library.execute(
                        "UPDATE track SET lifecycle='archived' WHERE track_id=?",
                        (track_id,))
                    notes.append("track %s archive finished on recovery" % track_id)
            elif row["lifecycle"] == "restoring":
                # restore_track leaves this state on any failure between its
                # first UPDATE and its last. Roll it back to whichever side
                # actually survived, or the track is stuck in a state no
                # function accepts and the candidate can neither open nor retry.
                if os.path.isfile(pwk):
                    library.execute(
                        "UPDATE track SET lifecycle='archived' WHERE track_id=?",
                        (track_id,))
                    notes.append("track %s restore did not finish; it is archived"
                                 " again and can be retried" % track_id)
                elif os.path.isdir(directory):
                    library.execute(
                        "UPDATE track SET lifecycle='active' WHERE track_id=?",
                        (track_id,))
                    notes.append("track %s restore finished on recovery" % track_id)
                else:
                    library.execute(
                        "UPDATE track SET lifecycle='lost' WHERE track_id=?",
                        (track_id,))
                    notes.append("track %s has neither an archive nor a"
                                 " directory; marked lost" % track_id)
            elif row["lifecycle"] == "archived" and not os.path.isfile(pwk):
                library.execute("UPDATE track SET lifecycle='lost' WHERE track_id=?",
                                (track_id,))
                notes.append("archive for %s is gone; marked lost" % track_id)
            lease = library.execute("SELECT * FROM track_lease WHERE track_id=?",
                                    (track_id,)).fetchone()
            if lease is not None and not S.lease_is_live(lease):
                S.release_lease(library, track_id)
                notes.append("reclaimed a dead lease on %s" % track_id)
        return notes
    finally:
        if own:
            library.close()
