# motus-anchor-opentimestamps

An `Anchor` for Motus checkpoints, backed by [OpenTimestamps][ots]: free,
aggregated into Bitcoin, **no wallet, no key custody, no gas, and no treasury
that runs dry on a Saturday night.** That last list is why ADR-021 names it the
production default and keeps TRON for demonstrations — a memo is legible in a
meeting and an `.ots` proof is not, but legibility is not what a receipt is for.

```python
from motus_anchor_opentimestamps import OpenTimestampsAnchor

anchor = OpenTimestampsAnchor()
receipt = anchor.publish(checkpoint_digest_bytes)   # -> state="pending"
...                                                 # hours later
receipt = anchor.upgrade(receipt)                   # -> state="anchored", or still pending
```

## It is pending, and that is not a defect

An OpenTimestamps commitment reaches Bitcoin when a block does, which takes
**hours**. `publish` therefore always returns `state="pending"`, and ADR-020
decision 7 is explicit that a verifier reporting `EXISTENCE` for a pending
proof states something false. Nothing here rounds that up.

`state()` and `upgrade()` ask the calendars whether the commitment has been
attested yet. Until one says so, the honest answer is the one you already have.

## Install

Not on PyPI, and neither is Motus.

    pip install ./plugs/motus-anchor-opentimestamps

`vitruvyan-motus` declares **zero** runtime dependencies and this package must
not be able to change that — which is why it is a separate distribution rather
than an extra.

[ots]: https://opentimestamps.org/
