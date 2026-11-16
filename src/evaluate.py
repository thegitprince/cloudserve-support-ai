"""CLI evaluation harness — implements NFR-02 (unattended, crash-resistant)
and NFR-05 (accepts --input/--output, never a hardcoded filename), per
CLAUDE.md's Unattended Gate Rule and Build Spec criteria A9/A10.

    python src/evaluate.py --input <path> --output <path> [--smoke-test]

NEVER hardcodes development_tickets.json or validation_tickets.json — the
input file is always the one passed on the command line, so this harness can
be pointed at the hidden 120-ticket set after submission without any code
change (Dataset_Guide.md, "Your harness must accept an input path").

Model access (Stage 5): every ticket is processed by
src.models.client.LLMModelClient, which calls the real build prompts in
src/prompts/*.yaml against whichever provider is configured in .env
(Setup_Guide.md §03). If no provider is configured, or a provider call fails
after its retries, LLMModelClient falls back internally to the deterministic
HeuristicModelClient (src/models/heuristic.py) rather than raising — this is
the behaviour Build Specification A11 requires when "the model provider is
unavailable... that is not counted against you", and it is what keeps
--smoke-test meaningfully runnable even with no API key configured.

Orchestration: each ticket runs through the 8-node LangGraph pipeline in
src/pipeline/graph.py (ingest/kill-switch, triage, routing, retrieval,
generation, guardrails, escalation handoff, finalize) rather than inline
if/else branches.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database.decision_log import Decision, DecisionLog
from src.models.client import LLMModelClient
from src.pipeline.graph import build_graph, run_ticket
from src.retrieval.indexer import KnowledgeBaseIndexer, load_documentation

# Lowered from the Setup_Guide.md/.env.example default of 0.80, based on an
# empirical check against storage/results_validation.json (80-ticket live
# gate, openrouter:meta-llama/llama-3.1-8b-instruct): 10 tickets retrieved the
# CORRECT expected_doc_id but still escalated purely because relevance_score
# fell between 0.72-0.80 (e.g. VAL-0006: 0.741, VAL-0044/VAL-0053: 0.784,
# VAL-0077: 0.775) — a near-miss band, not a quality-of-match problem. This is
# a tuning decision recorded here, not an invented number: it is still
# overridable per run via --confidence-threshold.
DEFAULT_CONFIDENCE_THRESHOLD = 0.70
DEFAULT_TOP_K = 3
SMOKE_TEST_SIZE = 10

# Shared reference data (Dataset_Guide.md): "200 reference answers written by
# senior agents... a comparison set for response quality." Fixed, not a
# per-run ticket file, so hardcoding this path does not violate CLAUDE.md's
# Unattended Gate Rule (which forbids hardcoding development_tickets.json /
# validation_tickets.json specifically) — same category as indexer.py's
# DEFAULT_DOCS_PATH. NOTE: this file covers only 200 of the 500 dev-set
# tickets and NONE of the 80 validation-set tickets by design (Dataset_Guide:
# it is a dev-set answer-quality reference, not a validation artifact), so
# must_not_claim checking only ever fires on a dev-set run against a covered
# ticket_id — on validation_tickets.json this join legitimately finds nothing
# for every ticket, which is correct, not a bug.
DEFAULT_GROUND_TRUTH_PATH = Path("Capstone_Pack/05_Datasets/ground_truth_responses.json")


def load_ground_truth(path: str | Path = DEFAULT_GROUND_TRUTH_PATH) -> dict[str, dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return {}
    entries = json.loads(p.read_text(encoding="utf-8"))
    return {e["ticket_id"]: e for e in entries}


def normalize_ticket(ticket: dict[str, Any], ground_truth_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Minimal FR-01 ingestion normalization: guarantees every field this
    pipeline depends on is present, even for a malformed or sparse input
    record, rather than raising a KeyError mid-batch (Build Spec, Ingest:
    "Handles missing fields, unusual characters and empty bodies without
    failing"). `ground_truth` is joined from the separate
    ground_truth_responses.json file by ticket_id — no input ticket record
    carries that key itself."""
    return {
        "ticket_id": ticket.get("ticket_id", "UNKNOWN"),
        "channel": ticket.get("channel", "email"),
        "subject": ticket.get("subject") or "",
        "body": ticket.get("body") or "",
        "customer_tier": ticket.get("customer_tier", "standard"),
        "customer_region": ticket.get("customer_region", "north_america"),
        "language_fluency": ticket.get("language_fluency", "fluent"),
        "labels": ticket.get("labels", {}),
        "history": ticket.get("history", {}),
        "ground_truth": ground_truth_map.get(ticket.get("ticket_id", ""), {}),
    }


