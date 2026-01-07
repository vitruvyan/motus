# Axis Production Roadmap — Option C (Full Framework)

**Decision Date:** January 7, 2026  
**Target Scope:** 20-30K lines (~25K realistic)  
**Timeline:** 3-4 months (accelerated via Vitruvyan reuse)  
**Approach:** AI-driven development (User + Copilot)

---

## Strategic Context

**Why Option C:**
- Vitruvyan has 70K+ lines production-proven code
- 4 Sacred Orders already exist: Orthodoxy, Pattern, Codex, Babel (~21K lines)
- Reuse factor: 60-70% of Order logic is portable to Axis
- User-AI development model validated (70K lines successfully delivered)
- "Facile come bere una birra" — we know what to build and how

**What This Unlocks:**
- Immediate competitive positioning vs LangGraph (feature parity)
- Freemium marketplace launch in Q2 2026 (not Q3-Q4)
- Reference Orders ready at launch (not "coming soon")
- Enterprise readiness from day 1 (not incremental)

---

## Phase Breakdown

### Phase 2.3: Production Features (~4,000 lines, 6-8 weeks)
**Status:** IN PROGRESS  
**Effort:** 100% new code (no reuse)  
**Critical Path:** YES (blocks everything else)

#### Week 1-2: Persistence Layer (~1,500 lines) ✅ COMPLETE
**Files to create:**
- `axis/persistence/protocol.py` (~100 lines): PersistenceProvider protocol
- `axis/persistence/json_adapter.py` (~300 lines): JSON file storage
- `axis/persistence/sqlite_adapter.py` (~400 lines): SQLite backend
- `axis/persistence/postgresql_adapter.py` (~500 lines): PostgreSQL backend
- `axis/persistence/__init__.py` (~50 lines): Public exports
- `tests/test_persistence.py` (~150 lines): 10 tests

**Acceptance Criteria:**
- GraphState serializable to JSON (all types including epistemic)
- SQLite adapter supports: save(), load(), query_by_trace_id()
- PostgreSQL adapter supports: save(), load(), query_by_trace_id(), query_by_timestamp()
- All 10 tests passing

#### Week 3-4: Error Recovery (~600 lines)
**Files to create:**
- `axis/recovery/retry.py` (~200 lines): Exponential backoff retry decorator
- `axis/recovery/circuit_breaker.py` (~200 lines): Circuit breaker pattern
- `axis/recovery/timeout.py` (~100 lines): Timeout wrapper
- `axis/recovery/__init__.py` (~50 lines): Public exports
- `tests/test_recovery.py` (~50 lines): 5 tests

**Acceptance Criteria:**
- retry() decorator: 3 attempts, exponential backoff 1s/2s/4s
- CircuitBreaker: open after 5 failures, half-open after 30s
- timeout() decorator: raises TimeoutError after N seconds
- All 5 tests passing

#### Week 5-6: Observability (~500 lines)
**Files to create:**
- `axis/observability/metrics.py` (~200 lines): Prometheus metrics via SynapticBus
- `axis/observability/logging.py` (~150 lines): Structured JSON logs
- `axis/observability/tracing.py` (~100 lines): OpenTelemetry spans
- `axis/observability/__init__.py` (~50 lines): Public exports

**Acceptance Criteria:**
- Prometheus metrics: axis_node_duration_seconds, axis_node_errors_total, axis_graph_executions_total
- JSON logs: structured with trace_id, node_name, timestamp, level, message
- OpenTelemetry spans: one span per node execution

#### Week 7-8: Streaming (~800 lines)
**Files to create:**
- `axis/streaming/async_runner.py` (~300 lines): Async version of Runner
- `axis/streaming/event_stream.py` (~200 lines): SSE event stream
- `axis/streaming/websocket.py` (~200 lines): WebSocket support
- `axis/streaming/__init__.py` (~50 lines): Public exports
- `tests/test_streaming.py` (~50 lines): 5 tests

