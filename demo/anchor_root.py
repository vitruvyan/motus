"""Publish a run's root to the TRON Nile testnet, and record the receipt.

    MOTUS_DEMO_ANCHOR_PRIVATE_KEY=<hex> python demo/anchor_root.py

Motus ships no anchor. This script is the one thing in the demo that talks
to a network outside this machine: it reads the root that
`demo/attack_this_run.py` already computed, publishes it as the memo field
of an ordinary TRX transfer on a public test network, and writes the receipt
`_anchor()` in attack_this_run.py knows how to read.

It publishes the root only — a 71-character digest. The decision itself,
the citations, the reviewer, never leave this machine. What gets committed
on-chain commits to nothing more than "this exact digest existed by this
exact block".

Because the run's own timestamps are wall-clock (`run.created_ts`), two
runs of attack_this_run.py never produce the same root — so a receipt is
only ever valid for the one root it names. This script therefore also
patches the anchor straight into the JSON it read from, rather than relying
on a later re-run of attack_this_run.py to pick the receipt up: a re-run
would compute a fresh root the old receipt does not cover, and would be
correctly refused by `_anchor()`. Regenerate the demo, then re-run this
script, in that order, whenever the run changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from tronpy import Tron
    from tronpy.keys import PrivateKey
    from tronpy.providers import HTTPProvider
except ImportError:
    sys.exit(
        "tronpy is not installed. This script is not part of the Motus "
        "runtime's own dependencies — install it only where you intend to "
        "publish: pip install tronpy"
    )

DEMO_DIR = Path(__file__).resolve().parent
DEFAULT_JSON = DEMO_DIR / "out" / "attack_this_run.json"
DEFAULT_RECEIPT = DEMO_DIR / "out" / "anchor_receipt.json"

ENDPOINTS = {
    "nile": "https://api.nileex.io",
}
EXPLORERS = {
    "nile": "https://nile.tronscan.org/#/transaction/",
}

MEMO_PREFIX = "VITRUVYAN_AUDIT:"


def publish(root: str, private_key_hex: str, network: str) -> dict:
    """Send the memo transaction and return the receipt fields.

    A minimal self-transfer: 1 sun, sender is the receiver. The transfer is
    not the point and the amount is deliberately not zero, since a zero-value
    transfer is rejected by the network. What matters is the memo field.
    """
    client = Tron(HTTPProvider(ENDPOINTS[network]))
    priv = PrivateKey(bytes.fromhex(private_key_hex))
    addr = priv.public_key.to_base58check_address()
    memo = f"{MEMO_PREFIX}{root}"

    txn = (
        client.trx.transfer(addr, addr, 1)
        .memo(memo)
        .fee_limit(10_000_000)
        .build()
        .sign(priv)
    )
    txn.broadcast()
    receipt = txn.wait()

    if receipt.get("result") == "FAILED" or "blockNumber" not in receipt:
        raise SystemExit(f"transaction did not succeed on-chain: {receipt}")

    block = receipt["blockNumber"]
    block_ts_ms = receipt["blockTimeStamp"]
    published_at = datetime.fromtimestamp(
        block_ts_ms / 1000, tz=timezone.utc
    ).isoformat().replace("+00:00", "Z")

    return {
        "network": network,
        "txid": txn.txid,
        "memo": memo,
        "explorer_url": f"{EXPLORERS[network]}{txn.txid}",
        "published_at": published_at,
        "block": block,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON,
                         help="the payload to read the root from, and to patch")
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT,
                         help="where to also write the standalone receipt")
    parser.add_argument("--network", choices=list(ENDPOINTS), default="nile")
    parser.add_argument("--force", action="store_true",
                         help="publish again even if the payload already has an anchor")
    args = parser.parse_args()

    private_key_hex = os.environ.get("MOTUS_DEMO_ANCHOR_PRIVATE_KEY")
    if not private_key_hex:
        sys.exit("set MOTUS_DEMO_ANCHOR_PRIVATE_KEY to the Nile testnet private key")

    data = json.loads(args.json.read_text(encoding="utf-8"))
    root = data.get("root")
    if not root:
        sys.exit(f"{args.json} has no root — run attack_this_run.py --json first")

    existing = data.get("anchor") or {}
    if existing.get("txid") and not args.force:
        sys.exit(
            f"{args.json} is already anchored at {existing.get('txid')} "
            "for this exact root. Pass --force to publish a new transaction anyway."
        )

    print(f"publishing root as memo on {args.network}: {root}")
    anchor = publish(root, private_key_hex, args.network)
    print(f"txid  {anchor['txid']}")
    print(f"block {anchor['block']}")
    print(f"seen  {anchor['explorer_url']}")

    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(anchor, indent=2) + "\n", encoding="utf-8")
    print(f"receipt written: {args.receipt}")

    data["anchor"] = anchor
    args.json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"payload patched: {args.json}")


if __name__ == "__main__":
    main()
