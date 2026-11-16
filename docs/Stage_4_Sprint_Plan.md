# Stage 4 — Sprint Plan

Written against `_context_md/Stage_4_Sprint_Plan.md` (workbook structure) and
`_context_md/02_Build_Specification.md` (the twelve acceptance criteria and
daily checkpoints this plan is sequenced against). This is an individual
project (`Capstone_Pack/01_Read_First/README.md`), so "owner" below is
always Prince Prakash; the column is kept to match the template and to make
explicit that nothing is silently assumed to belong to someone else.

---

## Section 1 — Capacity

| Week | Hours realistically available | Other commitments that week | Days I cannot work |
|---|---|---|---|
| Week one (discovery + requirements) | 22 | Discovery interviews/data analysis and PRD writing consumed most of this week already (Stages 1–3 complete as of 2026-11-11) | None recorded |
| Week two (build) | 28 | Reserve one day for the Stage 1/2/3 audit fixes just completed before build time starts; the rest is the build window this plan covers | None recorded |
| Week three (governance, report, video, submission) | 20 | Video recording needs a dry run and a second take (per README's "underestimating the video" warning); effort log must be back-filled honestly if any day was skipped | None recorded |

This plan treats 28 hours as the honest week-two budget. The Build
Specification's own guidance ("if you are behind at day three, cut scope
rather than cutting the end-to-end run") is taken literally in Section 5
below.

---

## Section 2 — The backlog

Estimates are in hours. Priority follows the Stage 2 PRD scale (Must/Should/
Could) mapped onto urgency of sequencing. Every item has exactly one owner
(Prince Prakash) and a Definition of Done written before work starts, per
the Stage 4 workbook's rule that a DoD written afterwards just describes
whatever happened.

| ID | Item | Est. hours | Priority | Depends on | Definition of Done |
|---|---|---|---|---|---|
| B-01 | Environment and dependency setup (venv, `requirements.txt`, `.env` from `.env.example`, `.gitignore` covering `.env` and `storage/`) | 2 | Must | — | `pip install -r requirements.txt` succeeds in a fresh venv; a test call to the configured model provider returns a response; `.env` is confirmed absent from `git status` |
| B-02 | Data loading and normalisation (`src/ingest.py`) — implements FR-01 | 3 | Must | B-01 | A ticket from each of the four channels (email, chat, docs_comment, forum) produces an identical-shape normalized object; an empty `subject` (chat) and an empty `body` do not raise |
| B-03 | Document chunking and embedding strategy decision + implementation (`src/retrieval/indexer.py`) — implements part of FR-05 | 3 | Must | B-02 | **Done in this session** — see Section 3 below and the file itself. At least two chunk-size configurations tried and compared, the choice recorded with a reason, not left at the Setup Guide's default without comment |
| B-04 | Vector store and retrieval query interface (`retrieve(query, top_k)`) — implements FR-05 | 2 | Must | B-03 | **Done in this session.** A known question returns the correct `doc_id` in the top-k results; querying with no relevant passage returns an empty/low-score result rather than something irrelevant |
| B-05 | Evaluation harness skeleton (`src/evaluate.py`), accepting `--input`/`--output`, metric stubs from `Evaluation_Framework.md` — implements NFR-02, NFR-05 | 4 | Must | B-02 | **Done in this session (skeleton + smoke test).** Harness runs against any file path passed via CLI args, never a hardcoded filename; `--smoke-test` processes 5 tickets end-to-end without crashing |
| B-06 | Intent and urgency classifier wiring (calls `triage_v1.yaml` against the configured model) — implements FR-02, FR-03 | 4 | Must | B-02 | Every ticket in a 20-ticket sample receives both an intent and a confidence; malformed LLM JSON is caught and does not crash the batch (ties to the Unattended Gate Rule, CLAUDE.md §2) |
| B-07 | Routing logic and `MANDATORY_ESCALATION_INTENTS` lookup table (`src/routing/policy.py`) — implements FR-04 | 2 | Must | B-06 | **Done in this session.** All 87 dataset-confirmed mandatory tickets plus all `data_residency` tickets route to escalation regardless of confidence, verified by the regression test named in `docs/Stage_3_Prompt_Library.md`'s audit note; same ticket submitted twice yields the same decision (A5) |
| B-08 | Answer generation with citations (wires `rag_response_v1.yaml` to real `retrieve()` output) — implements FR-06 | 4 | Must | B-04 | A ticket with a genuinely retrievable answer produces a response citing a real `doc_id` that resolves to actual corpus text; a ticket with no good match returns `can_answer: false` rather than a plausible guess |
| B-09 | Guardrails and validation — deterministic PII/`must_not_claim` checks first, `guardrail_check_v1.yaml` second — implements FR-07 | 4 | Must | B-08 | A ticket engineered to trigger a guardrail (e.g. a draft containing a fabricated refund commitment) is blocked and escalated, not sent (A7); guardrail results are recorded whether or not they blocked |
| B-10 | Decision logging (`src/database/decision_log.py`) — implements FR-09, NFR-05 | 3 | Must | B-07 | **Done in this session.** Every ticket processed in the smoke test produces exactly one decision log row per pipeline stage it passed through; row count reconciles exactly against tickets processed (A8) |
| B-11 | Kill switch (toggle + audit log) — implements FR-10 | 1 | Must | B-10 | **Done in this session.** Toggling the switch causes the next processed ticket to escalate without any code change; the toggle action itself is a logged, auditable row |
| B-12 | Triage Escalation Packet wiring (`triage_packet_v1.yaml` on every escalation path) — implements FR-08 | 3 | Must | B-07, B-08 | Every escalated ticket in a 20-ticket sample carries all four packet fields populated; mandatory-policy escalations correctly show "no documents retrieved" rather than an unexplained empty field |
| B-13 | First full unattended run over the validation set (**THE GATE**, A9) | 3 | Must | B-05, B-09, B-12 | `python src/evaluate.py --input Capstone_Pack/05_Datasets/validation_tickets.json --output <path>` completes for all 80 tickets with zero manual intervention; a metrics report file is produced automatically |
| B-14 | Monitoring (Prometheus counters/histograms + a Grafana dashboard) | 3 | Should | B-10 | Metrics endpoint exposes ticket throughput, latency histogram, and guardrail-block counter; a dashboard panel exists for each (per `Setup_Guide.md` §06) |
| B-15 | Continuous integration pipeline (`.github/workflows/ci.yml`) | 1 | Should | B-01 | A push triggers `pytest tests/ -v`; the workflow does not reference any committed secret |
| B-16 | Fairness audit (FR-11, NFR-06 — segment reporting by tier/region/fluency) | 3 | Should | B-13 | A single evaluation run produces the three segment tables named in `Governance_Framework.md`; any segment with >5-point variation is flagged explicitly, not averaged away |
| B-17 | PRD revision (Stage 5, compulsory) + revision log | 2 | Must | B-13 | `docs/PRD_v1.md` has a dated revision recording what Stage 2 got wrong against what Stage 4/5 build actually found |
| B-18 | Report, video and submission package | 6 | Must | all above | Video recorded with a live demonstration per channel; report follows the Submission Guide's prescribed section order; effort log has entries from every second day, not reconstructed at the end |

**Items marked "Done in this session"** (B-03, B-04, B-05 skeleton, B-07, B-10,
B-11) were built as foundational scaffolding alongside this sprint plan, per
the explicit instruction accompanying it — see `src/retrieval/indexer.py`,
`src/database/decision_log.py`, and `src/evaluate.py`. They remain in the
backlog because the DoD above is the bar for calling them actually finished
(e.g., B-03/B-04's "try at least two chunk-size configurations and compare"
is not yet done — only one configuration is implemented in this scaffolding
pass), and the hours column reflects the full item, not just the scaffold.

---

## Section 3 — The week two plan in detail

Mapped directly to the daily checkpoints in `_context_md/02_Build_Specification.md`
§05 ("Build checkpoints").

| Day | What will be finished by the end of the day (per Build Spec checkpoint) | Hours | Risk to this |
|---|---|---|---|
| Monday | **Checkpoint: Ingest.** B-01, B-02 complete. A ticket from each of the four channels comes through normalised; printed and inspected per the Build Spec's own verification method. | 5 | Low — this is the most mechanical item; main risk is an unexpected encoding in a real ticket body |
| Tuesday | **Checkpoint: Retrieval.** B-03, B-04 complete (chunking decision finalized with the second configuration comparison, not just the scaffold). Corpus embedded; a known question returns the correct passage, confirmed by querying with Sofia's named example wording mismatch ("my deployment keeps dying" vs. a differently-titled article). | 6 | Medium — chunk-size tuning can eat more time than budgeted; if it does, the scaffold's default chunking ships and the comparison is logged as incomplete rather than silently dropped |
| Wednesday | **Checkpoint: Classification and routing.** B-06, B-07 complete. Twenty tickets run through triage; the decision log (B-10, built ahead of schedule) is read and every decision has a reason; `data_residency`/mandatory-set regression test passes. | 6 | Medium — this is where the model provider's free-tier rate limits are first exercised at volume; backoff/retry logic (tied to A11) needs to exist by here, not be bolted on later |
| Thursday | **Checkpoint: Generation and guardrails.** B-08, B-09, B-12 complete. A ticket engineered to trigger a guardrail is submitted and confirmed blocked (A7); escalations carry a populated triage packet. | 6 | High — this is the day with the most new moving parts (generation + two-layer guardrail + packet); if behind, B-12 (packet polish beyond the scaffold's working version) is the first thing deferred, not B-09 (guardrails are non-negotiable per A7) |
| Friday | **Checkpoint: The full chain, unattended, over the full evaluation set.** B-13 (THE GATE) run against `validation_tickets.json`. Left alone, come back to a metrics report, per the Build Spec's own description of this checkpoint. | 5 | High by design — this is deliberately the day the Build Spec says the gate must clear; if B-13 does not pass cleanly, the rest of Friday is spent diagnosing the unattended run, not adding features |

**Total week two: 28 hours**, matching Section 1's capacity figure exactly.
B-14 and B-15 (monitoring, CI) are deliberately not scheduled into week two's
five days — they are Should-priority and are carried into week three's start
rather than competing with the gate for hours.

---

## Section 4 — The week three plan in detail

| Day | What will be finished by the end of the day | Hours | Risk to this |
|---|---|---|---|
| Monday | B-14 (monitoring/Grafana) and B-15 (CI pipeline) complete. Fairness audit (B-16) started. | 5 | Low — both are additive, well-scoped items with worked examples in the Setup Guide |
| Tuesday | B-16 (fairness audit) complete, including the segment tables and an honest explanation of any >5-point variation found. B-17 (PRD revision) started: differences between PRD v1.0 and what the build actually required are listed. | 5 | Medium — if the fairness audit surfaces a real gap (e.g. non-fluent retrieval quality, flagged as the most likely finding in `Governance_Framework.md`), this is the day to decide whether it can be mitigated in code or must be reported as a known limitation |
| Wednesday | B-17 (PRD revision log) complete and committed. First draft of the video recorded (per the README's explicit instruction to record a first version by the middle of the final week, not later). | 4 | Medium — the video draft reliably overruns on the first attempt; budgeting a full take here rather than a rehearsal protects Friday |
| Thursday | Report written to the Submission Guide's prescribed section order. Second video take recorded if the first one needs it. | 4 | Medium — report writing is where "reporting accuracy and stopping" (README's named failure mode) is easiest to fall into; each metric must be carried through to what it means for the business, not just listed |
| Friday | **Final presentation and submission.** Full submission checklist worked through the day before, not on the day itself, per the README's explicit warning. | 2 | Low if the rest of the week held; this day is intentionally light to absorb any slippage from earlier in the week |

**Total week three: 20 hours**, matching Section 1.

---

## Section 5 — What gets dropped if time runs out

Decided now, in week one, rather than in a panic on the final Wednesday —
per the Stage 4 workbook's own instruction. Order reflects what protects the
largest share of marks (Implementation 35%, Evaluation 20% — Build Spec §07
weighting table) while keeping the gate (A9) intact at all costs.

| Item | Cut order | Consequence of cutting it | What I will say about it in the report |
|---|---|---|---|
| B-14 Monitoring/Grafana dashboard | 1st to go | Prometheus counters may exist without a dashboard in front of them; metrics are still computed by the evaluation harness itself, which is what A10 actually requires | "Monitoring dashboarding was descoped under time pressure; the metrics it would have visualized are still produced by the evaluation harness and recorded in the decision log." |
| B-16 Fairness audit depth (reduced to the three dimensions named in NFR-06 only, dropping any intersectional analysis) | 2nd to go | Loses intersectional findings (e.g. non-fluent + enterprise) flagged as an open question in PRD_v1 §9; single-dimension segmentation still satisfies the stated NFR-06 acceptance bar | "Fairness analysis was limited to single-dimension segments given time constraints; intersectional analysis remains an open question (PRD_v1 §9) for a future iteration." |
| B-03/B-04 second chunk-size comparison | 3rd to go | Ships with the scaffold's default chunking (800/120, per `Setup_Guide.md`) uncompared against an alternative; retrieval quality may be suboptimal but the system still functions | "Only one chunking configuration was evaluated under time pressure; the Setup Guide's default was used and citation accuracy was measured against it, but a comparison against a second configuration was not completed." |
| B-15 CI pipeline | 4th to go | Tests still run locally with the documented command (A12 does not require CI specifically, only that tests run with one documented command); CI improves confidence but is not an acceptance criterion itself | "Continuous integration was not completed; the test suite runs locally via the documented command and this is what A12 was verified against." |
| **Never cut:** B-13, the full unattended gate run | — | The Build Spec is explicit that a system handling two intent categories properly and running the full set unattended passes the gate, while a system handling eight categories beautifully but never run in sequence does not | N/A — this is the one item the plan protects above all others |

---

## Section 6 — The daily check-in record

Ten minutes at the start of each day, the same three questions, per the
Stage 4 workbook. This section is intentionally left as a template with no
fabricated entries — build has not started yet as of this plan's date
(2026-11-11), so there is nothing truthful to log. Entries are added daily
once week two begins, not reconstructed from memory at the end (the README's
named failure mode for the effort log applies equally here).

| Date | Finished since yesterday | Doing today | Blocked by |
|---|---|---|---|
| *(to be filled in daily, starting day one of week two build)* | | | |
