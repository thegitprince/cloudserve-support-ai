# Product Requirements Document — CloudServe Intelligent Support System

## 1. Document Control & Metadata

| Field | Value |
|---|---|
| Version | 2.0 |
| Written by | Prince Prakash (chandrucb95@gmail.com), with AI pair-engineering support (Claude Sonnet 5) |
| Date | 2026-11-16 |
| Status | Revised Baseline (supersedes v1.0) |
| Approved by | Pending review |
| Source documents | `docs/PRD_v1.md`, `docs/Stage_5_PRD_Revision_Log.md`, `storage/results_validation_v4.json`, `storage/results_validation_v3.json` |
| Revision note | This is version 2.0, the compulsory Stage 5 revision. Every change here traces to a specific, cited finding from the live validation gate — see `docs/Stage_5_PRD_Revision_Log.md` for the full change table, assumption-by-assumption check, and reflection. Sections unchanged from v1.0 in substance are carried forward with a note; sections that changed are rewritten in place rather than appended as a diff, so this document stands on its own. |

---

## 2. Executive Summary & Problem Framing

Unchanged from v1.0: CloudServe asked for a chatbot; the Project Brief is explicit that building one would satisfy the request and fail the assignment. The problem statement, the 71.4%-of-volume answerable-from-docs figure, and the four-way framing (identify answerable tickets, retrieve despite the findability gap, draft only defensible answers, escalate everything else with a real triage packet) all carry forward unchanged — see `docs/PRD_v1.md` §2 for the full text. Building this system did not change the problem; it changed what the system actually does when the problem gets hard, which is the subject of this revision.

---

## 3. User Personas & Target Stakeholders

Unchanged from v1.0 (`docs/PRD_v1.md` §3) — Sofia, Daniel, Marcus, and the enterprise/Ravi segment, with the same needs and success signals. One update to Marcus's row: the fairness signal he should watch is no longer presumed to be the enterprise-tier gap identified in discovery — see §7 and `docs/Fairness_Audit.md` for what the system's own output actually showed.

---

## 4. Functional Requirements

FR-01 through FR-11 carry forward from `docs/PRD_v1.md` §4 with the amendment to FR-04 below (bold) and one new requirement, FR-12.

| ID | Requirement | Priority | Discovery Evidence | Acceptance Criteria |
|---|---|---|---|---|
| FR-01 – FR-03, FR-05 – FR-11 | Unchanged — see `docs/PRD_v1.md` §4 for full text. | Must / Should | See v1.0 | See v1.0 |
| **FR-04 (amended)** | The system shall classify every ticket into exactly one of two routing categories — **mandatory escalation** or **discretionary** — before any auto-response is considered, using a deterministic, code-level `MANDATORY_ESCALATION_INTENTS` lookup table as the authoritative decision on the classified intent. **This table is now explicitly documented as insufficient alone: it is keyed on the classifier's own output, and a classification error that crosses the mandatory/discretionary boundary bypasses it by construction. FR-12 is the independent backstop this amendment requires.** | Must | Unchanged dataset evidence (`docs/PRD_v1.md` §4), plus: **ticket VAL-0048 (`storage/results_validation_v3.json`, 2026-11-16) — a believed credential breach classified as the discretionary intent `api_key_issue` instead of `security_incident`, resulting in a zero-tolerance policy breach.** | Unchanged acceptance criteria, plus: **the system must also pass FR-12's independent content scan on every ticket, not only the intent-keyed lookup.** |
| **FR-12 (new)** | The system shall run a deterministic, literal keyword/phrase scan over the ticket's subject and body text — evaluated independently of, and before, the classified intent is trusted — and force escalation on any match, overriding the classified intent and confidence entirely. | Must | VAL-0048 (see FR-04 above). Implemented in `src/routing/policy.py` (`HIGH_RISK_KEYWORDS`, `contains_high_risk_keyword`), wired into `src/pipeline/graph.py`'s `node_route_mandatory`. | A ticket containing any term in `HIGH_RISK_KEYWORDS` (breach, compromised, unauthorized access, exposed credentials, hacked, vulnerability, cve, legal action, sue) always escalates, regardless of classified intent or confidence value, verified by the VAL-0048 regression case specifically (`storage/results_validation_v4.json`: `policy_breaches_count: 0`). Every ticket on the mandatory path now also writes its own "routing"-stage Decision Log record with the specific trigger reason — a gap in the original FR-09 implementation that this amendment also closed. |

---

## 5. Non-Functional Requirements

NFR-02, NFR-04, NFR-05 carry forward unchanged from `docs/PRD_v1.md` §5. NFR-01, NFR-03, and NFR-06 are amended below with measured results.

