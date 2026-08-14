# The integration MCP

`pip install vitruvyan-motus[mcp]`, then point your coding agent at
`motus-mcp`. It answers questions about the Motus protocol **from the documents
in the installation**, and runs the shipped validator over artefacts you name
by path.

It is off until installed. Nothing on any runtime path imports it, and a
subprocess test runs a real graph and asserts no module with `mcp` in its name
was loaded.

## Why it exists, in one measurement

The effect classification has been read wrong twice, by careful readers.

An automated reviewer read two worked examples and an integration brief and
**inverted the classification twice in one pull request** — a database write
called `recorded_effect`, read-only calls called the opposite. Then an
integrator's field report against v0.10.0 declared a node issuing nothing but
HTTP GETs as `external_effect` and called it conservative. The contract permits
that. **Nothing told them what it costs**, which is safe resumes, silently,
forever.

`motus_classify("I need to INSERT a row")` reports which §4.4 terms your text
contains and hands you the whole table, the cost of each conservative choice,
the strictest-class rule and §4.1.

**It does not tell you the class, and that is a withdrawal rather than a
limitation.** It used to. Two adversarial lenses measured the verdict wrong on
15 of 20 and 6 of 6 realistic descriptions, in both directions — *"computes the
invoice total and stores it in Postgres"* was answered `pure`, because
`compute` is a marked term and `stores` is not. Matching characters tells you
which marked terms a sentence contains; it cannot tell you which operation the
sentence names in words the table does not mark, and that is the operation that
decides the class. See ADR-025.

## What an answer is made of

Every answer is a sequence of spans, and there are exactly three kinds:

| Kind | What it is | What checks it |
|---|---|---|
| quoted | text that appears verbatim in a named installed source | verified when the span is built; a drifted quotation raises rather than shipping |
| computed | text a shipped program produced | carries the command that reproduces it, and the suite runs every one |
| cannot | `I cannot tell.` | carries the commands that were tried |

There is no fourth kind, so there is nowhere in an answer to put a sentence
somebody wrote from memory. If the document changes, the answer changes. If the
document is deleted, the tool raises.

## Configuring a client

```json
{
  "mcpServers": {
    "motus": {
      "command": "motus-mcp"
    }
  }
}
```

Equivalently `python -m vitruvyan_motus.mcp.server`. Everything the server
answers is also reachable from the command line, which is how you check it:

```
python -m vitruvyan_motus.mcp classify "the node POSTs to their API"
python -m vitruvyan_motus.mcp explain UnsafeResume
python -m vitruvyan_motus.mcp diagnose ./run-4711.json --symptom ReplayMismatch
```

## The surface

- **`motus_classify(description)`** — which §4.4 terms your description
  contains, the whole table, and the cost of each conservative choice;
- **`motus_review_graph(spec)`** — `GraphSpec.from_dict` on your spec. Not
  advice: the verdict of the code that will refuse you at runtime;
- **`motus_review_node(source, effect_class)`** — ambient draws (§6.4, reported
  as §6.2 requires) and whether anything supplies the idempotency key resume
  needs. Both are AST facts. A third check compared call names against §4.4's
  terms and is withdrawn: it read `payload.get` as an HTTP `GET`;
- **`motus_explain(error)`** — the docstring of the exception class that
  raises, and its ancestry;
- **`motus_start_here()`** — two shipped examples, verbatim;
- **`motus_where(intent)`** — every kernel module with its own one-line
  docstring. The whole map every time: selecting by shared words named seven of
  fourteen modules for the intent `"the"`;
- **`motus_diagnose(path, symptom)`** — the shipped code, run over your
  artefact, reporting what it said.

## Two things it will not do

**It will not enter the merits of an audit.** It does not say whether a
decision was right, whether recorded facts are true, or whether a trace is
trustworthy beyond what the shipped code computes. That line is the auditor's
and the customer's, and a tool that blurred it would automate the one thing
this company has committed never to automate.

**It will not suggest removing evidence.** If a trace does not verify, the
answer is which record broke the chain — never *regenerate the trace*. If a
declaration blocks a resume, the answer is which declaration and what the
protocol requires — never *declare it `pure`*. This is not caution. It is the
predictable failure mode: an agent under pressure takes the shortest path
offered, and here the shortest path destroys the property.

## Where your artefacts go

`motus_diagnose` takes **a filesystem path the server process can open**, and
refuses artefact content. That is the shape of the argument rather than a rule
somebody has to remember: a path is meaningless to a server on another machine,
so a remote deployment cannot work rather than working while it leaks.

A diagnosis names records by `seq` and `kind` and reports a violation's **rule
and path, never its message** — a message can quote the value that broke the
rule. You are not being kept from it: the reproduce line gives you the whole
verdict, message included, on your own machine. The withholding is from the
wire.

## When it cannot answer

It says so. The validator knows what it refuses; it does not know what a good
graph looks like. Half the questions an implementer has — *is this the right
decomposition?* — are outside what this server may answer, and it will decline
them where a summary-writing server would have answered fluently and sometimes
wrongly.

That is the trade ADR-022 took deliberately, and it will be experienced as the
tool being less useful than the alternative.

## A question it answers is a defect report

Against the documentation, not a replacement for it. A question asked often
through the server and answered nowhere in prose means the document should
answer it too. §4.4 and §6.4 of `contract/node-protocol.md` were written
because this server needed them, and they are normative there rather than
here — where a row and the clause above it disagree, the row is wrong.
