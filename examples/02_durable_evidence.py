"""Evidence on disk, and checking it without trusting the process that made it.

    python examples/02_durable_evidence.py

This is the part that makes Motus worth having. A run writes a file; anyone
can validate that file against the published contract, and replay it to see
whether the code still agrees with what was recorded.

The file is not a log. It is the account the runtime kept, in the form the
contract defines, and `contract/validate.py` will tell you if it is malformed
or internally inconsistent — including if someone edited it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from vitruvyan_motus import (
    DurabilityProfile, Fact, GraphSpec, JsonlTraceSink, ReplayEngine, Runtime,
    State, TraceBundle, Trace, ReplayMismatch,
)

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent

SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "settlement",
    "version": "1.0.0",
    "entry": "price",
    "nodes": [
        {"name": "price", "effect_class": "pure"},
        # `external_effect` says this node touches the world. Replay will never
        # re-execute it -- re-charging a card to verify a trace would be a
        # spectacular own goal -- so its recorded result is taken as given.
        {"name": "charge", "effect_class": "external_effect"},
    ],
    "transitions": {"price": {"kind": "next", "to": "charge"}, "charge": {"kind": "terminal"}},
})


def price(state: State) -> State:
    units = state.metadata("units") or 0
    return state.with_fact(Fact("total", units * 12, "pricing", NOW))


def charge(state: State) -> State:
    return state.with_fact(Fact("charged", True, "payments", NOW))


def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="motus-example-"))

    # `synchronous` means every record is on disk before the run proceeds.
    # The profile is a promise about crash survival, and JsonlTraceSink is
    # what keeps it -- it fsyncs, because flushing alone would not.
    sink = JsonlTraceSink(workspace, fsync=True)
    result = Runtime(
        SPEC, {"price": price, "charge": charge},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    ).run(State.empty("settle order 4471", metadata={"units": 3}), run_id="order-4471")

    artifact = sink.artifacts[0]
    print(f"run finished: {result.status}")
    print(f"evidence    : {artifact}")
    print(f"             {artifact.stat().st_size} bytes, "
          f"{len(artifact.read_text().splitlines())} lines\n")

    # --- 1. an outsider validates it, with no access to this process --------

    check = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"), "jsonl", str(artifact)],
        capture_output=True, text=True,
    )
    print("independent validation:", "PASS" if check.returncode == 0 else "FAIL")
    print(f"  {check.stdout.strip() or check.stderr.strip()}\n")

    # --- 2. tampering is caught --------------------------------------------

    lines = artifact.read_text(encoding="utf-8").splitlines()
    tampered = workspace / "tampered.jsonl"
    tampered.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")

    check = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"), "jsonl", str(tampered)],
        capture_output=True, text=True,
    )
    print("a truncated copy, validated strictly:",
          "PASS" if check.returncode == 0 else "REFUSED")
    print(f"  {(check.stdout + check.stderr).strip().splitlines()[0]}\n")

    # --- 3. replay: does the code still agree with the record? --------------

    document = json.loads(json.dumps({
        "schema_version": json.loads(lines[0])["schema_version"],
        "run": json.loads(lines[0])["run"],
        "records": [json.loads(line) for line in lines[1:]],
    }))
    bundle = TraceBundle(spec=SPEC, trace=Trace.from_dict(document))

    verified = ReplayEngine(bundle).verify({"price": price})
    print(f"verify replay: re-executed {len(verified.verified)} pure node(s), all agreed")

    # Now change the pricing rule, as a careless deploy would.
    def price_changed(state: State) -> State:
        units = state.metadata("units") or 0
        return state.with_fact(Fact("total", units * 15, "pricing", NOW))

    try:
        ReplayEngine(bundle).verify({"price": price_changed})
        print("verify replay: NOT DETECTED -- this should not happen")
    except ReplayMismatch as mismatch:
        print(f"verify replay against changed code: refused -- {mismatch}")

    print(
        f"\nThe evidence never left {workspace}."
        "\nEverything checked above was done from the file, by a separate process,"
        "\nusing only the published contract."
    )


if __name__ == "__main__":
    main()
