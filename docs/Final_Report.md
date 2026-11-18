# CloudServe Intelligent Support System — Capstone Final Report

Prince Prakash (chandrucb95@gmail.com) · 2026-11-17

**Declaration of AI tool use**: this project was built in pair-engineering sessions with Claude Sonnet 5 (Anthropic), used for code generation, prompt drafting, documentation drafting, and live debugging of evaluation runs under my direction and review. All architectural decisions, dataset interpretation, and acceptance of measured results are my own; every claim about performance in this report is backed by a committed, reproducible run named explicitly where it appears.

---

## 1. Executive summary

This system classifies, routes, retrieves for, and drafts responses to CloudServe Solutions support tickets, escalating anything it should not answer with a context-rich handoff packet rather than a bare forward. On the 80-ticket validation set (`storage/results_validation_v4.json`, `openrouter:google/gemini-2.5-flash`, 2026-11-16), it reaches **70.0% first-contact resolution** (target: 65%, baseline: 43.8%) and **93.8% classification accuracy** (target: ≥85%), while holding the escalation rate at **30.0%** (target: ≤30%) and — critically — **zero zero-tolerance policy breaches**, after fixing exactly one such breach found during build (§8, §9).

**The single most important caveat**: these numbers come from a system that got a real safety-net bypass wrong once during build (ticket VAL-0048, a security incident misclassified as a routine API-key rotation, which was auto-responded before the fix) and from a validation set small enough that several fairness-segment findings — most notably a -47-point FCR gap for the `latin_america` region — are built from as few as 7 tickets. Both are reported in full below, not smoothed over. Classification/generation latency (2.05s / 5.17s) and confidence calibration (5.3-point gap) both sit marginally over their NFR targets, accepted as a deliberate tradeoff against the accuracy and safety fixes that mattered more (§7, §9).

---

## 2. The problem

CloudServe asked for a chatbot to bring first-response time inside a 2-hour SLA. Discovery evidence (§3; full detail in `docs/stage_1_discovery_findings.md`) shows this framing targets the wrong bottleneck: first-contact resolution sits at a measured 43.8% against a 65% industry benchmark, and the operational cost is concentrated in escalation, not raw reply speed — escalated tickets take **23.8x** as long to resolve as non-escalated ones (726.6 vs. 30.5 minutes), far above Marcus Adeyemi's own estimate of "roughly 4x." A faster first reply does not fix a ticket that was going to be escalated anyway.

The gap between what was asked for and what the evidence shows is needed: CloudServe does not lack answers — 71.4% of tickets are answerable from its existing, accurate 29-article knowledge base — it lacks a reliable path from a customer's own phrasing to the right article, and a safe way to decide, per ticket, whether an automated answer is appropriate at all. The system therefore needed to (1) classify intent and urgency, (2) separate mandatory-escalation categories (security, compliance, feature requests, data residency, and anything unclassifiable) from discretionary ones before any auto-response is considered, (3) retrieve against ticket *body* semantics rather than title-keyword overlap, (4) draft only responses it can defend with a citation, and (5) hand everything else to a human with a real triage packet, not a bare forward.

---

## 3. Discovery findings

Full detail, methodology, and the complete evidence tables are in `docs/stage_1_discovery_findings.md`, built from five stakeholder interview transcripts and the 500-ticket development set. Three findings most directly shaped the design:

**Finding 1 — the escalation problem is roughly half avoidable.** Of 189 development-set tickets labeled `expected_route=escalate`, 87 (46.0%) are policy-mandatory (`must_not_auto_respond=True`); the remaining 102 (54.0%) are discretionary — almost exactly Daniel Okonkwo's (Tier Two Engineer) estimate that "about half" of what reaches him could have been resolved at tier one with more confidence or better findability. This sized the actual target: automate the discretionary half, never touch the mandatory half.

