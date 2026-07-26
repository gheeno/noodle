"""NOOD_0183 — cross-process coordination for parallel runs.

`--parallel` shards by feature file, so the scenarios *inside* one file are
always sequential. What it cannot know is that two DIFFERENT files share a
real-world resource — one login, one seeded order, one localhost port. Nothing
in Gherkin expresses "these must not overlap", so the suite either passes by
luck or fails in a way that reproduces only under load.

Two primitives, both built on the one filesystem operation that is atomic on
POSIX *and* Windows: `os.mkdir` either creates the directory or raises. No
dependency, no lock file to parse, no fcntl/msvcrt split.

  * `hold(name)`   — a named mutex, held by at most one worker at a time.
                     Drives the `@serial` / `@lock:<name>` tags.
  * `worker_index()` — a small stable 1..N lane number for this process, so a
                     suite with four accounts can hand one to each lane
                     (`SHOP_USER_1`..`SHOP_USER_4`) instead of serializing on
                     a single one.

ponytail: staleness is an mtime TTL, not a liveness probe — `os.kill(pid, 0)`
does not port to Windows, and a lock older than the TTL is a crashed worker in
every case that matters. Raise NOODLE_LOCK_TTL if a locked section can
legitimately run longer than 10 minutes.
"""
import os
import time
from pathlib import Path

from noodle.log import logger

_CONTROL = Path(".noodle")
_POLL_S = 0.1

# Locks held by THIS process, so release() can't drop someone else's and a
# re-entrant acquire (feature tag + scenario tag naming the same lock) is a
# no-op rather than a self-deadlock.
_held: dict[str, int] = {}
_my_index: int | None = None


def _ttl() -> float:
    return float(os.getenv("NOODLE_LOCK_TTL", "600"))


def _timeout() -> float:
    return float(os.getenv("NOODLE_LOCK_TIMEOUT", "300"))


def _claim(path: Path) -> bool:
    """Atomically take `path`, breaking it first if it has gone stale."""
    try:
        path.mkdir(parents=True)
        return True
    except FileExistsError:
        try:
            if time.time() - path.stat().st_mtime > _ttl():
                path.rmdir()                      # crashed holder — reclaim
                path.mkdir(parents=True)
                logger.warning(f"\n  🔓 Broke stale lock '{path.name}' (older than {_ttl():.0f}s)")
                return True
        except OSError:
            pass                                  # someone else won the race
        return False
    except OSError:
        return True    # unwritable control dir — never block the run over a lock


def lock_names(tags) -> list[str]:
    """`@serial` → the shared 'global' lock; `@lock:cart` → the 'cart' lock.
    Same `tag:value` shape as the existing @precondition: tags."""
    names = []
    for t in tags or ():
        if t == "serial":
            names.append("global")
        elif t.startswith("lock:") and t[5:].strip():
            names.append(t[5:].strip().replace("/", "_").replace("\\", "_"))
    return sorted(set(names))


def acquire(name: str) -> None:
    """Block until this process owns `name`. Times out loudly — a test that
    silently ran unlocked would reintroduce the exact race the tag exists to
    prevent."""
    if _held.get(name):
        _held[name] += 1
        return
    path = _CONTROL / "locks" / name
    deadline = time.monotonic() + _timeout()
    waited = False
    while not _claim(path):
        if time.monotonic() > deadline:
            raise AssertionError(
                f"Timed out after {_timeout():.0f}s waiting for lock '{name}'. "
                f"Another worker is holding it — if it crashed, delete {path} "
                f"or lower NOODLE_LOCK_TTL."
            )
        if not waited:
            logger.info(f"\n  ⏳ Waiting for lock '{name}' — another feature is using it")
            waited = True
        time.sleep(_POLL_S)
    _held[name] = 1


def release(name: str) -> None:
    if _held.get(name, 0) > 1:
        _held[name] -= 1
        return
    _held.pop(name, None)
    try:
        (_CONTROL / "locks" / name).rmdir()
    except OSError:
        pass


def release_all() -> None:
    for name in list(_held):
        _held[name] = 1
        release(name)


def worker_index() -> int:
    """This process's lane, 1..N. Sequential runs are always lane 1; parallel
    workers each claim a distinct free number at startup. Cached — the claim
    happens once per process."""
    global _my_index
    if _my_index is not None:
        return _my_index
    if os.getenv("NOODLE_PARALLEL_WORKER") != "1":
        _my_index = 1
        return _my_index
    for n in range(1, 1025):
        if _claim(_CONTROL / "workers" / str(n)):
            _my_index = n
            return n
    _my_index = 1          # 1024 live workers is not a real configuration
    return _my_index


def release_worker_index() -> None:
    global _my_index
    if _my_index is not None and os.getenv("NOODLE_PARALLEL_WORKER") == "1":
        try:
            (_CONTROL / "workers" / str(_my_index)).rmdir()
        except OSError:
            pass
    _my_index = None


def reset_control_dir() -> None:
    """Clear leftover locks/lanes before spawning workers — called once by the
    CLI parent, never by a worker."""
    import shutil
    for sub in ("locks", "workers"):
        shutil.rmtree(_CONTROL / sub, ignore_errors=True)


if __name__ == "__main__":   # ponytail: the one runnable check for the lock
    import tempfile
    os.chdir(tempfile.mkdtemp())
    assert lock_names(["serial", "lock:cart", "web", "lock: pay "]) == ["cart", "global", "pay"]
    acquire("cart")
    assert not _claim(_CONTROL / "locks" / "cart"), "a held lock must not be re-claimable"
    acquire("cart")                                       # re-entrant
    release("cart")
    assert not _claim(_CONTROL / "locks" / "cart"), "re-entrant release freed it too early"
    release("cart")
    assert _claim(_CONTROL / "locks" / "cart"), "released lock must be re-claimable"
    (_CONTROL / "locks" / "cart").rmdir()
    os.environ["NOODLE_PARALLEL_WORKER"] = "1"
    assert worker_index() == 1 and worker_index() == 1     # cached
    release_worker_index()
    os.environ["NOODLE_LOCK_TTL"] = "0"
    acquire("stale")
    _held.clear()                                          # simulate a crashed holder
    acquire("stale")                                       # must break the stale lock
    print("runlock self-check OK")
