"""The FIRST guardrail layer — deterministic, no model call. Implements the
fast-path half of FR-07 described in docs/Stage_3_Prompt_Library.md's PR-04
pipeline-order audit note: this runs before any LLM-based guardrail check
(src.models.client.LLMModelClient.guardrail_check), specifically to protect
the NFR-01 latency budget by making the expensive LLM call the exception
path rather than something every response pays for.

Split out of src/evaluate.py in Stage 5 so src/pipeline/graph.py's Node 6
(Guardrails) can call it without evaluate.py and graph.py importing from
each other.
"""
from __future__ import annotations

import re
from typing import Any

_PII_PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    "phone": re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "long_account_number": re.compile(r"\b\d{9,}\b"),
}
_FINANCIAL_COMMITMENT_PATTERNS = re.compile(
    r"\b(we have issued|we'?ve issued|issued a refund|issued you a refund|here is your refund|"
    r"we will refund|we will credit|credit has been applied|discount has been applied|fee waived)\b",
    re.IGNORECASE,
)
_DISCLOSURE_PREFIX = "this is an automated response"


def deterministic_guardrail_check(
    response_text: str,
    cited_doc_ids: list[str],
    retrieved_docs: list[dict[str, Any]],
    must_not_claim: list[str] | None,
) -> dict[str, Any]:
    violations: list[dict[str, str]] = []

    for category, pattern in _PII_PATTERNS.items():
        match = pattern.search(response_text)
        if match:
            violations.append({"category": "pii", "quoted_text": match.group(0), "detector": category})

    financial_match = _FINANCIAL_COMMITMENT_PATTERNS.search(response_text)
    if financial_match:
        violations.append({"category": "forbidden_claim_financial", "quoted_text": financial_match.group(0), "detector": "regex"})

    for claim in must_not_claim or []:
        if claim.lower() in response_text.lower():
            violations.append({"category": "must_not_claim_match", "quoted_text": claim, "detector": "substring"})

    if response_text and not response_text.strip().lower().startswith(_DISCLOSURE_PREFIX):
        violations.append({"category": "tone", "quoted_text": response_text[:60], "detector": "disclosure_missing"})

    valid_doc_ids = {d["doc_id"] for d in retrieved_docs}
    for doc_id in cited_doc_ids:
        if doc_id not in valid_doc_ids:
            violations.append({"category": "unsupported_citation", "quoted_text": doc_id, "detector": "citation_not_in_retrieved"})

    verdict = "fail" if violations else "pass"
    return {"verdict": verdict, "violations": violations, "layer": "deterministic"}