**Finding 2 — the problem is findability, not missing documentation.** Sofia Restrepo (Tier One Agent) reports internal keyword search doesn't match how customers phrase problems, so agents reconstruct answers from memory or unreviewed personal files. The data confirms her "seven out of ten" estimate almost exactly: 71.4% of tickets are `answerable_from_docs=True`. This determined the shape of the solution — semantic retrieval against ticket body text, not a documentation-authoring effort.

**Finding 3 — enterprise is currently the *worst*-served tier, not the one most at risk of regression.** Marcus and Ravi Menon (Customer) both frame enterprise service as the thing to protect from getting worse. The data shows enterprise already has the lowest FCR (37.3%), slowest resolution (483.7 min), and highest escalation rate (62.7%) of the three tiers today. This reframed the fairness requirement from "don't regress enterprise" to "enterprise needs the most improvement, and must be tracked, not assumed safe."

---

## 4. Requirements

The PRD (`docs/PRD_v1.md`, revised to `docs/PRD_v2.md`) defines twelve functional requirements and six non-functional requirements, each traced to a specific piece of discovery evidence. The structure: FR-01 (ingestion) normalizes all four channels into one representation; FR-02/FR-03 (triage) classify intent and urgency with a confidence score; **FR-04** routes every ticket into mandatory-escalation or discretionary using a deterministic, code-level lookup table (`MANDATORY_ESCALATION_INTENTS`) as the authoritative decision — amended in v2.0 to note this table alone is insufficient (§9); FR-05 retrieves against Chroma; FR-06/FR-07 generate and guardrail responses; FR-08 produces the triage handoff packet Daniel explicitly asked for ("if an escalation arrived saying here is the ticket, here is what I think it is about... I would be twice as fast"); FR-09/FR-10 cover decision logging and the administrative kill switch; FR-11 covers fairness segmentation; **FR-12** (new in v2.0) is the deterministic content-based safety net added after the VAL-0048 finding (§8, §9).

NFR-01 through NFR-06 cover latency, availability, accuracy/calibration, privacy, auditability, and fairness respectively — each with a measured result reported against its target in §7, not asserted.

---

## 5. Architecture and design

**Orchestration.** Each ticket runs through an 8-node LangGraph `StateGraph` (`src/pipeline/graph.py`): ingest (kill-switch check) → triage (classification) → mandatory-routing gate (now including the FR-12 keyword scan, §8) → [retrieval → threshold-routing → generation → guardrails] for discretionary tickets only → escalation handoff (triage packet) → finalize. Conditional edges, not nested if/else branches, decide each path, so every stage boundary — and its Decision Log write — is an explicit, inspectable node. This was chosen over a flat function pipeline specifically so the mandatory-escalation gate could sit as its own node, independently testable from the rest of the flow, which mattered directly once FR-12 needed to be inserted at exactly that point.

