"""The support-ticket orchestration pipeline — a LangGraph StateGraph that
replaces the inline if/else orchestration that used to live in
src/evaluate.py.process_ticket, so every stage boundary (and the Decision Log
write at that boundary) is an explicit, inspectable node rather than nested
branches.

Node layout, matching the Stage 5 build instruction exactly:
    1. node_ingest             — normalization already done by the caller;
                                  this node only checks the FR-10 kill switch.
    2. node_triage              — triage_v1 classification (FR-02/FR-03).
    3. node_route_mandatory +
       node_route_threshold     — routing logic (src/routing/policy.py),
                                  checking MANDATORY_ESCALATION_INTENTS and
                                  the confidence thresholds. This is ONE
                                  logical node split into two graph nodes
                                  because the confidence-threshold half needs
                                  retrieval_confidence, which only exists
                                  after Node 4 retrieves — the ordering
                                  src/evaluate.py's original process_ticket
                                  already used, carried over here rather than
                                  changed.
    4. node_retrieve            — Chroma retrieval (FR-05), skipped entirely
                                  for mandatory-escalation tickets.
    5. node_generate             — rag_response_v1 answer drafting (FR-06).
    6. node_guardrails            — deterministic PII/financial/disclosure/
                                  citation scan FIRST, then the LLM-based
                                  guardrail_check_v1 SECOND, per the
                                  pipeline-order audit fix in
                                  docs/Stage_3_Prompt_Library.md (PR-04).
    7. node_escalation_handoff    — triage_packet_v1 handoff packet (FR-08),
                                  built for every ticket that ends up
                                  escalated, regardless of which earlier node
                                  decided that.
    8. node_finalize              — assembles the per-ticket result dict the
                                  evaluation harness consumes. Decision Log
                                  writes (FR-09) happen inline at each stage
                                  above (the schema's own "stage" field is
                                  classification | routing | generation |
                                  validation, i.e. per-stage, not one record
                                  at the end) — this node does not duplicate
                                  those writes, it only returns the finished
                                  result.
"""
from __future__ import annotations

import time
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from src.database.decision_log import Decision, DecisionLog
from src.guardrails.deterministic import deterministic_guardrail_check
from src.routing.policy import is_mandatory_escalation, routing_decision

RetrieveFn = Callable[[str, int], list[dict[str, Any]]]


class PipelineState(TypedDict, total=False):
    ticket: dict[str, Any]
    killed: bool
    classification: dict[str, Any]
    authoritative_mandatory: bool
    retrieved_docs: list[dict[str, Any]]
    retrieval_confidence: float | None
    action: str
    reason: str
    escalation_reason: str
    generation: dict[str, Any]
    guardrail_result: dict[str, Any]
    escalation_packet: dict[str, Any] | None
    start_time: float
    classification_latency_seconds: float | None
    generation_latency_seconds: float | None
    _generation_path_start: float


