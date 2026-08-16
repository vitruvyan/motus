"""Anchor the three demo roots on OpenTimestamps — for real.

    .venv/bin/python demo/anchor_domains.py

The scenarios in `three_domains.py` are invented. **These commitments are not.**
Each root is submitted to the public OpenTimestamps calendars, and the returned
`.ots` proof is written beside the trace so a visitor can verify it with
OpenTimestamps' own tooling — not with ours, which is the entire point:

    ots verify demo/out/domains/<name>.ots

## Why this, and not the TRON anchor the first demo used

Free, no wallet, no key, and the thing being trusted is Bitcoin rather than us.
A demo about not having to trust us should not require us to hold a private key
to produce it.

## `pending` is the honest answer and it stays on the page

`publish()` **always** returns `pending`, because an OpenTimestamps commitment
reaches Bitcoin when a block does — hours away, not seconds. ADR-020 decision 7:
a verifier reporting existence for a pending proof states something false.

So the page renders a pending state until the proof is upgraded, and says so.
Running `upgrade_domains` later replaces it with a block height and a time. **A
demo that displayed a confirmation it did not have would be demonstrating the
opposite of the product**, and the absolute rule in the brief is exactly this
one: never write a hash, a block or a confirmation that is not in the payload.
"""

from __future__ import annotations

import json
from pathlib import Path

from motus_anchor_opentimestamps import OpenTimestampsAnchor

OUT = Path(__file__).resolve().parent / "out" / "domains"


def main() -> None:
    index = json.loads((OUT / "index.json").read_text(encoding="utf-8"))
    anchor = OpenTimestampsAnchor()

    for entry in index:
        root = entry["root"]
        # The bytes committed to are the root STRING as this project writes it,
        # `sha256:<hex>` — not the raw digest. A verifier reproducing this has
        # to know which of the two was submitted, so it is stated here and in
        # the receipt rather than left to be guessed.
        receipt = anchor.publish(root.encode("utf-8"))
        payload = receipt.to_dict() if hasattr(receipt, "to_dict") else dict(receipt)

        (OUT / f"{entry['name']}.anchor.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

        proof = payload.get("proof") or {}
        serialized = proof.get("serialized")
        if serialized:
            (OUT / f"{entry['name']}.ots").write_bytes(bytes.fromhex(serialized))

        entry["anchor"] = {
            "network": payload.get("network"),
            "state": payload.get("state"),
            "checkpoint": payload.get("checkpoint"),
            "committed_bytes": "the root string, utf-8",
            "calendars_accepted": proof.get("calendars_accepted", []),
            "calendars_refused": proof.get("calendars_refused", {}),
            "submitted_at": proof.get("submitted_at"),
            "proof_file": f"demo/out/domains/{entry['name']}.ots",
            "verify_with": f"ots verify demo/out/domains/{entry['name']}.ots",
        }
        print(f"{entry['name']:22s} {payload.get('state'):9s} "
              f"{len(proof.get('calendars_accepted', []))} calendars  {root[:26]}…")

    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\nstate is `pending` and stays pending on the page until a Bitcoin "
          "block carries it. That is the honest answer, not a limitation to "
          "design around.")


if __name__ == "__main__":
    main()
