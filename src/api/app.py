"""FastAPI service wrapper around the same LangGraph pipeline src/evaluate.py
runs in batch (Setup_Guide.md §08's project-structure convention names this
`api.py`). Exposes one ticket at a time over HTTP, plus the Prometheus
metrics Setup_Guide.md §06 names for the dashboard: tickets processed,
proportion auto-answered vs. escalated (by reason), and latency.

Run with:

    uvicorn src.api.app:app --reload

Metrics are available at GET /metrics (Prometheus text format) and the
pipeline is reachable at POST /v1/tickets.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from src.database.decision_log import DecisionLog
from src.evaluate import _result as build_result
from src.evaluate import load_ground_truth, normalize_ticket
from src.models.client import LLMModelClient
from src.pipeline.graph import build_graph, run_ticket
from src.retrieval.indexer import KnowledgeBaseIndexer, load_documentation

DEFAULT_CONFIDENCE_THRESHOLD = 0.70
DEFAULT_TOP_K = 3

# --- Prometheus metrics (Setup_Guide.md §06's three named dashboard inputs) -
TICKETS_PROCESSED_TOTAL = Counter(
    "tickets_processed_total", "Total tickets processed", ["channel", "action"]
)
ESCALATIONS_TOTAL = Counter(
    "escalations_total", "Total escalated tickets, by escalation reason", ["reason"]
)
PIPELINE_LATENCY_SECONDS = Histogram(
    "pipeline_latency_seconds", "End-to-end pipeline latency per ticket, in seconds"
)

_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Pre-warm exactly as src/evaluate.py's run_evaluation does, so the
    # first real request doesn't pay the embedding-model cold-start cost.
    indexer = KnowledgeBaseIndexer()
    if indexer.count() == 0:
        indexer.index_documents(load_documentation())

    model_client = LLMModelClient()
    decision_log = DecisionLog()
    pipeline_app = build_graph(
        model_client=model_client,
        decision_log=decision_log,
        retrieve_fn=indexer.retrieve,
        confidence_threshold=DEFAULT_CONFIDENCE_THRESHOLD,
        top_k=DEFAULT_TOP_K,
    )

    _state["pipeline_app"] = pipeline_app
    _state["decision_log"] = decision_log
    _state["ground_truth_map"] = load_ground_truth()
    _state["model_client"] = model_client

    yield

    decision_log.close()
    _state.clear()


app = FastAPI(title="CloudServe Intelligent Support System", lifespan=lifespan)


class TicketIn(BaseModel):
    """Mirrors the normalize_ticket() input contract (src/evaluate.py) —
    every field optional except ticket_id, since FR-01 requires the
    pipeline to tolerate a sparse/malformed record rather than reject it."""

    ticket_id: str
    channel: str = "email"
    subject: str = ""
    body: str = ""
    customer_tier: str = "standard"
    customer_region: str = "north_america"
    language_fluency: str = "fluent"
    labels: dict[str, Any] = Field(default_factory=dict)
    history: dict[str, Any] = Field(default_factory=dict)


@app.post("/v1/tickets")
def post_ticket(ticket: TicketIn) -> dict[str, Any]:
    """Runs one ticket through the same pipeline src/evaluate.py uses for
    batch runs, and returns the same per-ticket result shape. A single bad
    request never crashes the service — FastAPI's own exception handling
    returns a 500 with the error, matching the Unattended Gate Rule's
    per-ticket isolation at the HTTP layer instead of the batch-loop layer."""
    start = time.monotonic()
    normalized = normalize_ticket(ticket.model_dump(), _state["ground_truth_map"])
    final_state = run_ticket(_state["pipeline_app"], normalized)
    elapsed = time.monotonic() - start

    action = final_state.get("action") or "unknown"
    TICKETS_PROCESSED_TOTAL.labels(channel=normalized["channel"], action=action).inc()
    if action == "escalate":
        ESCALATIONS_TOTAL.labels(reason=final_state.get("escalation_reason") or "unspecified").inc()
    PIPELINE_LATENCY_SECONDS.observe(elapsed)

    return build_result(normalized, final_state, elapsed)


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health() -> dict[str, Any]:
    """Not one of the three required metrics — a plain liveness check so a
    deployment probe doesn't need to understand Prometheus text format."""
    return {
        "status": "ok",
        "kill_switch_enabled": _state["decision_log"].is_kill_switch_enabled() if _state else None,
        "model_client": _state["model_client"].name if _state else None,
    }
