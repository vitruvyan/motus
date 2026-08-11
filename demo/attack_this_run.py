"""Attack this run — what a trace can prove that a log cannot.

    python demo/attack_this_run.py            # prints the verdict table
    python demo/attack_this_run.py --html out.html

One decision, recorded twice: once as an event log of the kind most systems
keep — a row per event, in a table the system itself owns — and once as a Motus
trace. Then the same seven tamperings are applied to both, and something that
is not this program decides whether each one is visible.

The point is not that the log is written badly. It is that a log answers "what
happened" and is not built to answer "how do I know this was not edited
afterwards" — and that the second question is the one an auditor, a regulator
or an opposing lawyer actually asks.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from vitruvyan_motus import (  # noqa: E402
    Decision, DurabilityProfile, EffectClass, EffectDescriptor, EffectReceipt,
    Fact, GraphSpec, JsonlTraceSink, ReplayStatus, Runtime, State,
)
from vitruvyan_motus.trace import Trace, _canonical_bytes  # noqa: E402

T0 = datetime(2026, 8, 12, 9, 14, 0, tzinfo=timezone.utc)

# A verdict pipeline of the shape a review desk actually runs: pull the sources,
# check their classification, write the answer, seal it in the vault.
SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0",
    "name": "verdict-desk",
    "version": "1.0.0",
    "entry": "retrieve",
    "nodes": [
        {"name": "retrieve", "effect_class": "recorded_effect",
         "writes_declared": ["citations"]},
        {"name": "classify", "effect_class": "pure",
         "reads_declared": ["citations"], "writes_declared": ["tlp_ceiling", "releasable"]},
        {"name": "decide", "effect_class": "pure",
         "reads_declared": ["citations", "tlp_ceiling"], "writes_declared": ["verdict"]},
        {"name": "seal", "effect_class": "external_effect", "writes_declared": ["sealed"]},
    ],
    "transitions": {
        "retrieve": {"kind": "next", "to": "classify"},
        "classify": {"kind": "route", "on": "releasable",
                     "map": {"yes": "decide"}, "default": "seal"},
        "decide": {"kind": "next", "to": "seal"},
        "seal": {"kind": "terminal"},
    },
})

CITATIONS = [
    {"doc": "SOC-2024-0871.pdf", "page": 4, "tlp": "TLP:AMBER"},
    {"doc": "CERT-EU-ADV-2026-11.pdf", "page": 1, "tlp": "TLP:CLEAR"},
    {"doc": "internal-IR-4471.pdf", "page": 12, "tlp": "TLP:RED"},
]


def _retrieve(state: State, ctx) -> State:
    ctx.record_effect(EffectDescriptor(
        EffectClass.RECORDED_EFFECT, "GET vault://corpus?q=CVE-2026-3311",
        idempotency_key="corpus-4471",
        receipt=EffectReceipt("vault:read:88213", status="completed"),
    ))
    return state.with_fact(Fact("citations", CITATIONS, "corpus", T0))


def _classify(state: State) -> State:
    citations = state.fact("citations")
    ceiling = max(c["tlp"] for c in citations)
    return (state
            .with_fact(Fact("tlp_ceiling", ceiling, "classifier", T0))
            .with_decision(Decision(
                "releasable", "yes" if len(citations) >= 2 else "no", T0,
                reason=f"{len(citations)} sources, ceiling {ceiling}")))


def _decide(state: State) -> State:
    return state.with_fact(Fact("verdict", "APPROVED", "curator", T0))


def _seal(state: State, ctx) -> State:
    ctx.record_effect(EffectDescriptor(
        EffectClass.EXTERNAL_EFFECT, "POST vault://seal/4471",
        idempotency_key="seal-4471",
        receipt=EffectReceipt("vault:seal:99017", status="completed"),
    ))
    return state.with_fact(Fact("sealed", True, "vault", T0))


def produce_trace(workspace: Path) -> tuple[dict, str | None, Path]:
    """Run the graph for real and return (document, root, artifact)."""
    sink = JsonlTraceSink(workspace, fsync=False)
    result = Runtime(
        SPEC,
        {"retrieve": _retrieve, "classify": _classify,
         "decide": _decide, "seal": _seal},
        sink=sink, durability_profile=DurabilityProfile.SYNCHRONOUS,
    ).run(
        State.empty("release verdict on submission 4471",
                    metadata={"desk": "cti", "reviewer": "r-118"}),
        run_id="sub-4471",
        replay=ReplayStatus.declared("partial", constraints=["node:retrieve:external_source"]),
    )
    return result.trace.to_dict(), result.trace.root, sink.artifacts[0]


# --------------------------------------------------------------------------
# The same run as an event log, in the shape systems keep them.
#
# Modelled on a real one: a row per event, correlated by trace_id, carrying a
# causation_id and a sha256 of the payload. That per-row hash is the strongest
# thing such a table usually has, and it is what this demo credits it with.
# --------------------------------------------------------------------------

def produce_log(document: dict) -> list[dict]:
    rows: list[dict] = []
    previous_id = None
    for i, record in enumerate(document["records"]):
        payload = {k: v for k, v in record.items() if k != "integrity"}
        row = {
            "event_id": f"evt-{i + 1:04d}",
            "trace_id": document["run"]["run_id"],
            "causation_id": previous_id,
            "event_type": record["kind"],
            "source": record.get("node") or "runner",
            "channel": "bus.verdict",
            "payload": payload,
            "payload_hash": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "created_at": (T0 + timedelta(milliseconds=120 * i)).isoformat(),
        }
        rows.append(row)
        previous_id = row["event_id"]
    return rows


def check_log(rows: list[dict]) -> tuple[bool, str]:
    """Everything such a table can check about itself.

    Per-row digests, and the causation links being intact. There is nothing
    else to check: no row commits to any other, and no value commits to the set.
    """
    for row in rows:
        recomputed = hashlib.sha256(
            json.dumps(row["payload"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if recomputed != row["payload_hash"]:
            return False, f"{row['event_id']}: payload_hash does not match the payload"
    seen = {row["event_id"] for row in rows}
    for row in rows:
        if row["causation_id"] is not None and row["causation_id"] not in seen:
            return False, f"{row['event_id']}: causation_id names an event that is not here"
    return True, "accepted: every row's digest matches its payload"


def check_trace(document: dict, workspace: Path, name: str) -> tuple[bool, str]:
    """The verdict is NOT this program's. It is motus-validate's, in another
    process, reading only the file and the published contract."""
    artifact = workspace / f"{name}.json"
    artifact.write_text(json.dumps(document), encoding="utf-8")
    spec = workspace / "spec.json"
    spec.write_text(json.dumps(SPEC.to_dict()), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(ROOT / "contract" / "validate.py"),
         "trace", str(artifact), "--spec", str(spec)],
        capture_output=True, text=True,
    )
    if done.returncode == 0:
        return True, "accepted"
    first = (done.stdout + done.stderr).strip().splitlines()[0]
    return False, first


def reseal(document: dict) -> dict:
    """What an editor who read the contract does: fix every hash after editing.

    No privilege and no secret: rule T11 is published, precisely so that anyone
    can recompute the chain. That is what makes the ROOT — and not the chain —
    the thing worth publishing where the editor cannot reach it.
    """
    trace = Trace(document["run"], schema_version=document["schema_version"])
    for record in document["records"]:
        bare = dict(record)
        bare["integrity"] = {"payload_hash": None, "prev_hash": None}
        trace = trace._append_runtime(trace._seal(bare))
    return trace.to_dict()


def reseal_stale_header(document: dict) -> dict:
    """Reseal, but leave the first link pointing at the OLD header digest.

    The subtlest of the seven, and the one that says why an anchor holder must
    recompute rather than compare. Every declared digest recomputes; the chain
    reads as internally perfect; and the terminal digest does not move, because
    a digest covers the STRING naming its predecessor and not the predecessor.
    """
    prev = document["records"][0]["integrity"]["prev_hash"]
    for record in document["records"]:
        payload = dict(record)
        payload["integrity"] = {"payload_hash": None, "prev_hash": prev}
        digest = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
        record["integrity"] = {"payload_hash": digest, "prev_hash": prev}
        prev = digest
    return document


def _tampers():
    def flip_verdict_trace(d):
        for record in d["records"]:
            for fact in (record.get("writes") or {}).get("facts", []):
                if fact["key"] == "verdict":
                    fact["value"] = "REJECTED"

    def flip_verdict_log(rows):
        for row in rows:
            for fact in (row["payload"].get("writes") or {}).get("facts", []):
                if fact["key"] == "verdict":
                    fact["value"] = "REJECTED"

    def declassify_trace(d):
        for record in d["records"]:
            for fact in (record.get("writes") or {}).get("facts", []):
                if fact["key"] == "citations":
                    fact["value"][2]["tlp"] = "TLP:CLEAR"

    def declassify_log(rows):
        for row in rows:
            for fact in (row["payload"].get("writes") or {}).get("facts", []):
                if fact["key"] == "citations":
                    fact["value"][2]["tlp"] = "TLP:CLEAR"

    return [
        ("Ribalta il verdetto", "APPROVED diventa REJECTED",
         flip_verdict_trace, flip_verdict_log, False),
        ("Declassifica una fonte", "la citazione TLP:RED diventa TLP:CLEAR",
         declassify_trace, declassify_log, False),
        ("Cancella un passo", "il passo che ha classificato le fonti sparisce",
         lambda d: d["records"].pop(3), lambda rows: rows.pop(3), False),
        ("Riscrive di chi e' la run", "run_id sub-4471 diventa sub-0000",
         lambda d: d["run"].__setitem__("run_id", "sub-0000"),
         lambda rows: [r.__setitem__("trace_id", "sub-0000") for r in rows], False),
        ("Cambia la policy", "strict diventa exploration: gli errori non fermano piu' la run",
         lambda d: d["run"].__setitem__("policy", "exploration"),
         lambda rows: None, False),
        ("Duplica un passo", "un passo compare due volte",
         lambda d: d["records"].insert(3, copy.deepcopy(d["records"][3])),
         lambda rows: rows.insert(3, copy.deepcopy(rows[3])), False),
        ("Ribalta il verdetto E RISIGILLA", "l'attaccante conosce il contratto e rifa' ogni hash",
         flip_verdict_trace, flip_verdict_log, True),
        ("Riscrive l'intestazione, link stantio",
         "cambia la policy e lascia il primo prev_hash dov'era: ogni hash torna",
         lambda d: d["run"].__setitem__("policy", "exploration"),
         lambda rows: None, "stale"),
    ]


def run_demo() -> dict:
    workspace = Path(tempfile.mkdtemp(prefix="motus-demo-"))
    document, root, artifact = produce_trace(workspace)
    rows = produce_log(document)

    baseline_log = check_log(copy.deepcopy(rows))
    baseline_trace = check_trace(copy.deepcopy(document), workspace, "pristine")

    results = []
    for title, detail, on_trace, on_log, resealing in _tampers():
        edited_doc = json.loads(json.dumps(document))
        on_trace(edited_doc)
        if resealing == "stale":
            edited_doc = reseal_stale_header(edited_doc)
        elif resealing:
            edited_doc = reseal(edited_doc)
        trace_ok, trace_msg = check_trace(edited_doc, workspace, "tampered")
        # Derived, never read back. The raw field is what the tampered document
        # CLAIMS its root is, and for two of the tampers below it claims the
        # published value exactly. `Trace.root` recomputes the chain from the
        # document and answers None when the claim is not earned.
        try:
            new_root = Trace.from_dict(edited_doc).root
        except (ValueError, TypeError):
            new_root = None
        raw_root = (edited_doc["records"][-1]["integrity"]["payload_hash"]
                    if edited_doc["records"] else None)

        edited_rows = copy.deepcopy(rows)
        on_log(edited_rows)
        if resealing:
            for row in edited_rows:
                row["payload_hash"] = hashlib.sha256(json.dumps(
                    row["payload"], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
        log_ok, log_msg = check_log(edited_rows)

        results.append({
            "title": title, "detail": detail,
            "log_caught": not log_ok, "log_msg": log_msg,
            "trace_caught": not trace_ok, "trace_msg": trace_msg,
            "root_moved": new_root != root,
            "raw_field_unmoved": raw_root == root,
            "new_root": new_root,
        })
    return {
        "root": root, "artifact": str(artifact), "workspace": str(workspace),
        "records": len(document["records"]),
        "baseline_log": baseline_log, "baseline_trace": baseline_trace,
        "results": results, "document": document, "rows": rows,
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _who_caught(result: dict) -> tuple[str, str]:
    """Which of the two checks noticed, if either.

    The distinction matters and the demo would lie without it. The validator
    reads the file alone and catches an editor who did not reseal. The anchor
    catches the one who did — and only the anchor can, because a resealed trace
    is internally perfect. That is why the root is published and the chain is not.
    """
    if result["trace_caught"]:
        if result["raw_field_unmoved"] and result["new_root"] is None:
            # The chain reads as internally perfect and the raw field still
            # carries the published value: anyone comparing that field to the
            # anchor sees agreement. Only running the validator, or asking
            # `trace.root` — which recomputes — disagrees.
            return "validatore", (
                "il campo grezzo non si muove: chi lo confronta con l'ancora "
                "vede accordo. trace.root risponde None")
        return "validatore", result["trace_msg"]
    if result["root_moved"]:
        return "ancora", "la traccia e' coerente, ma la root non e' piu' quella pubblicata"
    return "nessuno", "invisibile"


def render_terminal(data: dict) -> None:
    print(f"\nuna run, {data['records']} record")
    print(f"root  {data['root']}")
    print(f"prova {data['artifact']}\n")
    print(f"{'manomissione':38s} {'event log':>12s}   {'traccia Motus':>16s}")
    print("-" * 72)
    for result in data["results"]:
        who, _ = _who_caught(result)
        log = "beccata" if result["log_caught"] else "invisibile"
        trace = "invisibile" if who == "nessuno" else f"beccata ({who})"
        print(f"{result['title']:38s} {log:>12s}   {trace:>16s}")
    print("-" * 72)
    caught_log = sum(1 for r in data["results"] if r["log_caught"])
    caught_trace = sum(1 for r in data["results"] if _who_caught(r)[0] != "nessuno")
    total = len(data["results"])
    print(f"{'':38s} {f'{caught_log}/{total}':>12s}   {f'{caught_trace}/{total}':>16s}\n")
    print("Il verdetto sulla traccia non lo da' questo programma: lo da'")
    print("contract/validate.py, in un altro processo, leggendo solo il file")
    print("e il contratto pubblicato.\n")


CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#272e38;--ink:#e6edf3;--dim:#8b949e;
--ok:#3fb950;--bad:#f85149;--warn:#d29922;--acc:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--dim);margin:0 0 32px;font-size:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:20px 22px;margin-bottom:18px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.09em;
color:var(--dim);margin:0 0 14px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}
.kv .k{color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.kv .v{font-size:15px;margin-top:3px}
.qa{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.qa .q{color:var(--dim)}
.seals{display:flex;gap:10px;flex-wrap:wrap}
.seal{border:1px solid var(--line);border-radius:999px;padding:5px 13px;font-size:13px}
.seal.ok{border-color:#1f6f34;color:var(--ok)}
.cite{display:flex;justify-content:space-between;padding:9px 0;
border-bottom:1px solid var(--line);font-size:14px}
.cite:last-child{border-bottom:0}
.tlp{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;padding:2px 8px;border-radius:4px}
.tlp-red{background:#3d1418;color:#ff7b72}
.tlp-amber{background:#3a2a10;color:#e3b341}
.tlp-clear{background:#12261a;color:#56d364}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.root{background:#0b1a2b;border:1px solid #1f4b7a;border-radius:8px;padding:14px 16px;
word-break:break-all;color:#9cd2ff}
table{width:100%;border-collapse:collapse;margin-top:6px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
color:var(--dim);font-weight:600;padding:9px 10px;border-bottom:1px solid var(--line)}
td{padding:11px 10px;border-bottom:1px solid var(--line);vertical-align:top;font-size:14px}
tr:last-child td{border-bottom:0}
.tamper{font-weight:600}
.tamper span{display:block;font-weight:400;color:var(--dim);font-size:13px;margin-top:2px}
.v{font-weight:600;white-space:nowrap}
.v.bad{color:var(--bad)}.v.ok{color:var(--ok)}.v.warn{color:var(--warn)}
.why{color:var(--dim);font-size:12.5px;margin-top:3px;font-family:ui-monospace,Menlo,monospace;
word-break:break-word}
.tot td{border-top:2px solid var(--line);font-weight:700}
.note{color:var(--dim);font-size:13.5px;margin-top:14px}
.hi{color:var(--ink)}
details{margin-top:12px}summary{cursor:pointer;color:var(--dim);font-size:13px}
pre{background:#0b0f14;border:1px solid var(--line);border-radius:8px;padding:14px;
overflow-x:auto;font-size:12px;color:#c9d1d9}
"""


