"""Probe: what can a caller actually put into a run_id and into recorded state?

Before attacking the sink, find out which hostile inputs the *writer* even lets
through.  Anything the runtime rejects is not the sink's problem; anything it
accepts is a byte the sink has to survive.
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from x6_common import (  # noqa: E402
    CHAIN, NOW, Report, State, Fact, JsonlTraceSink, SYNC, registry, runtime, scratch,
)

LS = " "
PS = " "
NEL = ""
LONE = "\ud800"

VALUES = {
    "nan": float("nan"),
    "inf": float("inf"),
    "ninf": float("-inf"),
    "u2028": f"before{LS}after",
    "u2029": f"before{PS}after",
    "u0085": f"before{NEL}after",
    "lone_surrogate": f"lone{LONE}end",
    "nul": "a\x00b",
    "newline": "a\nb",
    "crlf": "a\r\nb",
    "big": "x" * 100,
}

RUN_IDS = {
    "traversal": "../../escaped",
    "absolute": "/etc/passwd",
    "dot": ".",
    "dotdot": "..",
    "nul": "a\x00b",
    "newline": "a\nb",
    "control": "a\x01\x02b",
    "lone_surrogate": f"run{LONE}id",
    "unicode": "テスト" * 10,
    "leading_dash": "-rf",
    "sanitises_empty": "///",
    "windows_con": "CON",
    "long_a": "A" * 96 + "1",
    "long_b": "A" * 96 + "2",
    "trailing_dot": "run.",
    "u2028": f"run{LS}id",
}


def main() -> int:
    rep = Report("x6_01 probe: what the writer accepts")

    for name, value in VALUES.items():
        state = State.empty("probe")
        try:
            state = state.with_fact(Fact("k", value, "x6", NOW))
        except BaseException as exc:  # noqa: BLE001
            rep.note(f"value {name}: REJECTED by State/Fact: {type(exc).__name__}: {exc}")
            continue
        try:
            out = json.dumps(state.to_dict(), ensure_ascii=False, allow_nan=False)
            rep.note(f"value {name}: accepted, strict-json ok ({len(out)}b)")
        except BaseException as exc:  # noqa: BLE001
            rep.note(f"value {name}: ACCEPTED by State but strict json refuses: "
                     f"{type(exc).__name__}: {exc}")

    for name, run_id in RUN_IDS.items():
        directory = scratch(f"probe-runid-{name}")
        sink = JsonlTraceSink(directory, fsync=False)
        try:
            runtime(sink=sink).run(State.empty("probe"), run_id=run_id)
        except BaseException as exc:  # noqa: BLE001
            rep.note(f"run_id {name!r}: run raised {type(exc).__name__}: {exc}")
        names = sorted(p.name for p in directory.iterdir())
        rep.note(f"run_id {name}: files={names}")

    rep.dump()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