**Acceptance Criteria:**
- AsyncRunner: async def run() with async Nodes
- SSE: Server-Sent Events stream of node completions
- WebSocket: Real-time bidirectional updates
- All 5 tests passing

**Phase 2.3 Output:**
- Axis codebase: 774 → ~5,000 lines
- Production-ready for Vitruvyan migration
- Feature parity with LangGraph core

---

### Phase 2.4: Orders Integration (~15,000 lines, 8-10 weeks)
**Status:** NOT STARTED  
**Effort:** 70% reuse from Vitruvyan, 30% new  
**Critical Path:** NO (can overlap with 2.3 final weeks)

#### Weeks 9-10: Pattern Weaver Order (~3,500 lines)
**Source:** Vitruvyan Pattern Weavers (1,283 lines) + Qdrant integration  
**Reuse:** Pattern detection logic, embedding generation, Qdrant queries  
**New:** Axis Node wrappers, epistemic types integration  

**Files to create:**
- `orders/pattern_weaver/nodes.py` (~1,000 lines): 5 Nodes (embed, search, synthesize, rank, store)
- `orders/pattern_weaver/config.py` (~200 lines): Qdrant connection, model configs
- `orders/pattern_weaver/graph.py` (~300 lines): Pattern Weaver graph definition
- `orders/pattern_weaver/tests.py` (~500 lines): 15 tests
- `orders/pattern_weaver/__init__.py` (~50 lines): Public exports
- `orders/pattern_weaver/README.md` (~200 lines): Documentation
- COPY from Vitruvyan: embedding logic (~1,000 lines), Qdrant utils (~250 lines)

**Acceptance Criteria:**
- Pattern detection: Takes GraphState, returns Pattern types
- Qdrant integration: Search similar patterns with confidence scores
- Immutable: No state mutation (all Axis compliance)
- 15 tests passing

#### Weeks 11-12: Orthodoxy Warden Order (~2,000 lines)
**Source:** Vitruvyan Orthodoxy Wardens (387 lines) + validation schemas  
**Reuse:** Validation rules, Pandas schemas  
**New:** Constraint/Violation epistemic types, reporting  

**Files to create:**
- `orders/orthodoxy_warden/nodes.py` (~600 lines): 3 Nodes (validate, check_constraints, report_violations)
- `orders/orthodoxy_warden/schemas.py` (~400 lines): Validation schemas
- `orders/orthodoxy_warden/graph.py` (~200 lines): Orthodoxy graph
- `orders/orthodoxy_warden/tests.py` (~400 lines): 12 tests
- `orders/orthodoxy_warden/__init__.py` (~50 lines): Public exports
- `orders/orthodoxy_warden/README.md` (~150 lines): Documentation
- COPY from Vitruvyan: validation logic (~200 lines)

**Acceptance Criteria:**
- Constraint checking: Returns Violation types for epistemic inconsistencies
- Schema validation: Pandas DataFrame validation
- MiFID II compliance: 5-year audit trail of violations
- 12 tests passing

#### Weeks 13-16: Codex Hunter Order (~7,000 lines)
**Source:** Vitruvyan Codex Hunters (16,424 lines) + yfinance/Reddit/FRED  
**Reuse:** API integration logic, data parsers  
**New:** Fact generation, async node architecture  

**Files to create:**
- `orders/codex_hunter/nodes.py` (~2,000 lines): 8 Nodes (fetch_yahoo, fetch_reddit, fetch_fred, parse, dedupe, enrich, categorize, store)
- `orders/codex_hunter/apis/` (~2,500 lines): API clients (yfinance ~800, Reddit ~900, FRED ~800)
- `orders/codex_hunter/parsers/` (~1,500 lines): Data parsers
- `orders/codex_hunter/graph.py` (~500 lines): Codex graph with parallel fetching
- `orders/codex_hunter/tests.py` (~800 lines): 20 tests
- `orders/codex_hunter/__init__.py` (~50 lines): Public exports
- `orders/codex_hunter/README.md` (~300 lines): Documentation
- COPY from Vitruvyan: API wrappers (~10,000 lines cleaned to ~2,500)

