# Fairness Audit — Stage 6

Per `_context_md/Governance_Framework.md` §3: "Your system will not treat all customers identically, and you need to know in which direction and by how much. Segment your test set and compare outcomes across segments."

**Run audited**: `storage/results_validation_v4.json` — 80-ticket validation set, `openrouter:google/gemini-2.5-flash`, `triage_v2.1` + FR-12 keyword safety net, 2026-11-16. Overall FCR 70.0%, overall classification accuracy 93.8%. This is the one run where the zero-tolerance policy breach (VAL-0048) is fixed, so it is the correct baseline to audit for fairness — auditing an earlier run with a known safety defect would conflate two different kinds of problem.

**Method**: "Resolution rate" is FCR-equivalent (`auto_respond` share) per segment. "Quality score" is classification accuracy per segment, restricted to tickets with a known `true_intent`. "Variation from best" is the gap, in percentage points, between a segment's figure and the best-performing segment on that same dimension — not against an external target, per NFR-06's own framing ("measured against the system's own outputs, not the historical baseline").

## Customer tier

| Segment | Tickets in segment | Resolution rate | Quality score | Variation from best | Explanation |
|---|---|---|---|---|---|
| Business | 30 | 90.0% | 96.67% | best on both | Highest resolution and quality in this run. |
| Enterprise | 8 | 75.0% | 87.5% | -15.0pt resolution / -9.17pt quality | Small sample (n=8); within the range plausible from sampling alone, but worth tracking — this is the tier CloudServe is most anxious about per discovery (Marcus, Interview 1). |
| Standard | 42 | 54.76% | 92.86% | **-35.24pt resolution** / -3.81pt quality | **Flagged** — largest segment (n=42, more than half the validation set), so this is not a thin-sample artifact. Classification quality is close to the best tier (92.86% vs. 96.67%), but far fewer standard-tier tickets end up auto-resolved. This looks like a routing/retrieval-confidence effect, not a classification-quality effect — standard-tier tickets are understood about as well as business-tier ones but resolved automatically much less often. |

## Customer region

| Segment | Tickets in segment | Resolution rate | Quality score | Variation from best | Explanation |
|---|---|---|---|---|---|
| Europe | 25 | 76.0% | 92.0% | best resolution | Highest resolution rate. |
| North America | 27 | 74.07% | 96.3% | -1.93pt resolution / best quality | Essentially at parity with Europe on resolution; best quality score. |
| Asia Pacific | 21 | 71.43% | 95.24% | -4.57pt resolution / -1.06pt quality | Within a reasonable band of the top performers. |
| **Latin America** | **7** | **28.57%** | **85.71%** | **-47.43pt resolution / -10.59pt quality** | **Flagged — see dedicated analysis below.** |

## Language fluency

| Segment | Tickets in segment | Resolution rate | Quality score | Variation from best | Explanation |
|---|---|---|---|---|---|
| Non-fluent | 19 | 68.42% | 89.47% | best resolution / -5.61pt quality | Resolution rate is actually marginally *higher* than fluent tickets this run. |
| Fluent | 61 | 70.49% | 95.08% (best) | +2.07pt resolution / best quality | Slightly higher resolution and the best quality score. |

**Note on the expected direction of this gap**: `Governance_Framework.md` names non-fluent English as the variation "you are most likely to find," because retrieval depends on phrasing matching the documentation. That did not reproduce here — non-fluent tickets resolved at a comparable (even slightly higher) rate this run, though with a real quality-score gap (89.47% vs. 95.08%) that didn't translate into a resolution-rate gap in this sample. Worth tracking, not acted on from n=19 alone.

## Ticket length (short vs. long/complex)

Median body length in this validation set is 131 characters; split at the median.

| Segment | Tickets in segment | Resolution rate | Quality score | Variation from best | Explanation |
|---|---|---|---|---|---|
| Long/complex (≥131 chars) | 40 | 75.0% | not separately computed | best | Longer tickets resolve slightly more often. |
| Short (<131 chars) | 40 | 65.0% | not separately computed | -10.0pt | A real but modest gap — shorter tickets give the classifier and retrieval less to work with, consistent with the general shape of the problem (short, terse tickets are the ones `unclear_request` overprediction hit hardest before `triage_v2`), though far smaller than the `latin_america` or `standard`-tier gaps. |

---

## Dedicated analysis: `latin_america` region

This is the largest variation found in this audit (-47.43 points on resolution rate, -10.59 points on quality) and the one `Governance_Framework.md`'s own standard asks be analyzed explicitly rather than averaged into the overall figure.

**The honest statistical read**: n=7. At this sample size, a single additional escalated ticket moves the segment's resolution rate by over 14 percentage points. The gap is mathematically real *in this sample* — 2 of 7 `latin_america` tickets resolved automatically, versus roughly 70% overall — but a 7-ticket sample cannot distinguish a genuine, structural regional effect from ordinary sampling variance. The quality-score gap (85.71% vs. the ~93.8% overall) is built from the same 7 tickets and carries the same caveat.

**What would make this a structural finding rather than noise**: if the same gap reproduced on the hidden 120-ticket set (which will contain a larger `latin_america` sample), or if a specific causal mechanism were visible in the 7 tickets themselves (e.g., all 7 happening to be unusually short, non-fluent, or concentrated in intents the classifier handles worse) rather than looking like an unremarkable mix.

**Recommendation**: do not make an architectural change on this sample alone. The resolution-rate gap could reflect (a) a genuine regional effect worth building toward, (b) the ticket-length or fluency effects already identified above correlating with this particular 7-ticket sample, or (c) ordinary small-sample noise — and nothing in this dataset distinguishes those three explanations. The appropriate response is **production monitoring**: track `latin_america` resolution rate as its own standing metric (per `Setup_Guide.md` §06's dashboard guidance — segment-level outcome tracking, not just the aggregate), re-examine with the hidden-set run's larger sample, and only consider an architectural response (e.g., region-specific retrieval tuning, or a lower discretionary-routing threshold for this segment specifically) if the gap persists at a sample size large enough to trust.

## What this audit does not cover

- Intersectional segments (e.g., non-fluent + `latin_america` together) — `docs/PRD_v1.md` §9 already flags this as an open question or NFR-06 methodology; not resolved here, same reason (thin samples get thinner still when intersected).
- A human-reviewed quality rubric (`Evaluation_Framework.md`'s customer-satisfaction proxy) — not built; "quality score" here is classification accuracy only, a narrower and fully automated substitute.
- The hidden 120-ticket set — by design, per `Evaluation_Framework.md`'s "go to the test set once" rule.

**The caution this report's own standard asks for**: these figures should be treated with caution because they come from an 80-ticket validation set with segment sizes ranging from 7 to 61 tickets, several of the flagged gaps (`latin_america` especially) are built from samples too small to separate a real effect from noise, and the "quality score" proxy used throughout is classification accuracy only — a narrower measure than the citation-accuracy/hallucination-rate figures `Evaluation_Framework.md` actually asks a full quality audit to use, which this report has not independently human-reviewed (see `storage/results_validation_v4.json`'s own `citation_hallucination_automated_proxy.caveat`).
