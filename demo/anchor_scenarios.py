"""Anchor the two scenario roots on OpenTimestamps — for real.

    .venv/bin/python demo/anchor_scenarios.py

The scenarios in `two_scenarios.py` are invented. **These commitments are not.**
Each root is submitted to the public OpenTimestamps calendars and the returned
`.ots` proof is written beside the trace, named `root.txt.ots` so that
OpenTimestamps' own client finds its target by stripping the suffix:

    ots verify root.txt.ots

`pending` is the honest answer and it stays on the page. A commitment reaches
Bitcoin when a block does — hours away, not seconds — and ADR-020 decision 7 is
explicit that a verifier reporting EXISTENCE for a pending proof states
something false. Re-running this script leaves an already-submitted root alone,
because a second publish restarts the aggregation and would make the evidence
younger rather than older.
"""

from __future__ import annotations

import json
from pathlib import Path

from anchor_domains import commit
from motus_anchor_opentimestamps import OpenTimestampsAnchor

OUT = Path(__file__).resolve().parent / "out" / "scenarios"


def main() -> None:
    anchor = OpenTimestampsAnchor()
    index = []
    for directory in sorted(p for p in OUT.iterdir() if p.is_dir()):
        path = directory / "index.json"
        entry = json.loads(path.read_text(encoding="utf-8"))
        if (entry.get("anchor") or {}).get("state"):
            print(f"{entry['name']:20s} {entry['anchor']['state']:9s} "
                  f"already submitted {entry['anchor'].get('submitted_at')} "
                  f"— left alone")
        else:
            entry["anchor"] = commit(anchor, entry["root"], directory, "root.txt")
            print(f"{entry['name']:20s} {entry['anchor']['state']:9s} "
                  f"{len(entry['anchor']['calendars_accepted'])} calendars  "
                  f"{entry['root'][:26]}…")
        path.write_text(json.dumps(entry, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        index.append(entry)

    (OUT / "index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\nstate stays `pending` on the page until a Bitcoin block carries "
          "it. That is the honest answer, not a limitation to design around.")


if __name__ == "__main__":
    main()