**Acceptance Criteria:**
- Multi-source fetching: yfinance + Reddit + FRED in parallel
- Fact generation: Structured Fact types with provenance
- Error handling: Retry + circuit breaker for API failures
- 20 tests passing

#### Weeks 17-18: Babel Garden Order (~2,500 lines)
**Source:** Vitruvyan Babel Gardens (1,000+ lines) + MiniLM/FinBERT  
**Reuse:** Embedding models, semantic analysis  
**New:** Relation inference, intent detection  

**Files to create:**
- `orders/babel_garden/nodes.py` (~800 lines): 4 Nodes (embed, detect_intent, infer_relations, semantic_search)
- `orders/babel_garden/models/` (~1,000 lines): MiniLM wrapper (~500), FinBERT wrapper (~500)
- `orders/babel_garden/graph.py` (~300 lines): Babel graph
- `orders/babel_garden/tests.py` (~400 lines): 12 tests
- `orders/babel_garden/__init__.py` (~50 lines): Public exports
- `orders/babel_garden/README.md` (~200 lines): Documentation

**Acceptance Criteria:**
- Intent inference: Returns Intent types from user queries
- Relation detection: Infers Relation types between facts
- Embedding: MiniLM-L6-v2 + FinBERT dual embeddings
- 12 tests passing

**Phase 2.4 Output:**
- 4 Premium Orders ready (~15,000 lines)
- Reference implementations for marketplace
- Vitruvyan migration proof of concept

---

### Phase 2.5: Marketplace Infrastructure (~3,000 lines, 3-4 weeks)
**Status:** NOT STARTED  
**Effort:** 100% new code  
**Critical Path:** NO (parallel with 2.4)

#### Weeks 15-18: Plugin System (~3,000 lines)
**Files to create:**
- `axis/marketplace/registry.py` (~500 lines): Order registry (discover, load, validate)
- `axis/marketplace/loader.py` (~400 lines): Dynamic import, dependency resolution
- `axis/marketplace/manifest.py` (~300 lines): Order metadata (order.yaml schema)
- `axis/marketplace/cli.py` (~800 lines): CLI commands (axis install, axis list, axis search)
- `axis/marketplace/installer.py` (~600 lines): Order installation (pip + git)
- `axis/marketplace/validator.py` (~300 lines): Order validation (tests, schemas)
- `axis/marketplace/__init__.py` (~50 lines): Public exports
- `tests/test_marketplace.py` (~100 lines): 8 tests

**Acceptance Criteria:**
- axis install pattern-weaver: Installs Order from PyPI/GitHub
- axis list: Shows installed Orders with versions
- Order manifest: order.yaml with name, version, author, license, dependencies
- 8 tests passing

**Phase 2.5 Output:**
- VS Code Extensions-style marketplace
- CLI for Order management
- Ready for 3rd party developers

---

### Phase 2.6: Admin UI (~3,000 lines, 3-4 weeks)
**Status:** NOT STARTED  
**Effort:** 100% new code  
**Critical Path:** NO (optional for launch)

#### Weeks 19-22: Monitoring Dashboard (~3,000 lines)
**Files to create:**
- `axis/admin/server.py` (~500 lines): FastAPI backend
- `axis/admin/frontend/` (~2,000 lines): React dashboard
  - GraphState viewer (~600 lines)
  - Metrics dashboard (~600 lines)
  - Order manager (~400 lines)
  - Configuration editor (~400 lines)
- `axis/admin/templates/` (~300 lines): HTML templates
- `axis/admin/__init__.py` (~50 lines): Public exports
- `tests/test_admin.py` (~100 lines): 5 tests