def _result(ticket: dict[str, Any], state: dict[str, Any], elapsed: float) -> dict[str, Any]:
    labels = ticket["labels"]
    classification = state.get("classification") or {}
    generation = state.get("generation") or {}
    guardrail_result = state.get("guardrail_result") or {}
    return {
        "ticket_id": ticket["ticket_id"],
        "channel": ticket["channel"],
        "customer_tier": ticket["customer_tier"],
        "customer_region": ticket["customer_region"],
        "language_fluency": ticket["language_fluency"],
        "predicted_intent": classification.get("intent"),
        "true_intent": labels.get("intent"),
        "confidence": classification.get("confidence"),
        "confidence_raw": classification.get("confidence_raw", classification.get("confidence")),
        "action_taken": state.get("action"),
        "reason": state.get("reason"),
        "must_not_auto_respond": labels.get("must_not_auto_respond"),
        "expected_doc_ids": labels.get("expected_doc_ids", []),
        "retrieved_doc_ids": [d["doc_id"] for d in state.get("retrieved_docs", [])],
        "cited_doc_ids": generation.get("cited_doc_ids", []),
        "draft_produced": bool(generation.get("can_answer", False)),
        "guardrail_verdict": guardrail_result.get("verdict"),
        "guardrail_violation_categories": [v.get("category") for v in guardrail_result.get("violations", [])],
        "guardrail_unsupported_sentences_count": len(guardrail_result.get("unsupported_sentences", [])),
        "latency_seconds": round(elapsed, 4),
        "classification_latency_seconds": (
            round(state["classification_latency_seconds"], 4)
            if state.get("classification_latency_seconds") is not None else None
        ),
        "generation_latency_seconds": (
            round(state["generation_latency_seconds"], 4)
            if state.get("generation_latency_seconds") is not None else None
        ),
    }