def _tlp_class(tlp: str) -> str:
    return {"TLP:RED": "tlp-red", "TLP:AMBER": "tlp-amber"}.get(tlp, "tlp-clear")


def render_html(data: dict) -> str:
    e = html.escape
    rows = []
    for result in data["results"]:
        who, why = _who_caught(result)
        log_cell = ('<span class="v ok">beccata</span>' if result["log_caught"]
                    else '<span class="v bad">invisibile</span>')
        if who == "nessuno":
            trace_cell = '<span class="v bad">invisibile</span>'
        elif who == "ancora":
            trace_cell = ('<span class="v warn">beccata</span>'
                          '<div class="why">solo dall\'ancora: la root non e\' piu\' quella pubblicata</div>')
        else:
            trace_cell = ('<span class="v ok">beccata</span>'
                          f'<div class="why">{e(why[:110])}</div>')
        rows.append(
            f'<tr><td class="tamper">{e(result["title"])}'
            f'<span>{e(result["detail"])}</span></td>'
            f'<td>{log_cell}</td><td>{trace_cell}</td></tr>'
        )
    caught_log = sum(1 for r in data["results"] if r["log_caught"])
    caught_trace = sum(1 for r in data["results"] if _who_caught(r)[0] != "nessuno")
    total = len(data["results"])

    citations = "".join(
        f'<div class="cite"><span>{e(c["doc"])} <span style="color:#8b949e">p. {c["page"]}</span></span>'
        f'<span class="tlp {_tlp_class(c["tlp"])}">{e(c["tlp"])}</span></div>'
        for c in CITATIONS
    )
    timeline = "".join(
        f'<div class="cite"><span>{e(r["event_type"])} '
        f'<span style="color:#8b949e">{e(str(r["source"]))}</span></span>'
        f'<span class="mono" style="color:#8b949e">{e(r["created_at"][11:23])}</span></div>'
        for r in data["rows"]
    )
    return f"""<!doctype html>
<html lang="it"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Attack this run — Vitruvyan Motus</title>
<style>{CSS}</style>
<div class="wrap">
<h1>Verdetto su submission 4471</h1>
<p class="sub">La stessa decisione registrata in due modi, e poi manomessa sette volte.</p>

<div class="card"><h2>La run</h2><div class="grid">
<div class="kv"><div class="k">desk</div><div class="v">cti · revisore r-118</div></div>
<div class="kv"><div class="k">quando</div><div class="v">12 ago 2026, 09:14 UTC</div></div>
<div class="kv"><div class="k">policy</div><div class="v">strict</div></div>
<div class="kv"><div class="k">record</div><div class="v">{data['records']}</div></div>
</div></div>

<div class="card"><h2>Domanda e risposta</h2><div class="qa">
<div><div class="q">richiesta</div><div>Si puo' rilasciare il parere su CVE-2026-3311 al cliente?</div></div>
<div><div class="q">verdetto</div><div><strong>APPROVED</strong> — soglia di classificazione TLP:RED, rilascio subordinato a espurgo</div></div>
</div></div>

<div class="card"><h2>Sigilli</h2><div class="seals">
<span class="seal ok">TLP verificato</span><span class="seal ok">Vault sigillato</span>
<span class="seal ok">Effetti con chiave di idempotenza</span>
<span class="seal ok">Replay: partial</span></div></div>

<div class="card"><h2>Fonti citate</h2>{citations}</div>

<div class="card"><h2>La root — l'unica cosa che pubblichi</h2>
<div class="root mono">{e(data['root'] or 'nessuna')}</div>
<p class="note">Ancorala dove non puoi riscriverla e diventa la domanda a cui
nessun'altra riga di questa pagina sa rispondere: <span class="hi">questo racconto
e' ancora quello di allora?</span></p></div>

<div class="card"><h2>Sette manomissioni</h2>
<table><thead><tr><th>manomissione</th><th style="width:150px">event log</th>
<th style="width:330px">traccia Motus</th></tr></thead><tbody>
{''.join(rows)}
<tr class="tot"><td>rilevate</td><td>{caught_log}/{total}</td><td>{caught_trace}/{total}</td></tr>
</tbody></table>
<p class="note">L'event log qui non e' uno spaventapasseri: ha un
<code>payload_hash</code> per riga, che e' quanto di piu' forte una tabella del
genere porta di solito. Cio' che non ha e' un valore che si impegni sull'<span
class="hi">insieme</span>: nessuna riga copre le altre, quindi togliere una riga,
riscrivere l'intestazione o cambiare la policy non lascia segno.</p>
<p class="note">L'ultima riga e' l'unica che conta davvero. Un attaccante che
conosce il contratto rifa' tutti gli hash e passa <span class="hi">entrambi</span>
i controlli — la traccia manomessa e' internamente perfetta. Quello che non puo'
fare e' rimettere a posto una root gia' pubblicata altrove.</p>
<details><summary>il verdetto non lo da' questa pagina</summary>
<pre>$ python contract/validate.py trace run.json --spec graph.json
{e(data['baseline_trace'][1])}</pre>
<p class="note">Processo separato, solo il file e il contratto pubblicato.</p>
</details></div>

<div class="card"><h2>Il timeline grezzo</h2>{timeline}</div>
</div></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", metavar="PATH", help="write the page here")
    args = parser.parse_args()
    data = run_demo()
    render_terminal(data)
    if args.html:
        Path(args.html).write_text(render_html(data), encoding="utf-8")
        print(f"pagina: {args.html}")


if __name__ == "__main__":
    main()
