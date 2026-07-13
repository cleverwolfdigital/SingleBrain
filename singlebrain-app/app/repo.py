"""Git helpers for the brain repo. Implements Pull -> (Read) -> Work -> Push.

Push failures are non-fatal: the commit still lands locally on the VPS, so no
journal data is ever lost even if GitHub credentials aren't set up yet.
"""
import subprocess
from . import config


def _git(*args):
    return subprocess.run(
        ["git", "-C", str(config.BRAIN_REPO), *args],
        capture_output=True, text=True,
    )


def pull():
    r = _git("pull", "--ff-only")
    return r.returncode == 0


def commit_and_push(message, paths=None):
    if paths is None:
        _git("add", "-A")
    else:
        _git("add", *[str(p) for p in paths])
    c = _git("commit", "-m", message)
    committed = c.returncode == 0
    pushed = False
    detail = (c.stdout + c.stderr).strip()
    if committed and config.GIT_PUSH:
        p = _git("push", "origin", "main")
        pushed = p.returncode == 0
        detail = (detail + "\n" + p.stdout + p.stderr).strip()
    return {"committed": committed, "pushed": pushed, "detail": detail}
