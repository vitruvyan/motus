# Handoff — the pilot: one real flow from `vitruvyan-core` through Motus

> Written on 2026-08-14, against Motus **v0.11.0**. **Do not trust it.** The
> author of this document could not read the machine you are on. Everything
> here about `vitruvyan-core` is a question, not a fact, and your first useful
> act is to say which of these questions have answers.

You are working with **Davide**, founder of Vitruvyan. Answer him in Italian.
He sets scope and accepts ADRs; you hold the technical calls on what you are
doing.

---

## 0. What this is for, and why the order matters

Motus is about to freeze its formats at 1.0.0. **The freeze is the promise that
a receipt written today verifies in ten years** — after it, changing the
commitment, checkpoint or receipt format costs a major version.

**This pilot is the last thing that can still say the format is wrong while
correcting it costs an afternoon.** That is its entire purpose. It is not a
migration, it is not a proof of concept, and shipping a working port is not
success on its own. **The deliverable that matters is §4: the list of things
`vitruvyan-core` does that Motus's format could not express.** An empty list is
a real and valuable answer — but it has to be an answer you reached, not one
you defaulted to.

Terraveler, the first external integrator, completed three phases against
v0.10.0 and found no Motus defect. They used Motus the way an outsider would.
`vitruvyan-core` is different in the way that matters: it is our own code, with
our own nodes, **already orchestrated by something else**.

---

## 1. Before anything, say what you can actually reach

**Never describe verification you did not perform.** An agent that reports a
command it could not run is the worst defect this project can acquire, and it
has happened here once already.

```bash
cd ~/refactor/motus
git fetch --all --tags && git describe --tags --abbrev=0    # expect v0.11.0
.venv/bin/python -c "import vitruvyan_motus; print(vitruvyan_motus.__version__)"
.venv/bin/python -m pytest -q                                # expect green, not a count
```

Then find `vitruvyan-core` **on the machine you are on**. If it is not there,
say so and stop — do not reach for it over the network. The production VPS is
`167.86.119.200` and **you have no business there**: the rule Davide set is
that this machine holds no path into production, and a pilot that broke it
would be worth less than no pilot.

Report, before planning anything:

- where `vitruvyan-core` is, or that it is not reachable;
- whether you can run it, or only read it;
- what the four commands above actually printed, including any disagreement
  with this document.

---

## 2. Read how LangGraph is used. Change nothing

**This step writes no code.** You are answering four questions about somebody
else's system, and the answers are what step 3 is built on.

1. **Where is the graph declared?** Which module, and is there one graph or
   several. Does the topology exist as data, or is it assembled by code at
   import time?
2. **What does a node receive, and what does it return?** LangGraph passes
   state between nodes; find out what that state actually is — a dict, a typed
   object, something accumulated by reducers — and whether a node mutates it
   in place or returns a new one. **Motus's state is append-only and immutable**
   (`contract/node-protocol.md` §2), so this is the first place the two models
   can fail to meet.
3. **What crosses between nodes that is not in the state?** Closures, module
   globals, a shared client, a cache, an open connection. These are the things
   a port loses silently, and they are the reason §4 exists.
4. **What is persisted today, and by what?** Checkpointers, a database, logs.
   For each: who writes it, when, and what a reader can conclude from it
   afterwards.

**Use the AST, not a pattern.** You will be reading Python to answer *what does
this call* and *what does this bind*. A regular expression matches characters
and knows nothing about what they mean; where a parser exists, go through it.
This project has paid for that lesson twice, most recently in the MCP itself.

Deliverable for this step: a page of prose Davide can read in five minutes,
naming files and line numbers, that says how the thing works — and **what you
could not determine**, which is a section, not an omission.

---

## 3. Name the real nodes, with their effect class and its price

This is where you use the MCP, and it is the first use of the thing it was
built for.

The classification is the error this protocol has measurably lost readers on —
twice, both times by careful readers. `recorded_effect` and `external_effect`
are two plausible names for the same intuition (*this node talks to the
outside*), and the protocol's meaning is the **asymmetry** between them:

- a **read** declared `external_effect` costs safe resumes — a real price, paid
  silently, forever;
- a **write** declared `recorded_effect` **never meets the resume guard at
  all**: the run resumes and the write happens twice, with no idempotency key
  required and no receipt demanded.

One error costs availability. The other costs correctness, in a system whose
purpose is to be believed.

