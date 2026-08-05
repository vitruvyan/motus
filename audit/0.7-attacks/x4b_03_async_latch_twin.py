"""x4b-03 — is the latch defect symmetric on AsyncStreamDriver?

`AsyncStreamDriver` already carried `_requested` before this commit, so if the
async twin shows the same "rejected reason burns the latch" behaviour on both
builds, the sync one is a NEW regression and the async one is pre-existing.
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/home/vitruvyan/motus/.attack")

from vitruvyan_motus import State  # noqa: E402
from x4b_common import Report, async_runtime, kinds  # noqa: E402


async def scenario() -> tuple[str, list[str]]:
    runtime = async_runtime()
    driver = runtime.astream(State.empty("alatch"))
    await driver.__anext__()
    try:
        await driver.aclose(123)  # type: ignore[arg-type]
    except TypeError:
        pass
    await driver.aclose("really stop now")
    trace = driver.trace
    return trace.records[-1]["kind"], kinds(trace)


def main() -> int:
    report = Report("x4b-03 async twin of the latch defect")
    terminal, seq = asyncio.run(scenario())
    report.record(
        "aclose() retry after a rejected reason still cancels",
        terminal == "run_cancelled",
        f"terminal={terminal!r} kinds={seq}",
    )
    return report.dump()


if __name__ == "__main__":
    raise SystemExit(main())
