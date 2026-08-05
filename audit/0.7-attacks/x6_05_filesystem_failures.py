"""Attack: what the sink does when the filesystem stops co-operating.

Directory removed mid-run, directory read-only, target name already taken by a
directory, quota exhausted (RLIMIT_FSIZE stands in for ENOSPC), and a recorded
value that is not encodable at all.  For each: does an exception escape where it
should not, is a descriptor leaked, and do `artifact` / `artifacts` still tell
the truth?
"""

from __future__ import annotations

import gc
import os
import resource
import signal
import stat
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from x6_common import (  # noqa: E402
    Fact, JsonlTraceSink, NOW, Report, State, SYNC, listing, passthrough,
    runtime, scratch, spec_path, validate_expected, CHAIN, Runtime,
)


def fds() -> set[int]:
    return {int(n) for n in os.listdir("/proc/self/fd") if n.isdigit()}


def open_paths() -> dict[int, str]:
    out = {}
    for n in os.listdir("/proc/self/fd"):
        try:
            out[int(n)] = os.readlink(f"/proc/self/fd/{n}")
        except OSError:
            pass
    return out


def directory_removed_mid_run(rep: Report) -> None:
    d = scratch("fs/removed")
    sink = JsonlTraceSink(d, fsync=False)
    driver = runtime(sink=sink).stream(State.empty("gone"), run_id="dir-removed")
    next(driver)
    next(driver)
    for path in list(d.iterdir()):
        path.unlink()
    d.rmdir()

    try:
        driver.close("directory is gone")
        raised = None
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    rep.record("close() over a removed directory does not raise at the caller",
               raised is None, f"{type(raised).__name__ if raised else ''}: {raised}")

    session = sink.sessions[0]
    art = session.artifact
    rep.record("artifact is not a path that does not exist",
               art is None or art.exists(),
               f"artifact={art} exists={art.exists() if art else '-'} "
               f"complete={session.complete}")
    rep.record("artifacts only lists files that exist",
               all(p.exists() for p in sink.artifacts),
               f"{[str(p) for p in sink.artifacts]}")
    leaked = [p for p in open_paths().values() if "dir-removed" in p]
    rep.record("no descriptor left open on the removed file", not leaked, str(leaked))


def readonly_directory(rep: Report) -> None:
    d = scratch("fs/readonly")
    sink_dir = d / "locked"
    sink_dir.mkdir(exist_ok=True)
    os.chmod(sink_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        sink = JsonlTraceSink(sink_dir, fsync=False)
        try:
            runtime(sink=sink).run(State.empty("ro"), run_id="readonly")
            rep.record("read-only directory fails the run", False, "the run succeeded")
        except BaseException as exc:  # noqa: BLE001
            rep.record("read-only directory fails the run as a sink failure",
                       type(exc).__name__ == "SinkFailed",
                       f"{type(exc).__name__}: {str(exc)[:120]}")
        rep.record("no session was registered for a failed open",
                   len(sink.sessions) == 0, f"sessions={len(sink.sessions)}")
    finally:
        os.chmod(sink_dir, stat.S_IRWXU)


def name_taken_by_a_directory(rep: Report) -> None:
    """Someone else already owns `<stem>.jsonl` -- as a directory."""
    d = scratch("fs/nametaken")
    sink = JsonlTraceSink(d, fsync=False)
    # Learn the stem the sink will choose by doing one throwaway run elsewhere.
    probe_dir = scratch("fs/nametaken-probe")
    probe = JsonlTraceSink(probe_dir, fsync=False)
    runtime(sink=probe).run(State.empty("probe"), run_id="taken")
    stem = probe.artifacts[0].name
    (d / stem).mkdir()

    try:
        runtime(sink=sink).run(State.empty("collide"), run_id="taken")
        raised = None
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    session = sink.sessions[0]
    rep.record("a name collision with a directory does not silently succeed",
               raised is not None or session.artifact is None
               or session.artifact.is_file(),
               f"raised={type(raised).__name__ if raised else None} "
               f"artifact={session.artifact} "
               f"is_file={session.artifact.is_file() if session.artifact else '-'} "
               f"listing={listing(d)}")
    left = [n for n in listing(d) if n.endswith(".part")]
    rep.record("no orphan .part is left claiming to be a live run",
               not left, f"{left} while complete={session.complete}")


def quota_exhausted(rep: Report) -> None:
    """RLIMIT_FSIZE stands in for a full disk: writes past the limit fail."""
    d = scratch("fs/quota")
    sink = JsonlTraceSink(d, fsync=False)
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    before = fds()
    resource.setrlimit(resource.RLIMIT_FSIZE, (2048, hard))

    def fat(state):
        for i in range(50):
            state = state.with_fact(Fact(f"k{i}", "z" * 500, "x6", NOW))
        return state

    try:
        Runtime(CHAIN, {"a": fat, "b": passthrough, "c": passthrough},
                sink=sink, durability_profile=SYNC).run(
            State.empty("quota"), run_id="quota")
        raised = None
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))

    rep.record("a full disk fails the run rather than passing it",
               raised is not None, f"raised={type(raised).__name__ if raised else None}")
    session = sink.sessions[0]
    art = session.artifact
    rep.record("artifact after a write failure names a file that exists",
               art is None or art.exists(),
               f"artifact={art} complete={session.complete} listing={listing(d)}")
    leaked = sorted(p for f, p in open_paths().items()
                    if f not in before and "quota" in p)
    rep.record("no descriptor leaked by a finish() that raised",
               not leaked, str(leaked))
    for name in listing(d):
        ok, out = validate_expected(d / name, spec=spec_path())
        rep.record(f"quota artifact {name[:26]} validates", ok, out[:300])


