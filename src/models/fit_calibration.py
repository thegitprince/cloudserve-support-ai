"""One-off calibration-fitting pass — calls ONLY the triage_v1 classification
stage (no retrieval/generation/guardrail calls, to keep this fast and cheap)
against labeled tickets, and writes (stated confidence, was it correct) pairs
to a dataset file for src/models/calibration.py to fit an isotonic
regression from at evaluation time.

Run this against development_tickets.json, never validation_tickets.json or
the hidden set — Evaluation_Framework.md: "Develop against the sample
tickets, keep a validation split of your own if you need one, and go to the
test set once." Fitting calibration on the same set compute_metrics later
scores would be circular (the calibration numbers would look good because
they were fit to look good on that exact data, not because they generalize).

    python -m src.models.fit_calibration --input Capstone_Pack/05_Datasets/development_tickets.json --output storage/calibration_dataset.json [--limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from src.models.client import LLMModelClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit a confidence-calibration dataset from labeled tickets")
    parser.add_argument("--input", required=True, help="A labeled ticket file (development_tickets.json)")
    parser.add_argument("--output", required=True, help="Where to write the (confidence, correct) pairs")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of tickets classified (cost control)")
    args = parser.parse_args()

    tickets = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.limit:
        tickets = tickets[: args.limit]

    model_client = LLMModelClient()
    print(f"[fit-calibration] model client: {model_client.name} (configured={model_client.is_configured()})")
    if not model_client.is_configured():
        print("[fit-calibration] WARNING: no provider configured — every pair will come from the "
              "HeuristicModelClient fallback, which uses a different confidence distribution than the "
              "live model this calibration is meant to correct. Configure .env before fitting for real.")

    pairs: list[dict] = []
    skipped_fallback = 0
    for i, ticket in enumerate(tickets, 1):
        true_intent = (ticket.get("labels") or {}).get("intent")
        if not true_intent:
            continue
        classification = model_client.classify_ticket({
            "subject": ticket.get("subject") or "",
            "body": ticket.get("body") or "",
            "customer_tier": ticket.get("customer_tier", "standard"),
            "customer_region": ticket.get("customer_region", "north_america"),
            "language_fluency": ticket.get("language_fluency", "fluent"),
        })
        if model_client.last_call_used_fallback:
            skipped_fallback += 1
            continue
        pairs.append({
            "ticket_id": ticket.get("ticket_id"),
            "confidence": classification["confidence"],
            "correct": classification["intent"] == true_intent,
        })
        if i % 25 == 0:
            print(f"[fit-calibration] {i}/{len(tickets)} tickets classified ({len(pairs)} usable, {skipped_fallback} fallback-skipped)")

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(pairs, indent=2), encoding="utf-8")
    print(f"[fit-calibration] wrote {len(pairs)} (confidence, correct) pairs to {args.output} "
          f"({skipped_fallback} tickets skipped because they fell back to the heuristic classifier)")


if __name__ == "__main__":
    main()
