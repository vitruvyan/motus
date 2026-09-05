"""Build the receipt bundle a visitor downloads — and can check without us.

    .venv/bin/python demo/bundle_hiring.py

Writes `demo/out/hiring/hiring-review-2026-00421.motus.zip`.

## The bundle has to be checkable by somebody who will not install Motus

Motus is not on PyPI and the repository is private, so "pip install and run our
verifier" is not a verification path for a stranger — it is an invitation to
trust us with an extra step. The bundle therefore carries a **standalone
derivation**, `derive_root.py`: stdlib only, one screen long, written from
ADR-019 rather than imported from the runtime, so a reader can check the recipe
itself instead of taking its result.

Three commands, none of which runs our code except the one they can read:

    python3 derive_root.py trace.json     # derives sha256:... from the bytes
    cat root.txt                          # the string the anchor timestamps
    ots verify root.txt.ots               # OpenTimestamps' own client

The third is the one that matters, and it is why `root.txt` is in here at all:
what was submitted to the calendars is the root **string**, utf-8, no newline.
A proof whose committed bytes have to be guessed proves nothing to the person
guessing.

## Deterministic, because a bundle that changes on every build cannot be diffed

Fixed member order, fixed timestamps, stored entries. Rebuilding from the same
trace produces the same bytes, so a reader who downloaded it last month can
tell whether anything moved.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "hiring"
ARCHIVE = OUT / "hiring-review-2026-00421.motus.zip"

#: A constant date, so the archive is a function of its contents alone.
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def _write_deterministic_zip(path: Path, members: list[tuple[str, bytes]]) -> None:
    """Write the legacy demo envelope without host-specific ZIP metadata."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(members):
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 0
            archive.writestr(info, payload)


DERIVE = '''#!/usr/bin/env python3
"""Derive a Motus trace root. Standard library only, no Motus required.

    python3 derive_root.py trace.json

This is ADR-019's recipe written out, not a call into the runtime, so what you
are trusting is the thirty lines below and not a package you did not read.

The canonical form: UTF-8, keys sorted at every depth, no insignificant
whitespace. The root is a chain — each record's digest covers the record with
its own `payload_hash` blanked and the PREVIOUS digest in place, so a record
cannot be moved, removed or reordered without breaking every link after it.

**Reading `records[-1].integrity.payload_hash` is not deriving the root.** That
field agrees with a forgery; the chain does not.
"""
import hashlib
import json
import sys


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def derived_root(doc):
    if doc.get("schema_version") not in ("3.0.0", "3.1.0"):
        return None, "below trace schema 3.0.0 or outside the anchorable chain versions"
    records = doc.get("records") or []
    if not records:
        return None, "no records"
    if records[-1].get("kind") not in ("run_completed", "run_failed",
                                       "run_cancelled"):
        return None, "the stream does not end in a terminal record"

    prev = "sha256:" + hashlib.sha256(canonical({
        "schema_version": doc["schema_version"],
        "run": doc.get("run") or {},
    })).hexdigest()

    for i, record in enumerate(records):
        integrity = record.get("integrity") or {}
        if integrity.get("prev_hash") != prev:
            return None, f"record {i} does not continue the chain"
        payload = dict(record)
        payload["integrity"] = {"payload_hash": None, "prev_hash": prev}
        digest = "sha256:" + hashlib.sha256(canonical(payload)).hexdigest()
        if integrity.get("payload_hash") != digest:
            return None, f"record {i} has been changed since it was sealed"
        prev = digest
    return prev, None


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "trace.json"
    with open(path, encoding="utf-8") as handle:
        root, why = derived_root(json.load(handle))
    if root is None:
        print(f"NO ROOT: {why}")
        raise SystemExit(1)
    print(root)
'''


