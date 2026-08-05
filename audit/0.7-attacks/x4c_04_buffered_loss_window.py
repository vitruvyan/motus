"""x4c-04 — BUFFERED: is the declared loss window the real loss window?

guarantees.md invariant II, buffered row:
  "Everything up to the last confirmed flush; the loss window is declared in
   the run header's `sink` object (flush_interval_ms / chunk_records ...).
   Transition records with outcome `raised` or `cancelled`, and the terminal
   records, flush immediately — failure evidence is never in the loss window."

The declared window is a TIME/COUNT window: after flush_interval_ms, or after
chunk_records records, the evidence is durable.  This script asks whether the
window ever actually closes on the teardown paths, and whether the promised
immediate flush of failure evidence covers hard teardown.

ADR-008 §2 states the governing principle for the other direction:
  "`in-memory` still promises no survival after process loss; it does not
   license silent loss while the process is alive."
"""

from __future__ import annotations

import gc
import json
import time

from vitruvyan_motus import DurabilityProfile, Policy, Runtime, State

from x4c_common import (
    LINEAR, LINEAR_SPEC_PATH, OUT, JsonlFileSink, Report, kinds_of_file,
    registry, validate_file,
)

INTERVAL = 120  # ms — small enough to wait out, large enough to buffer


def buffered(sink, **kw):
    return Runtime(
        LINEAR, kw.pop("nodes", None) or registry(),
        durability_profile=DurabilityProfile.BUFFERED, sink=sink,
        chunk_records=kw.pop("chunk_records", 1000),
        flush_interval_ms=kw.pop("flush_interval_ms", INTERVAL),
        **kw,
    )


def persisted(sink) -> list[str]:
    if not sink.sessions:
        return []
    return kinds_of_file(sink.sessions[-1].path)[1:]


def main() -> int:
    r = Report("x4c-04 buffered loss window")

    # --- 1. The window closes on its own while the run is LIVE ------------- #
    s = JsonlFileSink(OUT / "04" / "live-timer", prefix="s")
    rt = buffered(s)
    driver = rt.stream()
    for i, _ in enumerate(driver):
        if i >= 5:
            break
    before = len(persisted(s))
    time.sleep(INTERVAL / 1000 * 4)
    after = len(persisted(s))
    r.record(
        "live run: timer closes the window", after > before,
        f"{before} persisted at pause, {after} after 4x flush_interval_ms",
    )
    driver.close("done")  # tidy: reaches a terminal

    # --- 2. The window NEVER closes once the run is torn down -------------- #
    s2 = JsonlFileSink(OUT / "04" / "torn-timer", prefix="s")
    rt2 = buffered(s2)
    d2 = rt2.stream()
    for i, _ in enumerate(d2):
        if i >= 5:
            break
    emitted = i + 1
    del d2
    gc.collect()
    immediately = len(persisted(s2))
    time.sleep(INTERVAL / 1000 * 6)
    eventually = len(persisted(s2))
    r.record(
        "torn-down run: window still closes", eventually >= emitted,
        f"{emitted} records emitted, {immediately} persisted at teardown, "
        f"{eventually} after 6x flush_interval_ms (window is declared "
        f"{INTERVAL}ms / 1000 records)",
    )

    # --- 3. Failure evidence under a hard (BaseException) teardown --------- #
    def hard(state: State) -> State:
        raise KeyboardInterrupt("hard stop inside node c")

    nodes = dict(registry())
    nodes["c"] = hard
    s3 = JsonlFileSink(OUT / "04" / "hard-teardown", prefix="s")
    rt3 = buffered(s3, nodes=nodes)
    try:
        rt3.run()
    except KeyboardInterrupt:
        pass
    time.sleep(INTERVAL / 1000 * 4)
    got = persisted(s3)
    r.record(
        "node-protocol 7.3: unclosed attempt_started survives", bool(got),
        f"persisted={got or '[] — the attempt_started evidence was erased'}",
    )

    # The synchronous profile, same graph, for contrast.
    s3b = JsonlFileSink(OUT / "04" / "hard-teardown-sync", prefix="s")
    rt3b = Runtime(LINEAR, nodes,
                   durability_profile=DurabilityProfile.SYNCHRONOUS, sink=s3b)
    try:
        rt3b.run()
    except KeyboardInterrupt:
        pass
    r.record(
        "  (same case, synchronous profile)", bool(persisted(s3b)),
        f"persisted={persisted(s3b)}",
    )

    # --- 4. Soft failure evidence IS flushed immediately (control) --------- #
    def soft(state: State) -> State:
        raise ValueError("ordinary node failure")

    nodes4 = dict(registry())
    nodes4["c"] = soft
    s4 = JsonlFileSink(OUT / "04" / "soft-failure", prefix="s")
    rt4 = buffered(s4, nodes=nodes4, policy=Policy.EXPLORATION)
    rt4.run()
    kinds4 = persisted(s4)
    raised_index = next(
        (i for i, k in enumerate(kinds4) if k == "transition"), None
    )
    r.record(
        "CONTROL: raised transition flushes immediately",
        "run_completed" in kinds4 and len(kinds4) == 14,
        f"{len(kinds4)} records persisted, ends {kinds4[-1] if kinds4 else None}",
    )

    # --- 5. Does the header disclose a window it does not honour? ---------- #
    header = s2.headers[0]
    r.record(
        "header discloses buffered window",
        header.get("sink") == {"flush_interval_ms": INTERVAL, "chunk_records": 1000},
        f"run.sink = {header.get('sink')} ; run.durability_profile = "
        f"{header.get('durability_profile')}",
    )

    # --- 6. Validate what survives ---------------------------------------- #
    for tag, sink in (("torn", s2), ("hard-teardown", s3), ("soft", s4)):
        if not sink.sessions:
            r.record(f"artifact/{tag}", False, "no session")
            continue
        code, out = validate_file(sink.sessions[-1].path, "jsonl",
                                  spec_path=LINEAR_SPEC_PATH)
        r.record(f"artifact/{tag} validates", code == 0,
                 f"rc={code} {out.splitlines()[0][:120] if out else 'VALID'}")

    return r.dump()


if __name__ == "__main__":
    raise SystemExit(main())
