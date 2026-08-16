"""Ask the calendars whether the commitments have reached a Bitcoin block yet.

    .venv/bin/python demo/upgrade_anchors.py

Run this a few hours after anchoring, and again the next day. An OpenTimestamps
commitment reaches Bitcoin when a block does, so `pending` is not a failure and
not a permanent state — it is the honest answer for a while.

Rewrites each `*.anchor.json` and its `index.json` entry in place. The demo
page reads `state` from that payload, so the audit card answers differently by
itself the moment this script succeeds: nobody has to remember to edit copy,
and nobody CAN publish an `anchored` badge that the proof does not carry.

`upgrade()` returns a NEW receipt and leaves the old one valid — it was true
when it was written, and a pending proof does not become false by being
superseded. A calendar that cannot answer leaves the receipt exactly as it was,
because an outage is not a verdict.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from motus_anchor_opentimestamps import OpenTimestampsAnchor

DEMO = Path(__file__).resolve().parent


def targets() -> list[tuple[Path, Path]]:
    """(anchor payload, index carrying it) for every anchored artefact here."""
    found: list[tuple[Path, Path]] = []
    for index_path in sorted(DEMO.glob("out/*/index.json")) + sorted(
            DEMO.glob("out/*/*/index.json")):
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
            anchor = entry.get("anchor") or {}
            proof_file = anchor.get("proof_file")
            if not proof_file:
                continue
            stem = Path(proof_file).name[: -len(".ots")]
            candidate = index_path.parent / f"{stem}.anchor.json"
            if candidate.exists():
                found.append((candidate, index_path))
    return found


def main() -> None:
    anchor = OpenTimestampsAnchor()
    for payload_path, index_path in targets():
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        if payload.get("state") == "anchored":
            print(f"{payload_path.parent.name}/{payload_path.name:28s} already anchored "
                  f"{payload.get('reference')}")
            continue
        receipt = SimpleNamespace(checkpoint=payload["checkpoint"],
                                  proof=payload["proof"], state=payload["state"])
        try:
            upgraded = anchor.upgrade(receipt)
        except Exception as error:                      # noqa: BLE001
            print(f"{payload_path.parent.name}/{payload_path.name:28s} "
                  f"could not ask: {type(error).__name__}: {error}")
            continue

        fresh = (upgraded.to_dict() if hasattr(upgraded, "to_dict")
                 else dict(upgraded))
        payload_path.write_text(
            json.dumps(fresh, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        proof = fresh.get("proof") or {}
        if proof.get("serialized"):
            (payload_path.parent / f"{payload_path.name[: -len('.anchor.json')]}.ots"
             ).write_bytes(bytes.fromhex(proof["serialized"]))

        index = json.loads(index_path.read_text(encoding="utf-8"))
        entries = index if isinstance(index, list) else [index]
        for entry in entries:
            if (entry.get("anchor") or {}).get("checkpoint") == fresh["checkpoint"]:
                entry["anchor"]["state"] = fresh["state"]
                entry["anchor"]["reference"] = fresh.get("reference")
                entry["anchor"]["published_at"] = fresh.get("published_at")
                entry["anchor"]["bitcoin_block_heights"] = proof.get(
                    "bitcoin_block_heights", [])
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        print(f"{payload_path.parent.name}/{payload_path.name:28s} {fresh['state']:9s} "
              f"{fresh.get('reference') or ''}")


if __name__ == "__main__":
    main()
