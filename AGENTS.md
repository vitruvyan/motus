# Agents in this repository

Motus is a contract-first codebase with an unusually heavy verification
practice: a normative `contract/`, frozen corpora, an executable validator, a
performance gate that compares releases, and adversarial review before merges.
Most of that work is repetitive and mechanical. Some of it is not, and the
difference is worth money.

## The routing rule

**Match the model to the reasoning the task actually needs, not to the
importance of the code it touches.** Running the test suite against the
runtime's most delicate file is still just running the test suite.

| tier | agent | what it is for |
|---|---|---|
| `haiku` | **`motus-verifier`** | Running things and reporting what broke. The suite, the gates, the frozen-path guard, the examples, README/API consistency. Dispatch it freely — it is cheap, and it exists so that nobody spends a reasoning model reading green pytest output. |
| `sonnet` | **`motus-implementer`** | Changes whose shape is already decided: a named fix, a stated property to test, docs brought in line with code. It is instructed to stop and say so if the task turns out not to be decided after all. |
| `haiku` | **`motus-issue-auditor`** | Checking whether open issues still describe the code truthfully. Not a tracker — `gh issue list` already lists. This catches issues that went stale in silence, which is a thing no command does. |
| `opus` | **`motus-adversary`** | Breaking new code. Diagnosing an unexplained failure. Anything where the answer is not known in advance and being wrong is expensive. |

Decisions about the contract, the ADRs and the architecture stay with the
session lead. They are not delegated, because the failure mode is not a bug —
it is a wrong decision recorded as if it were right.

## Where the tokens actually go

Two lessons from the 0.7 cycle, both measured:

**Verification dominates by volume.** The suite ran dozens of times in a single
session. A reasoning model reading `534 passed` is pure waste; that is exactly
what `motus-verifier` is for, and why it is instructed to report anomalies
rather than output.

**Adversarial review dominates by value.** Every round found real defects in
code written hours earlier — including in the fixes made for the round before.
That is the one place to spend the expensive model without hesitating.

**Documents rot in pieces.** A long issue with six findings gets fixed a piece
at a time; comments record what closed and nobody rewrites the body. Issue #32
spent a day asserting, as current fact, two things that were already false.
That is what `motus-issue-auditor` is for, and it is why issues here should
carry one question rather than six.

The economy is not "use cheap models". It is **stop paying for reasoning where
none is required, so you can afford it where it is.**

### It paid for itself on its first run

`motus-verifier`, dispatched once as a smoke test, found that the README listed
`NodeConfigurationError` in the public surface while the package did not export
it — a false promise introduced the day before, by me, while *correcting* that
same list. 19k tokens on the cheapest tier. `motus-implementer` then fixed it
with a test and a mutation probe for another 19k.

Neither task needed a reasoning model. Both needed doing.

## Skills

- **`adversarial-round`** — how a hostile round is run and how findings are
  processed. Includes the rule that has mattered most: do not merge while the
  round is still running.
- **`release`** — the release act, which ADR-006 and ADR-012 make into one
  coupled operation. Encodes the failures that have actually happened.
- **`adr`** — the ADR house style, including the parts that make these
  documents worth reading: numbers instead of adjectives, the cost you are
  accepting, wrong turns recorded, and guesses labelled as guesses.

## Rules no agent may break

These come from `.github/copilot-instructions.md` and hold for every agent and
every model tier:

- **Authority order**: ADR-001 → `contract/` → frozen corpora → implementation.
  When implementation and contract disagree, the implementation is wrong.
- **Never edit** `tests/contract/` or `tests/compat/`. CI enforces this from
  the trusted base branch.
- **Never weaken, skip or delete an assertion** to make something pass.
- **A finding names an instance. Repair the class.** Before fixing what was
  reported, ask what *else* has the same shape, and go and look — the answer
  has twice been "a second site, and the worse one". #74 reported a number
  whose lexeme could be rewritten without moving the root; string escapes had
  the same hole, and an escaped letter renders as the word while `grep` for it
  fails. A round found a commitment log satisfied by a `Mock()`; the sink had
  the same hole. **Neither second site was reported by anybody.** When the class
  cannot be closed, say where it stays open and leave a test that fails when a
  new member appears.

  **And the class being real does not make your repair for it right.** Both
  repairs written under this rule on 2026-08-14 were killed by the round that
  followed: one covered half its own surface and refused a frozen artifact of
  ours; the other refused `xmlrpc.client.ServerProxy` from the standard library
  while missing the doubles the stdlib documentation recommends. **Generalising
  a fix widens its blast radius, so it raises the bar on measuring it, not
  lowers it.**
- **Build the general solution, not the particular one.** A fix that handles
  the reported case and nothing else is a fix that will be reported again from
  the next case. Where the general form costs more, pay it or say why you did
  not — *"this closes the reported instance and leaves X open"* is an
  acceptable sentence; leaving X open silently is not.
- **Avoid a regular expression where a parser exists, and this is not a style
  preference.** A regex matches *characters* and knows nothing about what they
  mean, so over structured text it answers a different question from the one
  being asked. Today's example, and it was one line from shipping: a rule about
  JSON numbers, written as a pattern over the raw bytes, flags
  `{"note": "cost 5.10 eur"}` — where `5.10` is somebody's prose and not a
  number at all. Hooked into the parser instead (`parse_float`, `parse_int`,
  `parse_string`), it sees exactly the tokens it is about and nothing else.

  A regex is fine for a **single opaque token whose grammar you own** —
  `sha256:[0-9a-f]{64}`, an identifier shape — and this repository uses it that
  way. It is not fine for finding structure inside a document that has a
  parser, a grammar, or an AST. **If you are matching a pattern to answer a
  question about meaning, you are answering the wrong question.**
- **No new runtime dependencies.** The package declares zero, and that is a
  claim the packaging tests check against a real built wheel.
- **Every fix carries a test that fails without it**, proved by neutering the
  fix and watching the test fail. Five tests in the 0.7 cycle passed for the
  wrong reason and only this caught them.

## One habit worth keeping

**Suspect your own tests first.** The recurring failure in this project has not
been bad code — it has been tests written against the implementation just
produced rather than against the property that should hold. They pass, they
look like coverage, and they are not.

## Under Pi

Pi reads this file, `.pi/settings.json`, `.pi/agents/`, `.pi/skills/` and `.pi/prompts/`; it does not read
`.claude/`. The four roles above exist in both forms — `.claude/agents/` is the source, `.pi/agents/` is
generated by `.pi/sync-agents.py` — and the skills are symlinked. Model routing under Pi is by subscription
only (`~/.pi/agent/AGENTS.md`): `motus-verifier`, `motus-issue-auditor` and `motus-implementer` on
`openai-codex/gpt-5.6-luna`, `motus-adversary` on `openai-codex/gpt-5.5`; when orchestrated through
pi-herdr, the adversary and any architect run as the `claude` Herdr kind on Claude Max. The tier names in
the table above (`haiku`/`sonnet`/`opus`) describe the reasoning needed, not a provider.

Flows: `/adversarial-round` in `.pi/prompts/`. Task briefs for the factory go in `.factory/tasks/` (the
Limen convention), one file per task, handed to the lead from the file.