| ID | Category | Requirement | Measured result (v4, `storage/results_validation_v4.json`, 80-ticket validation set, `openrouter:google/gemini-2.5-flash`, 2026-11-16) |
|---|---|---|---|
| **NFR-01 (amended)** | Latency | Classification P95 < 2s; end-to-end response generation P95 < 5s for the auto-respond path. | Classification P95 = **2.05s**; generation P95 = **5.17s**. Both marginally over budget. **Accepted as a known shortfall for this iteration** rather than compressing the prompt further — the prompt changes that fixed classification accuracy (52.5%→93.8%) and the VAL-0048 policy breach cost roughly a second of latency per stage, and further compression risks reopening those fixes (`docs/Stage_5_PRD_Revision_Log.md` §2/§4). Not yet measured against the hidden set. |
| **NFR-03 (amended)** | Accuracy | Intent classification precision ≥ 85%; confidence calibration within 5 points of observed accuracy; citation accuracy ≥ 95%; hallucination rate ≤ 5%. | Classification accuracy **93.8%** (≥85% target: pass). Calibration gap **5.3 points** (≤5pt target: marginal fail, improved from an initial +35.4pt raw-confidence miscalibration via isotonic recalibration, `src/models/calibration.py`). Citation accuracy / hallucination rate: **0% hallucination (automated proxy), not independently human-reviewed** — `Evaluation_Framework.md` requires a 50+-response, two-assessor human review for this figure, which this harness cannot fabricate; the automated proxy (deterministic + LLM guardrail layers, zero blocks this run) is a lower-confidence signal, not a substitute. |
| NFR-06 (amended — see §7) | Fairness | Quality shall differ by no more than 5 points between customer-tier, region, or fluency segments, or the difference shall be explicitly reported and explained. | `standard` tier (54.76% FCR vs. 70% overall) and `latin_america` region (28.57% FCR, n=7) both exceed the 5-point band and are reported explicitly — see `docs/Fairness_Audit.md`, not averaged away. |

---

## 6. Explicit Out-of-Scope Boundaries

Unchanged from `docs/PRD_v1.md` §6.

---

## 7. Success Metrics & Sentence Test

| Measure | Baseline | Target | **Measured (v4, 2026-11-16, validation set, n=80)** |
|---|---|---|---|
| First-contact resolution (FCR) | 43.8% measured | 65% | **70.0%** — pass |
| Escalation rate | 56.2% measured | ≤30% | **30.0%** — pass (at the boundary) |
| Classification accuracy | — | ≥85% | **93.8%** — pass |
| Confidence calibration gap | — | ≤5 points | **5.3 points** — marginal fail |
| Classification / generation latency P95 | — | <2s / <5s | **2.05s / 5.17s** — marginal fail on both, accepted this iteration |
| **Policy breaches** (`must_not_auto_respond` auto-answered) | n/a | **0%, zero-tolerance** | **0/80** — pass (1/80 in the prior run, fixed by FR-12) |
| Time to first reply | 8–12 hours | Under 2 hours / under 5 min auto-respond | Not independently measured against a real SLA clock — see `docs/PRD_v1.md` §9 open question, still open |
| Customer satisfaction | 2.97/5 measured | 4.0–4.2/5 | Not measured — no live customers (`docs/PRD_v1.md` §7 proxy-measure caveat still applies) |

**The sentence test, restated against the measured result:**
- *This revision will have been worth doing if* the governance condition that actually failed during build — the zero-tolerance policy breach on VAL-0048 — is the one thing fixed with certainty, even if FCR, latency, and calibration land close to but not perfectly inside their targets. A system that hits every numeric target but still has an unguarded safety-net bypass is not the one worth shipping; a system that is honest about marginal numeric misses but has closed that specific gap is.
- *We will know it did not work if* a future run reproduces a policy breach despite FR-12, or if the hidden-set run shows the 93.8% accuracy / 5.3-point calibration figures were artifacts of the validation set rather than real generalization.

---

## 8. Assumptions & Risk Log

Carried forward from `docs/PRD_v1.md` §8 with the assumption-by-assumption check now resolved — see `docs/Stage_5_PRD_Revision_Log.md` §3 for the full table (which assumptions held, which didn't, and what changed because of each). The headline addition: the risk named in v1.0 as *"the system answers confidently and incorrectly on an answerable-looking but actually ambiguous ticket"* was not hypothetical — it materialized exactly as VAL-0048, and FR-12 is the mitigation that risk entry called for before it happened.

---

## 9. Open Questions

Carried forward from `docs/PRD_v1.md` §9 (the 60%-vs-65% FCR target inconsistency remains genuinely unresolved — this revision adopted 65% and measured against it). Two new open questions from this revision:

| Question | Why it matters | Owner | Resolve by |
|---|---|---|---|
| Does `HIGH_RISK_KEYWORDS`' small, literal 9-term list (FR-12) have an acceptable false-negative rate against breach/compromise phrasing that doesn't use any of those exact substrings? | A content-based safety net that misses a real breach because of wording is a false sense of security, not a fix. | Prince Prakash | Before the hidden-set run — ideally against a deliberately adversarial phrasing test set, which does not currently exist. |
| Are the `standard`-tier and `latin_america` FCR gaps (`docs/Fairness_Audit.md`) a real, persistent fairness issue or validation-set sampling noise, given thin segment sizes (n=42, n=7)? | NFR-06 treats a >5-point gap as something to explain, not average away — but a 7-ticket sample cannot distinguish a real effect from noise. | Prince Prakash | Before any architectural response is considered — see `docs/Fairness_Audit.md`'s recommendation to monitor rather than act on this sample alone. |
