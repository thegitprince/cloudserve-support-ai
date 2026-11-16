"""Deterministic routing policy — implements FR-04.

This is the AUTHORITATIVE mandatory-escalation check. The LLM's own
`is_mandatory_escalation` flag, produced by src/prompts/triage_v1.yaml, is a
secondary signal only (see docs/Stage_3_Prompt_Library.md, the "[AUDIT FIX]
Mandatory-escalation scope" note). Routing never trusts that flag alone — it
recomputes the decision from the classified intent against the table below
and logs both values so any disagreement is visible rather than silently
reconciled.

Provenance of this set, verified by full enumeration of
Capstone_Pack/05_Datasets/development_tickets.json (not a sample):

  Dataset-confirmed (every ticket of this intent carries
  must_not_auto_respond=true, and no other intent carries it at the
  full-intent level):
    compliance_request   26/26
    security_incident    26/26
    feature_request      20/20
    unclear_request      15/15
    (26+26+20+15 = 87, matching the 87 must_not_auto_respond=true tickets
    in the full development set exactly)

  Policy extension (NOT dataset-confirmed at the full-intent level — only
  69% of development-set data_residency tickets, 20/29, carry
  must_not_auto_respond=true):
    data_residency
  Added as a whole-intent mandatory-escalation category on Daniel Okonkwo's
  (Tier Two Engineer) interview testimony: "anything involving data
  location... we get those wrong occasionally even as humans and the
  consequence is a compliance problem." This is a deliberate governance
  decision, not a data correction — see docs/PRD_v1.md FR-04 and Section 8.

  Explicitly NOT included: billing_query. Most billing_query tickets are
  legitimately answerable (45.8% historically non-escalated); the financial-
  commitment risk Daniel also names is handled at the generation/guardrail
  layer (FR-06/FR-07) by blocking any specific refund/credit/commitment
  claim, regardless of intent, rather than by escalating the whole category.
"""
from __future__ import annotations

MANDATORY_ESCALATION_INTENTS: frozenset[str] = frozenset(
    {
        "compliance_request",
        "security_incident",
        "feature_request",
        "unclear_request",
        "data_residency",
    }
)

# All 22 intents present in development_tickets.json, for validation at the
# call site — a classifier output outside this set is a bug, not a new
# category to silently accept.
KNOWN_INTENTS: frozenset[str] = frozenset(
    {
        "account_access",
        "api_key_issue",
        "api_usage_question",
        "authentication_failure",
        "billing_query",
        "compliance_request",
        "configuration_help",
        "data_export",
        "data_residency",
        "database_issue",
        "deployment_failure",
        "feature_request",
        "integration_help",
        "onboarding",
        "performance_degradation",
        "quota_or_overage",
        "rate_limit",
        "rollback_request",
        "security_incident",
        "sso_configuration",
        "unclear_request",
        "webhook_issue",
    }
)


def is_mandatory_escalation(intent: str) -> bool:
    """The authoritative mandatory-escalation check. Called with the
    classifier's output intent; returns True if this ticket must never
    receive an automated response, regardless of any confidence score."""
    if intent not in KNOWN_INTENTS:
        # An unrecognised intent is treated as unclear_request-equivalent:
        # escalate rather than guess. This is the same "say nothing rather
        # than something wrong" stance applied to the routing layer itself.
        return True
    return intent in MANDATORY_ESCALATION_INTENTS


def routing_decision(
    intent: str,
    classification_confidence: float,
    retrieval_confidence: float | None,
    confidence_threshold: float,
) -> tuple[str, str]:
    """Returns (action_taken, reason) for the Decision Log, where
    action_taken is one of "auto_respond" or "escalate".

    Deterministic: the same inputs always yield the same decision (A5).
    """
    if is_mandatory_escalation(intent):
        return "escalate", (
            f"intent '{intent}' is in the policy-mandatory escalation set; "
            "never auto-responded regardless of confidence"
        )
    if classification_confidence < confidence_threshold:
        return "escalate", (
            f"classification confidence {classification_confidence:.2f} is below "
            f"the routing threshold {confidence_threshold:.2f}"
        )
    if retrieval_confidence is None or retrieval_confidence < confidence_threshold:
        return "escalate", (
            "no documentation passage met the retrieval-confidence threshold "
            f"{confidence_threshold:.2f}"
        )
    return "auto_respond", (
        f"classification confidence {classification_confidence:.2f} and retrieval "
        f"confidence {retrieval_confidence:.2f} both meet the threshold "
        f"{confidence_threshold:.2f}, and intent '{intent}' is discretionary"
    )
