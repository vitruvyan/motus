# The integration MCP

`.venv/bin/pip install ".[mcp]"` from a checkout — **Motus is not on PyPI**, so
there is no index to install from — then point your coding agent at
`motus-mcp`.

**Install it into a virtualenv of its own, or with `pipx` — never into a
shared `--user` site-packages.** The kernel *imports* nothing outside the
standard library and a test proves that against a running interpreter; the
**distribution** installs one thing, `jsonschema`, which the shipped validator
needs. Those are two different claims and this page said the stronger one.
**The `[mcp]` extra pulls about two dozen more**, because the MCP SDK brings
`starlette`, `uvicorn`, `pydantic`, `httpx2` and `cryptography` with it. The
first integrator to install it this way pulled a `starlette` that an unrelated
`fastapi` on the same machine refused (#109). Their containerised services were
untouched, and so was Motus — but their host tooling was not.

It answers questions about the Motus protocol **from the documents in the
installation**, and runs the shipped validator over artefacts you name by
path.

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

Some clients ask for the same thing as fields rather than as JSON. The values
are:

| field | value |
|---|---|
| name | `motus` |
| transport | `stdio` |
| command | the absolute path to `motus-mcp` in the venv Motus is installed in |
| args | *empty* |
| environment | *empty* |

**The absolute path matters**: a client does not inherit your shell's
virtualenv. And the environment stays empty — this server takes no credential,
contacts nothing, and has no account. A field offering `API_KEY=secret` is
inviting you to configure something else.

Equivalently `python -m vitruvyan_motus.mcp.server`. Everything the server
answers is also reachable from the command line, which is how you check it:

```
python -m vitruvyan_motus.mcp classify "the node POSTs to their API"
python -m vitruvyan_motus.mcp explain UnsafeResume
python -m vitruvyan_motus.mcp diagnose ./run-4711.json --symptom ReplayMismatch
```

## How to see that it is connected

Three levels, and only the last one answers.

**The process starts.** `timeout 3 motus-mcp < /dev/null; echo $?` printing `0`
proves the executable exists and exits when its input does. It proves nothing
else.

**It speaks the protocol.** This is the check worth running, because it fails
in your terminal rather than inside a client that will show you the error
filtered:

```python
import asyncio, sys
from mcp import ClientSession, StdioServerParameters, stdio_client

async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "vitruvyan_motus.mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print("connected to :", info.server_info.name, info.server_info.version)
            tools = (await session.list_tools()).tools
            print("tools        :", len(tools))
            for tool in tools:
                print("   -", tool.name)
            answer = await session.call_tool(
                "motus_classify", {"description": "the node runs an INSERT"})
            text = answer.content[0].text
            print("first line   :", text.splitlines()[0])
            print("cites its source :", "contract/node-protocol.md" in text)
    return 0

raise SystemExit(asyncio.run(main()))
```

It proves three things a configuration file cannot: the process starts, it
**completes the handshake**, and a real call comes back with an answer that
**carries its source**. If any of the three is missing you find out here, where
the failure is legible.

**The client lists eight tools**, all prefixed `motus_`. Fewer than eight means
the server started and part of its surface did not register — and the check
above tells you whether that is Motus or the client.

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
- **`motus_find(term)`** — every passage in the shipped sources that contains
  the term, quoted verbatim with the file it came from. The sources are
  enumerated in `mcp/sources.py` and travel inside the wheel:
  `contract/node-protocol.md`, `contract/README.md`, `contract/guarantees.md`,
  `adr/ADR-022`, and three of the examples.
  **This is the one to reach for when a trace, an error or a review names
  something you do not recognise** — `opaque_config`, `durability_profile`,
  `effect_class`, or a rule id the prose actually defines. It reaches what the
  citable documents say, so `J2` and `J1` answer and `T11` does not — the
  T-rules live in `contract/validate.py`, which is code and not citable prose.
  A term it cannot find gets *I cannot tell* plus the list of what was searched,
  never a guess. It exists because the first
  external integrator held `node:check:opaque_config` from a real trace, asked
  this server what to do about it, and nothing here could reach the answer:
  `explain` knows exception class names, `where` returns the module map, and
  the paragraph they needed was in `contract/node-protocol.md` the whole time
  (#107). Every span is a quotation, so this tool has nowhere to put a sentence
  of its own;
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
