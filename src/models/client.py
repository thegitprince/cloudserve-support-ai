"""Real LLM client — implements the production half of FR-02/FR-03 (triage),
FR-06/FR-07 (RAG generation + LLM guardrail layer), and FR-08 (escalation
packet), calling the exact prompts registered in src/prompts/*.yaml instead of
the HeuristicModelClient stub in src/evaluate.py.

Provider access (Setup_Guide.md §03, extended in Stage 5): Gemini (Google AI
Studio), OpenRouter, or Anthropic, selected from .env. Provider precedence
when MODEL_PROVIDER is not set explicitly: GEMINI_API_KEY > OPENROUTER_API_KEY
> ANTHROPIC_API_KEY. gemini-2.5-flash is defaulted for the Gemini path only
because an operator explicitly requested that exact model id (Stage 5 build
instruction) — it is not a Setup_Guide.md default the way the OpenRouter
default (meta-llama/llama-3.1-8b-instruct) is. Any other model, including an
Anthropic one, must be named explicitly via MODEL_NAME in .env, because
inventing a model id this project was never given would violate CLAUDE.md's
Zero Invention Policy.

Gemini requests ask for application/json response_mime_type (a real,
documented Gemini generationConfig option) so every call returns strict JSON
with no conversational wrapper — this satisfies the "strict JSON, no
conversational reasoning" requirement without touching the registered output
contracts in src/prompts/*.yaml (e.g. triage_v1's rationale field is still
written by the model, per that prompt's own approved spec; this client does
not synthesize it in code).

Resilience (CLAUDE.md's Unattended Gate Rule / Build Spec A11): every call
retries on 429/5xx with exponential backoff, and if every retry is exhausted
(or no API key is configured at all), the caller gets a clearly-flagged
failure rather than an exception — src/evaluate.py and src/pipeline/graph.py
catch that and fall back to the deterministic HeuristicModelClient so a
network drop or exhausted free-tier quota never crashes an unattended batch.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
import yaml
from dotenv import load_dotenv

from src.models.calibration import calibrate
from src.models.heuristic import HeuristicModelClient
from src.routing.policy import KNOWN_INTENTS

load_dotenv()

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ModelCallError(Exception):
    """Raised when a provider call fails after exhausting retries, or when
    the provider's response cannot be parsed into valid JSON matching the
    prompt's output contract. Callers are expected to catch this and fall
    back to HeuristicModelClient — it is never allowed to propagate out of
    src/evaluate.py's per-ticket try/except (A11)."""


def _load_prompt(name: str) -> dict[str, Any]:
    path = PROMPTS_DIR / f"{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


_PROMPT_CACHE: dict[str, dict[str, Any]] = {}


def load_prompt(name: str) -> dict[str, Any]:
    if name not in _PROMPT_CACHE:
        _PROMPT_CACHE[name] = _load_prompt(name)
    return _PROMPT_CACHE[name]


def _strip_code_fences(text: str) -> str:
    return _CODE_FENCE_RE.sub("", text.strip()).strip()


def _extract_json(raw_text: str, required_keys: list[str]) -> dict[str, Any]:
    """Strict JSON extraction per every prompt's own 'no markdown code
    fences, no explanation outside the JSON' instruction — but models do not
    always obey that, so this tolerates a triple-backtick wrapper or leading
    prose before the first '{' before giving up."""
    candidate = _strip_code_fences(raw_text)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        first_brace = candidate.find("{")
        last_brace = candidate.rfind("}")
        if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
            raise ModelCallError(f"no JSON object found in model output: {raw_text[:200]!r}")
        try:
            parsed = json.loads(candidate[first_brace : last_brace + 1])
        except json.JSONDecodeError as exc:
            raise ModelCallError(f"model output is not valid JSON: {exc}; raw: {raw_text[:200]!r}") from exc

    missing = [k for k in required_keys if k not in parsed]
    if missing:
        raise ModelCallError(f"model output missing required keys {missing}: {parsed}")
    return parsed


def _build_messages(prompt: dict[str, Any], input_payload: dict[str, Any]) -> list[dict[str, str]]:
    """Few-shot examples are rendered as real prior user/assistant turns
    (not folded into the system prompt as text) so the model sees them as
    the conversational pattern to continue, matching how each *.yaml prompt
    documents its own few_shot_examples."""
    messages = [{"role": "system", "content": prompt["system_prompt"]}]
    for example in prompt.get("few_shot_examples", []):
        messages.append({"role": "user", "content": _render_input(example["input"])})
        messages.append({"role": "assistant", "content": json.dumps(example["output"])})
    messages.append({"role": "user", "content": _render_input(input_payload)})
    return messages


