"""Deterministic, offline stand-in for the real LLM calls described in
src/prompts/*.yaml. No network access, so it always "succeeds" in the sense
that it never times out or rate-limits.

Lives in its own module (split out of src/evaluate.py in Stage 5) so
src/models/client.py can import it as the Build Spec A11 fallback — "if the
API fails after retries, fall back to HeuristicModelClient" — without a
circular import (evaluate.py -> pipeline/graph.py -> models/client.py would
otherwise need to import back into evaluate.py).

This is NOT meant to hit the NFR-03 accuracy targets; it exists so the
pipeline has something non-trivial to run end-to-end without a network call,
either in --smoke-test mode with no API key configured, or as the degraded
path when a real provider call is exhausted mid-batch.
"""
from __future__ import annotations

from typing import Any

from src.routing.policy import is_mandatory_escalation

MODEL_NAME_STUB = "heuristic-stub-v1"
MODEL_VERSION_STUB = "1.0"

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "authentication_failure": ["login", "log in", "sign in", "password", "credential", "invalid credentials"],
    "account_access": ["locked out", "account access", "can't access my account", "cannot access my account"],
    "api_key_issue": ["api key", "apikey", "revoke", "rotate key"],
    "api_usage_question": ["how do i use the api", "api endpoint", "how does the api"],
    "billing_query": ["invoice", "bill", "charge", "overcharged", "refund"],
    "compliance_request": ["compliance", "audit", "sox", "gdpr request", "soc 2"],
    "configuration_help": ["configure", "configuration", "settings", "setup help"],
    "data_export": ["export my data", "data export", "download my data"],
    "data_residency": ["data residency", "data location", "stored", "data center", "data centre", "which region is our data"],
    "database_issue": ["database", "db connection", "query timeout", "sql error"],
    "deployment_failure": ["deploy", "deployment", "rollback loop", "failed to deploy", "rolling back"],
    "feature_request": ["feature request", "could you add", "please add", "it would be great if", "dark mode", "roadmap"],
    "integration_help": ["integration", "webhook integration", "connect to", "integrate with"],
    "onboarding": ["getting started", "onboarding", "new account", "first time setup"],
    "performance_degradation": ["slow", "latency", "performance", "timing out", "degraded"],
    "quota_or_overage": ["quota", "overage", "limit exceeded", "over my limit"],
    "rate_limit": ["rate limit", "429", "too many requests"],
    "rollback_request": ["rollback", "revert", "roll back"],
    "security_incident": ["unauthorized access", "security incident", "unrecognized ip", "compromised", "suspicious login"],
    "sso_configuration": ["sso", "single sign-on", "saml", "okta", "azure ad"],
    "webhook_issue": ["webhook", "callback url", "webhook not firing"],
}

_HIGH_URGENCY_KEYWORDS = ["blocked", "down", "outage", "urgent", "broken", "can't ship", "cannot ship", "production"]
_LOW_URGENCY_KEYWORDS = ["how does", "how do i", "just wondering", "not urgent", "whenever", "no rush"]


class HeuristicModelClient:
    """Deterministic, offline stand-in for the real LLM calls described in
    src/prompts/*.yaml."""

    name = MODEL_NAME_STUB
    version = MODEL_VERSION_STUB

    def classify_ticket(self, ticket: dict[str, Any]) -> dict[str, Any]:
        text = f"{ticket.get('subject', '')} {ticket.get('body', '')}".lower()
        scores: dict[str, int] = {}
        for intent, keywords in _INTENT_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw in text)
            if hits:
                scores[intent] = hits

        if not scores:
            intent, confidence = "unclear_request", 0.3
        else:
            intent = max(scores, key=lambda k: scores[k])
            confidence = min(0.95, 0.55 + 0.15 * scores[intent])

        if any(kw in text for kw in _HIGH_URGENCY_KEYWORDS):
            urgency = "high"
        elif any(kw in text for kw in _LOW_URGENCY_KEYWORDS):
            urgency = "low"
        else:
            urgency = "medium"

        return {
            "intent": intent,
            "urgency": urgency,
            "confidence": round(confidence, 3),
            "is_mandatory_escalation": is_mandatory_escalation(intent),
            "rationale": "heuristic keyword match (stub classifier, not the production triage_v1 prompt)",
        }

    def generate_response(
        self, ticket: dict[str, Any], retrieved_docs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if not retrieved_docs:
            return {"can_answer": False, "response_text": "", "cited_doc_ids": [], "confidence": 0.0}

        best = retrieved_docs[0]
        if best["relevance_score"] < 0.35:
            return {"can_answer": False, "response_text": "", "cited_doc_ids": [], "confidence": best["relevance_score"]}

        snippet = best["content"].strip().splitlines()
        first_line = next((ln for ln in snippet if ln.strip() and not ln.strip().startswith("#")), best["title"])
        response_text = (
            f"This is an automated response based on CloudServe documentation ({best['doc_id']}). "
            f"{first_line.strip()} See {best['doc_id']} ('{best['title']}') for the full resolution steps."
        )
        return {
            "can_answer": True,
            "response_text": response_text,
            "cited_doc_ids": [best["doc_id"]],
            "confidence": best["relevance_score"],
        }
