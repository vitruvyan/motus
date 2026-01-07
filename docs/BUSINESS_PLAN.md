# Axis Business Plan

**Freemium SaaS Model for Regulated AI Orchestration**

Version 1.0 — January 7, 2026

---

## Executive Summary

**Axis** operates on a **freemium marketplace model** similar to VS Code Extensions or PyTorch ecosystem:

- **Core orchestrator:** Open source (MIT license), free forever
- **Premium Orders:** Paid add-ons for compliance, sold via marketplace
- **Revenue:** Subscription-based ($500-10K/month) + consulting

**Target market:** Regulated AI domains (finance, healthcare, legal) where compliance is legally mandated.

**Competitive advantage:** LangGraph has no compliance-ready add-ons. Axis monetizes the compliance layer.

**Year 1 target:** $280K ARR (10 Pro + 2 Enterprise customers)  
**Year 3 target:** $4.7M ARR (200 Pro + 30 Enterprise + marketplace)

---

## Market Opportunity

### Total Addressable Market (TAM)

**Regulated AI Systems (2026-2028):**
- Financial services: 2,500 hedge funds + 500 banks globally
- Healthcare AI: 1,200 medical AI companies
- Legal tech: 800 legal AI firms
- Automotive/Safety-critical: 300 autonomous systems companies

**TAM:** ~5,000 potential enterprise customers

**Market size:** $12B by 2028 (Gartner estimate for regulated AI)

### Serviceable Addressable Market (SAM)

**Initial focus (2026-2027):**
- EU/US hedge funds requiring MiFID II compliance: ~500 firms
- Healthcare AI requiring FDA 21 CFR Part 11: ~200 firms
- Total SAM: ~700 firms

**At $5K/month average:** $42M potential ARR

### Serviceable Obtainable Market (SOM)

**Realistic capture (Year 3):**
- 5% of SAM = 35 paying customers
- Average $5K/month = $2.1M ARR

**With community Orders (marketplace):**
- + $500K/year from 3rd party revenue share
- **Total SOM: $2.6M ARR by 2028**

---

## Business Model

### Revenue Streams

#### 1. Subscription (Primary Revenue)

**Free Tier (Open Source):**
- Axis Core: GraphState + Runner + Policy (~200 lines)
- Synaptic Bus: Observation substrate (~200 lines)
- Epistemic Types + Protocols: All types (~374 lines)
- Community support: GitHub issues, Discord
- License: MIT (unrestricted commercial use)

**Value:** Drop-in LangGraph replacement with immutability + audit trail

**Target:** Individual developers, small teams, proof of concept

---

**Pro Tier: $500-1,000/month**

