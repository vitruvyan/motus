"""Final gate — area 9: multiprocessing-compatible rejection serialization."""
from __future__ import annotations
import multiprocessing
from datetime import datetime, timezone
from vitruvyan_motus import Rejection

NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)


def child(inbox, outbox):
    from vitruvyan_motus.trace import _MISSING as M
    obj = inbox.get()
    outbox.put((obj.to_dict(), "evidence" not in obj.to_dict(), obj.evidence is M))


if __name__ == "__main__":
    for method in ("spawn", "fork", "forkserver"):
        try:
            ctx = multiprocessing.get_context(method)
            inbox, outbox = ctx.Queue(), ctx.Queue()
            p = ctx.Process(target=child, args=(inbox, outbox))
            p.start()
            inbox.put(Rejection("w", "r", NOW))
            wire, absent, singleton = outbox.get(timeout=30)
            p.join(timeout=30)
            print(f"   {method:12}: wire={wire} absent={absent} child_singleton={singleton}")
        except Exception as exc:
            print(f"   {method:12}: {type(exc).__name__}: {str(exc)[:70]}")