**Retrieval.** `src/retrieval/indexer.py` embeds all 29 KB articles with `sentence-transformers/all-MiniLM-L6-v2` into a local Chroma `PersistentClient`, one chunk per article — a deliberate choice, not a default: article lengths (860–1336 characters, mean 1094) are already smaller than a typical embedding chunk, and the Dataset Guide warns that splitting inside a resolution sequence produces passages that retrieve well but read as incomplete. The indexer is pre-warmed once at process startup (`run_evaluation`/`src/api/app.py`'s lifespan), not per-ticket — this alone cut observed P95 latency from 25.59s to near-zero in the first smoke test, since the embedding-model cold start had previously been absorbed by whichever ticket happened to run first.

**Model access.** `src/models/client.py`'s `LLMModelClient` calls the real build prompts (`src/prompts/*.yaml`) against Gemini 2.5 Flash via OpenRouter (`openrouter:google/gemini-2.5-flash`), chosen over the initially-tested free-tier `meta-llama/llama-3.1-8b-instruct` after the latter measured only 47.5% classification accuracy and 7.5% FCR on a live validation run — a concrete, measured reason to switch, not a default preference. The client supports Gemini (direct or via OpenRouter), OpenRouter generally, and Anthropic, with exponential backoff on 429/5xx and a hard fallback to a deterministic, offline `HeuristicModelClient` (`src/models/heuristic.py`) on exhausted retries or missing credentials — this is what keeps `--smoke-test` and CI runnable with zero API keys configured (§6).

**Guardrails — two layers, in a specific order.** A deterministic, no-network Python check (PII regex, financial-commitment phrase match, disclosure-prefix check, citation-validity check against the retrieved set) runs first; only if it passes does the slower, LLM-based `guardrail_check_v1` run as a second check for subtler issues (unsupported claims, unreleased-feature statements). This ordering is deliberate: it protects the NFR-01 latency budget by making the expensive model call the exception path, not something every response pays for.

**Governance.** `src/database/decision_log.py` persists a SQLite record for every classification, routing, generation, and validation decision, matching `Governance_Framework.md`'s schema exactly, plus an administrative kill switch (`is_kill_switch_enabled`/`set_kill_switch`) that routes every subsequent ticket to escalation without a deployment, itself logged as an auditable action.

**Why not other options.** A flat, imperative pipeline (the Stage 4 scaffolding's original design) was replaced by the LangGraph state machine specifically to make the FR-12 safety-net insertion point explicit and independently testable — this is covered in more detail in §6 and §9, since it was a mid-build change, not an initial design choice. A single-layer, LLM-only guardrail was considered and rejected early: `guardrail_check_v1.yaml`'s own documentation states an LLM-based PII check alone cannot be the sole control for a zero-tolerance governance condition.

---

## 6. Implementation

The build went through four real iterations on the triage classifier, each driven by a specific measured failure, not a hunch:

1. **`triage_v1`** (initial): no per-intent definitions, told the model to default to `unclear_request` whenever "not beyond doubt." Measured result: 52.5% accuracy, 13 of 22 intents at 0.0 recall, 42 of 80 validation tickets misclassified as `unclear_request` (`storage/results_validation_gemini.json` after the model switch, later `storage/results_validation_calibrated.json`).
2. **`triage_v2.0`**: added a one-line definition and a distinguishing-neighbor cue for every intent, grounded in real `development_tickets.json` phrasing, and narrowed `unclear_request` to "no recognizable topic at all." Measured result: accuracy jumped to 97.5%, FCR to 70.0% — but classification P95 latency rose to 8.06s against the 2s NFR-01 budget, because the added prose cost more tokens than the dropped `rationale` field saved.
3. **`triage_v2.1`**: re-expressed the same 22 definitions and cues as dense keyword/cue lists instead of prose, to cut input-token count. Measured result: classification P95 fell to 3.28s, then to 2.05s after the FR-12 fix below also removed some redundant LLM round-trips; accuracy settled at 93.8% (a small, accepted tradeoff against v2.0's 97.5%, in exchange for roughly 4x lower latency).
4. **FR-12 (the keyword safety net)**: added after a specific live finding, covered in full in §8.

The hardest implementation problem was not a model or prompt issue at all: it was realizing that `MANDATORY_ESCALATION_INTENTS` — the "authoritative" code-level safety check — is only as safe as the intent label it receives, and that no amount of classifier tuning alone closes that gap, because the gap is structural, not a quality shortfall. This is the one thing in this build I would restructure from day one if starting over (§9, §10).

A known, explicitly-stated gap: no automated test suite (`tests/`) exists yet, despite the Submission Guide requiring one. The evaluation harness itself (`src/evaluate.py`) has been exercised extensively as an integration test across every run cited in this report, but unit-level tests for individual components (`src/routing/policy.py`, `src/guardrails/deterministic.py`) are not yet written. Stated plainly here rather than left for a reviewer to discover.

---

## 7. Evaluation

**Method**: all figures below come from the 80-ticket validation set (`Capstone_Pack/05_Datasets/validation_tickets.json`), run unattended through `src/evaluate.py`, with the model, confidence threshold, and prompt version named for each. The hidden 120-ticket set has not been used at any point in this build, per `Evaluation_Framework.md`'s "go to the test set once" rule — it will be run exactly once, at submission.

| Metric | Baseline | Target | **v4 result (final, `storage/results_validation_v4.json`, 2026-11-16)** |
|---|---|---|---|
| First-contact resolution | 43.8% | 65% | **70.0%** |
| Escalation rate | 56.2% | ≤30% | **30.0%** |
| Classification accuracy | — | ≥85% | **93.8%** |
| Confidence calibration gap | — | ≤5 points | **5.3 points** (marginal fail) |
| Classification P95 latency | — | <2s | **2.05s** (marginal fail) |
| Generation P95 latency (auto-respond path) | — | <5s | **5.17s** (marginal fail) |
| Retrieval hit rate (correct doc in top-k) | — | — | **84.91%** |
| **Policy breaches** | n/a | 0, zero-tolerance | **0 / 80** |
| Hallucination rate / citation accuracy | — | ≤5% / ≥95% | 0% / 100% (**automated proxy only** — not the independently human-reviewed figure `Evaluation_Framework.md` requires; see below) |

**Interpretation, not just the numbers**: the three headline business/technical targets (FCR, escalation rate, accuracy) all pass. The three that marginally miss (calibration, both latency figures) are all within roughly 0.05–0.3 of their bar, and all three were deliberately not chased further, because doing so would have meant either refitting calibration circularly against the same run being reported (§9) or risking a regression of the exact accuracy/safety fixes just made (§6, §8). This is a judgment call, stated here rather than hidden behind a rounded number.

**What this report cannot claim**: `hallucination_rate` and `citation_accuracy` as defined by `Evaluation_Framework.md` require independent human review — 50+ responses, two assessors, an inter-rater agreement figure. This project has not conducted that review. The 0%/100% figures reported are an automated proxy (this system's own two guardrail layers grading themselves), explicitly labeled as such in every output file (`storage/results_validation_v4.json`'s `citation_hallucination_automated_proxy.caveat`), and should not be read as satisfying NFR-03's actual methodology.

**Fairness**: full segment tables in `docs/Fairness_Audit.md`. The standout finding is the `latin_america` region, at -47.4 points FCR against the overall mean — built from only 7 tickets, analyzed explicitly (per Governance Framework's requirement not to average a finding like this away) and not acted on architecturally, because a 7-ticket sample cannot distinguish a real regional effect from noise. Recommendation: production monitoring, re-examined against the hidden set's larger sample.

---

## 8. Governance and risk

**The centerpiece finding of this entire build**: on 2026-11-16, the 80-ticket validation gate (`triage_v2.0`, `storage/results_validation_v3.json`) produced exactly one policy breach — ticket `VAL-0048`, whose body read *"one of our engineers believes their credentials may have been exposed in breach at another service. What should we be doing right now?"* The true intent is `security_incident` — a mandatory-escalation category under FR-04's own policy. The classifier, at confidence 1.0, labeled it `api_key_issue` — a discretionary category. Because `MANDATORY_ESCALATION_INTENTS` is keyed on the classified intent, the authoritative lookup table never triggered. The ticket passed through retrieval, generation, and the deterministic/LLM guardrail layers — which correctly found no PII, no financial claim, and a properly-grounded citation, because none of those checks are designed to catch *this* failure mode — and was auto-responded. A zero-tolerance governance condition (`PRD §7`: "0% — a governance condition, not a target to approach") failed.

**Why this matters more than any metric in this report**: the authoritative safety mechanism this entire architecture leans on was only as trustworthy as the label it was given, and a classification error that crosses the mandatory/discretionary boundary defeats it silently — no exception, no error, no log entry flagging anything wrong, because every component did exactly what it was asked to do with the (wrong) information it had. This is a different, deeper problem than "the classifier isn't accurate enough."

**The fix (FR-12)**: a second, independent, content-based gate — `src/routing/policy.py`'s `contains_high_risk_keyword()`, checking the ticket's own subject/body text against nine literal terms (breach, compromised, unauthorized access, exposed credentials, hacked, vulnerability, cve, legal action, sue) — runs *before* the classified intent is ever evaluated, and forces escalation on any match, overriding intent and confidence entirely. This cannot be bypassed by a classification error, because it does not depend on classification at all. Re-running the identical 80-ticket set after this fix (`storage/results_validation_v4.json`) confirmed `policy_breaches_count: 0`. The same defense-in-depth principle already used for the PII/financial-claim guardrail at generation time is applied one stage earlier, at routing — the architecture's existing pattern, extended to cover a gap in itself.

**What this does not guarantee**: FR-12's nine-term list is deliberately small and literal, chosen for auditability, not learned or semantic. A ticket describing a genuine breach without using any of those nine substrings would not be caught by this specific gate (it would still need to clear classification normally, which remains imperfect). This is recorded as an open question (`docs/PRD_v2.md` §9), not claimed as solved.

**Other governance controls, status**: decision-log coverage reconciles exactly on every run (`distinct_tickets_logged_this_run == tickets_processed`); the administrative kill switch exists and is itself logged when toggled (not exercised in this report's cited runs); zero PII occurrences detected across all generated responses in every run cited here (automated scan only, not independently human-reviewed — same caveat as §7's citation-accuracy figure).

---

## 9. The requirements revision

Full detail in `docs/Stage_5_PRD_Revision_Log.md` and `docs/PRD_v2.md`. Summary of the three realities discovered during build that changed the requirements:

1. **The safety-net bypass vulnerability** (§8) — FR-04 amended, FR-12 added.
2. **The calibration trap** — raw stated confidence from the model was badly overconfident (90%+ stated confidence was only 57.1% actually correct on an early live run), which meant the 0.70 routing threshold was comparing against a number that didn't mean what it claimed to mean. An isotonic-regression recalibration (`src/models/calibration.py`, fit on development-set-only (confidence, correct) pairs, per `Evaluation_Framework.md`'s "go to the test set once" rule and PRD §8's own budgeted contingency) closed most of the gap — from +35.4 points to the current 5.3 points, marginally still over the 5-point bar, not claimed as fully resolved.
3. **Latency vs. accuracy/safety tradeoff** — pushing classification accuracy from 52.5% to 93.8%–97.5% via richer intent definitions cost roughly 4–6x the original latency; two compression passes brought that back to within ~2–17% of the NFR-01 targets, accepted as a deliberate tradeoff rather than chased to zero, because further compression risked reopening the exact `unclear_request`-overprediction and policy-breach problems those definitions fixed.

---

## 10. Conclusions

**What I would do next**: fit confidence calibration on a larger, independent development-set sample before the hidden-set run (the current fit used 179 usable pairs from 200 attempted); build the missing `tests/` suite (§6); broaden FR-12's keyword list against a deliberately adversarial phrasing test set that does not currently exist, to understand its real false-negative rate rather than assuming the current nine terms are sufficient.

**What remains uncertain**: whether the measured 93.8% accuracy, 5.3-point calibration gap, and marginal latency overshoot generalize to the hidden 120-ticket set — not yet tested, by design; whether the `standard`-tier and `latin_america` fairness gaps (`docs/Fairness_Audit.md`) are real, persistent effects or validation-set sampling noise, given the small segment sizes involved.

**The one thing I would change if starting this project again on Monday**: build the FR-12 content-based safety net at the same time as FR-04's classifier-keyed lookup table, as a first-class design assumption from day one — "the classifier can put a ticket in the wrong bucket, and the mandatory/discretionary boundary is exactly where that is most dangerous" — rather than discovering it empirically after a real breach during validation. The system passes its headline numeric targets. The thing that actually mattered more than any of them was finding, and closing, the one gap where a passing accuracy score could still have sent something unsafe to a real customer.
