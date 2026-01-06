# Axis PoC: Orchestrator Demo

This proof of concept demonstrates an orchestrator built **on top of Axis**.

## What This Demonstrates

1. **Routing decisions are written to Axis** — no silent decisions
2. **Rejections are explicit** — know what paths were NOT taken and why
3. **Automatic explainability** — generate human-readable explanations from trace
4. **Complete auditability** — every decision, rejection, and fact is recorded

## Structure

```
poc/
├── nodes/
│   ├── parse_input.py      # Extract input characteristics
│   ├── evaluate.py          # Decide if LLM is needed
│   ├── call_llm.py          # Call LLM (mocked)
│   └── respond_direct.py    # Direct response without LLM
├── orchestrator.py          # Compose nodes + routing
├── explain.py               # Generate explanations from trace
└── demo.py                  # Run demonstration scenarios
```

## Run the Demo

### Mock LLM (no API key needed)
```bash
python3 poc/demo.py
```

### Real OpenAI Integration

**Option 1: Using .env file (recommended)**
```bash
# Copy example and add your key
cp .env.example .env
# Edit .env and add your OpenAI API key

# Run demo
python3 poc/demo_openai.py
```

**Option 2: Using environment variable**
```bash
export OPENAI_API_KEY="sk-..."
python3 poc/demo_openai.py
```

The OpenAI version demonstrates:
- Real LLM integration built on Axis
- Model selection recorded in trace
- API calls fully auditable
- Graceful fallback to mock if no API key

## Two Scenarios

### Scenario A: Complex Input
**Input:** "Explain quantum entanglement in simple terms"  
**Result:** LLM is called  
**Trace includes:**
- Decision: `needs_llm`
- Decision: `called_llm`
- Fact: `llm_response`

### Scenario B: Simple Input
**Input:** "What is 2+2?"  
**Result:** Direct response, LLM skipped  
**Trace includes:**
- Rejection: `llm_not_needed` (reason: "simple operation can be handled directly")
- Fact: `direct_response`

## Key Insight

**In Scenario B, the LLM was NOT called, and we know exactly why.**

This is the difference: LangGraph tells you **what happened**.  
Axis tells you **why** (including what didn't happen).

## Code Metrics

- **Nodes:** 4 files, ~120 lines
- **Orchestrator:** 1 file, ~60 lines
- **Explain:** 1 file, ~90 lines
- **Demo:** 1 file, ~80 lines

**Total: ~350 lines** including comments and formatting.

## What This Proves

1. Orchestration logic can be **simple and explicit** when built on Axis
2. Complete traceability doesn't require complex instrumentation
3. Explainability is **automatic**, not manual
4. Debugging becomes **reading the trace**, not debugging the code

---

**This is not a framework. It's a demonstration that Axis enables a different kind of orchestration.**
