"""The request boundary: origin, host, cookie, header, remote identity.

The only module allowed to decide that a request may proceed. Every check is
positive: a request is refused unless it matches something named here.

Status: the request checks still live in bridge.py. `trusted_executable` moved
here first because it has no dependency on `PORT`, which is what blocks the rest
of the extraction, and because two callers now need it: the provider CLIs and
the PDF text extractor. One copy of an ownership check is the point of the
module.
"""

import os
import shutil
import stat


def trusted_executable(name, fallbacks=()):
    """Resolve a CLI and reject files another local account could replace.

    The realpath must be a regular file owned by root or by this user, not
    group- or world-writable, and every directory on the way to it must satisfy
    the same two conditions. A writable parent is enough to swap the binary, so
    checking the file alone would be theatre.
    """
    candidates = [shutil.which(name)] + [os.path.expanduser(p) for p in fallbacks]
    for candidate in candidates:
        if not candidate:
            continue
        real = os.path.realpath(candidate)
        try:
            info = os.stat(real)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        if info.st_uid not in (0, os.getuid()):
            continue
        if info.st_mode & 0o022:  # group/world writable executable
            continue
        parent, parents_ok = os.path.dirname(real), True
        while parent and parent != os.path.dirname(parent):
            try:
                parent_info = os.stat(parent)
            except OSError:
                parents_ok = False
                break
            if (parent_info.st_uid not in (0, os.getuid())
                    or parent_info.st_mode & 0o022):
                parents_ok = False
                break
            parent = os.path.dirname(parent)
        if not parents_ok:
            continue
        return real
    return None