**Acceptance Criteria:**
- Web UI at localhost:8080
- Live GraphState inspection (trace viewer)
- Prometheus metrics visualization (Grafana-lite)
- Order enable/disable toggles
- 5 tests passing

**Phase 2.6 Output:**
- Self-hosted admin dashboard
- Enterprise feature (not required for Core)

---

## Total Effort Summary

| Phase | Lines | Weeks | Status | Critical Path |
|-------|-------|-------|--------|---------------|
| 2.3 Production | ~4,000 | 6-8 | NOT STARTED | YES |
| 2.4 Orders | ~15,000 | 8-10 | NOT STARTED | NO |
| 2.5 Marketplace | ~3,000 | 3-4 | NOT STARTED | NO |
| 2.6 Admin UI | ~3,000 | 3-4 | NOT STARTED | NO |
| **TOTAL** | **~25,000** | **16-20** | **3-4 months** | |

**Parallelization Opportunities:**
- Phase 2.4 weeks 13-18 can overlap with Phase 2.5 weeks 15-18
- Phase 2.6 can run entirely in parallel (optional)
- Realistic timeline: **14-16 weeks (3.5-4 months)**

---

## Implementation Sequence

### Month 1 (Weeks 1-4): Critical Path
1. Week 1-2: Persistence (JSON + SQLite + PostgreSQL)
2. Week 3-4: Error Recovery (retry + circuit breaker + timeout)
3. **Milestone:** Axis Core production-ready (~2,000 lines)

### Month 2 (Weeks 5-8): Production Complete
4. Week 5-6: Observability (Prometheus + logging + tracing)
5. Week 7-8: Streaming (AsyncRunner + SSE + WebSocket)
6. **Milestone:** Axis Full Production Stack (~5,000 lines)

### Month 3 (Weeks 9-12): First Orders
7. Week 9-10: Pattern Weaver Order (~3,500 lines)
8. Week 11-12: Orthodoxy Warden Order (~2,000 lines)
9. **Milestone:** 2 Premium Orders ready, marketplace proof-of-concept

### Month 4 (Weeks 13-18): Complete Orders + Marketplace
10. Week 13-16: Codex Hunter Order (~7,000 lines, largest)
11. Week 17-18: Babel Garden Order (~2,500 lines)
12. Week 15-18: Marketplace Infrastructure (parallel) (~3,000 lines)
13. **Milestone:** All 4 Orders ready, marketplace functional

### Optional (Weeks 19-22): Admin UI
14. Week 19-22: Admin Dashboard (~3,000 lines)
15. **Milestone:** Enterprise-ready with monitoring UI

---

## Success Metrics

### Technical Milestones
- [ ] Phase 2.3 complete: Axis Core 774 → 5,000 lines (production-ready)
- [ ] Phase 2.4 complete: 4 Orders (~15,000 lines) migrated from Vitruvyan
- [ ] Phase 2.5 complete: Marketplace CLI functional (axis install/list/search)
- [ ] Phase 2.6 complete: Admin UI deployed (optional)
- [ ] Total codebase: ~25,000 lines (Option C target 20-30K)

### Quality Gates
- [ ] 100 tests minimum (50 core + 50 Orders)
- [ ] All tests passing
- [ ] Zero external dependencies in Core (stdlib only)
- [ ] Orders dependencies isolated (requirements.txt per Order)
- [ ] Documentation complete (README + order.yaml per Order)

### Business Milestones
- [ ] Vitruvyan migration started (after Phase 2.3, Week 8)
- [ ] First Premium Order sale (Pattern Weaver or Orthodoxy, Month 3)
- [ ] Marketplace open to 3rd party devs (Month 4)
- [ ] First external Order published (Month 5-6)

---

## Risk Mitigation

### Technical Risks
1. **Performance degradation from immutability**
   - Mitigation: Benchmark after Phase 2.3, optimize if >30% overhead
   - Fallback: Structural sharing (copy-on-write) for large GraphState