def verify_text(index: dict) -> str:
    anchor = index.get("anchor") or {}
    state = anchor.get("state", "unknown")
    calendars = "\n".join(f"      {c}" for c in anchor.get("calendars_accepted", []))
    pending_note = (
        "\n"
        "   STATE RIGHT NOW: pending.\n"
        "   Three calendars have accepted the commitment and no Bitcoin block\n"
        "   carries it yet. A commitment reaches a block in hours, not seconds.\n"
        "   Until it does, this evidence supports INTEGRITY and does NOT yet\n"
        "   support EXISTENCE. `ots upgrade root.txt.ots` asks the calendars\n"
        "   again; run it tomorrow and it will answer differently.\n"
        if state == "pending" else
        f"\n   STATE RIGHT NOW: {state}. Reference: {anchor.get('reference')}\n"
    )
    return f"""MOTUS EVIDENCE BUNDLE — how to check it without trusting us
================================================================

Run id            {index['run_id']}
Derived root      {index['root']}
Bundle            {index['bundle_fingerprint']}
Graph             {index['graph_fingerprint']}
Records           {index['records']}

THE SCENARIO IS INVENTED. There is no applicant and no employer; the trace
itself says so in its first fact, `record_is_synthetic`. What is not invented
is the trace: it came out of the Motus runtime, it validates against the Motus
contract, and the commitment below was published to calendars nobody here runs.


1. DERIVE THE ROOT YOURSELF
---------------------------

    python3 derive_root.py trace.json

`derive_root.py` is standard library only and about thirty lines. Read it
first — that is the point of it being short. It walks the whole hash chain; it
does not read the root out of a field, because that field agrees with a forgery
and the chain does not.

It should print, exactly:

    {index['root']}

Now change ANYTHING in trace.json — one digit of the shortfall, one character
of a reason — and run it again. It will name the record that stopped matching.


2. CHECK THAT THE ROOT IS WHAT WAS TIMESTAMPED
----------------------------------------------

    cat root.txt

`root.txt` holds the root string in UTF-8 with no trailing newline. Those exact
bytes are what was submitted; the proof is a proof of THEM, not of trace.json.


3. VERIFY THE TIMESTAMP WITH OPENTIMESTAMPS, NOT WITH US
--------------------------------------------------------

    pip install opentimestamps-client
    ots verify root.txt.ots

Calendars that accepted the commitment:
{calendars}
{pending_note}

WHAT THIS BUNDLE PROVES
-----------------------

  INTEGRITY   this evidence has not been modified since it was sealed.
              You checked that in step 1, with your own copy of Python.

  EXISTENCE   this evidence existed no later than T — once, and only once, a
              Bitcoin block carries the commitment. See the state above.


WHAT IT DOES NOT PROVE, AND WE WILL NOT LET YOU BELIEVE OTHERWISE
-----------------------------------------------------------------

  - not that the recommendation was CORRECT;
  - not that the facts recorded in it are TRUE;
  - not that every decision the organisation made passed through Motus;
  - not that a run which was never registered did not happen;
  - not compliance with any regulation. Motus produces evidence with which
    compliance can be demonstrated. The demonstration is somebody else's.

That list is ADR-020 decision 5, and it is normative rather than a disclaimer.
A system that cannot state its limits has not established any.


FILES
-----

  trace.json        the execution record
  graph.json        the graph declaration the run was bound to
  bundle.json       both of the above as one Motus trace bundle
  root.txt          the root string, exactly as committed
  root.txt.ots      the OpenTimestamps proof of those bytes
  anchor.json       the anchor receipt: checkpoint, calendars, state
  explain.json      the runtime's own causal account of the run
  derive_root.py    thirty lines of stdlib Python that check all of it
  VERIFY.txt        this file
"""


def main() -> None:
    index = json.loads((OUT / "index.json").read_text(encoding="utf-8"))
    trace = json.loads((OUT / "trace.json").read_text(encoding="utf-8"))
    index = dict(index, run_id=trace["run"]["run_id"])

    members: list[tuple[str, bytes]] = [
        ("VERIFY.txt", verify_text(index).encode("utf-8")),
        ("trace.json", (OUT / "trace.json").read_bytes()),
        ("graph.json", (OUT / "graph.json").read_bytes()),
        ("explain.json", (OUT / "explain.json").read_bytes()),
        ("anchor.json", (OUT / "root.txt.anchor.json").read_bytes()),
        ("root.txt", (OUT / "root.txt").read_bytes()),
        ("root.txt.ots", (OUT / "root.txt.ots").read_bytes()),
        ("derive_root.py", DERIVE.encode("utf-8")),
        ("bundle.json", json.dumps({
            "bundle_version": "1.0.0",
            "graph_spec": json.loads((OUT / "graph.json").read_text("utf-8")),
            "trace": trace,
        }, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"),
    ]

    _write_deterministic_zip(ARCHIVE, members)

    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    index["bundle_file"] = ARCHIVE.name
    index["bundle_sha256"] = f"sha256:{digest}"
    index["bundle_bytes"] = ARCHIVE.stat().st_size
    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"{ARCHIVE.name}  {ARCHIVE.stat().st_size} bytes")
    print(f"sha256:{digest}")


if __name__ == "__main__":
    main()