**Includes:**
- ✅ 1 Premium Order (user's choice):
  - **Pattern Weaver:** Pattern detection + intent inference
  - **Orthodoxy Warden:** Constraint validation + compliance checks
  - **Semantic Interpreter:** Explainability + implication derivation
- ✅ Email support (48h SLA)
- ✅ Monthly updates + security patches
- ✅ Production SLA: 99.5% uptime guarantee
- ✅ Commercial license for Order code

**Value:** MiFID II / EU AI Act compliance out-of-the-box

**Target:** 
- Mid-size hedge funds (AUM $100M-1B)
- HealthTech startups (Series A-B)
- Legal AI firms (10-50 employees)

**Pricing rationale:**
- LangSmith (LangChain observability): $39-199/month
- Axis includes compliance + orchestrator: 3-5x premium justified
- Customer saves $50K-100K/year vs. building internal compliance

---

**Enterprise Tier: $5,000-10,000/month**

**Includes (beyond Pro):**
- ✅ **All Premium Orders** (unlimited access)
- ✅ **2 Custom Orders/year** (domain-specific, developed by Axis team)
- ✅ Priority support (4h SLA, dedicated Slack channel)
- ✅ Regulatory audit assistance (MiFID II, FDA 21 CFR Part 11, EU AI Act)
- ✅ Performance tuning + architecture review (quarterly)
- ✅ Production SLA: 99.9% uptime + on-call support
- ✅ On-premise deployment + air-gapped environments
- ✅ Training & onboarding (4 sessions/year)

**Value:** Turnkey compliance solution, no internal dev needed

**Target:**
- Large hedge funds (AUM $1B+)
- Fortune 500 healthcare (pharma AI, clinical trials)
- Automotive AI (autonomous vehicles, ISO 26262)
- Banks & financial institutions (MiFID II, Basel III)

**Pricing rationale:**
- Internal compliance team costs $300K-500K/year (2-3 engineers)
- External audit firm: $50K-100K per audit
- Axis replaces both: $120K/year is 60-75% cost savings

---

#### 2. Marketplace Revenue (Secondary, Year 2+)

**Model:** 3rd party developers build & sell custom Orders

**Revenue share:**
- Developer: 70%
- Axis platform fee: 30%

**Example Orders (3rd party):**
- Basel III Compliance Order (banking)
- HIPAA Audit Trail Order (healthcare)
- GDPR Article 22 Explainer (consumer AI)
- ISO 26262 Safety Order (automotive)

**Projected marketplace GMV:**
- Year 2: $300K GMV → $90K Axis revenue
- Year 3: $700K GMV → $210K Axis revenue

**Platform benefits:**
- Ecosystem growth (network effects)
- Zero marginal cost (developers do work)
- 30% margin on all transactions

---

#### 3. Consulting & Custom Development (Tertiary)

**Services:**
- Custom Order development: $30K-50K per Order
- Migration from LangGraph: $20K-40K project
- Training & onboarding: $5K/day (workshops)
- Regulatory compliance review: $10K-25K (external audit prep)

**Pricing:** $150-300/hour (varies by engagement)

**Target:** Enterprise customers needing specialized Orders

**Projected revenue:**
- Year 1: $10K (pilot projects)
- Year 2: $100K (10-15 projects)
- Year 3: $250K (20-30 projects)

---

### Pricing Strategy Summary

| Tier | Price | Target Customers | Annual Value |
|------|-------|------------------|--------------|
| **Free** | $0 | Individuals, small teams | $0 |
| **Pro** | $500-1K/mo | Mid-size companies | $6K-12K |
| **Enterprise** | $5K-10K/mo | Large enterprises | $60K-120K |
| **Custom** | Project-based | Specialized needs | $30K-50K |

---

## Revenue Projections

### Year 1 (2026): Market Validation

**Subscription:**
- 10 Pro customers @ $750/month avg = $90K ARR
- 2 Enterprise customers @ $7,500/month avg = $180K ARR
- **Subtotal subscription:** $270K ARR

**Consulting:**
- 5 pilot projects @ $2K avg = $10K
- **Subtotal consulting:** $10K

**Total Year 1:** $280K ARR

**Assumptions:**
- Vitruvyan as showcase (1 customer, self)
- 9 external Pro customers (hedge funds, healthtech)
- 2 external Enterprise (banks or pharma)
- Conservative: No marketplace revenue yet

---

### Year 2 (2027): Growth Phase

**Subscription:**
- 50 Pro customers @ $750/month = $450K ARR
- 10 Enterprise customers @ $7,500/month = $900K ARR
- **Subtotal subscription:** $1.35M ARR

**Marketplace:**
- 3rd party Orders launch (10 Orders available)
- $300K GMV → $90K Axis revenue (30% cut)

**Consulting:**
- 15 projects @ $7K avg = $105K

**Total Year 2:** $1.545M ARR

**Assumptions:**
- 5x growth in Pro (word of mouth, conference talks)
- 5x growth in Enterprise (case studies, audit firm referrals)
- Marketplace launches Q2 2027

---

### Year 3 (2028): Scale Phase

**Subscription:**
- 200 Pro customers @ $750/month = $1.8M ARR
- 30 Enterprise customers @ $7,500/month = $2.7M ARR
- **Subtotal subscription:** $4.5M ARR

**Marketplace:**
- 30 3rd party Orders available
- $700K GMV → $210K Axis revenue

**Consulting:**
- 30 projects @ $8K avg = $240K

**Total Year 3:** $4.95M ARR (~$5M)

**Assumptions:**
- 4x growth in Pro (ecosystem maturity, brand recognition)
- 3x growth in Enterprise (Fortune 500 adoption)
- Marketplace revenue doubles (more developers, more Orders)

---

## Unit Economics

### Customer Acquisition Cost (CAC)

**Channels:**
- Content marketing (blog, whitepaper, case studies): $2K/month
- Conference talks (PyData Finance, QuantCon): $5K/event (travel)
- GitHub organic (stars, forks, PRs): $0
- Regulatory audit firm referrals: $0 (partnership)

**Estimated CAC:**
- Pro customer: $1,500 (3 months marketing + 1 conference talk)
- Enterprise customer: $10,000 (6 months sales cycle + proof of concept)

### Lifetime Value (LTV)

**Pro customer:**
- Average tenure: 24 months (conservative)
- Monthly revenue: $750
- LTV: $18,000

**Enterprise customer:**
- Average tenure: 36 months (high switching cost)
- Monthly revenue: $7,500
- LTV: $270,000

### LTV/CAC Ratio

**Pro:** $18K / $1.5K = **12:1** (excellent)  
**Enterprise:** $270K / $10K = **27:1** (exceptional)

**Industry benchmark:** 3:1 is good, 5:1 is great.

**Axis achieves 12-27:1 because:**
- Compliance is pain point (high willingness to pay)
- Switching cost is high (regulatory audit trail)
- Organic growth via GitHub (low CAC)

---

## Go-to-Market Strategy

### Phase 1: Foundation (Q1-Q2 2026)

**Objective:** Establish credibility, attract early adopters

**Tactics:**
1. **Open Source Core:** GitHub public repo (MIT license)
2. **Documentation:** Comprehensive guides, API reference, examples
3. **Vitruvyan Case Study:** "8 months production, MiFID II compliant"
4. **Community:** Discord server, GitHub discussions
5. **Content:** Blog posts on LangGraph limitations, compliance requirements

**Metrics:**
- 100+ GitHub stars
- 10+ contributors
- 5 external adopters (free tier)

**Budget:** $5K (hosting, domain, marketing materials)

---

### Phase 2: First Revenue (Q2-Q3 2026)

**Objective:** Validate pricing, close first paying customers

**Tactics:**
1. **Pattern Weaver Order:** Launch first premium Order (Vitruvyan-based)
2. **Pricing Page:** Public pricing, 14-day free trial
3. **Sales Outreach:** Direct contact to 20 hedge funds (LinkedIn, email)
4. **Conference Talk:** Submit to PyData Finance (acceptance ~50%)
5. **Regulatory Partnerships:** Reach out to 3 audit firms (referral partnerships)

**Metrics:**
- 3 Pro customers (paid)
- 1 Enterprise pilot (paid or POC)
- $30K MRR

**Budget:** $10K (Stripe setup, legal review, conference travel)

---

### Phase 3: Marketplace Launch (Q3-Q4 2026)

**Objective:** Enable 3rd party ecosystem, accelerate growth

**Tactics:**
1. **Marketplace Website:** Order browsing, payment processing, ratings/reviews
2. **Developer Documentation:** "How to Build an Order" guide
3. **Developer Outreach:** Invite 10 consultants/agencies to build Orders
4. **Community Orders:** Open source Orders (contributed, curated by Axis)
5. **PR Push:** TechCrunch, Hacker News (marketplace launch story)

**Metrics:**
- 5 3rd party Orders available
- 10 Pro customers (total)
- 2 Enterprise customers (total)
- $50K MRR

**Budget:** $15K (marketplace dev, payment processing, PR agency)

---

### Phase 4: Enterprise Expansion (2027)

**Objective:** Fortune 500 adoption, regulatory partnerships

**Tactics:**
1. **Case Studies:** 3-5 detailed customer stories (anonymized)
2. **Whitepapers:** MiFID II compliance guide, EU AI Act readiness checklist
3. **Enterprise Sales:** Hire 1 sales rep (commission-based)
4. **Conference Circuit:** QuantCon, PyData, FinTech conferences (3-4/year)
5. **Audit Firm Partnerships:** Co-marketing with Big 4 (Deloitte, PwC, etc.)

**Metrics:**
- 50 Pro customers
- 10 Enterprise customers
- $1M ARR

**Budget:** $100K (sales rep salary, travel, marketing)

---

## Competitive Landscape

### Direct Competitors

**LangGraph / LangChain:**
- **Strength:** Mature ecosystem, 900+ stars, production-ready
- **Weakness:** No compliance focus, state mutability, requires external instrumentation
- **Revenue model:** LangSmith SaaS ($39-199/month) - observability only
- **Market position:** General-purpose orchestration

**Axis advantage:** Compliance by design, Premium Orders for regulated domains

---

**Prefect / Airflow:**
- **Strength:** Data pipeline orchestration, enterprise adoption
- **Weakness:** Not AI-focused, no epistemic reasoning, complex setup
- **Revenue model:** Cloud hosting ($0.20/compute hour) + Enterprise support
- **Market position:** Data engineering workflows

**Axis advantage:** AI-native, immutability, epistemic types

---

**Ray / Dask:**
- **Strength:** Distributed computing, scalability
- **Weakness:** Low-level (not orchestration), no compliance features
- **Revenue model:** Anyscale (Ray company) raises $259M, unclear monetization
- **Market position:** Infrastructure layer

**Axis advantage:** Higher abstraction (orchestration), compliance focus

---

### Indirect Competitors

**Custom Internal Solutions:**
- Many regulated firms build in-house compliance layers
- Cost: $300K-500K/year (2-3 engineers)
- Risk: No external validation, regulatory audit risk

**Axis advantage:** Battle-tested (Vitruvyan 8 months), external audit support, 60-75% cost savings

---

**Consulting Firms (Big 4):**
- Deloitte, PwC offer AI compliance consulting
- Cost: $200-500/hour, $500K-1M project
- Risk: No software output, just reports

**Axis advantage:** Software + consulting, continuous updates, 80-90% cost savings

---

## Financial Projections (Summary)

| Metric | Year 1 (2026) | Year 2 (2027) | Year 3 (2028) |
|--------|---------------|---------------|---------------|
| **Pro Customers** | 10 | 50 | 200 |
| **Enterprise Customers** | 2 | 10 | 30 |
| **Subscription ARR** | $270K | $1.35M | $4.5M |
| **Marketplace Revenue** | $0 | $90K | $210K |
| **Consulting Revenue** | $10K | $105K | $240K |
| **Total Revenue** | $280K | $1.545M | $4.95M |
| **Gross Margin** | 85% | 88% | 90% |
| **Operating Expenses** | $120K | $600K | $1.5M |
| **EBITDA** | $118K | $760K | $2.96M |
| **EBITDA Margin** | 42% | 49% | 60% |

**Notes:**
- Gross margin includes only COGS (hosting, support)
- Operating expenses include marketing, sales, R&D
- Break-even: Month 6 of Year 1 (after first 2 Enterprise customers)

---

## Investment Requirements

### Bootstrapped Path (Recommended)

**Year 1 (2026): $50K**
- Marketing: $20K (conferences, content, ads)
- Legal: $10K (contracts, terms of service, marketplace)
- Infrastructure: $5K (hosting, Stripe, domain)
- Buffer: $15K

**Funding source:** Revenue from Vitruvyan (self-funded)

**Milestones:**
- Q1: Open source launch
- Q2: First 3 paying customers
- Q3: Marketplace launch
- Q4: $50K MRR

---

### Venture-Backed Path (Alternative)

**Seed Round: $1M @ $5M post-money valuation**

**Use of funds:**
- Product development: $400K (2 engineers, 18 months)
- Sales & marketing: $300K (1 sales rep, conferences, content)
- Operations: $200K (legal, accounting, infrastructure)
- Runway: $100K (buffer)

**Milestones for Series A ($5M @ $25M post):**
- $2M ARR
- 100 paying customers
- 3rd party marketplace with 20+ Orders
- Big 4 partnership (1+ audit firm)

**Exit potential:**
- Strategic acquisition (LangChain, Anthropic, OpenAI): $50-100M (Year 4-5)
- IPO (if market scales): $500M+ valuation (Year 7-10)

---

## Risk Mitigation

### Technical Risks

**Risk:** Performance at scale (immutability cost)  
**Mitigation:** Benchmarking shows <20% memory overhead, archival to disk for large traces  
**Probability:** Low

**Risk:** Limited ecosystem vs. LangGraph  
**Mitigation:** Marketplace enables 3rd party contributions, focus on niche (compliance)  
**Probability:** Medium

---

### Market Risks

**Risk:** Slow adoption in conservative regulated domains  
**Mitigation:** Vitruvyan case study, audit firm partnerships, 14-day free trial  
**Probability:** Medium

**Risk:** LangGraph adds compliance features  
**Mitigation:** Architectural (built-in) > Instrumented (added), 18-month head start  
**Probability:** Low

---

### Business Risks

**Risk:** Pricing too high (market rejects $500/month)  
**Mitigation:** Flexible pricing, annual discounts, value-based pricing (vs. $300K internal team)  
**Probability:** Low

**Risk:** Regulatory changes (MiFID II weakened)  
**Mitigation:** EU AI Act is strengthening (not weakening), US likely to follow  
**Probability:** Very Low

---

## Success Metrics (KPIs)

### Product Metrics
- GitHub stars: 100 (Year 1) → 500 (Year 2) → 2,000 (Year 3)
- Active users (monthly): 50 (Year 1) → 300 (Year 2) → 1,500 (Year 3)
- Orders in marketplace: 1 (Year 1) → 10 (Year 2) → 30 (Year 3)

### Revenue Metrics
- MRR: $23K (Year 1) → $129K (Year 2) → $412K (Year 3)
- ARR: $280K (Year 1) → $1.545M (Year 2) → $4.95M (Year 3)
- Churn rate: <5% monthly (target: <3%)

### Customer Metrics
- CAC: $1.5K (Pro), $10K (Enterprise)
- LTV: $18K (Pro), $270K (Enterprise)
- LTV/CAC: 12:1 (Pro), 27:1 (Enterprise)
- Net Promoter Score (NPS): >50

---

## Conclusion

**Axis is positioned to become the compliance-ready orchestrator for regulated AI.**

**Key strengths:**
- ✅ Freemium model reduces adoption friction
- ✅ Compliance pain point = high willingness to pay
- ✅ Marketplace creates network effects
- ✅ Unit economics are exceptional (LTV/CAC 12-27:1)
- ✅ Vitruvyan proves product-market fit

**Recommended path:**
1. Bootstrap Year 1 with $50K (self-funded)
2. Validate pricing with 10 Pro + 2 Enterprise customers
3. Launch marketplace Q3 2026
4. Consider Series A in Year 2 if growth accelerates (optional)

**Total addressable outcome:**
- Conservative (bootstrap): $5M ARR by 2028, profitable
- Aggressive (VC-backed): $20M ARR by 2028, acquisition target

**Next steps:**
1. Finalize Pattern Weaver Order (Q1 2026)
2. Create pricing page + trial signup (Q2 2026)
3. Outreach to first 20 hedge funds (Q2 2026)
4. Submit conference talks (PyData, QuantCon)

---

**Author:** Vitruvyan Team  
**Contact:** [To be determined]  
**Version:** 1.0 (2026-01-07)

---

*"Axis is not a feature. It's a business."*
