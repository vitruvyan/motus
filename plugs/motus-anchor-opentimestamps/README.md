# motus-anchor-opentimestamps

An `Anchor` for Motus checkpoints, backed by [OpenTimestamps][ots]: free,
aggregated into Bitcoin, **no wallet, no key custody, no gas, and no treasury
that runs dry on a Saturday night.** That last list is why ADR-021 names it the
production default and keeps TRON for demonstrations — a memo is legible in a
meeting and an `.ots` proof is not, but legibility is not what a receipt is for.

```python
from datetime import datetime, timezone
import json
from urllib.request import urlopen
from motus_anchor_opentimestamps import OpenTimestampsAnchor

def block_time(height):
    with urlopen(f"https://blockstream.info/api/block-height/{height}") as response:
        block_hash = response.read().decode().strip()
    with urlopen(f"https://blockstream.info/api/block/{block_hash}") as response:
        timestamp = json.load(response)["timestamp"]
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")

anchor = OpenTimestampsAnchor(
    block_time=block_time,
    block_time_source="https://blockstream.info/api/")
receipt = anchor.publish(checkpoint_digest_bytes)   # -> state="pending"
...                                                 # hours later
receipt = anchor.upgrade(receipt)                   # -> state="anchored", or still pending
```

## It is pending, and that is not a defect

An OpenTimestamps commitment reaches Bitcoin when a block does, which takes
**hours**. `publish` therefore always returns `state="pending"`, and ADR-020
decision 7 is explicit that a verifier reporting `EXISTENCE` for a pending
proof states something false. Nothing here rounds that up.

`state()` derives pending or anchored from the proof without contacting a
calendar. `upgrade()` refuses to guess a publication time: OpenTimestamps
proves the block, not its wall-clock time, so an anchored receipt requires a
resolver supplied by the embedder. A resolver-backed receipt records its
`block_time_source`; failures from a supplied resolver still raise.

## Install

Not on PyPI, and neither is Motus.

    pip install ./plugs/motus-anchor-opentimestamps

`vitruvyan-motus` declares **zero** runtime dependencies and this package must
not be able to change that — which is why it is a separate distribution rather
than an extra.

[ots]: https://opentimestamps.org/