def process_ticket(
    ticket: dict[str, Any],
    pipeline_app: Any,
    ground_truth_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Runs one ticket through the LangGraph pipeline (src/pipeline/graph.py)
    and shapes the result for the evaluation report. Any exception here is
    caught by the caller so a single bad ticket never crashes the batch
    (A11)."""
    start = time.monotonic()
    ticket = normalize_ticket(ticket, ground_truth_map)
    final_state = run_ticket(pipeline_app, ticket)
    elapsed = time.monotonic() - start
    return _result(ticket, final_state, elapsed)


def compute_metrics(
    results: list[dict[str, Any]],
    decision_log: DecisionLog,
    errors: int,
    classifier_name: str,
    run_ticket_ids: list[str],
) -> dict[str, Any]:
    total = len(results) + errors
    auto = sum(1 for r in results if r["action_taken"] == "auto_respond")
    escalated = sum(1 for r in results if r["action_taken"] == "escalate")
    blocked = sum(1 for r in results if r.get("guardrail_verdict") == "fail")

    latencies = sorted(r["latency_seconds"] for r in results)

    def _pct(vals: list[float], p: float) -> float | None:
        if not vals:
            return None
        idx = min(len(vals) - 1, max(0, int(round(p * (len(vals) - 1)))))
        return vals[idx]

    labeled = [r for r in results if r.get("true_intent")]
    per_class: dict[str, dict[str, int]] = {}
    for r in labeled:
        true_i, pred_i = r["true_intent"], r["predicted_intent"]
        per_class.setdefault(true_i, {"tp": 0, "fp": 0, "fn": 0})
        per_class.setdefault(pred_i, {"tp": 0, "fp": 0, "fn": 0})
        if true_i == pred_i:
            per_class[true_i]["tp"] += 1
        else:
            per_class[true_i]["fn"] += 1
            per_class[pred_i]["fp"] += 1

    classification_report = {}
    for cls, counts in sorted(per_class.items()):
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        classification_report[cls] = {
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
            "support": tp + fn,
        }
    overall_accuracy = (
        sum(1 for r in labeled if r["true_intent"] == r["predicted_intent"]) / len(labeled)
        if labeled else None
    )

    retrieval_evaluable = [r for r in results if r.get("expected_doc_ids")]
    retrieval_hits = sum(
        1 for r in retrieval_evaluable
        if set(r["expected_doc_ids"]) & set(r["retrieved_doc_ids"])
    )
    retrieval_hit_rate = retrieval_hits / len(retrieval_evaluable) if retrieval_evaluable else None

    # --- NFR-01: two SEPARATE latency budgets, not one blended figure.
    # Classification (FR-02/FR-03) must hit P95 < 2s; end-to-end response
    # generation (retrieval+draft+guardrail, FR-05-FR-07) must hit P95 < 5s,
    # and that second budget is specifically "for the auto-respond path"
    # (PRD §5) — escalated tickets that still ran generation (abstained or
    # got guardrail-blocked) are excluded from that specific P95, though
    # their latency is still recorded for diagnostics below.
    classification_latencies = sorted(
        r["classification_latency_seconds"] for r in results if r.get("classification_latency_seconds") is not None
    )
    generation_latencies_all = sorted(
        r["generation_latency_seconds"] for r in results if r.get("generation_latency_seconds") is not None
    )
    generation_latencies_auto_respond = sorted(
        r["generation_latency_seconds"] for r in results
        if r.get("generation_latency_seconds") is not None and r["action_taken"] == "auto_respond"
    )
    classification_p95 = _pct(classification_latencies, 0.95)
    generation_auto_p95 = _pct(generation_latencies_auto_respond, 0.95)

    # --- NFR-03 calibration: exact reference implementation from
    # _context_md/Evaluation_Framework.md ("def calibration_table"). 5
    # equal-width confidence bands; stated = mean reported confidence in the
    # band, observed = actual accuracy in the band. The governance condition
    # is "stated confidence within 5 points of observed accuracy" per band.
    calibration_predictions = [
        (r["confidence"], r["true_intent"] == r["predicted_intent"])
        for r in labeled if r.get("confidence") is not None
    ]
    calibration_rows = []
    bands = 5
    for i in range(bands):
        low, high = i / bands, (i + 1) / bands
        # The reference formula's half-open bands (low <= x < high) silently
        # drop confidence==1.0 from every band, since the top band's own
        # upper bound (1.0) is exclusive. Made the top band inclusive on
        # both ends instead — found when a well-calibrated run put 75/80
        # tickets at confidence exactly 1.0 and the table showed only 5.
        group = (
            [p for p in calibration_predictions if low <= p[0] <= high]
            if i == bands - 1
            else [p for p in calibration_predictions if low <= p[0] < high]
        )
        if not group:
            continue
        stated = sum(c for c, _ in group) / len(group)
        observed = sum(1 for _, correct in group if correct) / len(group)
        calibration_rows.append({
            "band_low": round(low, 2), "band_high": round(high, 2), "count": len(group),
            "stated_confidence": round(stated, 3), "observed_accuracy": round(observed, 3),
            "gap_points": round(100 * (stated - observed), 1),
        })
    calibration_meets_nfr03 = all(abs(row["gap_points"]) <= 5.0 for row in calibration_rows) if calibration_rows else None

    # --- Automated citation/hallucination PROXY. The Evaluation Framework
    # requires an independently human-reviewed hallucination rate (50+
    # responses, two assessors) and a per-citation human check against the
    # sentence it supports — this harness cannot fabricate that human review,
    # so this is explicitly a lower-confidence automated signal using this
    # system's own two guardrail layers, not a substitute for NFR-03's actual
    # required methodology.
    drafts = [r for r in results if r["draft_produced"]]
    drafts_blocked_any_reason = [r for r in drafts if r["guardrail_verdict"] == "fail"]
    drafts_blocked_grounding = [
        r for r in drafts
        if "unsupported_citation" in (r.get("guardrail_violation_categories") or [])
        or (r.get("guardrail_unsupported_sentences_count") or 0) > 0
    ]
    hallucination_proxy_pct = round(100 * len(drafts_blocked_grounding) / len(drafts), 2) if drafts else None
    citation_accuracy_proxy_pct = round(100 - hallucination_proxy_pct, 2) if hallucination_proxy_pct is not None else None

    # --- Zero-tolerance policy-breach check (PRD §7: "0% — a governance
    # condition, not a target to approach"). A ticket whose own label says
    # must_not_auto_respond=True that nonetheless ended in auto_respond is
    # the single worst outcome this system can produce.
    policy_breach_ticket_ids = [
        r["ticket_id"] for r in results if r["action_taken"] == "auto_respond" and r.get("must_not_auto_respond") is True
    ]

    # --- FR-11/NFR-06 fairness segmentation. "Quality" here is FCR-vs-
    # escalation outcome (the same business metric computed overall, split
    # by segment) — PRD NFR-06's 5-point band, flagged explicitly rather than
    # averaged away, per FR-11's acceptance criteria.
    overall_fcr = 100 * auto / total if total else None
    fairness: dict[str, Any] = {}
    flagged_segments: list[dict[str, Any]] = []
    for dimension in ("customer_tier", "customer_region", "language_fluency"):
        segments: dict[str, list[dict[str, Any]]] = {}
        for r in results:
            segments.setdefault(r.get(dimension) or "unknown", []).append(r)
        dimension_rows = {}
        for segment_value, segment_results in sorted(segments.items()):
            n = len(segment_results)
            seg_auto = sum(1 for r in segment_results if r["action_taken"] == "auto_respond")
            seg_escalated = sum(1 for r in segment_results if r["action_taken"] == "escalate")
            seg_fcr = round(100 * seg_auto / n, 2) if n else None
            dimension_rows[segment_value] = {
                "tickets": n,
                "first_contact_resolution_pct": seg_fcr,
                "escalation_rate_pct": round(100 * seg_escalated / n, 2) if n else None,
            }
            if overall_fcr is not None and seg_fcr is not None and abs(seg_fcr - overall_fcr) > 5.0:
                flagged = {
                    "dimension": dimension, "segment": segment_value, "tickets": n,
                    "segment_fcr_pct": seg_fcr, "overall_fcr_pct": round(overall_fcr, 2),
                    "deviation_points": round(seg_fcr - overall_fcr, 2),
                }
                flagged_segments.append(flagged)
        fairness[dimension] = dimension_rows

    return {
        "run_metadata": {
            "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
            "classifier": classifier_name,
            "tickets_in_input": total,
        },
        "volume": {
            "tickets_processed": len(results),
            "tickets_errored": errors,
            "answered_automatically": auto,
            "escalated": escalated,
            "blocked_by_guardrail": blocked,
        },
        "business": {
            "first_contact_resolution_pct": round(100 * auto / total, 2) if total else None,
            "escalation_rate_pct": round(100 * escalated / total, 2) if total else None,
        },
        "technical": {
            "classification_accuracy_overall": round(overall_accuracy, 3) if overall_accuracy is not None else None,
            "classification_report_per_intent": classification_report,
            "retrieval_hit_rate_pct": round(100 * retrieval_hit_rate, 2) if retrieval_hit_rate is not None else None,
            "retrieval_evaluable_tickets": len(retrieval_evaluable),
            "latency_seconds_overall_per_ticket": {
                "mean": round(statistics.mean(latencies), 4) if latencies else None,
                "median": round(statistics.median(latencies), 4) if latencies else None,
                "p95": round(_pct(latencies, 0.95), 4) if latencies else None,
                "note": "whole-ticket wall-clock time, every action type; NOT the NFR-01 figure, see nfr01_latency below",
            },
            "nfr01_latency": {
                "classification_p95_seconds": round(classification_p95, 4) if classification_p95 is not None else None,
                "classification_target_seconds": 2.0,
                "classification_meets_target": (classification_p95 < 2.0) if classification_p95 is not None else None,
                "classification_sample_size": len(classification_latencies),
                "generation_auto_respond_p95_seconds": round(generation_auto_p95, 4) if generation_auto_p95 is not None else None,
                "generation_target_seconds": 5.0,
                "generation_meets_target": (generation_auto_p95 < 5.0) if generation_auto_p95 is not None else None,
                "generation_auto_respond_sample_size": len(generation_latencies_auto_respond),
                "generation_all_paths_sample_size": len(generation_latencies_all),
            },
            "confidence_calibration": {
                "bands": calibration_rows,
                "meets_nfr03_within_5_points": calibration_meets_nfr03,
                "method": "Evaluation_Framework.md reference calibration_table: 5 equal-width confidence bands, stated=mean reported confidence, observed=actual accuracy",
            },
            "citation_hallucination_automated_proxy": {
                "drafts_produced": len(drafts),
                "drafts_blocked_any_guardrail_reason": len(drafts_blocked_any_reason),
                "drafts_blocked_for_unsupported_grounding": len(drafts_blocked_grounding),
                "hallucination_rate_automated_proxy_pct": hallucination_proxy_pct,
                "citation_accuracy_automated_proxy_pct": citation_accuracy_proxy_pct,
                "nfr03_target_hallucination_pct": 5.0,
                "nfr03_target_citation_accuracy_pct": 95.0,
                "caveat": (
                    "AUTOMATED PROXY ONLY, not the NFR-03 metric itself. Evaluation_Framework.md requires "
                    "independent human review (50+ responses, two assessors, inter-rater agreement reported) "
                    "for hallucination_rate, and a per-citation human check against the sentence it supports "
                    "for citation_accuracy. This figure instead reuses this system's own two guardrail layers "
                    "(deterministic unsupported-citation check + guardrail_check_v1's unsupported_sentences) "
                    "as a self-graded signal — informative for catching regressions, not a substitute for the "
                    "required human-reviewed figure."
                ),
            },
        },
        "governance": {
            "decisions_logged_total": decision_log.total_decisions(),
            "distinct_tickets_logged_this_run": decision_log.distinct_tickets_logged_among(run_ticket_ids),
            "distinct_tickets_logged_all_time": decision_log.distinct_tickets_logged(),
            "tickets_processed_matches_decisions_logged": (
                decision_log.distinct_tickets_logged_among(run_ticket_ids) == total
            ),
            "guardrail_blocks": blocked,
            "policy_breaches_count": len(policy_breach_ticket_ids),
            "policy_breaches_ticket_ids": policy_breach_ticket_ids,
            "policy_breach_free": len(policy_breach_ticket_ids) == 0,
        },
        "fairness_fr11_nfr06": {
            "segments": fairness,
            "flagged_segments_over_5_points": flagged_segments,
            "nfr06_band_points": 5.0,
            "method": "FCR-equivalent (auto_respond share) and escalation rate per segment, compared to the overall run figure; PRD NFR-06 measures the system's own output, not the pre-existing human-handled baseline gap.",
        },
    }


def run_evaluation(input_path: str, output_path: str, smoke_test: bool, confidence_threshold: float, top_k: int) -> dict[str, Any]:
    tickets = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if smoke_test:
        tickets = tickets[:SMOKE_TEST_SIZE]
        print(f"[smoke-test] limiting run to first {len(tickets)} ticket(s) from {input_path}")

    ground_truth_map = load_ground_truth()
    print(f"[startup] ground_truth_responses.json: {len(ground_truth_map)} reference entries loaded")

    # Pre-warm the retriever BEFORE the ticket loop: constructing
    # KnowledgeBaseIndexer loads the sentence-transformers embedding model
    # and opens the Chroma client (a ~20s cold start observed in the Stage 4
    # smoke test), and index_documents embeds and stores all 29 articles.
    # Doing this once, up front, means no individual ticket's latency
    # absorbs that cost — only this run's startup does.
    print("[startup] pre-warming retrieval index (embedding model load + Chroma index build)...")
    warm_start = time.monotonic()
    indexer = KnowledgeBaseIndexer()
    if indexer.count() == 0:
        indexer.index_documents(load_documentation())
    print(f"[startup] retrieval index ready in {time.monotonic() - warm_start:.2f}s ({indexer.count()} articles indexed)")

    model_client = LLMModelClient()
    print(f"[startup] model client: {model_client.name} (configured={model_client.is_configured()})")

    decision_log = DecisionLog()
    pipeline_app = build_graph(
        model_client=model_client,
        decision_log=decision_log,
        retrieve_fn=indexer.retrieve,
        confidence_threshold=confidence_threshold,
        top_k=top_k,
    )

    results: list[dict[str, Any]] = []
    errors = 0
    run_ticket_ids: list[str] = []
    for ticket in tickets:
        ticket_id = ticket.get("ticket_id", "UNKNOWN")
        run_ticket_ids.append(ticket_id)
        try:
            result = process_ticket(ticket, pipeline_app, ground_truth_map)
            results.append(result)
        except Exception as exc:  # noqa: BLE001 — a bad ticket must not crash the batch (A11)
            errors += 1
            print(f"[error] ticket {ticket_id} failed to process: {exc!r}", file=sys.stderr)
            decision_log.record(
                Decision(
                    ticket_id=ticket_id,
                    stage="validation",
                    action_taken="escalate",
                    reason=f"unhandled processing error, escalated by default: {exc!r}",
                    model_name="src.evaluate", model_version="1.0",
                    prediction_value=None, prediction_confidence=0.0,
                    requirement_ids=["NFR-02"],
                )
            )

    report = compute_metrics(results, decision_log, errors, model_client.name, run_ticket_ids)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps({"metrics": report, "results": results}, indent=2), encoding="utf-8")
    decision_log.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="CloudServe support-ticket evaluation harness")
    parser.add_argument("--input", required=True, help="Path to a ticket JSON file (never hardcoded)")
    parser.add_argument("--output", required=True, help="Path to write the metrics report JSON")
    parser.add_argument("--smoke-test", action="store_true", help=f"Process only the first {SMOKE_TEST_SIZE} tickets, to verify the pipeline runs end-to-end")
    parser.add_argument("--confidence-threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args()

    report = run_evaluation(args.input, args.output, args.smoke_test, args.confidence_threshold, args.top_k)
    print(json.dumps(report, indent=2))
    print(f"\nMetrics report written to {args.output}")


if __name__ == "__main__":
    main()
