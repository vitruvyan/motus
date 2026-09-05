"""Anchor the three demo roots on OpenTimestamps — for real.

    .venv/bin/python demo/anchor_domains.py

The scenarios in `three_domains.py` are invented. **These commitments are not.**
Each root is submitted to the public OpenTimestamps calendars, and the returned
`.ots` proof is written beside the trace so a visitor can verify it with
OpenTimestamps' own tooling — not with ours, which is the entire point:

    ots verify -f demo/out/domains/<name>.root.txt demo/out/domains/<name>.root.txt.ots

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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from motus_anchor_opentimestamps import OpenTimestampsAnchor

OUT = Path(__file__).resolve().parent / "out" / "domains"


def commit(anchor: "OpenTimestampsAnchor", root: str, out: Path,
           stem: str) -> dict:
    """Submit one root and write the proof beside it.

    The bytes committed to are the root STRING as this project writes it,
    `sha256:<hex>` — not the raw digest. A verifier reproducing this has to
    know which of the two was submitted, so it is stated here, in the receipt,
    and in the bundle's VERIFY.txt rather than left to be guessed.
    """
    receipt = anchor.publish(root.encode("utf-8"))
    payload = receipt.to_dict() if hasattr(receipt, "to_dict") else dict(receipt)

    (out / f"{stem}.anchor.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    proof = payload.get("proof") or {}
    serialized = proof.get("serialized")
    if serialized:
        # Keep the committed bytes beside the proof. The default client finds
        # this target by stripping `.ots` from the proof name.
        (out / stem).write_bytes(root.encode("utf-8"))
        (out / f"{stem}.ots").write_bytes(bytes.fromhex(serialized))

    repo_root = Path(__file__).resolve().parent.parent
    display_target = (out / stem).relative_to(repo_root).as_posix()
    display_proof = f"{display_target}.ots"
    return {
        "network": payload.get("network"),
        "state": payload.get("state"),
        "checkpoint": payload.get("checkpoint"),
        "committed_bytes": "the root string, utf-8",
        "calendars_accepted": proof.get("calendars_accepted", []),
        "calendars_refused": proof.get("calendars_refused", {}),
        "submitted_at": proof.get("submitted_at"),
        "proof_file": display_proof,
        "verify_with": f"ots verify -f {display_target} {display_proof}",
    }


def anchor_hiring(anchor: "OpenTimestampsAnchor") -> None:
    """The demo's spine, whose index is one object rather than a list."""
    out = Path(__file__).resolve().parent / "out" / "hiring"
    index_path = out / "index.json"
    if not index_path.exists():
        print("hiring: nothing generated yet — run demo/hiring_review.py first")
        return
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if (index.get("anchor") or {}).get("state"):
        print(f"{'hiring_review':22s} {index['anchor']['state']:9s} "
              f"already submitted {index['anchor'].get('submitted_at')} "
              f"\u2014 left alone")
        return
    index["anchor"] = commit(anchor, index["root"], out, "root.txt")
    index_path.write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{'hiring_review':22s} {index['anchor']['state']:9s} "
          f"{len(index['anchor']['calendars_accepted'])} calendars  "
          f"{index['root'][:26]}\u2026")


def main() -> None:
    from motus_anchor_opentimestamps import OpenTimestampsAnchor

    index = json.loads((OUT / "index.json").read_text(encoding="utf-8"))
    anchor = OpenTimestampsAnchor()

    for entry in index:
        # **Do not resubmit what is already submitted.** A second publish
        # returns a fresh proof whose aggregation starts over, throwing away
        # however many hours the first one had already spent waiting for a
        # block. Re-running this script must not make the evidence younger.
        if (entry.get("anchor") or {}).get("state"):
            print(f"{entry['name']:22s} {entry['anchor']['state']:9s} "
                  f"already submitted {entry['anchor'].get('submitted_at')} "
                  f"\u2014 left alone")
            continue
        entry["anchor"] = commit(
            anchor, entry["root"], OUT, f"{entry['name']}.root.txt")
        print(f"{entry['name']:22s} {entry['anchor']['state']:9s} "
              f"{len(entry['anchor']['calendars_accepted'])} calendars  "
              f"{entry['root'][:26]}\u2026")

    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    anchor_hiring(anchor)

    print("\nstate is `pending` and stays pending on the page until a Bitcoin "
          "block carries it. That is the honest answer, not a limitation to "
          "design around.")


if __name__ == "__main__":
    main()