def unencodable_value(rep: Report) -> None:
    """A lone surrogate is a str Python accepts and UTF-8 cannot carry."""
    d = scratch("fs/surrogate")
    sink = JsonlTraceSink(d, fsync=False)
    before = fds()

    def bad(state):
        return state.with_fact(Fact("k", "lone\ud800end", "x6", NOW))

    try:
        Runtime(CHAIN, {"a": bad, "b": passthrough, "c": passthrough},
                sink=sink, durability_profile=SYNC).run(
            State.empty("surrogate"), run_id="surrogate")
        raised = None
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    rep.record("an unencodable recorded value fails the run",
               raised is not None, f"raised={type(raised).__name__}")
    session = sink.sessions[0]
    art = session.artifact
    rep.record("surrogate: artifact names a file that exists",
               art is None or art.exists(),
               f"artifact={art} complete={session.complete} listing={listing(d)}")
    leaked = sorted(p for f, p in open_paths().items()
                    if f not in before and "surrogate" in p)
    rep.record("surrogate: no descriptor leaked", not leaked, str(leaked))
    for name in listing(d):
        ok, out = validate_expected(d / name, spec=spec_path())
        rep.record(f"surrogate artifact {name[:26]} validates", ok, out[:300])


def abandoned_sessions_leak(rep: Report) -> None:
    """Hundreds of runs: does anything stay open?"""
    d = scratch("fs/many")
    before = len(fds())
    sink = JsonlTraceSink(d, fsync=False)
    for i in range(300):
        runtime(sink=sink).run(State.empty("many"), run_id=f"many-{i}")
    gc.collect()
    after = len(fds())
    rep.record("300 completed runs leak no descriptors", after - before < 5,
               f"{before} -> {after}")

    # abandoned drivers, never advanced
    d2 = scratch("fs/abandoned")
    before2 = len(fds())
    sink2 = JsonlTraceSink(d2, fsync=False)
    for i in range(300):
        drv = runtime(sink=sink2).stream(State.empty("ab"), run_id=f"ab-{i}")
        del drv
    gc.collect()
    after2 = len(fds())
    rep.record("300 never-advanced drivers leak no descriptors",
               after2 - before2 < 5, f"{before2} -> {after2} listing={len(listing(d2))}")
    rep.record("300 never-advanced drivers publish no artifact",
               listing(d2) == [], f"{listing(d2)[:5]}")


def main() -> int:
    rep = Report("x6_05 filesystem failures")
    directory_removed_mid_run(rep)
    readonly_directory(rep)
    name_taken_by_a_directory(rep)
    quota_exhausted(rep)
    unencodable_value(rep)
    abandoned_sessions_leak(rep)
    return rep.dump()


if __name__ == "__main__":
    raise SystemExit(main())
