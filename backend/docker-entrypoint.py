#!/usr/bin/env python3
"""Prepare the evidence store, then drop privileges and exec the server.

Why this exists
---------------
Everything that makes PRAMAAN a chain of custody -- the SQLite case file, the
stored evidence bytes, the perceptual-hash index, generated reports and the
append-only audit chain -- lives under ``PRAMAAN_DATA_DIR``. In production that
directory is a mounted volume (a Render persistent disk, or ``docker run -v``),
and a freshly mounted volume belongs to root. The application runs as an
unprivileged user, so without this step its first write fails with EACCES and
the service either crashes on boot or, worse, reports errors for every upload
while looking healthy.

The mount cannot be prepared at build time -- it does not exist yet -- so it is
prepared here, at container start, as root, and the server is then exec'd as
``pramaan``. Being an exec, uvicorn inherits PID 1 and receives SIGTERM directly,
so Render's shutdown signal still reaches it.

This is deliberately not a shell script: the ownership logic below has a
condition (recurse only when the top-level owner is wrong) that is clearer in
Python than in test(1), and the image already has Python as its only runtime.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: The unprivileged account created in the Dockerfile.
RUNTIME_USER = os.environ.get("PRAMAAN_RUNTIME_USER", "pramaan")

#: Settings whose value is a directory the application must be able to write.
#: ``PRAMAAN_CORPUS_DIR`` is included because the synthetic corpus index is
#: rebuilt in place; it is baked into the image, so it is normally already owned
#: correctly and this is a no-op for it.
STORAGE_ENV_VARS = ("PRAMAAN_DATA_DIR", "PRAMAAN_REPORTS_DIR", "PRAMAAN_CORPUS_DIR")


def _log(message: str) -> None:
    print(f"[entrypoint] {message}", flush=True)


def _target_ids() -> tuple[int, int]:
    """The uid/gid to hand the storage to, resolved from the account name."""
    import pwd

    record = pwd.getpwnam(RUNTIME_USER)
    return record.pw_uid, record.pw_gid


def _claim(path: Path, uid: int, gid: int) -> None:
    """Make ``path`` (and its contents, if they are misowned) writable by uid:gid.

    The recursive walk runs only when the directory itself has the wrong owner --
    a first boot on an empty volume, or a volume last written by a container that
    ran as root. When the owner is already correct, the files inside were written
    by this same account on a previous boot, so walking an evidence store of
    thousands of files on every restart would cost time and change nothing.
    """
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    stat = path.stat()
    if stat.st_uid == uid and stat.st_gid == gid:
        return

    os.chown(path, uid, gid)
    if created:
        _log(f"created {path} for {RUNTIME_USER} ({uid}:{gid})")
        return

    fixed = 0
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            entry = Path(root) / name
            try:
                os.lchown(entry, uid, gid)
                fixed += 1
            except OSError as exc:  # a broken symlink, or a read-only sub-mount
                _log(f"could not chown {entry}: {exc}")
    _log(f"took ownership of {path} for {RUNTIME_USER} ({uid}:{gid}); {fixed} entries updated")


def _prepare_storage() -> None:
    uid, gid = _target_ids()
    for name in STORAGE_ENV_VARS:
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        try:
            _claim(Path(value), uid, gid)
        except OSError as exc:
            # Loud, not fatal: the application's own startup check reports the
            # unwritable directory with the setting that named it, which is a
            # better message than a traceback from this script.
            _log(f"WARNING: {name}={value} could not be prepared: {exc}")


def _writable(path: Path) -> bool:
    """Whether ``path`` can be written, or created and then written.

    A directory that does not exist yet is not a problem as long as something can
    create it, so the question is asked of the nearest ancestor that does exist.
    Reporting "not writable" for a path the application would have created on
    startup would send a reader looking for a permission fault that is not there.
    """
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK | os.X_OK)


def _drop_privileges() -> None:
    """Become ``RUNTIME_USER`` for good: supplementary groups, gid, then uid.

    Order matters -- ``setgid`` after ``setuid`` would already be forbidden -- and
    ``setuid`` on Linux with no capabilities retained is irreversible, which is
    the point. ``HOME`` is corrected too: torch and huggingface both write caches
    under it, and left pointing at ``/root`` those writes fail after the drop.
    """
    import pwd

    record = pwd.getpwnam(RUNTIME_USER)
    os.setgroups([record.pw_gid])
    os.setgid(record.pw_gid)
    os.setuid(record.pw_uid)
    os.environ["HOME"] = record.pw_dir
    os.environ["USER"] = os.environ["LOGNAME"] = RUNTIME_USER
    _log(f"running as {RUNTIME_USER} (uid {os.getuid()})")


def main(argv: list[str]) -> int:
    if not argv:
        _log("no command given; nothing to exec")
        return 2

    if os.geteuid() == 0:
        _prepare_storage()
        _drop_privileges()
    else:
        # Already unprivileged (an explicit `docker run --user`, or a platform
        # that pins the uid). Nothing here can fix a root-owned mount in that
        # case, so say so once rather than failing silently on the first upload.
        for name in STORAGE_ENV_VARS:
            value = os.environ.get(name, "").strip()
            if value and not _writable(Path(value)):
                _log(
                    f"WARNING: {name}={value} is not writable by uid {os.getuid()} "
                    f"and this process is not root, so it cannot be fixed here. "
                    f"Evidence, reports and the audit chain cannot be persisted."
                )

    os.execvp(argv[0], argv)
    return 0  # unreachable: execvp either replaces this process or raises


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