2. **Orders integration complexity**
   - Mitigation: Start with simplest Order (Orthodoxy 387 lines), learn patterns
   - Fallback: Reduce scope to 2 Orders (Pattern + Orthodoxy) if timeline slips

3. **Marketplace adoption slow**
   - Mitigation: Launch with 4 reference Orders, incentivize early devs (90/10 split first 6 months)
   - Fallback: Focus on direct sales (Pro/Enterprise) if marketplace <10% revenue

### Timeline Risks
1. **Phase 2.4 takes longer than 10 weeks**
   - Mitigation: Codex is 70% of Order LOC, start early (Week 9), accept longer timeline
   - Fallback: Ship 3 Orders (skip Codex), add later as update

2. **User (dreamer) context switching**
   - Mitigation: AI-driven development minimizes user time (decisions only, not coding)
   - Fallback: Clear decision checkpoints (end of each Phase), async validation

---

## Next Steps (Immediate)

### Week 1 Actions
1. **Create Phase 2.3 file structure:**
   ```
   axis/
   ├── persistence/
   │   ├── protocol.py
   │   ├── json_adapter.py
   │   ├── sqlite_adapter.py
   │   └── postgresql_adapter.py
   ├── recovery/
   │   ├── retry.py
   │   ├── circuit_breaker.py
   │   └── timeout.py
   └── observability/
       ├── metrics.py
       ├── logging.py
       └── tracing.py
   ```

2. **Implement persistence protocol:**
   - PersistenceProvider protocol (100 lines)
   - JSON adapter first (simplest, 300 lines)
   - Test with existing GraphState (7 core tests extended)

3. **Validate serialization:**
   - All epistemic types to_dict() / from_dict()
   - Handle tuples → lists for JSON
   - Preserve immutability on deserialization

4. **Daily cadence:**
   - User reviews output daily (1-2 hours)
   - AI implements 500-1,000 lines/day
   - Tests run continuously (TDD approach)

### Decision Checkpoint (End Week 2)
- [ ] Persistence working? (JSON + SQLite)
- [ ] GraphState serialization correct? (all types)
- [ ] Performance acceptable? (<100ms save/load)
- [ ] Continue to Week 3-4 (Error Recovery)?

---

## Timeline Visualization

```
Month 1: [========== CRITICAL PATH ==========]
         Week 1-2: Persistence (~1,500 lines)
         Week 3-4: Error Recovery (~600 lines)
         
Month 2: [========== PRODUCTION COMPLETE ==========]
         Week 5-6: Observability (~500 lines)
         Week 7-8: Streaming (~800 lines)
         
Month 3: [====== ORDERS START ======] [== MARKETPLACE ==]
         Week 9-10: Pattern (~3,500)   |
         Week 11-12: Orthodoxy (~2,000)| Week 15-18: CLI
         
Month 4: [========== ORDERS COMPLETE ==========]
         Week 13-16: Codex (~7,000 lines)
         Week 17-18: Babel (~2,500 lines)
         
Optional: [== ADMIN UI ==]
          Week 19-22: Dashboard (~3,000 lines)
```

---

## Commitment

**This roadmap is:**
- Ambitious but achievable (70K Vitruvyan validates AI-driven model)
- Reuse-optimized (70% of Order code exists in Vitruvyan)
- Milestone-driven (clear checkpoints every 2 weeks)
- Risk-aware (fallbacks for each major risk)

**Success requires:**
- User validation at end of each Phase (2-4 week intervals)
- AI execution of implementation (500-1,000 lines/day sustained)
- Vitruvyan as reference (architecture + code patterns)
- Axis principles maintained (immutability, simplicity, no redesign)

**Start date:** January 7, 2026  
**Target completion:** April 30, 2026 (16 weeks)  
**First Order sale:** March 2026 (Week 12, Pattern Weaver)  
**Marketplace launch:** May 2026 (Week 18)

---

**Andiamo. "Facile come bere una birra."**