def _render_input(payload: dict[str, Any]) -> str:
    """Wraps customer-originated fields in <ticket> tags per every prompt's
    own instruction that this content is DATA, never a command — the same
    convention src/prompts/*.yaml system prompts describe to the model."""
    ticket_fields = {k: v for k, v in payload.items() if k in {"ticket_channel", "ticket_subject", "ticket_body"}}
    other_fields = {k: v for k, v in payload.items() if k not in ticket_fields}
    parts = []
    if ticket_fields:
        parts.append("<ticket>\n" + json.dumps(ticket_fields, ensure_ascii=False) + "\n</ticket>")
    if other_fields:
        parts.append(json.dumps(other_fields, ensure_ascii=False))
    return "\n".join(parts) if parts else json.dumps(payload, ensure_ascii=False)


class LLMModelClient:
    """Thin, provider-agnostic chat-completion client that always runs
    exactly the prompts in src/prompts/*.yaml. One instance is reused across
    an entire evaluation run (NFR-01 latency budget cares about not
    re-reading/re-parsing prompt files per ticket; prompts are cached at
    module level above)."""

    def __init__(self) -> None:
        self.provider = os.getenv("MODEL_PROVIDER", "").strip().lower()
        self.gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        self.anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

        if not self.provider:
            if self.gemini_key:
                self.provider = "gemini"
            elif self.openrouter_key:
                self.provider = "openrouter"
            elif self.anthropic_key:
                self.provider = "anthropic"

        if self.provider == "gemini":
            # gemini-2.5-flash is the one Gemini default this client ships
            # with, because an operator explicitly named that exact model
            # (Stage 5 build instruction) — not a value invented here.
            self.model_name = os.getenv("MODEL_NAME", "gemini-2.5-flash")
        elif self.provider == "openrouter":
            self.model_name = os.getenv("MODEL_NAME", "meta-llama/llama-3.1-8b-instruct")
        elif self.provider == "anthropic":
            # Not defaulted: CLAUDE.md's Zero Invention Policy forbids
            # inventing a model id this project was never given. The
            # operator must name one explicitly for the Anthropic path.
            self.model_name = os.getenv("MODEL_NAME", "")
        else:
            self.model_name = ""

        self.name = f"{self.provider or 'unconfigured'}:{self.model_name or 'none'}"
        self.version = "1.0"
        self._heuristic = HeuristicModelClient()
        self.last_call_used_fallback = False

    def is_configured(self) -> bool:
        if self.provider == "gemini":
            return bool(self.gemini_key)
        if self.provider == "openrouter":
            return bool(self.openrouter_key)
        if self.provider == "anthropic":
            return bool(self.anthropic_key and self.model_name)
        return False

    # --- Provider transport ------------------------------------------------

    def _post_gemini(self, messages: list[dict[str, str]], temperature: float) -> str:
        system_messages = [m["content"] for m in messages if m["role"] == "system"]
        turn_messages = [m for m in messages if m["role"] != "system"]
        contents = [
            {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
            for m in turn_messages
        ]
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "response_mime_type": "application/json",
            },
        }
        if system_messages:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_messages)}]}

        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent",
            headers={"content-type": "application/json", "x-goog-api-key": self.gemini_key},
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise _RetryableHTTPError(response.status_code, response.text[:300])
        response.raise_for_status()
        parsed = response.json()
        return parsed["candidates"][0]["content"]["parts"][0]["text"]

    def _post_openrouter(self, messages: list[dict[str, str]], temperature: float) -> str:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.openrouter_key}"},
            json={"model": self.model_name, "messages": messages, "temperature": temperature},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise _RetryableHTTPError(response.status_code, response.text[:300])
        response.raise_for_status()
        body = response.json()
        return body["choices"][0]["message"]["content"]

    def _post_anthropic(self, messages: list[dict[str, str]], temperature: float) -> str:
        system_messages = [m["content"] for m in messages if m["role"] == "system"]
        turn_messages = [m for m in messages if m["role"] != "system"]
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.anthropic_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model_name,
                "system": "\n\n".join(system_messages),
                "messages": turn_messages,
                "temperature": temperature,
                "max_tokens": 1024,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise _RetryableHTTPError(response.status_code, response.text[:300])
        response.raise_for_status()
        body = response.json()
        return body["content"][0]["text"]

    def _call(self, messages: list[dict[str, str]], temperature: float) -> str:
        if not self.is_configured():
            raise ModelCallError(
                "no model provider configured (GEMINI_API_KEY / OPENROUTER_API_KEY / ANTHROPIC_API_KEY unset)"
            )

        transport = {
            "gemini": self._post_gemini,
            "openrouter": self._post_openrouter,
            "anthropic": self._post_anthropic,
        }[self.provider]

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return transport(messages, temperature)
            except _RetryableHTTPError as exc:
                last_error = exc
            except requests.RequestException as exc:
                last_error = exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(BACKOFF_BASE_SECONDS * (2**attempt))
        raise ModelCallError(f"provider call failed after {MAX_RETRIES} attempts: {last_error!r}")

    def _run_prompt(self, prompt_name: str, input_payload: dict[str, Any], required_output_keys: list[str]) -> dict[str, Any]:
        prompt = load_prompt(prompt_name)
        messages = _build_messages(prompt, input_payload)
        raw_text = self._call(messages, prompt.get("temperature", 0.0))
        return _extract_json(raw_text, required_output_keys)

    # --- Prompt-shaped public methods --------------------------------------
    # Each mirrors the input/output contract documented in the matching
    # src/prompts/*.yaml file exactly, so callers never need to know whether
    # the underlying model is OpenRouter- or Anthropic-hosted.

    def classify_ticket(self, ticket: dict[str, Any]) -> dict[str, Any]:
        self.last_call_used_fallback = False
        payload = {
            "ticket_channel": ticket.get("channel", "email"),
            "ticket_subject": ticket.get("subject", ""),
            "ticket_body": ticket.get("body", ""),
            "customer_tier": ticket.get("customer_tier", "standard"),
            "customer_region": ticket.get("customer_region", "north_america"),
            "language_fluency": ticket.get("language_fluency", "fluent"),
        }
        try:
            # triage_v2 (PR-01 v2.0): supersedes triage_v1 as of Stage 5's
            # prompt iteration (docs/Stage_3_Prompt_Library.md). v2 drops the
            # model-authored `rationale` field (NFR-01 latency) — this
            # client builds that field deterministically in code below from
            # the structured output, instead of trusting LLM-written prose.
            result = self._run_prompt(
                "triage_v2", payload,
                ["intent", "urgency", "confidence", "is_mandatory_escalation"],
            )
        except ModelCallError:
            self.last_call_used_fallback = True
            return self._heuristic.classify_ticket(ticket)

        coerced_from: str | None = None
        if result["intent"] not in KNOWN_INTENTS:
            # triage_v2.yaml instructs the model to use "unclear_request" for
            # anything outside the fixed 22-class list, but a weaker model
            # can still invent a label of its own (e.g. "api_key_failure"
            # instead of "api_key_issue"). Coerce rather than trust it
            # silently, so downstream metrics and routing see the actual
            # taxonomy, not a hallucinated 23rd class.
            coerced_from = result["intent"]
            result = {**result, "intent": "unclear_request", "is_mandatory_escalation": True}

        # Recalibration (PRD §8 Risk Log contingency): the raw stated
        # confidence from triage_v1 was measured badly overconfident
        # (storage/results_validation_gemini.json: 80-100% band was only
        # 57.1% actually correct). If src/models/fit_calibration.py has been
        # run against the development set, remap the raw confidence through
        # that fitted isotonic model before it reaches routing — routing
        # thresholds are meaningless against an uncalibrated score. No-ops
        # (returns the raw value unchanged) if no calibration dataset exists
        # yet, so this is never a hard dependency.
        raw_confidence = result["confidence"]
        calibrated_confidence = calibrate(raw_confidence)

        # Deterministic rationale (triage_v2 no longer returns one — see the
        # module-level call above). Built from the structured fields this
        # call already produced, not invented post-hoc prose.
        rationale = f"Classified as {result['intent']} (raw={raw_confidence:.2f}, cal={calibrated_confidence:.2f}) via triage_v2"
        if coerced_from is not None:
            rationale = f"[coerced from invalid intent {coerced_from!r}] {rationale}"

        result = {
            **result,
            "confidence": calibrated_confidence,
            "confidence_raw": raw_confidence,
            "rationale": rationale,
        }
        return result

    def generate_response(self, ticket: dict[str, Any], retrieved_docs: list[dict[str, Any]]) -> dict[str, Any]:
        self.last_call_used_fallback = False
        payload = {
            "ticket_channel": ticket.get("channel", "email"),
            "ticket_subject": ticket.get("subject", ""),
            "ticket_body": ticket.get("body", ""),
            "customer_tier": ticket.get("customer_tier", "standard"),
            "language_fluency": ticket.get("language_fluency", "fluent"),
            "retrieved_docs": [
                {"doc_id": d["doc_id"], "title": d["title"], "content": d["content"]} for d in retrieved_docs
            ],
        }
        must_not_claim = (ticket.get("ground_truth") or {}).get("must_not_claim")
        if must_not_claim:
            payload["must_not_claim"] = must_not_claim
        try:
            return self._run_prompt(
                "rag_response_v1", payload,
                ["can_answer", "response_text", "cited_doc_ids", "confidence"],
            )
        except ModelCallError:
            self.last_call_used_fallback = True
            return self._heuristic.generate_response(ticket, retrieved_docs)

    def guardrail_check(
        self,
        response_text: str,
        cited_doc_ids: list[str],
        retrieved_docs: list[dict[str, Any]],
        must_not_claim: list[str] | None,
    ) -> dict[str, Any]:
        """Secondary, LLM-based guardrail layer (FR-07). If the provider call
        fails after retries, this does NOT fall back to a silent "pass" —
        guardrail_check_v1.yaml's own stated governance position is that an
        unsafe automated reply is worse than an escalation, so an unavailable
        secondary check fails conservatively (verdict=fail) rather than
        skipping the check it cannot actually perform."""
        self.last_call_used_fallback = False
        source_doc_contents = [
            {"doc_id": d["doc_id"], "content": d["content"]} for d in retrieved_docs if d["doc_id"] in cited_doc_ids
        ]
        payload: dict[str, Any] = {
            "draft_response_text": response_text,
            "cited_doc_ids": cited_doc_ids,
            "source_doc_contents": source_doc_contents,
        }
        if must_not_claim:
            payload["must_not_claim"] = must_not_claim
        try:
            return self._run_prompt(
                "guardrail_check_v1", payload,
                ["verdict", "violations", "unsupported_sentences"],
            )
        except ModelCallError as exc:
            self.last_call_used_fallback = True
            return {
                "verdict": "fail",
                "violations": [{
                    "category": "tone",
                    "quoted_text": "",
                    "explanation": f"LLM guardrail layer unavailable, failing conservatively: {exc}",
                }],
                "unsupported_sentences": [],
            }

    def build_escalation_packet(
        self,
        ticket: dict[str, Any],
        classification: dict[str, Any],
        retrieved_docs: list[dict[str, Any]],
        escalation_reason: str,
    ) -> dict[str, Any]:
        """If the LLM call fails, falls back to a packet assembled directly
        from already-known fields rather than an LLM summary — this never
        blocks the escalation itself (the ticket still routes to a human
        either way), it only degrades the handoff packet's quality."""
        self.last_call_used_fallback = False
        payload = {
            "ticket_channel": ticket.get("channel", "email"),
            "ticket_subject": ticket.get("subject", ""),
            "ticket_body": ticket.get("body", ""),
            "customer_tier": ticket.get("customer_tier", "standard"),
            "triage_result": {
                "intent": classification["intent"],
                "urgency": classification["urgency"],
                "confidence": classification["confidence"],
                "is_mandatory_escalation": classification["is_mandatory_escalation"],
            },
            "retrieved_docs": [
                {"doc_id": d["doc_id"], "title": d["title"], "relevance_score": d["relevance_score"]}
                for d in retrieved_docs
            ],
            "escalation_reason": escalation_reason,
        }
        try:
            return self._run_prompt(
                "triage_packet_v1", payload,
                [
                    "ticket_summary", "predicted_intent", "predicted_urgency",
                    "documents_considered", "what_the_system_was_not_confident_about",
                    "recommended_starting_point",
                ],
            )
        except ModelCallError:
            self.last_call_used_fallback = True
            return {
                "ticket_summary": (ticket.get("body", "") or "")[:400],
                "predicted_intent": classification["intent"],
                "predicted_urgency": classification["urgency"],
                "documents_considered": [
                    {"doc_id": d["doc_id"], "title": d["title"], "why_it_was_or_was_not_sufficient": "escalation-packet LLM layer unavailable; relevance_score was %.3f" % d["relevance_score"]}
                    for d in retrieved_docs
                ],
                "what_the_system_was_not_confident_about": f"escalation_reason={escalation_reason} (packet LLM layer unavailable, showing raw reason code)",
                "recommended_starting_point": "none available (escalation-packet LLM layer unavailable)",
            }


class _RetryableHTTPError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body}")
        self.status_code = status_code