def build_graph(
    model_client: Any,
    decision_log: DecisionLog,
    retrieve_fn: RetrieveFn,
    confidence_threshold: float,
    top_k: int,
):
    """Returns a compiled LangGraph app. `model_client` must expose
    classify_ticket, generate_response, guardrail_check, and
    build_escalation_packet (src.models.client.LLMModelClient satisfies
    this; it already falls back to HeuristicModelClient internally per call,
    so this graph never needs to know about that fallback itself).
    `retrieve_fn` is src.retrieval.indexer.retrieve, or an equivalent bound
    to an already pre-warmed KnowledgeBaseIndexer instance."""

    def node_ingest(state: PipelineState) -> PipelineState:
        state["start_time"] = time.monotonic()
        killed = not decision_log.is_kill_switch_enabled()
        state["killed"] = killed
        if killed:
            decision_log.record(Decision(
                ticket_id=state["ticket"]["ticket_id"], stage="routing",
                action_taken="escalate",
                reason="administrative kill switch is engaged; all tickets escalate",
                model_name="system", model_version="n/a",
                prediction_value=None, prediction_confidence=0.0,
                requirement_ids=["FR-10"],
            ))
            state["action"], state["reason"], state["escalation_reason"] = (
                "escalate", "kill_switch_engaged", "mandatory_policy",
            )
        return state

    def node_triage(state: PipelineState) -> PipelineState:
        ticket = state["ticket"]
        classification = model_client.classify_ticket(ticket)
        state["classification"] = classification
        # NFR-01's classification budget (P95 < 2s) covers exactly this
        # stage, ingest-to-triage-done — not the heavier retrieval/generation
        # stage that follows, which has its own, separate 5s budget below.
        state["classification_latency_seconds"] = time.monotonic() - state["start_time"]
        decision_log.record(Decision(
            ticket_id=ticket["ticket_id"], stage="classification",
            action_taken="escalate" if classification["is_mandatory_escalation"] else "auto_respond",
            reason=classification["rationale"],
            model_name=model_client.name, model_version=model_client.version,
            prediction_value=classification["intent"],
            prediction_confidence=classification["confidence"],
            prompt_version="triage_v1",
            requirement_ids=["FR-02", "FR-03"],
        ))
        return state

    def node_route_mandatory(state: PipelineState) -> PipelineState:
        intent = state["classification"]["intent"]
        state["authoritative_mandatory"] = is_mandatory_escalation(intent)
        if state["authoritative_mandatory"]:
            state["action"] = "escalate"
            state["reason"] = (
                f"intent '{intent}' is in MANDATORY_ESCALATION_INTENTS (authoritative code-level policy)"
            )
            state["escalation_reason"] = "mandatory_policy"
            state["retrieved_docs"] = []
        return state

    def node_retrieve(state: PipelineState) -> PipelineState:
        state["_generation_path_start"] = time.monotonic()
        ticket = state["ticket"]
        docs = retrieve_fn(f"{ticket['subject']} {ticket['body']}".strip(), top_k)
        state["retrieved_docs"] = docs
        state["retrieval_confidence"] = max((d["relevance_score"] for d in docs), default=None)
        return state

    def node_route_threshold(state: PipelineState) -> PipelineState:
        ticket = state["ticket"]
        classification = state["classification"]
        intent = classification["intent"]
        action, reason = routing_decision(
            intent, classification["confidence"], state.get("retrieval_confidence"), confidence_threshold,
        )
        state["action"], state["reason"] = action, reason
        if action == "escalate":
            state["escalation_reason"] = (
                "low_classification_confidence"
                if classification["confidence"] < confidence_threshold
                else "low_retrieval_confidence"
            )
        flag_agreement = state["authoritative_mandatory"] == classification["is_mandatory_escalation"]
        decision_log.record(Decision(
            ticket_id=ticket["ticket_id"], stage="routing",
            action_taken=action, reason=reason,
            model_name="src.routing.policy", model_version="1.0",
            prediction_value=intent, prediction_confidence=classification["confidence"],
            threshold_applied=confidence_threshold,
            sources_used=[{"doc_id": d["doc_id"], "score": d["relevance_score"]} for d in state.get("retrieved_docs", [])],
            requirement_ids=["FR-04"],
            guardrail_results={"llm_flag_agreement": "pass" if flag_agreement else "disagreement_logged"},
        ))
        return state

    def node_generate(state: PipelineState) -> PipelineState:
        ticket = state["ticket"]
        generation = model_client.generate_response(ticket, state.get("retrieved_docs", []))
        state["generation"] = generation
        decision_log.record(Decision(
            ticket_id=ticket["ticket_id"], stage="generation",
            action_taken="auto_respond" if generation["can_answer"] else "escalate",
            reason="grounded answer drafted" if generation["can_answer"] else "model abstained: retrieved passages did not support an answer",
            model_name=model_client.name, model_version=model_client.version,
            prediction_value=generation["response_text"][:200],
            prediction_confidence=generation["confidence"],
            sources_used=[{"doc_id": d, "score": None} for d in generation["cited_doc_ids"]],
            prompt_version="rag_response_v1",
            requirement_ids=["FR-06"],
        ))
        if not generation["can_answer"]:
            state["action"], state["reason"] = "escalate", "generation abstained (can_answer=false)"
            state["escalation_reason"] = "no_documents_retrieved" if not state.get("retrieved_docs") else "low_retrieval_confidence"
            state["generation_latency_seconds"] = time.monotonic() - state["_generation_path_start"]
        return state

    def node_guardrails(state: PipelineState) -> PipelineState:
        ticket = state["ticket"]
        generation = state["generation"]
        ground_truth = ticket.get("ground_truth", {})
        deterministic_result = deterministic_guardrail_check(
            generation["response_text"], generation["cited_doc_ids"],
            state.get("retrieved_docs", []), ground_truth.get("must_not_claim"),
        )
        decision_log.record(Decision(
            ticket_id=ticket["ticket_id"], stage="validation",
            action_taken="block" if deterministic_result["verdict"] == "fail" else "auto_respond",
            reason=f"deterministic guardrail layer: {deterministic_result['verdict']}",
            model_name="src.guardrails.deterministic", model_version="1.0",
            prediction_value=deterministic_result["verdict"], prediction_confidence=1.0,
            guardrail_results={"deterministic": deterministic_result["verdict"]},
            requirement_ids=["FR-07"],
        ))

        if deterministic_result["verdict"] == "fail":
            state["guardrail_result"] = deterministic_result
            state["action"] = "escalate"
            state["reason"] = f"guardrail_block (deterministic layer): {[v['category'] for v in deterministic_result['violations']]}"
            state["escalation_reason"] = "guardrail_block"
            state["generation_latency_seconds"] = time.monotonic() - state["_generation_path_start"]
            return state

        llm_result = model_client.guardrail_check(
            generation["response_text"], generation["cited_doc_ids"],
            state.get("retrieved_docs", []), ground_truth.get("must_not_claim"),
        )
        decision_log.record(Decision(
            ticket_id=ticket["ticket_id"], stage="validation",
            action_taken="block" if llm_result["verdict"] == "fail" else "auto_respond",
            reason=f"LLM guardrail layer: {llm_result['verdict']}",
            model_name=model_client.name, model_version=model_client.version,
            prediction_value=llm_result["verdict"], prediction_confidence=1.0,
            prompt_version="guardrail_check_v1",
            guardrail_results={"llm": llm_result["verdict"]},
            requirement_ids=["FR-07"],
        ))
        state["guardrail_result"] = llm_result
        if llm_result["verdict"] == "fail":
            state["action"] = "escalate"
            state["reason"] = f"guardrail_block (LLM layer): {[v['category'] for v in llm_result['violations']]}"
            state["escalation_reason"] = "guardrail_block"
        # This is the end of the retrieval+draft+guardrail chain for every
        # ticket that reaches it — NFR-01's "end-to-end response generation"
        # budget (P95 < 5s) is reported only over the subset that ends in
        # auto_respond, filtered at the metrics layer (src/evaluate.py).
        state["generation_latency_seconds"] = time.monotonic() - state["_generation_path_start"]
        return state

    def node_escalation_handoff(state: PipelineState) -> PipelineState:
        ticket = state["ticket"]
        classification = state.get("classification")
        if classification is None:
            # Kill-switch path: triage never ran, so there is nothing to
            # summarize beyond the fact every ticket is escalating.
            state["escalation_packet"] = None
            return state
        packet = model_client.build_escalation_packet(
            ticket, classification, state.get("retrieved_docs", []),
            state.get("escalation_reason", "mandatory_policy"),
        )
        state["escalation_packet"] = packet
        decision_log.record(Decision(
            ticket_id=ticket["ticket_id"], stage="validation",
            action_taken="escalate", reason="escalation handoff packet prepared for tier-two engineer",
            model_name=model_client.name, model_version=model_client.version,
            prediction_value=packet.get("predicted_intent"), prediction_confidence=None,
            prompt_version="triage_packet_v1",
            requirement_ids=["FR-08"],
        ))
        return state

    def node_finalize(state: PipelineState) -> PipelineState:
        return state

    graph = StateGraph(PipelineState)
    graph.add_node("ingest", node_ingest)
    graph.add_node("triage", node_triage)
    graph.add_node("route_mandatory", node_route_mandatory)
    graph.add_node("retrieve", node_retrieve)
    graph.add_node("route_threshold", node_route_threshold)
    graph.add_node("generate", node_generate)
    graph.add_node("guardrails", node_guardrails)
    graph.add_node("escalation_handoff", node_escalation_handoff)
    graph.add_node("finalize", node_finalize)

    graph.set_entry_point("ingest")
    graph.add_conditional_edges("ingest", lambda s: "escalation_handoff" if s["killed"] else "triage")
    graph.add_edge("triage", "route_mandatory")
    graph.add_conditional_edges(
        "route_mandatory",
        lambda s: "escalation_handoff" if s["authoritative_mandatory"] else "retrieve",
    )
    graph.add_edge("retrieve", "route_threshold")
    graph.add_conditional_edges(
        "route_threshold",
        lambda s: "generate" if s["action"] == "auto_respond" else "escalation_handoff",
    )
    graph.add_conditional_edges(
        "generate",
        lambda s: "guardrails" if s["generation"]["can_answer"] else "escalation_handoff",
    )
    graph.add_conditional_edges(
        "guardrails",
        lambda s: "finalize" if s["action"] == "auto_respond" else "escalation_handoff",
    )
    graph.add_edge("escalation_handoff", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


def run_ticket(app: Any, ticket: dict[str, Any]) -> PipelineState:
    """Runs one already-normalized ticket through the compiled graph and
    returns the final state."""
    return app.invoke({"ticket": ticket})
