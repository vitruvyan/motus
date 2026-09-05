"""Build the proof bundle a visitor downloads — one per scenario.

    .venv/bin/python demo/bundle_scenarios.py

The bundle has to be checkable by somebody who will not install Motus. We are
not on PyPI and the repository is private, so "pip install our verifier" is not
a verification path — it is trust with an extra step. Each bundle therefore
carries `derive_root.py`: standard library only, about thirty lines, written
from ADR-019 rather than imported from the runtime, so what a reader trusts is
the recipe they just read.

Three commands, and the third runs OpenTimestamps' own client:

    python3 derive_root.py trace.json
    cat root.txt
    ots verify root.txt.ots

Deterministic: fixed member order, fixed timestamps. Rebuilding from the same
trace produces the same bytes, so somebody who downloaded it last month can
tell whether anything moved.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from bundle_hiring import DERIVE, ZIP_DATE

OUT = Path(__file__).resolve().parent / "out" / "scenarios"


def verify_text(entry: dict) -> str:
    anchor = entry.get("anchor") or {}
    state = anchor.get("state", "unknown")
    calendars = "\n".join(f"      {c}" for c in anchor.get("calendars_accepted", []))
    status = (
        "   STATE RIGHT NOW: pending.\n"
        "   Three calendars have accepted the commitment and no Bitcoin block\n"
        "   carries it yet. A commitment reaches a block in hours, not seconds.\n"
        "   Until it does this evidence supports INTEGRITY and does NOT yet\n"
        "   support EXISTENCE. `ots upgrade root.txt.ots` asks the calendars\n"
        "   again; run it tomorrow and it will answer differently.\n"
        if state == "pending" else
        f"   STATE RIGHT NOW: {state}. Reference: {anchor.get('reference')}\n"
        f"   Published at: {anchor.get('published_at')}\n"
    )
    return f"""MOTUS EVIDENCE BUNDLE — how to check it without trusting us
================================================================

Scenario          {entry['name']}
Question          {entry['prompt']}
Answer            {entry['answer']}

Run id            {entry['run_id']}
Derived root      {entry['root']}
Bundle            {entry['bundle_fingerprint']}
Graph             {entry['graph_fingerprint']}
Records           {entry['records']}

THE SCENARIO IS INVENTED. Every organisation, person and asset named in it was
made up for a demonstration, and the trace says so in its own first fact,
`record_is_synthetic`. What is NOT invented is the trace: it came out of the
Motus runtime, it validates against the Motus contract, and the commitment
below was published to calendars nobody at Vitruvyan runs.


1. DERIVE THE FINGERPRINT YOURSELF
----------------------------------

    python3 derive_root.py trace.json

Read `derive_root.py` first — that is the point of it being thirty lines. It
walks the whole hash chain; it does not read the root out of a field, because
that field agrees with a forgery and the chain does not.

It should print, exactly:

    {entry['root']}

Now change ANYTHING in trace.json — one digit, one character of a reason — and
run it again. It will name the record that stopped matching.


2. CHECK THAT THIS IS WHAT WAS TIMESTAMPED
------------------------------------------

    cat root.txt

`root.txt` holds the root string in UTF-8 with no trailing newline. Those exact
bytes are what was submitted. The proof is a proof of THEM, not of trace.json.


3. VERIFY THE TIMESTAMP WITH OPENTIMESTAMPS, NOT WITH US
--------------------------------------------------------

    pip install opentimestamps-client
    ots verify root.txt.ots

Calendars that accepted the commitment:
{calendars}

{status}

WHAT THIS BUNDLE PROVES
-----------------------

  INTEGRITY   this evidence has not been modified since it was sealed.
              You checked that in step 1, with your own copy of Python.

  EXISTENCE   this evidence existed no later than T — once, and only once, a
              Bitcoin block carries the commitment. See the state above.


WHAT IT DOES NOT PROVE
----------------------

  - not that the recommendation was CORRECT;
  - not that the facts recorded in it are TRUE;
  - not that a node did not INFER something from the values it did read —
    Motus records which values a node read, and that is a different claim;
  - not that every decision the organisation made passed through Motus;
  - not that a run which was never registered did not happen;
  - not compliance with any regulation. Motus produces evidence with which
    compliance can be demonstrated. The demonstration is somebody else's.

That list is ADR-020 decision 5. It is normative, not a disclaimer: a system
that cannot state its limits has not established any.


FILES
-----

  trace.json        the execution record
  graph.json        the graph declaration the run was bound to
  bundle.json       both of the above as one Motus trace bundle
  explain.json      the runtime's own causal account of the run
  root.txt          the root string, exactly as committed
  root.txt.ots      the OpenTimestamps proof of those bytes
  anchor.json       the anchor receipt: checkpoint, calendars, state
  derive_root.py    thirty lines of stdlib Python that check all of it
  VERIFY.txt        this file
"""


def main() -> None:
    index = []
    for directory in sorted(p for p in OUT.iterdir() if p.is_dir()):
        entry = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        archive_path = directory / f"{entry['name']}.motus.zip"
        trace = json.loads((directory / "trace.json").read_text(encoding="utf-8"))
        graph = json.loads((directory / "graph.json").read_text(encoding="utf-8"))

        members = [
            ("VERIFY.txt", verify_text(entry).encode("utf-8")),
            ("trace.json", (directory / "trace.json").read_bytes()),
            ("graph.json", (directory / "graph.json").read_bytes()),
            ("explain.json", (directory / "explain.json").read_bytes()),
            ("anchor.json", (directory / "root.txt.anchor.json").read_bytes()),
            ("root.txt", (directory / "root.txt").read_bytes()),
            ("root.txt.ots", (directory / "root.txt.ots").read_bytes()),
            ("derive_root.py", DERIVE.encode("utf-8")),
            ("bundle.json", json.dumps(
                {"bundle_version": "1.0.0", "graph_spec": graph, "trace": trace},
                indent=2, ensure_ascii=False).encode("utf-8") + b"\n"),
        ]
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members:
                info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
                info.external_attr = 0o644 << 16
                archive.writestr(info, payload)

        entry["bundle_file"] = archive_path.name
        entry["bundle_sha256"] = "sha256:" + hashlib.sha256(
            archive_path.read_bytes()).hexdigest()
        entry["bundle_bytes"] = archive_path.stat().st_size
        (directory / "index.json").write_text(
            json.dumps(entry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        index.append(entry)
        print(f"{entry['name']:20s} {archive_path.name}  "
              f"{entry['bundle_bytes']} bytes")

    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
