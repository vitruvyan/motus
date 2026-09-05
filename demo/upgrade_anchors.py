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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


DEMO = Path(__file__).resolve().parent

#: Where the demo asks what time a Bitcoin block was mined.
#:
#: An OpenTimestamps attestation proves "committed under the merkle root of
#: block N" and says nothing about the hour. The anchor refuses to invent one —
#: it used to publish this machine's clock, three days late, under the label
#: "Evidence timestamp" — so the embedder has to decide whom to ask, and the
#: demo asks a public explorer and RECORDS THAT IT DID.
#:
#: The block height is the proven part. The hour is a lookup, and the receipt
#: says so, because a reader with a Bitcoin node can check the height without
#: trusting anybody and should not be led to think the hour came the same way.
BLOCK_TIME_SOURCE = "https://blockstream.info/api"


def block_time(height: int) -> str:
    """height -> ISO-8601 UTC, from a public explorer. Raises if it cannot."""
    import json as _json
    import urllib.request

    with urllib.request.urlopen(
            f"{BLOCK_TIME_SOURCE}/block-height/{height}", timeout=20) as handle:
        block_hash = handle.read().decode().strip()
    with urllib.request.urlopen(
            f"{BLOCK_TIME_SOURCE}/block/{block_hash}", timeout=20) as handle:
        block = _json.loads(handle.read().decode())
    return (datetime.fromtimestamp(block["timestamp"], timezone.utc)
            .isoformat().replace("+00:00", "Z"))


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


def main(force: bool = False) -> None:
    from motus_anchor_opentimestamps import OpenTimestampsAnchor

    anchor = OpenTimestampsAnchor(block_time=block_time)
    for payload_path, index_path in targets():
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        if payload.get("state") == "anchored" and not force:
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
                entry["anchor"]["calendars_unreachable"] = proof.get(
                    "calendars_unreachable", [])
                # Named, so the page can say the hour was looked up while the
                # block was proven. They are not the same kind of fact.
                entry["anchor"]["block_time_source"] = (
                    BLOCK_TIME_SOURCE if fresh.get("published_at") else None)
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        print(f"{payload_path.parent.name}/{payload_path.name:28s} {fresh['state']:9s} "
              f"{fresh.get('reference') or ''}")


if __name__ == "__main__":
    # `--force` re-asks for a receipt already marked anchored. Needed once,
    # when `published_at` had to be corrected from this machine's clock to the
    # block's own time: the payload said `anchored` and was therefore skipped,
    # so the wrong value would have survived the fix that removed it.
    import sys
    main(force="--force" in sys.argv[1:])