Ask the MCP, and read what it gives you:

```
motus_classify("the node issues an HTTP GET and writes the result to Postgres")
```

**It will not tell you the class, and that is deliberate.** A version that did
was measured wrong on most realistic descriptions — *"computes the invoice
total and stores it in Postgres"* came back `pure`, because `compute` is a
marked term and `stores` is not. It now reports which of §4.4's marked terms
your text contains and hands you the table, the cost column, the
strictest-class rule and §4.1. **You do the classifying**, from the document,
holding the node.

Two rules from `contract/node-protocol.md` §4.4 you will need and might not
find on your own:

- **a node whose work falls in more than one row takes the strictest class any
  of them names.** Mutating is not cancelled by also reading;
- **nothing enforces this.** The runtime catches only the sub-case where a node
  declared `recorded_effect` voluntarily records an `external_effect`
  descriptor. A node that simply performs the write is invisible to every gate
  Motus has. It is a rule for the person declaring the node, and §4.4 says so.

Deliverable: one row per node.

| node | what it does | honest class | conservative class costs | reads not in state | idempotency key available? |
|---|---|---|---|---|---|

The last two columns are the ones that will hurt, and they are the ones §4 is
built from.

---

## 4. Port one flow — and then answer the question the freeze is waiting for

Pick **one** flow, end to end. Not the simplest one and not the largest: the
one whose nodes cover the most distinct rows of your table from §3.

Run it beside the LangGraph one. Then answer, in this order:

1. **Does it produce the same result?** If not, why — and resist the first
   explanation.
2. **What does Motus record that nothing records today?** The trace, the
   integrity chain, the derived root, the effect declarations. Be concrete:
   what could somebody now ask of a run that they could not ask before.
3. **What could the format not express?** ← **this is the deliverable.**

For (3), the things worth looking for specifically, because they are where a
format usually fails first:

- a node that must return *more than one* logical outcome;
- state that is not JSON, or that only round-trips through a custom encoder;
- a value whose identity matters but whose serialization is not stable;
- an effect that is neither a read nor a mutation of the outside world;
- a control-flow shape Motus's transitions cannot describe — a loop, a fan-out,
  a human interrupt, a resume that changes the graph;
- anything you had to *store differently* to make it fit.

For each: what you did instead, and what it cost. **"I had to flatten it" is a
finding. "I found a way" is not an answer to this question.**

---

## 5. The rules that are not negotiable here

**Never describe verification you did not perform.** Repeated because it is the
one that matters most in a handoff between machines.

**"I cannot tell" is a real answer** and it is often the correct one. A
confident wrong finding here sends somebody to change a format that is about to
be frozen.

**Do not modify `vitruvyan-core`.** Read it; port *beside* it. If a change to it
turns out to be necessary, that is a finding to report, not a change to make.

**Do not touch the production VPS.**

**A finding names an instance — repair the class.** Ask *what else in this
codebase has this shape?* and go and look, with a parser or a probe, never with
a judgement. **And the class being real does not make your repair for it
right**: generalising a fix widens its blast radius, so it raises the bar on
measuring it, not lowers it. Both rules were paid for on 2026-08-14, when two
correct generalisations of two real defects were shipped and withdrawn the same
day.

**A question the MCP answers well is a defect report against the
documentation.** If you had to ask the server something the prose should have
told you, say so — that runs one way only, and the MCP is never a reason to
leave a document wrong.

---

## 6. What to hand back

Four things, in this order of importance:

1. **the list from §4.3** — what the format could not express, with what it
   cost. If it is empty, say what you looked for;
2. **the node table from §3**, with the honest class and the price of the
   conservative one for every node;
3. **the reading from §2**, including what you could not determine;
4. **the ported flow itself**, and whether it agreed with the original.

If you get through only §2, that is still worth having and should be handed
back as it stands. If you get through §4 and the list is empty, say that
plainly and say what you searched for — **an empty list from a real search is
what unblocks the freeze, and an empty list from a shallow one would freeze a
defect into a ten-year promise.**


**A count is deliberately not written above.** It read `971` and was true only
at the v0.11.0 tag; `main` runs a different number and so does any branch. It
also depends on the environment — CI skips one test because the MCP SDK is not
installed there, so the same commit counts one fewer in CI than locally. A
number that cannot be true in both places is worse than no number.
